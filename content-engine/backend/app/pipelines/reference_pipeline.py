"""
app/pipelines/reference_pipeline.py — Workflow 2: Competitor Link → Article.

Concrete implementation of BasePipeline for the "reference URL" workflow.

Step sequence:
  1. _fetch()    — async httpx GET via extract_article_content (bridged to sync)
  2. _extract()  — returns the Markdown from the same async call (bundled)
  3. _rewrite()  — Gemini SEO rewriter → returns structured AI payload dict
  4. _finalize() — bleach sanitise → upsert Article row → mark job completed

The async scraper is bridged with asyncio.run() because Celery workers
execute in a plain thread with no running event loop.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import bleach
import structlog

from app.logger import get_logger
from app.models.article import Article, ArticleStatus
from app.models.job import Job, JobStatus
from app.pipelines.base import BasePipeline
from app.scrapers.article_scraper import extract_article_content
from app.ai.gemini_writer import rewrite_article_for_seo, _substitute_image_placeholders
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

log = get_logger(__name__)

# Maximum content images allowed for Workflow 2 (clamped)
_MAX_CONTENT_IMAGES: int = 5
_MAX_FEATURED_AND_CONTENT: int = 6

# ── Bleach sanitisation config (mirrors gemini_writer) ────────────────────────
_ALLOWED_TAGS: list[str] = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "u",
    "a", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "figure", "figcaption",
    "img",
]
_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "a": ["href", "title", "rel", "target"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
    "blockquote": ["cite"],
    "code": ["class"],
    "img": ["src", "alt", "title", "width", "height", "loading", "class"],
}


class ReferencePipeline(BasePipeline):
    """
    Workflow 2: Fetch a competitor article URL and rewrite it as SEO-optimised HTML.

    Attributes:
        url: Source URL read from ``job.input_url`` at construction time.
    """

    def __init__(self, job_id: str, db: Session, job: Job) -> None:
        super().__init__(job_id, db, job)
        self.url: str = job.input_url or ""
        
        # ── Image configuration (from job.meta, sanitised & clamped) ─────────
        meta: dict = job.meta or {}
        raw_content_count = int(meta.get("content_image_count", 0))
        self.content_image_count: int = max(0, min(raw_content_count, _MAX_CONTENT_IMAGES))
        self.include_featured_image: bool = bool(meta.get("include_featured_image", True))
        
        # Clamp total to hard cap
        total = self.content_image_count + int(self.include_featured_image)
        if total > _MAX_FEATURED_AND_CONTENT:
            self.content_image_count = _MAX_FEATURED_AND_CONTENT - int(self.include_featured_image)

        self._logger: structlog.BoundLogger = log.bind(
            job_id=job_id,
            pipeline="reference",
            url=self.url,
            content_images=self.content_image_count,
            featured_image=self.include_featured_image,
        )

    # ── Public entry-point ────────────────────────────────────────────────────

    def run(self) -> dict:
        """
        Execute the full Workflow 2 pipeline.

        Progress checkpoints (visible to the polling UI):
          10 % → start
          30 % → fetch + extract complete
          60 % → AI rewrite complete
          90 % → article persisted
         100 % → job completed (set by task shell via _set_completed)

        Returns:
            A dict merged into ``job.meta`` by the task shell.
        """
        self._logger.info("reference_pipeline.run.start")
        self._set_job_status("scraping", progress=10)

        # ── Stage 1 & 2: Fetch URL and extract Markdown ───────────────────────
        markdown_content: str = self._fetch()
        
        # Save extracted source content to job meta
        if self.job.meta is None:
            self.job.meta = {}
        self.job.meta["extracted_source"] = markdown_content
        flag_modified(self.job, "meta")
        self.db.commit()

        self._logger.info(
            "reference_pipeline.fetch_extract.done",
            markdown_chars=len(markdown_content),
        )
        self._update_progress(30)

        # ── Stage 3: AI rewrite → structured payload ──────────────────────────
        self._set_job_status("writing", progress=35)
        ai_payload: dict = self._rewrite(markdown_content)
        self._logger.info(
            "reference_pipeline.rewrite.done",
            title=ai_payload.get("title"),
        )
        
        # ── Update job.meta with SEO data immediately ─────────────────────────
        if self.job.meta is None:
            self.job.meta = {}
        self.job.meta["seo_title"] = ai_payload.get("seo_title")
        self.job.meta["seo_description"] = ai_payload.get("seo_description")
        self.job.meta["target_keyword"] = ai_payload.get("target_keyword")
        flag_modified(self.job, "meta")
        self.db.commit()
        
        self._update_progress(60)

        # ── Stage 4: Generate images ──────────────────────────────────────────
        self._set_job_status("generating_images", progress=65)
        # We use prompts generated by Gemini in Stage 3
        featured_image, content_images = self._generate_images_from_payload(ai_payload)
        self._update_progress(80)

        # ── Stage 5: Sanitise + persist ───────────────────────────────────────
        self._set_job_status("finalizing", progress=85)
        self._finalize(
            parsed_ai_payload=ai_payload,
            featured_image=featured_image,
            content_images=content_images
        )
        self._update_progress(95)

        self._logger.info("reference_pipeline.run.complete")
        return {
            "source_url": self.url,
            "article_title": ai_payload.get("title"),
            "target_keyword": ai_payload.get("target_keyword"),
            "seo_title": ai_payload.get("seo_title"),
            "seo_description": ai_payload.get("seo_description"),
        }

    # ── Stage implementations ─────────────────────────────────────────────────

    def _fetch(self) -> str:
        """
        Fetch the source URL and extract article Markdown in one async call.

        Bridges the async scraper to this synchronous pipeline via asyncio.run().
        The Celery worker thread has no running event loop so this is safe.

        Returns:
            Clean Markdown string of the article body.

        Raises:
            ArticleFetchError: HTTP / network failure.
            ArticleExtractionError: trafilatura found no usable content.
        """
        self._logger.info("reference_pipeline.fetch.start")
        # asyncio.run() creates a brand-new event loop, runs the coroutine,
        # closes the loop, and returns the result — thread-safe for Celery.
        markdown: str = asyncio.run(extract_article_content(self.url))
        return markdown

    def _extract(self, raw_html: str) -> str:  # noqa: ARG002
        """
        Not called directly — extraction is bundled inside _fetch() via the
        async scraper. Kept to satisfy the original stub contract.
        """
        # extraction happens inside extract_article_content(); this is a no-op.
        return raw_html

    def _rewrite(self, markdown_content: str) -> dict:
        """
        Call Gemini to rewrite the Markdown and provide metadata.

        Returns:
            Dict with keys: title, markdown_content, target_keyword, slug,
                           seo_title, seo_description, image_x_prompt.

        Raises:
            GeminiWriterError: Any Vertex AI / generation failure.
        """
        self._logger.info("reference_pipeline.rewrite.start")
        return rewrite_article_for_seo(markdown_content)

    def _generate_images_from_payload(self, ai_payload: dict) -> tuple[dict | None, list[dict]]:
        """
        Generate images for the article using Imagen prompts from AI payload.
        """
        from app.ai.imagen_client import (
            generate_section_image,
            ImagenSafetyError,
            ImagenError,
        )

        featured_image: dict | None = None
        content_images: list[dict] = []

        # ── 1. Featured image (image_1_prompt) ────────────────────────────────
        if self.include_featured_image:
            prompt = ai_payload.get("image_1_prompt")
            if prompt:
                self._logger.info("reference_pipeline.images.featured.start", prompt=prompt)
                try:
                    featured_image = generate_section_image(prompt, aspect_ratio="16:9")
                    self._logger.info("reference_pipeline.images.featured.done")
                except (ImagenSafetyError, ImagenError) as exc:
                    self._logger.warning("reference_pipeline.images.featured.failed", error=str(exc))

        # ── 2. Content images (image_2 to 5 prompts) ─────────────────────────
        for i in range(2, self.content_image_count + 2):
            prompt = ai_payload.get(f"image_{i}_prompt")
            if prompt:
                self._logger.info(f"reference_pipeline.images.content_{i}.start", prompt=prompt)
                try:
                    img = generate_section_image(prompt, aspect_ratio="16:9")
                    content_images.append(img)
                    self._logger.info(f"reference_pipeline.images.content_{i}.done")
                except (ImagenSafetyError, ImagenError) as exc:
                    self._logger.warning(f"reference_pipeline.images.content_{i}.failed", error=str(exc))

        return featured_image, content_images

    def _generate_images(self, article_title: str) -> tuple[dict | None, list[dict]]:
        """Legacy method — redirected to _generate_images_from_payload."""
        # This is just a stub for BasePipeline if needed
        return None, []

    def _finalize(
        self,
        parsed_ai_payload: dict,
        featured_image: dict | None = None,
        content_images: list[dict] | None = None
    ) -> None:
        """
        Sanitise the AI-generated content and persist the Article to the database.
        """
        self._logger.info("reference_pipeline.finalize.start")

        # ── HTML handling ────────────────────────────────────────────────────
        # Now using html_content directly as requested from improved Gemini prompt
        html_body = parsed_ai_payload.get("html_content", "")

        # ── Image Placeholder Substitution ───────────────────────────────────
        # Workflow 2: image_1 is featured, image_2..5 are content
        all_generated_images = []
        if featured_image:
            all_generated_images.append(featured_image)
        if content_images:
            all_generated_images.extend(content_images)

        if all_generated_images:
            # Map "1" -> index 0 (featured), "2" -> index 1 (content 1), etc.
            placeholder_map = {str(i+1): i for i in range(len(all_generated_images))}
            html_body = _substitute_image_placeholders(
                html_body, placeholder_map, all_generated_images, self._logger
            )

        # ── Defence-in-depth sanitisation ────────────────────────────────────
        clean_html: str = bleach.clean(
            html_body,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            strip=True,
            strip_comments=True,
        )

        title: str = parsed_ai_payload.get("title", "Untitled Article").strip()
        slug: str = parsed_ai_payload.get("slug") or _slugify(title)
        
        # Compile all image dicts for storage
        all_images: list[dict] = []
        if featured_image:
            all_images.append({**featured_image, "role": "featured"})
        if content_images:
            for img in content_images:
                all_images.append({**img, "role": "content"})

        # ── Upsert Article ───────────────────────────────────────────────────
        article: Article | None = (
            self.db.query(Article)
            .filter(Article.job_id == self.job.id)
            .first()
        )

        if article is None:
            article = Article(
                job_id=self.job.id,
                user_id=self.job.user_id,
                integration_id=self.job.integration_id,
            )
            self.db.add(article)

        article.title = title
        article.slug = slug
        article.html_content = clean_html
        article.focus_keyword = parsed_ai_payload.get("target_keyword")
        article.meta_description = parsed_ai_payload.get("seo_description")
        article.word_count = len(re.findall(r"\w+", clean_html))
        article.images = all_images
        article.source_urls = [self.url]
        article.article_status = ArticleStatus.DRAFT_READY
        article.updated_at = datetime.now(timezone.utc)

        # Save extra SEO metadata to job.meta for frontend visibility
        if self.job.meta is None:
            self.job.meta = {}
        self.job.meta["seo_title"] = parsed_ai_payload.get("seo_title")
        self.job.meta["seo_description"] = parsed_ai_payload.get("seo_description")
        self.job.meta["target_keyword"] = parsed_ai_payload.get("target_keyword")
        flag_modified(self.job, "meta")

        self.db.commit()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _set_job_status(self, status: str, progress: int) -> None:
        """Write a granular status string and progress percentage to the DB."""
        self.job.status = status
        self.job.progress_pct = progress
        self.db.commit()


# ── Module-level slug helper (avoids circular import with gemini_writer) ────────

def _slugify(text: str) -> str:
    """Minimal slug generator — mirrors the one in gemini_writer.py."""
    import unicodedata
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower())
    return slug.strip("-")[:580] or "article"
