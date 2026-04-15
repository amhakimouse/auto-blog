"""
app/schemas/job.py — Pydantic schemas for Job request / response payloads.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, field_serializer, model_validator

from app.models.job import JobStatus, WorkflowType


class RelatedArticle(BaseModel):
    """A related article used for internal linking injection."""

    title: str = Field(..., min_length=2, max_length=300)
    url: str = Field(..., min_length=5, max_length=2048)


# ── Single-job request ────────────────────────────────────────────────────────


class JobCreate(BaseModel):
    """
    Used as the POST /api/v1/jobs request body.
    Exactly ONE of keyword or reference_url must be provided.
    """

    workflow_type: WorkflowType
    keyword: str | None = Field(
        default=None,
        min_length=2,
        max_length=200,
        description="Target keyword for Workflow 1.",
    )
    reference_url: HttpUrl | None = Field(
        default=None,
        description="Competitor URL for Workflow 2.",
    )

    # Target brand profile — which WP site to associate the job with
    integration_id: uuid.UUID | None = Field(
        default=None,
        description="Integration (Brand Profile) to associate this job with.",
    )

    # ── Image configuration (Workflow 1 only) ─────────────────────────────────
    content_image_count: int = Field(
        default=1,
        ge=0,
        le=5,
        description="Number of in-content images to generate (0–5).",
    )
    include_featured_image: bool = Field(
        default=True,
        description="Whether to generate a hero/featured image.",
    )

    # ── Internal linking (Workflow 1 only) ────────────────────────────────────
    related_articles: list[RelatedArticle] = Field(
        default_factory=list,
        max_length=3,
        description="Up to 3 related articles for internal linking.",
    )

    @model_validator(mode="after")
    def check_workflow_input(self):
        if self.workflow_type == WorkflowType.KEYWORD_TO_ARTICLE:
            if not self.keyword:
                raise ValueError(
                    "keyword is required for workflow_type=keyword_to_article"
                )
        elif self.workflow_type == WorkflowType.REFERENCE_TO_ARTICLE:
            if not self.reference_url:
                raise ValueError(
                    "reference_url is required for workflow_type=reference_to_article"
                )
        return self


# ── Bulk-job request ──────────────────────────────────────────────────────────


class BulkJobItem(BaseModel):
    """
    One item inside a bulk job submission.
    Exactly ONE of keyword or reference_url must be provided.
    """

    workflow_type: WorkflowType
    keyword: str | None = Field(default=None, min_length=2, max_length=200)
    reference_url: HttpUrl | None = None

    @model_validator(mode="after")
    def check_item_input(self) -> "BulkJobItem":
        if self.workflow_type == WorkflowType.KEYWORD_TO_ARTICLE and not self.keyword:
            raise ValueError("keyword required for keyword_to_article")
        if (
            self.workflow_type == WorkflowType.REFERENCE_TO_ARTICLE
            and not self.reference_url
        ):
            raise ValueError("reference_url required for reference_to_article")
        return self


class BulkJobCreate(BaseModel):
    """POST /api/v1/jobs/bulk request body."""

    integration_id: uuid.UUID = Field(
        ..., description="Brand Profile all jobs in this batch are assigned to."
    )
    items: list[BulkJobItem] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Between 1 and 50 job definitions.",
    )
    # Image config applied uniformly to all items in the batch
    content_image_count: int = Field(default=1, ge=0, le=5)
    include_featured_image: bool = Field(default=True)


class BulkJobRead(BaseModel):
    """Summary returned immediately after a bulk submission."""

    integration_id: uuid.UUID
    queued: int
    failed_validation: int
    job_ids: list[uuid.UUID]
    errors: list[str]

    @field_serializer("integration_id")
    def serialize_integration_id(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer("job_ids")
    def serialize_job_ids(self, value: list[uuid.UUID]) -> list[str]:
        return [str(x) for x in value]


# ── Single-job response ───────────────────────────────────────────────────────


class JobRead(BaseModel):
    """Minimal representation returned immediately after job creation."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    workflow_type: str
    status: str
    progress_pct: int
    integration_id: uuid.UUID | None
    input_keyword: str | None
    input_url: str | None
    celery_task_id: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)

    @field_serializer("integration_id")
    def serialize_integration_id(self, value: uuid.UUID | None) -> str | None:
        return str(value) if value else None


# ── Internal status update (used by worker, not exposed) ──────────────────────
class JobStatusUpdate(BaseModel):
    status: JobStatus
    progress_pct: int | None = None
    error_message: str | None = None
    celery_task_id: str | None = None
    meta: dict | None = None
