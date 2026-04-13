"""
app/models/article.py — Article ORM model.

Stores the final generated content produced by a completed Job.
One Job → One Article (1:1). Article is deleted if the parent Job is deleted.

Lifecycle statuses (article_status column):
  draft_ready    — pipeline complete, awaiting human review
  scheduled      — approved, waiting for scheduled_publish_at
  published      — live on WordPress (wp_post_id populated)
  publish_failed — WP push failed; human action required
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ArticleStatus(str):
    """String constants for article_status column — not a Python Enum so Alembic
    can use them as plain strings without a Postgres ENUM type migration."""
    DRAFT_READY = "draft_ready"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    PUBLISH_FAILED = "publish_failed"


class Article(Base):
    __tablename__ = "articles"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # ── Foreign Keys ──────────────────────────────────────────────────────────
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        unique=True,     # enforces 1:1 with Job
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Which brand profile / WP site this article belongs to
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Lifecycle Status ──────────────────────────────────────────────────────
    # Plain string column — see ArticleStatus constants above.
    article_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=ArticleStatus.DRAFT_READY,
        index=True,
    )

    # ── Content ───────────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    slug: Mapped[str | None] = mapped_column(
        String(600), nullable=True, unique=True, index=True
    )
    # Sanitized HTML — XSS-safe output from bleach.clean()
    html_content: Mapped[str] = mapped_column(Text, nullable=False)
    # Plain-text version for word count / readability scoring
    plain_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ── SEO Metadata ──────────────────────────────────────────────────────────
    meta_description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    focus_keyword: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Tags stored as a Postgres array for efficient querying
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list
    )

    # ── Images ────────────────────────────────────────────────────────────────
    # e.g. [{"url": "...", "alt": "...", "caption": "...", "role": "featured|content"}]
    images: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    featured_image_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )

    # ── WordPress Publish Status ──────────────────────────────────────────────
    wp_post_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wp_post_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Set by the frontend editor before calling POST /articles/{id}/publish.
    # When set, the publish Celery task fires at this time via Celery Beat.
    scheduled_publish_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Source References ─────────────────────────────────────────────────────
    source_urls: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    job: Mapped["Job"] = relationship(  # noqa: F821
        "Job", back_populates="article", lazy="select"
    )
    integration: Mapped["Integration | None"] = relationship(  # noqa: F821
        "Integration", back_populates="articles", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f"<Article id={self.id} title={self.title!r} "
            f"status={self.article_status!r} words={self.word_count}>"
        )
