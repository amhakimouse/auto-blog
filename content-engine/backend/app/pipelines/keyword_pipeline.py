"""
app/pipelines/keyword_pipeline.py — Workflow 1: Keyword → Article.

Concrete implementation of BasePipeline for the "keyword" workflow.

Changes vs. previous version:
  - _scrape_outline(): NEW — extracts H1/H2/H3 headings + first paragraph per
    heading from competitor HTML using BeautifulSoup instead of passing raw text.
    Passed to gap analysis instead of truncated raw content.
  - _generate_images(): respects job.meta["content_image_count"] (0-5) and
    job.meta["include_featured_image"] (bool). Distributes content images evenly
    across sections. Generates featured image separately when requested.
  - _finalize(): passes section_image_indices, related_articles, and
    generated_images through to write_article_from_outline() for placeholder
    substitution.
  - Image distribution: _compute_image_section_indices() helper.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import bleach
import structlog
from bs4 import BeautifulSoup

from app.logger import get_logger
from app.models.article import Article, ArticleStatus
from app.models.job import Job
from app.pipelines.base import BasePipeline
from sqlalchemy.orm import Session

log = get_logger(__name__)

# ── Bleach sanitisation config ────────────────────────────────────────────────
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

# Maximum competitor URLs to scrape
_MAX_URLS: int = 5
# Minimum scraped outlines required before gap analysis proceeds
_MIN_SCRAPED: int = 2

# Caps on image configuration
_MAX_CONTENT_IMAGES: int = 5
_MAX_FEATURED_AND_CONTENT: int = 6  # total hard cap


class KeywordPipeline(BasePipeline):
    """
    Workflow 1: Research, gap-analyse, outline, and write an SEO article for *keyword*.

    Pipeline stages:
      1. _search()              — DuckDuckGo → top-N competitor URLs
      2. _scrape_outline()      — async httpx → BeautifulSoup outline extraction
      3. _analyze_gaps()        — Gemini gap analysis on outline summaries
      4. _generate_outline()    — Gemini structured outline
      5. _write_article()       — Gemini per-section HTML with image placeholders
      6. _generate_images()     — Imagen for featured + content images (configurable)
      7. _finalize()            — sanitise → upsert Article → WP publish
    """

    def __init__(self, job_id: str, db: Session, job: Job) -> None:
        super().__init__(job_id, db, job)
        self.keyword: str = job.input_keyword or ""

        # ── Image configuration (from job.meta, sanitised & clamped) ─────────
        meta: dict = job.meta or {}
        raw_content_count = int(meta.get("content_image_count", 1))
        self.content_image_count: int = max(0, min(raw_content_count, _MAX_CONTENT_IMAGES))
        self.include_featured_image: bool = bool(meta.get("include_featured_image", True))
        # Clamp total to hard cap
        total = self.content_image_count + int(self.include_featured_image)
        if total > _MAX_FEATURED_AND_CONTENT:
            self.content_image_count = _MAX_FEATURED_AND_CONTENT - int(self.include_featured_image)

        # ── Related articles for internal linking (from job.meta) ─────────────
        self.related_articles: list[dict] = meta.get("related_articles", [])[:3]

        self._logger: structlog.BoundLogger = log.bind(
            job_id=job_id,
            pipeline="keyword",
            keyword=self.keyword,
            content_images=self.content_image_count,
            featured_image=self.include_featured_image,
        )

    # ── Public entry-point ────────────────────────────────────────────────────

    def run(self) -> dict:
        """
        Execute the full Workflow 1 pipeline.

        Progress checkpoints:
           5 % → start
          20 % → search complete
          35 % → outline scraping complete
          50 % → gap analysis complete
          65 % → article outline complete
          85 % → article written
          90 % → images generated
          92 % → article persisted
         100 % → job completed (set by task shell)
        """
        self._logger.info("keyword_pipeline.run.start")
        self._set_job_status("searching", progress=5)

        # ── Stage 1: Search ───────────────────────────────────────────────────
        urls: list[str] = self._search()
        self._logger.info("keyword_pipeline.search.done", url_count=len(urls))
        self._update_progress(20)

        # ── Stage 2: Scrape & extract structural outlines ─────────────────────
        self._set_job_status("scraping", progress=22)
        outline_articles: list[dict] = self._scrape_outline(urls)
        self._logger.info(
            "keyword_pipeline.scrape_outline.done",
            scraped=len(outline_articles),
            skipped=len(urls) - len(outline_articles),
        )
        self._update_progress(35)

        # ── Stage 3: Content gap analysis ─────────────────────────────────────
        self._set_job_status("analyzing", progress=37)
        gap_analysis: dict = self._analyze_gaps(outline_articles)
        self._logger.info(
            "keyword_pipeline.gap_analysis.done",
            focus_keyword=gap_analysis.get("focus_keyword"),
            gaps=len(gap_analysis.get("content_gaps", [])),
        )
        self._update_progress(50)

        # ── Stage 4: Generate outline ──────────────────────────────────────────
        self._set_job_status("outlining", progress=52)
        outline: list[dict] = self._generate_outline(gap_analysis)
        self._logger.info("keyword_pipeline.outline.done", sections=len(outline))
        self._update_progress(65)

        # ── Stage 5: Write article ─────────────────────────────────────────────
        self._set_job_status("writing", progress=67)
        # Compute which sections should receive a content image placeholder
        content_sections = [s for s in outline if "_meta" not in s]
        section_image_indices = _compute_image_section_indices(
            section_count=len(content_sections),
            image_count=self.content_image_count,
        )
        article_payload: dict = self._write_article(
            outline,
            gap_analysis,
            section_image_indices=section_image_indices,
        )
        self._logger.info(
            "keyword_pipeline.write.done",
            title=article_payload.get("title"),
            word_count=article_payload.get("word_count"),
        )
        self._update_progress(85)

        # ── Stage 6: Generate images ───────────────────────────────────────────
        self._update_progress(87)
        title = article_payload.get("title", "Article")
        sections_for_images = [s for s in outline if "_meta" not in s]
        featured_image: dict | None
        content_images: list[dict]
        featured_image, content_images = self._generate_images(
            outline=sections_for_images,
            article_title=title,
            section_image_indices=section_image_indices,
        )
        self._update_progress(90)

        # ── Stage 7: Persist to DB ─────────────────────────────────────────────
        self._set_job_status("writing", progress=91)
        self._finalize(
            article_payload=article_payload,
            featured_image=featured_image,
            content_images=content_images,
            section_image_indices=section_image_indices,
            source_urls=urls,
        )
        self._update_progress(92)

        self._logger.info("keyword_pipeline.run.complete")
        return {
            "scraped_urls": urls,
            "focus_keyword": gap_analysis.get("focus_keyword"),
            "outline_sections": len([s for s in outline if "_meta" not in s]),
            "article_title": article_payload.get("title"),
            "word_count": article_payload.get("word_count"),
        }

    # ── Stage implementations ──────────────────────────────────────────────────

    def _search(self) -> list[str]:
        from app.scrapers.duckduckgo import DuckDuckGoScraper
        self._logger.info("keyword_pipeline.search.start")
        scraper = DuckDuckGoScraper(polite_delay_ms=800)
        return scraper.search(self.keyword, n=_MAX_URLS)

    def _scrape_outline(self, urls: list[str]) -> list[dict]:
        """
        Concurrently fetch each competitor URL and extract a dense structural outline.

        Instead of passing raw article bodies (expensive in tokens), we extract:
          - Every H1 / H2 / H3 heading in document order.
          - The first <p> sibling immediately following each heading.

        This gives Gemini a complete structural overview of what competitors wrote
        at a fraction of the token cost compared to full-body extraction.

        Returns:
            List of dicts: ``{"url": str, "content": str}`` — the "content" is
            the outline summary (plain text, ~500-900 chars per article).

        Raises:
            RuntimeError: Fewer than ``_MIN_SCRAPED`` outlines could be extracted.
        """
        from app.scrapers.article_scraper import (
            ArticleScraperError,
            _fetch_raw_html,   # raw HTML fetcher (not the trafilatura wrapper)
        )

        self._logger.info("keyword_pipeline.scrape_outline.start", url_count=len(urls))
        results: list[dict] = []

        for url in urls:
            url_logger = self._logger.bind(url=url)
            try:
                # Fetch raw HTML (async) then extract outline (sync)
                raw_html: str = asyncio.run(_fetch_raw_html(url))
                outline_text: str = _extract_outline_summary(raw_html)
                if outline_text:
                    results.append({"url": url, "content": outline_text})
                    url_logger.info(
                        "keyword_pipeline.scrape_outline.url_ok",
                        chars=len(outline_text),
                    )
                else:
                    url_logger.warning("keyword_pipeline.scrape_outline.empty_outline")
            except ArticleScraperError as exc:
                url_logger.warning(
                    "keyword_pipeline.scrape_outline.url_failed", error=str(exc)
                )
            except Exception as exc:
                url_logger.error(
                    "keyword_pipeline.scrape_outline.unexpected_error",
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )

        if len(results) < _MIN_SCRAPED:
            raise RuntimeError(
                f"Only {len(results)} of {len(urls)} URLs yielded valid outlines "
                f"(minimum required: {_MIN_SCRAPED}). Cannot proceed with gap analysis."
            )

        return results

    def _analyze_gaps(self, outline_articles: list[dict]) -> dict:
        from app.ai.gemini_writer import analyze_content_gaps
        self._logger.info(
            "keyword_pipeline.gap_analysis.start",
            article_count=len(outline_articles),
        )
        return analyze_content_gaps(outline_articles)

    def _generate_outline(self, gap_analysis: dict) -> list[dict]:
        from app.ai.gemini_writer import generate_article_outline
        self._logger.info("keyword_pipeline.outline.start")
        return generate_article_outline(gap_analysis)

    def _write_article(
        self,
        outline: list[dict],
        gap_analysis: dict,
        section_image_indices: set[int] | None = None,
    ) -> dict:
        """
        Write all sections via Gemini. Injects image placeholder instructions
        for sections at the given indices and related_articles for internal linking.

        Note: generated_images are NOT available here (they come from _generate_images
        which runs after writing). The placeholders are substituted in _finalize.
        """
        from app.ai.gemini_writer import write_article_from_outline
        self._logger.info("keyword_pipeline.write.start")
        return write_article_from_outline(
            outline=outline,
            gap_analysis=gap_analysis,
            section_image_indices=section_image_indices,
            related_articles=self.related_articles if self.related_articles else None,
            generated_images=None,  # substituted in _finalize after image generation
        )

    def _generate_images(
        self,
        outline: list[dict],
        article_title: str,
        section_image_indices: set[int],
    ) -> tuple[dict | None, list[dict]]:
        """
        Generate images for the article using Imagen.

        Returns:
            (featured_image, content_images) where:
              - featured_image: image dict (base64_image, alt_text, …) or None
              - content_images: list of image dicts in section order (may be shorter
                than section_image_indices if some generations fail)
        """
        from app.ai.imagen_client import (
            build_section_prompt,
            generate_section_image,
            ImagenSafetyError,
            ImagenError,
        )

        featured_image: dict | None = None
        content_images: list[dict] = []

        # ── 1. Featured image (hero) ──────────────────────────────────────────
        if self.include_featured_image and outline:
            first_section = next(
                (s for s in outline if s.get("type") != "conclusion"), outline[0]
            )
            hero_heading = first_section.get("heading", "Introduction")
            hero_prompt = build_section_prompt(article_title, hero_heading)
            self._logger.info("keyword_pipeline.images.featured.start", prompt=hero_prompt)
            try:
                featured_image = generate_section_image(hero_prompt, aspect_ratio="16:9")
                self._logger.info("keyword_pipeline.images.featured.done")
            except ImagenSafetyError as exc:
                self._logger.warning(
                    "keyword_pipeline.images.featured.safety_blocked", error=str(exc)
                )
            except ImagenError as exc:
                self._logger.warning(
                    "keyword_pipeline.images.featured.error", error=str(exc)
                )

        # ── 2. Content images (in section order) ─────────────────────────────
        sorted_indices = sorted(section_image_indices)
        for sec_idx in sorted_indices:
            if sec_idx >= len(outline):
                continue
            if len(content_images) >= self.content_image_count:
                break

            section = outline[sec_idx]
            heading = section.get("heading", f"Section {sec_idx + 1}")
            prompt = build_section_prompt(article_title, heading)
            self._logger.info(
                "keyword_pipeline.images.content.start",
                section_idx=sec_idx,
                prompt=prompt,
            )
            try:
                img = generate_section_image(prompt, aspect_ratio="16:9")
                content_images.append(img)
                self._logger.info(
                    "keyword_pipeline.images.content.done", section_idx=sec_idx
                )
            except ImagenSafetyError as exc:
                self._logger.warning(
                    "keyword_pipeline.images.content.safety_blocked",
                    section_idx=sec_idx,
                    error=str(exc),
                )
            except ImagenError as exc:
                self._logger.warning(
                    "keyword_pipeline.images.content.error",
                    section_idx=sec_idx,
                    error=str(exc),
                )

        return featured_image, content_images

    def _finalize(
        self,
        article_payload: dict,
        featured_image: dict | None,
        content_images: list[dict],
        section_image_indices: set[int],
        source_urls: list[str],
    ) -> None:
        """
        Substitute IMAGE_PLACEHOLDER_{id} tags, sanitise HTML, persist Article,
        and publish to WordPress if an Integration exists for the job owner.
        """
        from app.ai.gemini_writer import _substitute_image_placeholders

        self._logger.info("keyword_pipeline.finalize.start")

        # ── Re-run placeholder substitution now that we have real images ──────
        # write_article_from_outline was called with generated_images=None, so
        # placeholders are still present in the html. We substitute here.
        raw_html: str = article_payload.get("html_content", "")
        import structlog as _structlog
        finalize_logger = _structlog.get_logger(__name__).bind(
            job_id=self.job_id, stage="finalize"
        )

        # Build the same placeholder_map that write_article_from_outline would have.
        placeholder_map: dict[str, int] = {
            str(i): i for i in range(len(content_images))
        }
        raw_html = _substitute_image_placeholders(
            raw_html, placeholder_map, content_images, finalize_logger
        )

        # ── Defence-in-depth sanitisation ────────────────────────────────────
        clean_html: str = bleach.clean(
            raw_html,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
            strip=True,
            strip_comments=True,
        )

        title: str = article_payload.get("title", "Untitled Article").strip()
        slug: str = article_payload.get("slug") or _slugify(title)
        word_count: int = article_payload.get("word_count") or len(
            re.findall(r"\w+", clean_html)
        )
        meta_description: str = article_payload.get("meta_description", "")
        focus_keyword: str = article_payload.get("focus_keyword", "")
        tags: list[str] = article_payload.get("semantic_keywords", [])[:10]

        # Compile all image dicts for storage (featured first, then content)
        all_images: list[dict] = []
        if featured_image:
            all_images.append({**featured_image, "role": "featured"})
        for img in content_images:
            all_images.append({**img, "role": "content"})

        # ── Upsert Article ────────────────────────────────────────────────────
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
            self._logger.info("keyword_pipeline.article.created")
        else:
            self._logger.info("keyword_pipeline.article.updating_existing")

        article.title = title
        article.slug = slug
        article.html_content = clean_html
        article.word_count = word_count
        article.meta_description = meta_description or None
        article.focus_keyword = focus_keyword or None
        article.tags = tags
        article.images = all_images
        article.source_urls = source_urls
        article.article_status = ArticleStatus.DRAFT_READY   # awaiting human review
        article.updated_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(article)

        self._logger.info(
            "keyword_pipeline.finalize.db_done",
            article_id=str(article.id),
            article_status=article.article_status,
            word_count=word_count,
            images_total=len(all_images),
        )
        # ── Auto-publish is intentionally REMOVED ─────────────────────────────
        # Articles land in `draft_ready` status and wait for human review via
        # the POST /api/v1/articles/{id}/publish endpoint.

        # (end of _finalize — publishing is handled by POST /articles/{id}/publish)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _set_job_status(self, status: str, progress: int) -> None:
        """Write a granular status string and progress pct to the DB."""
        self.job.status = status
        self.job.progress_pct = progress
        self.db.commit()


# ── Module-level helpers ──────────────────────────────────────────────────────

def _extract_outline_summary(raw_html: str) -> str:
    """
    Extract a dense structural outline from competitor HTML.

    For each heading tag (H1/H2/H3) find in document order:
      - The heading text.
      - The first non-empty <p> sibling immediately following that heading.

    Returns plain text in the format:
        ## Heading Text
        First paragraph text (truncated to 200 chars).

    This gives Gemini a complete picture of what the article covers at a
    fraction of the token cost of the full body.
    """
    try:
        soup = BeautifulSoup(raw_html, "html.parser")
    except Exception:
        return ""

    lines: list[str] = []
    for heading in soup.find_all(["h1", "h2", "h3"]):
        heading_text = heading.get_text(separator=" ", strip=True)
        if not heading_text:
            continue

        tag_name = heading.name  # "h1", "h2", "h3"
        prefix = "#" * int(tag_name[1])   # → "#", "##", "###"
        lines.append(f"{prefix} {heading_text}")

        # Walk next siblings to find the first non-empty paragraph
        for sibling in heading.next_siblings:
            if hasattr(sibling, "name"):
                if sibling.name == "p":
                    para_text = sibling.get_text(separator=" ", strip=True)
                    if para_text:
                        # Truncate to 200 chars to keep outline dense
                        lines.append(para_text[:200])
                        break
                elif sibling.name in ("h1", "h2", "h3"):
                    # Reached the next heading — no paragraph found, stop looking
                    break

    return "\n".join(lines)


def _compute_image_section_indices(section_count: int, image_count: int) -> set[int]:
    """
    Distribute *image_count* images evenly across *section_count* sections.

    Uses integer spacing to find the best insertion points. Never places an
    image in the first section (introduction) or the last section (conclusion),
    unless there are very few sections.

    Examples:
        sections=10, images=2  → {3, 7}   (approx. every 10/3 = 3.3 sections)
        sections=10, images=3  → {2, 5, 8}
        sections=5,  images=1  → {2}
        sections=2,  images=2  → {0, 1}   (fallback: just fill from start)
    """
    if image_count <= 0 or section_count <= 0:
        return set()

    image_count = min(image_count, section_count)

    # Try to avoid first (intro) and last (conclusion) sections
    usable_start = 1 if section_count > 2 else 0
    usable_end = section_count - 1 if section_count > 2 else section_count
    usable_count = usable_end - usable_start

    if usable_count <= 0:
        # Very few sections — just place images starting from 0
        return set(range(min(image_count, section_count)))

    # Divide the usable range into (image_count + 1) equal intervals
    # and take the midpoints
    indices: set[int] = set()
    for i in range(1, image_count + 1):
        idx = usable_start + int(usable_count * i / (image_count + 1))
        indices.add(min(idx, usable_end - 1))

    return indices


def _slugify(text: str) -> str:
    """Minimal slug generator — mirrors the one in gemini_writer.py."""
    import unicodedata
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower())
    return slug.strip("-")[:580] or "article"
