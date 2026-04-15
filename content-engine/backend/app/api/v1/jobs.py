"""
app/api/v1/jobs.py — Job creation, status, cancel, bulk-queue, and list endpoints.

Endpoints:
  POST   /jobs          — create one job (credits check + billing)
  GET    /jobs          — list jobs for the authenticated user (paginated)
  GET    /jobs/{job_id} — poll status (scoped to auth user)
  DELETE /jobs/{job_id} — cancel a pending/processing job
  POST   /jobs/bulk     — enqueue up to 50 jobs against one Integration

Design decisions:
  - Heavy work is NEVER done in the API layer — Celery handles everything.
  - Bulk endpoint creates DB rows and dispatches tasks in a single transaction;
    SSRF-invalid URLs are skipped and reported in the `errors` list.
  - Rate limiting via slowapi protects GCP API credits.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from jose import jwt
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_active_user
from app.config import settings
from app.database import get_db
from app.logger import get_logger
from app.models.integration import Integration
from app.models.job import Job, JobStatus, WorkflowType
from app.models.user import User
from app.schemas.job import BulkJobCreate, BulkJobRead, JobCreate, JobRead
from app.security import SSRFViolationError, validate_url_for_ssrf
from app.worker.celery_app import celery_app
from app.worker import tasks  # noqa: F401 — ensures tasks are registered

log = get_logger(__name__)

router = APIRouter()


def user_id_or_ip(request: Request) -> str:
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth.split(" ")[1]
        try:
            from app.security.auth import ALGORITHM

            payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except Exception:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=user_id_or_ip)
_RATE_LIMIT = "10/minute"

_TASK_MAP: dict[WorkflowType, str] = {
    WorkflowType.KEYWORD_TO_ARTICLE: "app.worker.tasks.run_keyword_to_article",
    WorkflowType.REFERENCE_TO_ARTICLE: "app.worker.tasks.run_reference_to_article",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_owned_integration(
    integration_id: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> Integration:
    """Return the Integration if it belongs to *user*, else 404."""
    result = await db.execute(
        select(Integration).where(
            Integration.id == integration_id,
            Integration.user_id == user.id,
        )
    )
    integration = result.scalar_one_or_none()
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found or does not belong to you.",
        )
    return integration


def _build_job_meta(payload: JobCreate) -> dict:
    return {
        "content_image_count": payload.content_image_count,
        "include_featured_image": payload.include_featured_image,
        "related_articles": [
            {"title": a.title, "url": a.url} for a in payload.related_articles
        ],
    }


# ── POST /jobs ────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a new content generation job",
)
@limiter.limit(_RATE_LIMIT)
async def create_job(
    request: Request,
    payload: JobCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Job:
    """
    Submit a single job. Returns 202 Accepted immediately.
    Celery worker handles all heavy processing asynchronously.
    """
    # ── Credits pre-flight ────────────────────────────────────────────────────
    if user.credits <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Insufficient credits to perform this action.",
        )

    # ── Validate integration ownership (if provided) ──────────────────────────
    if payload.integration_id:
        await _get_owned_integration(payload.integration_id, user, db)

    # ── SSRF check for Workflow 2 ─────────────────────────────────────────────
    reference_url_str: str | None = None
    if payload.workflow_type == WorkflowType.REFERENCE_TO_ARTICLE:
        assert payload.reference_url is not None
        try:
            reference_url_str = validate_url_for_ssrf(str(payload.reference_url))
        except SSRFViolationError as exc:
            log.warning(
                "jobs.ssrf_blocked", url=str(payload.reference_url), error=str(exc)
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"URL rejected for security reasons: {exc}",
            ) from exc

    # ── Create Job record ─────────────────────────────────────────────────────
    job = Job(
        user_id=user.id,
        integration_id=payload.integration_id,
        workflow_type=payload.workflow_type.value,
        status=JobStatus.PENDING.value,
        input_keyword=payload.keyword,
        input_url=reference_url_str,
        meta=_build_job_meta(payload),
    )
    db.add(job)
    await db.flush()

    # ── Dispatch Celery task ──────────────────────────────────────────────────
    task_kwargs = {"job_id": str(job.id)}

    celery_result = celery_app.send_task(
        _TASK_MAP[payload.workflow_type],
        kwargs=task_kwargs,
        queue="default",
    )
    job.celery_task_id = celery_result.id
    await db.commit()
    await db.refresh(job)

    log.info(
        "jobs.created",
        job_id=str(job.id),
        workflow=payload.workflow_type,
        integration_id=str(payload.integration_id),
        celery_task_id=celery_result.id,
    )
    return job


# ── POST /jobs/bulk ───────────────────────────────────────────────────────────
# NOTE: this route must be defined BEFORE /{job_id} so FastAPI doesn't treat
# the literal "bulk" as a UUID segment.


@router.post(
    "/bulk",
    response_model=BulkJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue multiple jobs against a single Integration in one call",
)
@limiter.limit(_RATE_LIMIT)
async def create_bulk_jobs(
    request: Request,
    payload: BulkJobCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> BulkJobRead:
    """
    Enqueue up to 50 content jobs in a single API call.

    - Validates the Integration belongs to the caller.
    - Checks the user has enough credits for the whole batch.
    - SSRF-validates any reference URLs; invalid ones are skipped (not killed).
    - Creates a DB row and dispatches a Celery task for every valid item.
    - Returns a BulkJobRead summary immediately.
    """
    # ── Validate integration ownership ────────────────────────────────────────
    await _get_owned_integration(payload.integration_id, user, db)

    # ── Credits check (whole batch up front) ──────────────────────────────────
    if user.credits < len(payload.items):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Insufficient credits. You have {user.credits} credit(s) but "
                f"the batch requires {len(payload.items)}."
            ),
        )

    job_ids: list[uuid.UUID] = []
    errors: list[str] = []
    failed_validation = 0

    for item in payload.items:
        # ── SSRF check for reference URLs ─────────────────────────────────
        reference_url_str: str | None = None
        if item.workflow_type == WorkflowType.REFERENCE_TO_ARTICLE:
            assert item.reference_url is not None
            try:
                reference_url_str = validate_url_for_ssrf(str(item.reference_url))
            except SSRFViolationError as exc:
                errors.append(f"URL {item.reference_url!r} rejected: {exc}")
                failed_validation += 1
                continue

        # ── Create Job row ────────────────────────────────────────────────
        job = Job(
            user_id=user.id,
            integration_id=payload.integration_id,
            workflow_type=item.workflow_type.value,
            status=JobStatus.PENDING.value,
            input_keyword=item.keyword,
            input_url=reference_url_str,
            meta={
                "content_image_count": payload.content_image_count,
                "include_featured_image": payload.include_featured_image,
                "related_articles": [],
            },
        )
        db.add(job)
        await db.flush()  # get UUID before dispatching

        # ── Dispatch Celery task ──────────────────────────────────────────
        task_kwargs = {"job_id": str(job.id)}

        celery_result = celery_app.send_task(
            _TASK_MAP[item.workflow_type],
            kwargs=task_kwargs,
            queue="default",
        )
        job.celery_task_id = celery_result.id
        job_ids.append(job.id)

    await db.commit()

    log.info(
        "jobs.bulk_created",
        user_id=str(user.id),
        integration_id=str(payload.integration_id),
        queued=len(job_ids),
        failed_validation=failed_validation,
    )
    return BulkJobRead(
        integration_id=payload.integration_id,
        queued=len(job_ids),
        failed_validation=failed_validation,
        job_ids=job_ids,
        errors=errors,
    )


# ── GET /jobs ────────────────────────────────────────────────────────────────────

from typing import Annotated


@router.get(
    "",
    response_model=list[JobRead],
    summary="List jobs for the current user (newest first)",
)
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
    limit: Annotated[
        int, Query(ge=1, le=100, description="Max records to return")
    ] = 20,
    offset: Annotated[int, Query(ge=0, description="Pagination offset")] = 0,
) -> list[Job]:
    """
    Return a paginated list of jobs belonging to the authenticated user,
    ordered by `created_at` descending (newest first).

    Use `limit` and `offset` to paginate:
      - Page 1: `?limit=20&offset=0`
      - Page 2: `?limit=20&offset=20`
    """
    result = await db.execute(
        select(Job)
        .where(Job.user_id == user.id)
        .order_by(desc(Job.created_at))
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


# ── GET /jobs/{job_id} ──────────────────────────────────────────────────────────────


@router.get(
    "/{job_id}",
    response_model=JobRead,
    summary="Get the status of a job",
)
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> Job:
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    return job


# ── DELETE /jobs/{job_id} ─────────────────────────────────────────────────────


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a pending job",
)
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> None:
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )

    if job.status not in (JobStatus.PENDING.value, JobStatus.PROCESSING.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel a job in '{job.status}' state.",
        )

    if job.celery_task_id:
        celery_app.control.revoke(job.celery_task_id, terminate=True)

    job.status = JobStatus.FAILED.value
    job.error_message = "Cancelled by user."
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    log.info("jobs.cancelled", job_id=str(job_id))
