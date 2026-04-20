"""
app/pipelines/_pipeline_helpers.py — Shared pipeline utilities.

Provides:
  - run_concurrent_image_pipeline()  Concurrent Imagen + WebP + WP upload
  - generate_final_html()            Gemini final HTML structuring
  - save_article()                   Upsert Article ORM row with word_count

Design:
  - All async work (Imagen, WP upload) is bundled into a single asyncio.run()
    call at the top level of the sync Celery worker — no nested asyncio.run().
  - Image failures are individually isolated: one bad image never aborts the job.
  - step_cb is a sync callable that updates job.meta["current_step_label"] so
    the SSE stream surfaces live progress like "Generating image 2/3...".
"""
from __future__ import annotations

import asyncio
import base64
import re
from datetime import datetime, timezone
from typing import Callable

import bleach
import structlog
from sqlalchemy.orm import Session

from app.ai.gemini_rest_client import generate_content_rest
from app.ai.imagen_client import ImagenError, generate_section_image
from app.models.article import Article, ArticleStatus
from app.models.job import Job
from app.utils.image import convert_to_webp

log = structlog.get_logger(__name__)

# ── HTML sanitisation allow-list ──────────────────────────────────────────────
_ALLOWED_TAGS: list[str] = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "ul", "ol", "li",
    "strong", "em", "b", "i", "u", "a",
    "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "figure", "figcaption", "img",
]
_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "a": ["href", "title", "rel", "target"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
    "blockquote": ["cite"],
    "code": ["class"],
    "img": ["src", "alt", "title", "width", "height", "loading", "class"],
}


# ── Single image coroutine ────────────────────────────────────────────────────

async def _process_one_image(
    index: int,
    total: int,
    prompt_data: dict,
    integration,        # Integration ORM row or None
    logger: structlog.BoundLogger,
    step_cb: Callable[[str], None],
) -> dict:
    """
    Generate one image, convert to WebP, optionally upload to WordPress.

    Always returns a dict with 'status': 'success' | 'failed'.
    Never raises — all exceptions are caught and included in the result.
    """
    title_hint = prompt_data.get("title", f"image {index + 1}")
    step_cb(f"Generating image {index + 1}/{total}: {title_hint}...")
    logger.info("image_pipeline.start", index=index, title=title_hint)

    result: dict = {
        "status": "failed",
        "index": index,
        "metadata": prompt_data,
    }

    # 1. Generate via Imagen (sync I/O — run in thread pool)
    try:
        img_result = await asyncio.to_thread(
            generate_section_image,
            prompt_data["prompt"],
            aspect_ratio="16:9",
        )
    except ImagenError as exc:
        logger.warning("image_pipeline.imagen_failed", index=index, error=str(exc))
        result["error"] = f"Imagen: {exc}"
        return result
    except Exception as exc:
        logger.error("image_pipeline.imagen_unexpected", index=index, error=str(exc))
        result["error"] = f"Unexpected: {exc}"
        return result

    # 2. Convert PNG → WebP + resize (sync CPU work — thread pool)
    try:
        webp_bytes: bytes = await asyncio.to_thread(
            convert_to_webp, img_result["base64_image"]
        )
    except Exception as exc:
        logger.warning("image_pipeline.webp_failed", index=index, error=str(exc))
        result["error"] = f"WebP conversion: {exc}"
        return result

    result["webp_bytes"] = webp_bytes
    result["webp_base64"] = base64.b64encode(webp_bytes).decode("utf-8")

    # 3. Upload to WordPress (optional — only if integration is configured)
    wp_url: str | None = None
    wp_media_id: int | None = None
    if integration:
        step_cb(f"Uploading image {index + 1}/{total} to WordPress...")
        from app.integrations.wordpress import WordPressPublisher, WordPressPublishError

        safe_title = title_hint[:40].replace(" ", "-").lower()
        filename = f"img-{index}-{safe_title}.webp"

        try:
            publisher = WordPressPublisher(
                wp_site_url=integration.wp_url,
                wp_username=integration.wp_username,
                wp_app_password=integration.wp_password,
            )
            upload_result = await publisher.upload_image(
                image_bytes=webp_bytes,
                filename=filename,
                alt_text=prompt_data.get("alt_text", ""),
                caption=prompt_data.get("caption", ""),
                description=prompt_data.get("description", ""),
            )
            wp_url = upload_result.get("source_url")
            wp_media_id = upload_result.get("id")
            logger.info("image_pipeline.wp_upload_success", index=index, url=wp_url, media_id=wp_media_id)
        except WordPressPublishError as exc:
            logger.warning("image_pipeline.wp_upload_failed", index=index, error=str(exc))
        except Exception as exc:
            logger.warning("image_pipeline.wp_upload_unexpected", index=index, error=str(exc))

    result.update({"status": "success", "wp_url": wp_url, "wp_media_id": wp_media_id})
    step_cb(f"Image {index + 1}/{total} ready ✓")
    logger.info("image_pipeline.done", index=index, wp_url=wp_url)
    return result


