"""
app/api/router.py — Top-level API router that aggregates all versioned routes.
"""
from fastapi import APIRouter

from app.api.v1 import articles, auth, events, integrations, jobs

api_router = APIRouter()

api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["auth"],
)

# NOTE: /jobs/bulk is declared before /jobs/{job_id} inside jobs.py so FastAPI
# does not treat the literal "bulk" as a UUID path parameter.
api_router.include_router(
    jobs.router,
    prefix="/jobs",
    tags=["jobs"],
)
api_router.include_router(
    articles.router,
    prefix="/articles",
    tags=["articles"],
)
api_router.include_router(
    integrations.router,
    prefix="/integrations",
    tags=["integrations"],
)
api_router.include_router(
    events.router,
    prefix="/events",
    tags=["events"],
)
