"""
app/pipelines/reference_pipeline.py — Workflow 2: Reference URL → Article.

8-stage pipeline with granular status labels surfaced to the frontend via SSE:

  Stage 1  (10%)  scraping           Extract content from the reference URL
  Stage 2  (30%)  content_extracted  Extracted content stored; shown in frontend
  Stage 3  (45%)  analyzing          Single-shot Gemini unified analysis
  Stage 4  (60%)  awaiting_preview   Analysis table shown; user approves to continue
  --- user clicks Approve & Resume ---
  Stage 5  (65%)  generating_assets  Concurrent Imagen + WebP + WP media upload
  Stage 6  (85%)  writing            Gemini final HTML structuring
  Stage 7  (92%)  publishing         WP create draft post + RankMath SEO meta
  Stage 8  (100%) completed          Article row saved; credits deducted by BaseJobTask

Same rules as KeywordPipeline:
  - Existing scraping system (article_scraper.py) is untouched.
  - Images via asyncio.gather — no Celery group.
  - bleach.clean() always applied.
"""
from __future__ import annotations

import asyncio

import structlog

from app.logger import get_logger
from app.models.job import Job, JobStatus
from app.pipelines.base import BasePipeline
from sqlalchemy.orm import Session

log = get_logger(__name__)

_MAX_IMAGES: int = 5


