"""
app/api/v1/dashboard.py — Dashboard endpoints for user statistics and recent jobs.

Endpoints:
  GET /dashboard/stats - Get user statistics (jobs, articles, credits)
  GET /dashboard/recent-jobs - Get recent jobs with pagination
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_active_user
from app.database import get_db
from app.models.article import Article
from app.models.job import Job
from app.models.user import User
from app.schemas.job import JobRead

router = APIRouter()


@router.get(
    "/stats",
    summary="Get user statistics",
)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> dict:
    """
    Return statistics for the authenticated user:
    - total_jobs: Total number of jobs created
    - completed_jobs: Number of completed jobs
    - failed_jobs: Number of failed jobs
    - total_articles: Total number of articles generated
    - published_articles: Number of articles published to WordPress
    - credits: User's remaining credits
    """
    # Count total jobs
    total_jobs_result = await db.execute(
        select(func.count(Job.id)).where(Job.user_id == user.id)
    )
    total_jobs = total_jobs_result.scalar() or 0

    # Count completed jobs
    completed_jobs_result = await db.execute(
        select(func.count(Job.id)).where(
            Job.user_id == user.id, Job.status == "completed"
        )
    )
    completed_jobs = completed_jobs_result.scalar() or 0

    # Count failed jobs
    failed_jobs_result = await db.execute(
        select(func.count(Job.id)).where(Job.user_id == user.id, Job.status == "failed")
    )
    failed_jobs = failed_jobs_result.scalar() or 0

    # Count total articles
    total_articles_result = await db.execute(
        select(func.count(Article.id)).join(Job).where(Job.user_id == user.id)
    )
    total_articles = total_articles_result.scalar() or 0

    # Count published articles (status = published)
    published_articles_result = await db.execute(
        select(func.count(Article.id))
        .join(Job)
        .where(Job.user_id == user.id, Article.status == "published")
    )
    published_articles = published_articles_result.scalar() or 0

    return {
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "total_articles": total_articles,
        "published_articles": published_articles,
        "credits": user.credits,
    }


@router.get(
    "/recent-jobs",
    response_model=list[JobRead],
    summary="Get recent jobs for the current user",
)
async def get_recent_jobs(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
    limit: int = Query(default=10, ge=1, le=20, description="Max records to return"),
) -> list[Job]:
    """
    Return a list of recent jobs for the authenticated user,
    ordered by `created_at` descending (newest first).

    Use `limit` to control the number of results (1-20).
    """
    result = await db.execute(
        select(Job)
        .where(Job.user_id == user.id)
        .order_by(desc(Job.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())
