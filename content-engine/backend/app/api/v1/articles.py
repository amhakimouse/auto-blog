"""
app/api/v1/articles.py — Article retrieval and publish endpoints.

Endpoints:
  GET  /articles/{article_id}           — retrieve an article by ID
  GET  /articles/by-job/{job_id}        — retrieve the article for a job
  POST /articles/{article_id}/publish   — editor review → WordPress publish

Publish lifecycle:
  1. Celery pipeline ends → Article.article_status = "draft_ready"
  2. Frontend editor opens the article, edits if needed, calls POST /publish
     with the final html_content.
  3a. scheduled_publish_at is None → publish to WordPress immediately.
  3b. scheduled_publish_at is set → save datetime, set status = "scheduled",
      and let the `publish_scheduled_articles` Celery Beat task handle it.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import bleach
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_active_user
from app.database import get_db
from app.logger import get_logger
from app.models.article import Article, ArticleStatus
from app.models.integration import Integration
from app.models.user import User
from app.schemas.article import ArticlePublishRequest, ArticleRead

log = get_logger(__name__)

router = APIRouter()

# ── Bleach allow-list (same as pipeline — defence in depth) ──────────────────
_ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "u",
    "a", "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "figure", "figcaption", "img",
]
_ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
    "blockquote": ["cite"],
    "code": ["class"],
    "img": ["src", "alt", "title", "width", "height", "loading", "class"],
}


# ── GET /articles/{article_id} ────────────────────────────────────────────────

@router.get(
    "/{article_id}",
    response_model=ArticleRead,
    summary="Retrieve a generated article by ID",
)
async def get_article(
    article_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Article:
    result = await db.execute(
        select(Article).where(
            Article.id == article_id,
            Article.user_id == user.id,
        )
    )
    article = result.scalar_one_or_none()
    if article is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Article not found"
        )
    return article


# ── GET /articles/by-job/{job_id} ─────────────────────────────────────────────

@router.get(
    "/by-job/{job_id}",
    response_model=ArticleRead,
    summary="Retrieve the article produced by a specific job",
)
async def get_article_by_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Article:
    result = await db.execute(
        select(Article).where(
            Article.job_id == job_id,
            Article.user_id == user.id,
        )
    )
    article = result.scalar_one_or_none()
    if article is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No article found for this job. It may still be processing.",
        )
    return article


# ── POST /articles/{article_id}/publish ──────────────────────────────────────

@router.post(
    "/{article_id}/publish",
    response_model=ArticleRead,
    summary="Publish a draft article to WordPress (immediately or scheduled)",
)
async def publish_article(
    article_id: uuid.UUID,
    payload: ArticlePublishRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Article:
    """
    Human Review → Publish gateway.

    The TipTap editor sends back the (possibly modified) HTML. This endpoint:
      1. Validates ownership and draft status.
      2. Re-sanitizes the editor HTML (bleach).
      3a. If scheduled_publish_at is provided: saves the datetime and sets
          article_status = "scheduled". Celery Beat will pick it up.
      3b. If no schedule: calls WordPressPublisher synchronously (runs in a
          thread executor so we don't block the async event loop), updates
          wp_post_id / wp_post_url, sets article_status = "published".
    """
    # ── Load and ownership check ──────────────────────────────────────────────
    result = await db.execute(
        select(Article).where(
            Article.id == article_id,
            Article.user_id == user.id,
        )
    )
    article = result.scalar_one_or_none()
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    # Only allow publishing from draft_ready or scheduled states
    if article.article_status not in (
        ArticleStatus.DRAFT_READY, ArticleStatus.SCHEDULED, ArticleStatus.PUBLISH_FAILED
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Article is in status '{article.article_status}'. "
                "Only draft_ready, scheduled, or publish_failed articles can be published."
            ),
        )

    # Already published? Idempotency guard.
    if article.wp_post_id and article.article_status == ArticleStatus.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Article is already published to WordPress.",
        )

    # ── Sanitize editor HTML ──────────────────────────────────────────────────
    clean_html = bleach.clean(
        payload.html_content,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        strip=True,
        strip_comments=True,
    )
    article.html_content = clean_html

    # ── Scheduled publish path ────────────────────────────────────────────────
    if payload.scheduled_publish_at is not None:
        # Ensure the datetime is UTC-aware
        scheduled_at = payload.scheduled_publish_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        article.scheduled_publish_at = scheduled_at
        article.article_status = ArticleStatus.SCHEDULED
        article.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(article)

        log.info(
            "articles.publish.scheduled",
            article_id=str(article_id),
            scheduled_at=scheduled_at.isoformat(),
        )
        return article

    # ── Immediate publish path ────────────────────────────────────────────────
    # Resolve WordPress credentials from the article's Integration
    integration: Integration | None = None
    if article.integration_id:
        int_result = await db.execute(
            select(Integration).where(Integration.id == article.integration_id)
        )
        integration = int_result.scalar_one_or_none()

    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No Integration (Brand Profile) is linked to this article. "
                "Assign an integration before publishing."
            ),
        )

    # ── Call WordPressPublisher in a thread (sync SDK, non-blocking) ──────────
    from app.integrations.wordpress import WordPressPublisher, WordPressAuthError

    publisher = WordPressPublisher(
        wp_site_url=integration.wp_url,
        wp_username=integration.wp_username,
        wp_app_password=integration.wp_password,
    )

    # Determine featured image bytes if available
    import base64 as _b64
    image_bytes: bytes | None = None
    image_alt: str = article.title
    for img in article.images or []:
        if img.get("role") == "featured" and img.get("base64_image"):
            image_bytes = _b64.b64decode(img["base64_image"])
            image_alt = img.get("alt_text", article.title)
            break

    try:
        # asyncio.run() inside an async endpoint would deadlock — use run_in_executor
        loop = asyncio.get_event_loop()
        publish_result: dict = await loop.run_in_executor(
            None,
            lambda: asyncio.run(
                publisher.publish(
                    title=article.title,
                    html_content=clean_html,
                    image_bytes=image_bytes,
                    image_alt=image_alt,
                    slug=article.slug,
                    meta_description=article.meta_description,
                )
            ),
        )
    except WordPressAuthError as exc:
        log.error("articles.publish.auth_error", article_id=str(article_id), error=str(exc))
        article.article_status = ArticleStatus.PUBLISH_FAILED
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"WordPress authentication failed: {exc}",
        ) from exc
    except Exception as exc:
        log.error("articles.publish.error", article_id=str(article_id), error=str(exc))
        article.article_status = ArticleStatus.PUBLISH_FAILED
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"WordPress publish failed: {type(exc).__name__}: {exc}",
        ) from exc

    # ── Persist success ───────────────────────────────────────────────────────
    article.wp_post_id = publish_result["wp_post_id"]
    article.wp_post_url = publish_result["wp_post_url"]
    if publish_result.get("featured_image_url"):
        article.featured_image_url = publish_result["featured_image_url"]
    article.published_at = datetime.now(timezone.utc)
    article.article_status = ArticleStatus.PUBLISHED
    article.updated_at = datetime.now(timezone.utc)
    # Clear the schedule if it was previously set
    article.scheduled_publish_at = None
    await db.commit()
    await db.refresh(article)

    log.info(
        "articles.publish.success",
        article_id=str(article_id),
        wp_post_id=article.wp_post_id,
        wp_post_url=article.wp_post_url,
    )
    return article