class ReferencePipeline(BasePipeline):
    """Workflow 2: Reference URL → SEO Article."""

    def __init__(self, job_id: str, db: Session, job: Job) -> None:
        super().__init__(job_id, db, job)
        self.url: str = job.input_url or ""
        meta: dict = job.meta or {}
        self.content_image_count: int = max(
            0, min(int(meta.get("content_image_count", 1)), _MAX_IMAGES)
        )
        self.include_featured_image: bool = bool(meta.get("include_featured_image", True))
        
        # ── Granular Content Controls ─────────────────────────────────────
        from app.schemas.job import ContentCustomization
        raw_customization = meta.get("customization", {})
        try:
            self.customization = ContentCustomization.model_validate(raw_customization)
        except Exception:
            self.customization = ContentCustomization()

        self._logger: structlog.BoundLogger = log.bind(
            job_id=job_id,
            pipeline="reference",
            url=self.url,
        )

    # ── Entry point ────────────────────────────────────────────────────────────

    def run(self) -> dict:
        self._logger.info("reference_pipeline.run", status=self.job.status)

        # ── Stage 1: Scrape reference URL ──────────────────────────────────────
        self._set_step("scraping", 10, f"Extracting content from {self.url}...")
        content = self._scrape_url()

        # ── Stage 2: Content extracted ─────────────────────────────────────────
        self._set_step(
            "content_extracted", 30,
            "Content extracted ✓",
            meta_patch={
                "scraped_urls": [
                    {"url": self.url, "chars": len(content), "status": "ok"}
                ],
                "extracted_source": content[:20_000],
            },
        )

        # ── Stage 3: Unified Gemini analysis ──────────────────────────────────
        self._set_step("analyzing", 45, "Running AI content analysis...")
        from app.ai.gemini_writer import run_unified_analysis

        num_images = self.content_image_count + (1 if self.include_featured_image else 0)
        analysis = run_unified_analysis(
            content,
            num_images=num_images,
            logger=self._logger,
            language=self.customization.language,
            customization=self.customization,
        )

        # We no longer pause for user preview. Store analysis, continue.
        self._set_step(
            JobStatus.PROCESSING.value, 60,
            "Analysis complete. Proceeding to asset generation...",
            meta_patch={"unified_analysis": analysis},
        )

        integration = self._load_integration()

        # ── Stage 5: Concurrent image generation ──────────────────────────────
        num_images = self.content_image_count + (1 if self.include_featured_image else 0)
        prompts = analysis.get("image_prompts", [])[:num_images]

        # ── Smart Internal Linking: fetch WP posts (Redis-cached, graceful fallback) ──
        # Priority: manual related_articles in job.meta override auto-fetched posts.
        link_candidates: list[dict] = []
        manual_links = (self.job.meta or {}).get("related_articles", [])
        if manual_links:
            link_candidates = manual_links
            self._logger.info(
                "reference_pipeline.internal_links.manual",
                count=len(link_candidates),
            )
        elif integration:
            from app.ai.internal_linker import fetch_link_candidates
            try:
                link_candidates = fetch_link_candidates(
                    integration_id=str(integration.id),
                    wp_url=integration.wp_url,
                    wp_username=integration.wp_username,
                    wp_app_password=integration.wp_password,
                    logger=self._logger,
                )
                self._logger.info(
                    "reference_pipeline.internal_links.auto_fetched",
                    count=len(link_candidates),
                )
            except Exception as exc:
                self._logger.warning(
                    "reference_pipeline.internal_links.fetch_failed",
                    error=str(exc),
                )

        # ── Enhance image prompts with image_style from ContentCustomization ──
        if self.customization.image_style != "standard" and prompts:
            from app.ai.prompt_builder import enhance_imagen_prompt
            prompts = [
                {
                    **p,
                    "prompt": enhance_imagen_prompt(
                        p.get("prompt", ""), self.customization.image_style
                    ),
                }
                for p in prompts
            ]
            self._logger.info(
                "reference_pipeline.image_prompts_enhanced",
                style=self.customization.image_style,
                count=len(prompts),
            )

        self._set_step(
            "generating_assets", 65,
            f"Starting {len(prompts)} image generation(s)...",
        )

        def _step_cb(label: str) -> None:
            self._set_step("generating_assets", 70, label)

        from app.pipelines._pipeline_helpers import run_concurrent_image_pipeline
        image_results = run_concurrent_image_pipeline(
            prompts=prompts,
            integration=integration,
            logger=self._logger,
            step_cb=_step_cb,
        )

        # Store image previews for frontend
        self._set_step(
            "writing", 85,
            "Writing final SEO article HTML...",
            meta_patch={
                "generated_images": [
                    {
                        "index": r["index"],
                        "status": r["status"],
                        "webp_base64": r.get("webp_base64"),
                        "wp_url": r.get("wp_url"),
                        "metadata": r.get("metadata", {}),
                    }
                    for r in image_results
                ]
            },
        )

        # ── Stage 6: Final HTML structuring ───────────────────────────────────
        from app.pipelines._pipeline_helpers import generate_final_html
        final_html = generate_final_html(
            analysis, 
            image_results, 
            self._logger,
            link_candidates=link_candidates,
            customization=self.customization,
            language=self.customization.language,
        )

        payload = {
            "title": analysis["title"],
            "html_content": final_html,
            "slug": analysis["slug"],
            "meta_description": analysis["seo_description"],
            "focus_keyword": analysis["focus_keyword"],
        }

        from app.pipelines._pipeline_helpers import save_article
        article = save_article(self.db, self.job, payload, image_results)

        # ── Stage 7: Publish to WordPress ─────────────────────────────────────
        if integration:
            self._set_step("publishing", 92, "Creating WordPress draft post...")
            self._publish_to_wp(article, integration, analysis)

        # ── Stage 8: Complete ──────────────────────────────────────────────────
        self._set_step(JobStatus.COMPLETED.value, 100, "Article ready! ✓")
        return {"status": "completed", "article_id": str(article.id)}

    # ── Stage helpers ──────────────────────────────────────────────────────────

    def _scrape_url(self) -> str:
        """Extract content from the reference URL using the existing scraper."""
        from app.scrapers.article_scraper import (
            extract_article_content,
            ArticleExtractionError,
        )
        try:
            content = asyncio.run(extract_article_content(self.url))
            self._logger.info("reference_pipeline.scrape_ok", chars=len(content))
            return content
        except ArticleExtractionError as exc:
            self._logger.error("reference_pipeline.scrape_failed", error=str(exc))
            raise
        except Exception as exc:
            self._logger.error("reference_pipeline.scrape_unexpected", error=str(exc))
            raise

    def _load_integration(self):
        if not self.job.integration_id:
            return None
        from app.models.integration import Integration
        integration = (
            self.db.query(Integration)
            .filter(Integration.id == self.job.integration_id)
            .first()
        )
        if integration is None:
            self._logger.warning(
                "reference_pipeline.integration_not_found",
                integration_id=str(self.job.integration_id),
            )
        return integration

    def _publish_to_wp(self, article, integration, analysis: dict) -> None:
        """Create a WordPress draft post and update RankMath SEO meta."""
        import base64 as _b64

        from app.integrations.wordpress import (
            WordPressPublisher,
            WordPressPublishError,
            WordPressSEOError,
        )

        # Resolve featured image from article images
        image_bytes: bytes | None = None
        featured_media_id: int | None = None
        image_alt: str = article.title
        for img in article.images or []:
            if img.get("wp_media_id"):
                featured_media_id = img["wp_media_id"]
                image_alt = (img.get("metadata") or {}).get("alt_text", article.title)
                break
            elif img.get("webp_base64") and not img.get("url"):
                try:
                    image_bytes = _b64.b64decode(img["webp_base64"])
                    image_alt = (img.get("metadata") or {}).get("alt_text", article.title)
                    break
                except Exception:
                    pass

        publisher = WordPressPublisher(
            wp_site_url=integration.wp_url,
            wp_username=integration.wp_username,
            wp_app_password=integration.wp_password,
        )

        try:
            pub_result = asyncio.run(
                publisher.publish(
                    title=article.title,
                    html_content=article.html_content,
                    image_bytes=image_bytes,
                    featured_media_id=featured_media_id,
                    image_alt=image_alt,
                    slug=article.slug,
                    meta_description=article.meta_description,
                    focus_keyword=article.focus_keyword,
                    post_status="draft",
                )
            )
            article.wp_post_id = pub_result["wp_post_id"]
            article.wp_post_url = pub_result["wp_post_url"]
            if pub_result.get("featured_image_url"):
                article.featured_image_url = pub_result["featured_image_url"]
            self.db.commit()
            self._logger.info(
                "reference_pipeline.wp_published",
                wp_post_id=article.wp_post_id,
                url=article.wp_post_url,
            )
        except WordPressSEOError as exc:
            # SEO meta failed — article was still created, log and continue
            self._logger.warning("reference_pipeline.wp_seo_failed", error=str(exc))
            warnings = self.job.meta.get("warnings", [])
            warnings.append(f"RankMath SEO Warning: {exc}")
            self.job.meta["warnings"] = warnings
            self.db.commit()
        except WordPressPublishError as exc:
            self._logger.warning("reference_pipeline.wp_publish_failed", error=str(exc))
            warnings = self.job.meta.get("warnings", [])
            warnings.append(f"WP Publish Warning: {exc}")
            self.job.meta["warnings"] = warnings
            self.db.commit()
        except Exception as exc:
            self._logger.error("reference_pipeline.wp_unexpected", error=str(exc))
