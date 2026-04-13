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
from app.ai.gemini_writer import rewrite_article_for_seo
from sqlalchemy.orm import Session

log = get_logger(__name__)

# ── Bleach sanitisation config (mirrors gemini_writer) ────────────────────────
_ALLOWED_TAGS: list[str] = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "u",
    "a", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "figure", "figcaption",
]
_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "a": ["href", "title", "rel", "target"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
    "blockquote": ["cite"],
    "code": ["class"],
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
        self._logger: structlog.BoundLogger = log.bind(
            job_id=job_id,
            pipeline="reference",
            url=self.url,
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
            word_count=ai_payload.get("word_count"),
        )
        self._update_progress(60)

        # ── Stage 4: Sanitise + persist ───────────────────────────────────────
        self._set_job_status("writing", progress=70)
        self._finalize(ai_payload)
        self._update_progress(90)

        self._logger.info("reference_pipeline.run.complete")
        return {
            "source_url": self.url,
            "article_title": ai_payload.get("title"),
            "word_count": ai_payload.get("word_count"),
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
        Call Gemini to rewrite the Markdown as SEO HTML.

        Returns:
            Dict with keys: title, html_content, word_count, slug.

        Raises:
            GeminiWriterError: Any Vertex AI / generation failure.
        """
        self._logger.info("reference_pipeline.rewrite.start")
        return rewrite_article_for_seo(markdown_content)

    def _finalize(self, parsed_ai_payload: dict) -> None:
        """
        Sanitise the AI-generated HTML and persist the Article to the database.

        Steps:
          1. Run bleach.clean() on ``html_content`` (defence-in-depth XSS strip).
          2. Get-or-create the Article row for this job (idempotent for Celery retries).
          3. Populate all Article fields from the AI payload.
          4. Commit to the database.

        Args:
            parsed_ai_payload: Dict returned by ``_rewrite()``.
                               Must contain ``title`` and ``html_content``.
        """
        self._logger.info("reference_pipeline.finalize.start")

        # ── Defence-in-depth sanitisation (AI output is untrusted) ────────────
        raw_html: str = parsed_ai_payload.get("html_content", "")
        clean_html: str = bleach.clean(
            raw_html,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            strip=True,
            strip_comments=True,
        )

        title: str = parsed_ai_payload.get("title", "Untitled Article").strip()
        slug: str = parsed_ai_payload.get("slug") or _slugify(title)
        word_count: int = parsed_ai_payload.get("word_count") or len(
            re.findall(r"\w+", clean_html)
        )

        # ── Upsert Article (safe for Celery retries) ──────────────────────────
        article: Article | None = (
            self.db.query(Article)
            .filter(Article.job_id == self.job.id)
            .first()
        )

        if article is None:
            article = Article(
                job_id=self.job.id,
                user_id=self.job.user_id,
                integration_id=self.job.integration_id,  # brand profile FK
            )
            self.db.add(article)
            self._logger.info("reference_pipeline.article.created")
        else:
            self._logger.info("reference_pipeline.article.updating_existing")

        article.title = title
        article.slug = slug
        article.html_content = clean_html
        article.word_count = word_count
        article.source_urls = [self.url]
        article.article_status = ArticleStatus.DRAFT_READY   # awaiting human review
        article.updated_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(article)

        self._logger.info(
            "reference_pipeline.finalize.db_done",
            article_id=str(article.id),
            article_status=article.article_status,
            word_count=word_count,
        )
        # ── Auto-publish removed ────────────────────────────────────────────
        # Publishing is exclusively handled by POST /api/v1/articles/{id}/publish.

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
