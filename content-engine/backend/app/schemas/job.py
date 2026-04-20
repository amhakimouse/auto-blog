"""
app/schemas/job.py — Pydantic schemas for Job request / response payloads.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_serializer, field_validator, model_validator

from app.models.job import JobStatus, WorkflowType


# ── Granular Content Controls ─────────────────────────────────────────────────

# Valid enum values (used in Literal type and for strict fallback validation)
_VALID_ARTICLE_TYPES = {"informational", "educational", "guide", "course_module", "listicle"}
_VALID_STRUCTURES    = {"standard", "content_cards", "q_and_a", "step_by_step"}
_VALID_IMAGE_STYLES  = {"standard", "ultra_realistic", "minimalist", "infographic"}
_VALID_TONES         = {"professional", "conversational", "witty", "academic"}

# Supported output languages — ISO-friendly names used verbatim in the prompt.
# Add new entries here; the coercion validator ensures unknown values fall back
# to "english" without raising a hard 422 error.
_VALID_LANGUAGES: set[str] = {
    "english", "french", "spanish", "german", "italian", "portuguese",
    "dutch", "arabic", "turkish", "polish", "russian", "japanese",
    "chinese", "korean", "hindi", "indonesian", "swedish", "norwegian",
    "danish", "finnish",
}


class ContentCustomization(BaseModel):
    """
    Granular content-control settings that shape Gemini's HTML output and
    Imagen's image generation.  Every field is optional and falls back to a
    safe default so existing jobs without this field continue to work.
    """

    article_type: Literal[
        "informational", "educational", "guide", "course_module", "listicle"
    ] = Field(
        default="informational",
        description="Depth and format of the article (informational, guide, listicle…).",
    )

    article_structure: Literal[
        "standard", "content_cards", "q_and_a", "step_by_step"
    ] = Field(
        default="standard",
        description="HTML layout pattern for the generated article.",
    )

    image_style: Literal[
        "standard", "ultra_realistic", "minimalist", "infographic"
    ] = Field(
        default="standard",
        description="Visual style applied to Imagen generation prompts.",
    )

    tone_of_voice: Literal[
        "professional", "conversational", "witty", "academic"
    ] = Field(
        default="professional",
        description="Writing style applied to the Gemini copywriting step.",
    )

    language: str = Field(
        default="english",
        description=(
            "Output language for the generated article. "
            f"Supported values: {', '.join(sorted(_VALID_LANGUAGES))}. "
            "Unknown values fall back to 'english'."
        ),
    )

    @field_validator("article_type", mode="before")
    @classmethod
    def _coerce_article_type(cls, v: str) -> str:
        """Silently fall back to 'informational' for unknown values."""
        return v if v in _VALID_ARTICLE_TYPES else "informational"

    @field_validator("article_structure", mode="before")
    @classmethod
    def _coerce_structure(cls, v: str) -> str:
        return v if v in _VALID_STRUCTURES else "standard"

    @field_validator("image_style", mode="before")
    @classmethod
    def _coerce_image_style(cls, v: str) -> str:
        return v if v in _VALID_IMAGE_STYLES else "standard"

    @field_validator("tone_of_voice", mode="before")
    @classmethod
    def _coerce_tone(cls, v: str) -> str:
        return v if v in _VALID_TONES else "professional"

    @field_validator("language", mode="before")
    @classmethod
    def _coerce_language(cls, v: object) -> str:
        """Normalise to lowercase; fall back to 'english' for unrecognised values."""
        if not isinstance(v, str):
            return "english"
        normalised = v.strip().lower()
        return normalised if normalised in _VALID_LANGUAGES else "english"


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
    # NOTE: These manual overrides are still accepted for edge cases, but the
    # Smart Internal Linking Engine auto-fetches posts from WP and takes priority
    # when an integration_id is present.
    related_articles: list[RelatedArticle] = Field(
        default_factory=list,
        max_length=3,
        description="Up to 3 manual related articles for internal linking (overrides auto-fetch).",
    )

    # ── Granular Content Controls (Tier 3 feature) ────────────────────────────
    customization: ContentCustomization = Field(
        default_factory=ContentCustomization,
        description="Content shape, tone, and image style controls.",
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


# ── Scheduled/Drip-Feed request ───────────────────────────────────────────────


class ScheduledJobItem(BaseModel):
    """
    One item inside a drip-feed schedule submission.
    """

    workflow_type: WorkflowType
    keyword: str | None = Field(default=None, min_length=2, max_length=200)
    reference_url: HttpUrl | None = None
    execute_at: datetime = Field(..., description="When this job should be executed.")

    @model_validator(mode="after")
    def check_item_input(self) -> "ScheduledJobItem":
        if self.workflow_type == WorkflowType.KEYWORD_TO_ARTICLE and not self.keyword:
            raise ValueError("keyword required for keyword_to_article")
        if (
            self.workflow_type == WorkflowType.REFERENCE_TO_ARTICLE
            and not self.reference_url
        ):
            raise ValueError("reference_url required for reference_to_article")
        return self


class BatchScheduleCreate(BaseModel):
    """POST /api/v1/jobs/scheduled request body."""

    integration_id: uuid.UUID = Field(
        ..., description="Brand Profile all jobs in this batch are assigned to."
    )
    items: list[ScheduledJobItem] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Between 1 and 10 job definitions with assigned execution dates.",
    )
    # Image config applied uniformly to all items in the batch
    content_image_count: int = Field(default=1, ge=0, le=5)
    include_featured_image: bool = Field(default=True)


class BatchScheduleRead(BaseModel):
    """Summary returned immediately after a scheduled batch submission."""

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
    article_id: uuid.UUID | None = None
    input_keyword: str | None
    input_url: str | None
    celery_task_id: str | None
    error_message: str | None
    meta: dict | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @field_serializer("id", "integration_id", "article_id")
    def serialize_uuids(self, value: uuid.UUID | None) -> str | None:
        return str(value) if value else None


# ── Internal status update (used by worker, not exposed) ──────────────────────
class JobStatusUpdate(BaseModel):
    status: JobStatus
    progress_pct: int | None = None
    error_message: str | None = None
    celery_task_id: str | None = None
    meta: dict | None = None