# ── Public: Concurrent image pipeline ────────────────────────────────────────

def run_concurrent_image_pipeline(
    prompts: list[dict],
    integration,        # Integration ORM row or None
    logger: structlog.BoundLogger,
    step_cb: Callable[[str], None],
) -> list[dict]:
    """
    Run all image generation + conversion + WP upload concurrently.

    Uses a single asyncio.run(asyncio.gather(...)) — safe to call from a
    synchronous Celery worker (no outer event loop exists).

    Each image failure is isolated — others continue unaffected.
    Returns a list of result dicts in the same order as *prompts*.
    """
    if not prompts:
        return []

    total = len(prompts)
    logger.info("image_pipeline.concurrent_start", count=total)

    async def _gather_all() -> list[dict]:
        tasks = [
            _process_one_image(i, total, p, integration, logger, step_cb)
            for i, p in enumerate(prompts)
        ]
        # return_exceptions=True so one failure never prevents others from completing
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[dict] = []
        for i, r in enumerate(raw):
            if isinstance(r, Exception):
                logger.error("image_pipeline.gather_exception", index=i, error=str(r))
                results.append({
                    "status": "failed",
                    "index": i,
                    "error": str(r),
                    "metadata": prompts[i],
                })
            else:
                results.append(r)
        return results

    results = asyncio.run(_gather_all())
    success = sum(1 for r in results if r["status"] == "success")
    logger.info("image_pipeline.concurrent_done", success=success, total=total)
    return results


# ── Public: Final HTML via Gemini ─────────────────────────────────────────────

