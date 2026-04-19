"""
app/pipelines/base.py — Abstract base class for all workflow pipelines.

Each pipeline receives the job ID and a sync DB session from the Celery task.
The `run()` method must return a dict that is merged into job.meta.
"""
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.job import Job


class BasePipeline(ABC):
    def __init__(self, job_id: str, db: Session, job: Job) -> None:
        self.job_id = job_id
        self.db = db
        self.job = job

    def _set_step(
        self,
        status: str,
        progress: int,
        label: str | None = None,
        meta_patch: dict | None = None,
    ) -> None:
        """
        Atomically update job status, progress, step label, and optional meta keys.

        Args:
            status:     New job.status string.
            progress:   0-100 progress_pct value.
            label:      Human-readable label stored in job.meta["current_step_label"]
                        and surfaced to the frontend SSE stream.
            meta_patch: Optional dict merged into job.meta (e.g. scraped_urls, analysis).
        """
        self.job.status = status
        self.job.progress_pct = progress

        if label is not None or meta_patch:
            if self.job.meta is None:
                self.job.meta = {}
            if label is not None:
                self.job.meta["current_step_label"] = label
            if meta_patch:
                self.job.meta.update(meta_patch)
            flag_modified(self.job, "meta")

        self.db.commit()

    def _update_progress(self, pct: int) -> None:
        """Legacy compat shim — prefer _set_step() in new code."""
        self.job.progress_pct = pct
        self.db.commit()

    @abstractmethod
    def run(self) -> dict:
        """Execute the full pipeline. Return a metadata dict."""
        ...
