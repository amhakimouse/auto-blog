"""
app/schemas/__init__.py — Pydantic v2 schemas for request/response validation.
"""
from app.schemas.article import ArticleRead
from app.schemas.job import JobCreate, JobRead, JobStatusUpdate

__all__ = ["JobCreate", "JobRead", "JobStatusUpdate", "ArticleRead"]
