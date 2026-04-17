"""
app/pipelines/reference_pipeline.py — Workflow 2: Competitor Link → Article.

Refactored to use the Unified Analysis Pattern and robust asset processing.
"""
from __future__ import annotations

import asyncio
import base64
import re
from datetime import datetime, timezone

import bleach
import structlog

from app.logger import get_logger
from app.models.article import Article, ArticleStatus
from app.models.job import Job, JobStatus
from app.pipelines.base import BasePipeline
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

log = get_logger(__name__)

class ReferencePipeline(BasePipeline):
    def __init__(self, job_id: str, db: Session, job: Job) -> None:
        super().__init__(job_id, db, job)
        self.url: str = job.input_url or ""
        meta = job.meta or {}
        self.content_image_count = max(0, min(int(meta.get("content_image_count", 0)), 5))
        self._logger = log.bind(job_id=job_id, pipeline="reference", url=self.url)

    def run(self) -> dict:
        if self.job.status == JobStatus.AWAITING_PREVIEW.value:
            return self._resume_generation()

        self._set_job_status("scraping", progress=10)
        from app.scrapers.article_scraper import extract_article_content
        markdown = asyncio.run(extract_article_content(self.url))
        
        if self.job.meta is None: self.job.meta = {}
        self.job.meta["extracted_source"] = markdown
        self.db.commit()

        self._set_job_status("analyzing", progress=35)
        from app.ai.gemini_writer import run_unified_analysis
        analysis = run_unified_analysis(markdown, logger=self._logger)
        
        self.job.meta["unified_analysis"] = analysis
        flag_modified(self.job, "meta")
        self.db.commit()

        self._set_job_status(JobStatus.AWAITING_PREVIEW.value, progress=60)
        return {"status": "awaiting_preview", "analysis": analysis}

    def _resume_generation(self) -> dict:
        analysis = self.job.meta.get("unified_analysis")
        self._set_job_status(JobStatus.PROCESSING.value, progress=65)
        
        # ── Stage 3: Parallel Asset Pipeline ──────────────────────────────────
        self._set_job_status("generating_assets", progress=75)
        from celery import group
        from app.worker.tasks import process_single_asset
        
        prompts = analysis.get("image_prompts", [])[:self.content_image_count + 1]
        self._logger.info("reference_pipeline.parallel_assets.started", count=len(prompts))

        job_tasks = [
            process_single_asset.s(p_data, str(self.job.integration_id) if self.job.integration_id else None) 
            for p_data in prompts
        ]
        
        results = group(job_tasks).apply_async().get(disable_sync_subtasks=False)
        self._logger.info("reference_pipeline.parallel_assets.joined", success_count=len([r for r in results if r["status"] == "success"]))

        # ── Stage 4: HTML Structuring ─────────────────────────────────────────
        self._set_job_status("writing", progress=85)
        final_payload = self._generate_final_content(analysis, results)
        
        # ── Stage 5: Finalize & Speculative Sync ──────────────────────────────
        self._finalize_refactored(final_payload, results)
        self._set_job_status(JobStatus.COMPLETED.value, progress=100)
        return {"status": "completed"}

    def _generate_final_content(self, analysis: dict, asset_results: list[dict]) -> dict:
        from app.ai.gemini_rest_client import generate_content_rest
        
        image_markdown_map = {}
        for i, res in enumerate(asset_results):
            if res["status"] == "success":
                url = res.get("wp_url") or f"data:image/webp;base64,{res['webp_base64']}"
                image_markdown_map[f"IMAGE_PLACEHOLDER_{i}"] = url
            else:
                image_markdown_map[f"IMAGE_PLACEHOLDER_{i}"] = ""

        prompt = f"""Write an SEO article in HTML.
Focus Keyword: {analysis.get('focus_keyword')}
Title: {analysis.get('title')}
Inject {len(asset_results)} placeholders: <img src="IMAGE_PLACEHOLDER_N">.
"""
        raw_html = generate_content_rest(system_prompt=prompt, user_message=analysis.get("markdown_content", ""), logger=self._logger)
        raw_html = re.sub(r"^```(?:html)?\s*", "", raw_html, flags=re.IGNORECASE).strip(" `\n")
        
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
            "focus_keyword": analysis["focus_keyword"]
        }

    def _finalize_refactored(self, payload: dict, asset_results: list[dict]) -> None:
        from app.worker.tasks import publish_article_to_wp
        
        images_meta = []
        for res in asset_results:
            if res["status"] == "success":
                images_meta.append({
                    "url": res.get("wp_url"),
                    "base64": res.get("webp_base64") if not res.get("wp_url") else None,
                    "metadata": res.get("metadata")
                })

        article = self.db.query(Article).filter(Article.job_id == self.job.id).first()
        if not article:
            article = Article(job_id=self.job.id, user_id=self.job.user_id, integration_id=self.job.integration_id)
            self.db.add(article)

        article.title, article.slug, article.html_content = payload["title"], payload["slug"], payload["html_content"]
        article.focus_keyword, article.meta_description = payload["focus_keyword"], payload["meta_description"]
        article.article_status = ArticleStatus.DRAFT_READY
        article.meta = {"images": images_meta}
        self.db.commit()

        if self.job.integration_id:
            self._logger.info("reference_pipeline.wp_sync.dispatched")
            publish_article_to_wp.delay(str(article.id))

    def _set_job_status(self, status: str, progress: int) -> None:
        self.job.status, self.job.progress_pct = status, progress
        self.db.commit()
