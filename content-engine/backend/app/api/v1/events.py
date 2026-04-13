"""
app/api/v1/events.py — Server-Sent Events (SSE) for real-time job status.

Endpoint:
  GET /events/jobs/{job_id}   — SSE stream; the client receives a 'job_update'
                                event every time the job's status or progress
                                changes. The stream closes automatically when
                                the job reaches a terminal state
                                (completed / failed / cancelled).

Why SSE instead of WebSockets?
  - SSE is one-directional (server → client) which perfectly matches job
    status polling.
  - SSE works over plain HTTP/1.1 — no upgrade handshake, proxy-friendly.
  - The browser's built-in EventSource API handles reconnection automatically.
  - Far less infrastructure complexity than a WebSocket manager.

How it works:
  1. Client opens `EventSource('/api/v1/events/jobs/{job_id}')`.
  2. Server polls the DB every POLL_INTERVAL_SECONDS seconds.
  3. When the job row changes (status or progress_pct), the server emits
     an SSE 'job_update' event with the serialised JobRead payload.
  4. When the job reaches a terminal state, the server emits a final event
     and then closes the stream.
  5. If the job is not found or doesn't belong to the user, the stream
     closes with an error event immediately.

Limitations / production notes:
  - Each open SSE connection holds an open DB connection for the duration.
    For high concurrency, consider a Redis pub/sub fan-out or a dedicated
    WebSocket gateway instead.
  - Nginx / load balancers must be configured to disable response buffering
    (`X-Accel-Buffering: no`) for SSE to work correctly.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_active_user
from app.database import get_db
from app.logger import get_logger
from app.models.job import Job, JobStatus
from app.models.user import User

log = get_logger(__name__)
router = APIRouter()

# How often (in seconds) to re-query the DB for job changes
POLL_INTERVAL_SECONDS: float = 2.0
# How long (in seconds) to keep a stream alive with no terminal state before
# sending a heartbeat comment to keep proxies/load balancers happy.
HEARTBEAT_INTERVAL_SECONDS: float = 15.0
# Maximum lifetime of a stream (safety valve — prevent zombie connections)
MAX_STREAM_DURATION_SECONDS: float = 3600.0   # 1 hour


def _job_to_sse_data(job: Job) -> str:
    """Serialise a Job row to a JSON string suitable for an SSE data field."""
    payload = {
        "id": str(job.id),
        "status": job.status,
        "progress_pct": job.progress_pct,
        "workflow_type": job.workflow_type,
        "integration_id": str(job.integration_id) if job.integration_id else None,
        "input_keyword": job.input_keyword,
        "input_url": str(job.input_url) if job.input_url else None,
        "celery_task_id": job.celery_task_id,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
    return json.dumps(payload)


def _is_terminal(status_value: str) -> bool:
    """Return True if the job has reached a state where it will not change."""
    return status_value in (
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        # 'cancelled' is stored as 'failed' with error_message = 'Cancelled by user.'
        # but we check for it by status value just in case.
        "cancelled",
    )


async def _job_event_stream(
    job_id: uuid.UUID,
    user: User,
    db: AsyncSession,
) -> AsyncIterator[str]:
    """
    Async generator that yields SSE-formatted strings.

    Format reminder:
      event: <event-name>\n
      data: <json-string>\n
      \n
    A bare ': comment' line acts as a heartbeat (keeps connection alive).
    """
    last_poll = datetime.utcnow()
    stream_start = datetime.utcnow()
    heartbeat_timer: float = 0.0
    last_status: str | None = None
    last_progress: int | None = None

    try:
        while True:
            # ── Safety valve ──────────────────────────────────────────────────
            elapsed = (datetime.utcnow() - stream_start).total_seconds()
            if elapsed > MAX_STREAM_DURATION_SECONDS:
                yield "event: error\ndata: {\"detail\": \"Stream timeout\"}\n\n"
                return

            # ── Poll DB ───────────────────────────────────────────────────────
            result = await db.execute(
                select(Job).where(Job.id == job_id, Job.user_id == user.id)
            )
            job = result.scalar_one_or_none()

            if job is None:
                yield (
                    "event: error\n"
                    "data: {\"detail\": \"Job not found or access denied\"}\n\n"
                )
                return

            # ── Emit update only when something changed ────────────────────────
            changed = (job.status != last_status or job.progress_pct != last_progress)

            if changed:
                last_status = job.status
                last_progress = job.progress_pct
                heartbeat_timer = 0.0  # reset heartbeat after a real event

                data = _job_to_sse_data(job)
                yield f"event: job_update\ndata: {data}\n\n"

                log.debug(
                    "sse.job_update",
                    job_id=str(job_id),
                    status=job.status,
                    progress=job.progress_pct,
                )

                if _is_terminal(job.status):
                    # Send a 'done' event so the client knows to close the stream
                    yield f"event: done\ndata: {{\"status\": \"{job.status}\"}}\n\n"
                    return

            # ── Heartbeat ─────────────────────────────────────────────────────
            heartbeat_timer += POLL_INTERVAL_SECONDS
            if heartbeat_timer >= HEARTBEAT_INTERVAL_SECONDS:
                heartbeat_timer = 0.0
                yield ": heartbeat\n\n"

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    except asyncio.CancelledError:
        # Client disconnected — clean exit, no error log needed
        log.info("sse.client_disconnected", job_id=str(job_id))
        return
    except Exception as exc:
        log.error("sse.error", job_id=str(job_id), error=str(exc))
        yield f"event: error\ndata: {{\"detail\": \"{exc}\"}}\n\n"
        return


# ── GET /events/jobs/{job_id} ─────────────────────────────────────────────────

@router.get(
    "/jobs/{job_id}",
    summary="Real-time job status stream via Server-Sent Events",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE stream of job_update events",
            "content": {"text/event-stream": {}},
        },
        401: {"description": "Not authenticated"},
        404: {"description": "Job not found"},
    },
)
async def job_event_stream(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> StreamingResponse:
    """
    Open a Server-Sent Events stream for a single job.

    **Client usage (JavaScript):**
    ```javascript
    const source = new EventSource(
      `/api/v1/events/jobs/${jobId}`,
      { withCredentials: true }  // if using cookies; for Bearer tokens see note below
    );
    source.addEventListener('job_update', (e) => {
      const job = JSON.parse(e.data);
      console.log(job.status, job.progress_pct);
    });
    source.addEventListener('done', () => source.close());
    source.addEventListener('error', () => source.close());
    ```

    **Note on Authorization for native EventSource:**
    The browser's EventSource API does not support custom headers (so you can't
    pass a Bearer token). Work around this by passing the token as a query
    parameter (`?token=<jwt>`) and adding it to the `oauth2_scheme` token
    extractor, or use an HTTP-only cookie for auth on the SSE route.
    For the initial phase, you can make this endpoint accept a `token` query
    parameter as an alternative to the Authorization header.
    """
    # Verify the job exists and belongs to the current user before opening the stream
    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.user_id == user.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or does not belong to you.",
        )

    return StreamingResponse(
        _job_event_stream(job_id, user, db),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # Disable Nginx buffering
            "Connection": "keep-alive",
        },
    )
