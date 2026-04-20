"""
app/models/job.py — Job ORM model.

A Job represents one unit of work submitted by a user (keyword→article or
reference-link→article). It is created synchronously by the API and then
processed asynchronously by the Celery worker.

Status lifecycle:
    queued    → pending → processing → completed
    (drip-feed)           ⇘ failed
"""
import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class JobStatus(str, Enum):
    QUEUED = "queued"              # drip-feed: waiting for execute_at to pass
    PENDING = "pending"            # ready for immediate dispatch
    PROCESSING = "processing"
    AWAITING_PREVIEW = "awaiting_preview"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowType(str, Enum):
    KEYWORD_TO_ARTICLE = "keyword_to_article"      # Workflow 1
    REFERENCE_TO_ARTICLE = "reference_to_article"  # Workflow 2


class Job(Base):
    __tablename__ = "jobs"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # ── Foreign Key ───────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Target Brand Profile / WordPress site for this job
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Workflow Classification ───────────────────────────────────────────────
    workflow_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # stores WorkflowType enum value

    # ── Input Payload ─────────────────────────────────────────────────────────
    # Store raw input so the worker has everything it needs without extra DB calls
    input_keyword: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # Workflow 1
    input_url: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )  # Workflow 2

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=JobStatus.PENDING.value,
        index=True,
    )

    # ── Progress & Error Reporting ────────────────────────────────────────────
    progress_pct: Mapped[int] = mapped_column(
        default=0, nullable=False
    )  # 0–100, updated by worker
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # populated on failure

    # ── Celery Task ID (for task introspection / revocation) ──────────────────
    celery_task_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )

    # ── Metadata (flexible JSONB bag) ─────────────────────────────────────────
    # e.g. {"scraped_urls": [...], "gap_analysis": {...}, "outline": [...]}
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

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
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Drip-Feed Scheduling ──────────────────────────────────────────────────
    # Set for batch-scheduled jobs. NULL means "dispatch immediately".
    # Celery Beat polls this column hourly to trigger generation.
    execute_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,   # indexed: Beat task queries status='queued' AND execute_at<=NOW()
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    owner: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="jobs", lazy="select"
    )
    integration: Mapped["Integration | None"] = relationship(  # noqa: F821
        "Integration", back_populates="jobs", lazy="select"
    )
    article: Mapped["Article | None"] = relationship(  # noqa: F821
        "Article", back_populates="job", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Job id={self.id} workflow={self.workflow_type!r} "
            f"status={self.status!r}>"
        )

    @property
    def article_id(self) -> uuid.UUID | None:
        # Avoid MissingGreenlet by using __dict__ check or assuming it's loaded
        if "article" in self.__dict__ and self.article is not None:
            return self.article.id
        return None
