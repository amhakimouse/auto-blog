"""
app/schemas/article.py — Pydantic schemas for Article request / response payloads.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_serializer


class ArticleRead(BaseModel):
    """Full article representation returned by GET endpoints."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    job_id: uuid.UUID
    integration_id: uuid.UUID | None
    article_status: str
    title: str
    slug: str | None
    html_content: str
    word_count: int
    meta_description: str | None
    focus_keyword: str | None
    tags: list[str]
    images: list[dict]
    featured_image_url: str | None
    wp_post_id: int | None
    wp_post_url: str | None
    scheduled_publish_at: datetime | None
    published_at: datetime | None
    source_urls: list[str]
    created_at: datetime
    updated_at: datetime


class ArticlePublishRequest(BaseModel):
    """
    POST /api/v1/articles/{article_id}/publish request body.

    The frontend TipTap editor sends back the (possibly modified) html_content.
    Set scheduled_publish_at to defer publishing; omit / set to null to publish
    immediately via the synchronous WordPressPublisher call.
    """

    html_content: str = Field(
        ...,
        min_length=1,
        description="Final HTML from the TipTap editor (will be re-sanitized server-side).",
    )
    scheduled_publish_at: datetime | None = Field(
        default=None,
        description=(
            "If set, the article is queued for publishing at this UTC datetime "
            "via Celery Beat. If null, publishes immediately."
        ),
    )
    integration_id: uuid.UUID | None = Field(
        default=None,
        description="Optional: Assign or update the Brand Profile (Integration) for this article before publishing.",
    )