def generate_final_html(
    analysis: dict,
    image_results: list[dict],
    logger: structlog.BoundLogger,
    *,
    link_candidates: list[dict] | None = None,
    customization=None,  # ContentCustomization | None — imported lazily to avoid circular
    language: str = "english",
) -> str:
    """
    Ask Gemini to write the full SEO article HTML using the unified analysis
    and the real image URLs (or inline WebP data-URIs as fallback).

    Now powered by the PromptBuilder which injects:
      - Language directive (mandatory, placed first)
      - Smart Internal Linking instructions (if link_candidates provided)
      - Granular Content Controls (article_type, structure, tone, image_style)

    IMAGE_PLACEHOLDER_N markers are injected in the prompt and then substituted
    with the final URLs before returning bleach-sanitised HTML.
    """
    from app.ai.prompt_builder import build_article_system_prompt

    total_images = len(image_results)

    # Build placeholder → URL map
    placeholder_map: dict[str, str] = {}
    for r in image_results:
        i = r["index"]
        key = f"IMAGE_PLACEHOLDER_{i}"
        if r["status"] == "success":
            url = r.get("wp_url") or (
                f"data:image/webp;base64,{r['webp_base64']}"
                if r.get("webp_base64")
                else ""
            )
            placeholder_map[key] = url
        else:
            placeholder_map[key] = ""  # will be stripped from HTML

    img_instruction = (
        "IMAGES: Insert exactly "
        + str(total_images)
        + " images using these exact src values: "
        + ", ".join(
            f'<img src="IMAGE_PLACEHOLDER_{i}" alt="...">'
            for i in range(total_images)
        )
    ) if total_images > 0 else ""

    # Assemble system prompt via PromptBuilder (language directive is first)
    system_prompt = build_article_system_prompt(
        link_candidates=link_candidates or [],
        customization=customization,
        total_images=total_images,
        image_placeholder_instructions=img_instruction,
        language=language,
    )

    user_message = (
        f"Focus Keyword: {analysis.get('focus_keyword')}\n"
        f"Title: {analysis.get('title')}\n\n"
        f"Content Plan:\n{analysis.get('markdown_content', '')}"
    )

    logger.info(
        "pipeline_helpers.final_html.prompt_built",
        has_links=bool(link_candidates),
        link_count=len(link_candidates) if link_candidates else 0,
        language=language,
        customization=customization.model_dump() if customization else None,
    )

    try:
        raw_html = generate_content_rest(
            system_prompt=system_prompt,
            user_message=user_message,
            generation_config={"temperature": 0.7, "maxOutputTokens": 8192},
            logger=logger,
        )
    except Exception as exc:
        logger.error("pipeline_helpers.final_html.failed", error=str(exc))
        raise

    # Strip accidental markdown fences
    raw_html = re.sub(r"^```(?:html)?\s*", "", raw_html, flags=re.IGNORECASE)
    raw_html = re.sub(r"\s*```$", "", raw_html).strip()

    # Substitute image placeholders
    for key, url in placeholder_map.items():
        if url:
            raw_html = raw_html.replace(key, url)
        else:
            raw_html = re.sub(
                rf'<img[^>]*src="{re.escape(key)}"[^>]*>',
                "",
                raw_html,
            )

    # Sanitise with bleach
    clean_html = bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        strip=True,
    )
    logger.info("pipeline_helpers.final_html.done", chars=len(clean_html))
    return clean_html


# ── Public: Article persistence ───────────────────────────────────────────────

def save_article(
    db: Session,
    job: Job,
    payload: dict,
    image_results: list[dict],
) -> Article:
    """
    Upsert the Article row for this job.

    Computes word_count from the HTML. Sets status to DRAFT_READY.
    Stores image metadata (url, webp_base64 fallback, prompt metadata).
    """
    images_meta = [
        {
            "url": r.get("wp_url"),
            "webp_base64": r.get("webp_base64") if not r.get("wp_url") else None,
            "wp_media_id": r.get("wp_media_id"),
            "metadata": r.get("metadata", {}),
            "status": r.get("status"),
        }
        for r in image_results
    ]

    article = db.query(Article).filter(Article.job_id == job.id).first()
    if not article:
        article = Article(
            job_id=job.id,
            user_id=job.user_id,
            integration_id=job.integration_id,
        )
        db.add(article)

    html_content: str = payload["html_content"]

    # Compute plain-text word count from HTML
    plain_text = re.sub(r"<[^>]+>", " ", html_content)
    plain_text = re.sub(r"\s+", " ", plain_text).strip()
    word_count = len(plain_text.split())

    article.title = payload["title"]
    article.slug = payload["slug"]
    article.html_content = html_content
    article.plain_text = plain_text[:50_000]
    article.word_count = word_count
    article.focus_keyword = payload["focus_keyword"]
    article.meta_description = payload["meta_description"]
    article.article_status = ArticleStatus.DRAFT_READY
    article.images = images_meta
    article.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(article)

    log.info(
        "pipeline_helpers.article_saved",
        article_id=str(article.id),
        word_count=word_count,
        images=len(images_meta),
    )
    return article
