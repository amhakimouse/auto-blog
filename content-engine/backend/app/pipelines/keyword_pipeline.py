"""
app/pipelines/keyword_pipeline.py — Workflow 1: Keyword → Article.

Refactored to use the Unified Analysis Pattern (Single-Shot Gemini).
Includes a pause for user preview and robust asset/SEO processing.
"""
from __future__ import annotations

import asyncio
import base64
import re
from datetime import datetime, timezone

import bleach
import structlog
from bs4 import BeautifulSoup

from app.logger import get_logger
from app.models.article import Article, ArticleStatus
from app.models.job import Job, JobStatus
from app.pipelines.base import BasePipeline
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

log = get_logger(__name__)

# ── Configuration & Caps ──────────────────────────────────────────────────────
_MAX_URLS: int = 5
_MAX_CONTENT_IMAGES: int = 5
_MAX_TOTAL_IMAGES: int = 6

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


class KeywordPipeline(BasePipeline):
    """
    Workflow 1: Keyword → Article refactored for speed and quality.
    """

    def __init__(self, job_id: str, db: Session, job: Job) -> None:
        super().__init__(job_id, db, job)
        self.keyword: str = job.input_keyword or ""
        
        meta: dict = job.meta or {}
        self.content_image_count: int = max(0, min(int(meta.get("content_image_count", 1)), _MAX_CONTENT_IMAGES))
        self.include_featured_image: bool = bool(meta.get("include_featured_image", True))

        self._logger: structlog.BoundLogger = log.bind(
            job_id=job_id,
            pipeline="keyword",
            keyword=self.keyword
        )

    def run(self) -> dict:
        self._logger.info("keyword_pipeline.run.start", status=self.job.status)
        
        if self.job.status == JobStatus.AWAITING_PREVIEW.value:
            return self._resume_generation()

        self._set_job_status(JobStatus.PROCESSING.value, progress=5)

        # ── Stage 1: Search ───────────────────────────────────────────────────
        urls = self._search()
        self._update_progress(20)

        # ── Stage 2: Scrape Top 3 ─────────────────────────────────────────────
        self._set_job_status("scraping", progress=25)
        scraped_content = self._scrape_top_3(urls)
        self._update_progress(45)

        # ── Stage 3: Unified Analysis ─────────────────────────────────────────
        self._set_job_status("analyzing", progress=50)
        from app.ai.gemini_writer import run_unified_analysis
        analysis_result = run_unified_analysis(scraped_content, logger=self._logger)
        
        if self.job.meta is None: self.job.meta = {}
        self.job.meta["unified_analysis"] = analysis_result
        flag_modified(self.job, "meta")
        self.db.commit()

        self._set_job_status(JobStatus.AWAITING_PREVIEW.value, progress=60)
        return {"status": "awaiting_preview", "analysis": analysis_result}

    def _resume_generation(self) -> dict:
        analysis = self.job.meta.get("unified_analysis")
        if not analysis:
            raise RuntimeError("Missing unified analysis in job meta.")

        self._set_job_status(JobStatus.PROCESSING.value, progress=65)
        
        # ── Stage 4: Parallel Asset Pipeline ──────────────────────────────────
        self._set_job_status("generating_assets", progress=70)
        from celery import group
        from app.worker.tasks import process_single_asset
        
        prompts = analysis.get("image_prompts", [])[:self.content_image_count + 1]
        self._logger.info("keyword_pipeline.parallel_assets.started", count=len(prompts))

        # Fan-out
        job_tasks = [
            process_single_asset.s(p_data, str(self.job.integration_id) if self.job.integration_id else None) 
            for p_data in prompts
        ]
        
        # Execute group and wait for results (The Join Barrier)
        # Note: In a production sync worker, we use .get() which is safe if configured.
        results = group(job_tasks).apply_async().get(disable_sync_subtasks=False)
        self._logger.info("keyword_pipeline.parallel_assets.joined", success_count=len([r for r in results if r["status"] == "success"]))
        
        # ── Stage 5: Final HTML Structuring ───────────────────────────────────
        self._set_job_status("writing", progress=85)
        final_payload = self._generate_final_content(analysis, results)
        
        # ── Stage 6: Finalize & Speculative Sync ──────────────────────────────
        self._finalize_refactored(final_payload, results)
        
        self._set_job_status(JobStatus.COMPLETED.value, progress=100)
        return {"status": "completed"}

    def _generate_final_content(self, analysis: dict, asset_results: list[dict]) -> dict:
        from app.ai.gemini_rest_client import generate_content_rest
        
        # Build image mapping for placeholders
        image_markdown_map = {}
        for i, res in enumerate(asset_results):
            if res["status"] == "success":
                url = res.get("wp_url") or f"data:image/webp;base64,{res['webp_base64']}"
                image_markdown_map[f"IMAGE_PLACEHOLDER_{i}"] = url
            else:
                image_markdown_map[f"IMAGE_PLACEHOLDER_{i}"] = ""

        prompt = f"""You are an Expert SEO Copywriter. Write a full SEO article in HTML based on this analysis.
Focus Keyword: {analysis.get('focus_keyword')}
Title: {analysis.get('title')}

Instructions:
- Use H2/H3 tags.
- Inject exactly {len(asset_results)} image placeholders: <img src="IMAGE_PLACEHOLDER_N"> where N is 0 to {len(asset_results)-1}.
- Output ONLY the HTML body content.
"""
        user_msg = f"Analysis: {analysis.get('markdown_content')}"
        
        try:
            raw_html = generate_content_rest(system_prompt=prompt, user_message=user_msg, logger=self._logger)
            raw_html = re.sub(r"^```(?:html)?\s*", "", raw_html, flags=re.IGNORECASE)
            raw_html = re.sub(r"\s*```$", "", raw_html).strip()
            
            # Substitute placeholders
            for key, val in image_markdown_map.items():
                if val:
                    raw_html = raw_html.replace(key, val)
                else:
                    raw_html = re.sub(f'<img[^>]*src="{key}"[^>]*>', "", raw_html)

            return {
                "title": analysis["title"],
                "html_content": raw_html,
                "slug": analysis["slug"],
                "meta_description": analysis["seo_description"],
                "focus_keyword": analysis["focus_keyword"],
            }
        except Exception as exc:
            self._logger.error("keyword_pipeline.final_html.failed", error=str(exc))
            raise

    def _finalize_refactored(self, final_payload: dict, asset_results: list[dict]) -> None:
        from app.worker.tasks import publish_article_to_wp
        
        # ── 1. Store images metadata ──────────────────────────────────────────
        images_meta = []
        for res in asset_results:
            if res["status"] == "success":
                images_meta.append({
                    "url": res.get("wp_url"),
                    "base64": res.get("webp_base64") if not res.get("wp_url") else None,
                    "metadata": res.get("metadata")
                })

        # ── 2. Persist Article (Atomic Update) ────────────────────────────────
        article = self.db.query(Article).filter(Article.job_id == self.job.id).first()
        if not article:
            article = Article(job_id=self.job.id, user_id=self.job.user_id, integration_id=self.job.integration_id)
            self.db.add(article)

        article.title = final_payload["title"]
        article.slug = final_payload["slug"]
        article.html_content = final_payload["html_content"]
        article.focus_keyword = final_payload["focus_keyword"]
        article.meta_description = final_payload["meta_description"]
        article.article_status = ArticleStatus.DRAFT_READY
        article.meta = {"images": images_meta}
        
        self.db.commit()

        # ── 3. Decoupled WP Sync (Speculative) ────────────────────────────────
        if self.job.integration_id:
            self._logger.info("keyword_pipeline.wp_sync.dispatched")
            publish_article_to_wp.delay(str(article.id))

    def _set_job_status(self, status: str, progress: int) -> None:
        self.job.status = status
        self.job.progress_pct = progress
        self.db.commit()
