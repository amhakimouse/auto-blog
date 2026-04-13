"""
app/models/__init__.py — Re-export all ORM models for convenience.

Alembic's env.py imports Base and all models from here so it can detect tables.
"""
from app.models.article import Article, ArticleStatus
from app.models.job import Job, JobStatus, WorkflowType
from app.models.user import User
from app.models.integration import Integration

__all__ = [
    "User",
    "Job", "JobStatus", "WorkflowType",
    "Article", "ArticleStatus",
    "Integration",
]
