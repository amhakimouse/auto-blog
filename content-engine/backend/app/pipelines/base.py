"""
app/pipelines/base.py — Abstract base class for all workflow pipelines.

Each pipeline receives the job ID and a sync DB session from the Celery task.
The `run()` method must return a dict that is merged into job.meta.
"""
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.models.job import Job


class BasePipeline(ABC):
    def __init__(self, job_id: str, db: Session, job: Job) -> None:
        self.job_id = job_id
        self.db = db
        self.job = job

    def _update_progress(self, pct: int) -> None:
        """Persist a progress percentage update to the DB."""
        self.job.progress_pct = pct
        self.db.commit()

    @abstractmethod
    def run(self) -> dict:
        """Execute the full pipeline. Return metadata dict."""
        ...
