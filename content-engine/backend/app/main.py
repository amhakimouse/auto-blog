"""
app/main.py — FastAPI application factory.

Responsibilities:
  - Mount the versioned API router
  - Configure CORS (locked down; update origins in production)
  - Configure rate limiter (slowapi)
  - Lifecycle: run Alembic migrations (dev) or table creation on startup
  - Expose /health and /readiness endpoints

This file stays THIN. No business logic lives here.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import redis.asyncio as redis
from sqlalchemy import text

from app.api.router import api_router
from app.config import settings
from app.database import create_all_tables, engine
from app.logger import get_logger

log = get_logger(__name__)


# ── Rate limiter (shared with route handlers via app.state) ───────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=[])


# ── Application Lifespan ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    log.info("app.startup", env=settings.app_env)

    # In development: auto-create tables (no migrations needed to start).
    # In production: run `alembic upgrade head` as part of your deploy pipeline.
    if settings.app_env == "development":
        await create_all_tables()

    log.info("app.ready")
    yield  # ← app runs here

    log.info("app.shutdown")


# ── Application Factory ───────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Async content generation engine: keyword → SEO article, "
            "or competitor URL → rewritten article."
        ),
        docs_url="/docs" if settings.debug else None,   # hide Swagger in prod
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
        lifespan=lifespan,
    )

    # ── Rate Limiter Middleware ────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ── CORS ──────────────────────────────────────────────────────────────────
    # In production, replace "*" with your actual frontend domain.
    allowed_origins = (
        ["*"] if settings.app_env == "development"
        else ["https://your-production-domain.com"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    # ── API Routes ────────────────────────────────────────────────────────────
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # ── Health / Readiness Probes ─────────────────────────────────────────────
    @app.get("/health", tags=["ops"], include_in_schema=False)
    async def health() -> JSONResponse:
        return JSONResponse({"status": "healthy"})

    @app.get("/readiness", tags=["ops"], include_in_schema=False)
    async def readiness(request: Request) -> JSONResponse:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

            r = redis.from_url(settings.redis_url)
            await r.ping()
            await r.close()
        except Exception as exc:
            log.error("readiness.failed", error=str(exc))
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable", "detail": str(exc)}
            )

        return JSONResponse({"status": "ready"})

    return app


# ── ASGI entrypoint ───────────────────────────────────────────────────────────
app = create_app()
