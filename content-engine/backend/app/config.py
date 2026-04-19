"""
app/config.py — Centralised settings loaded from environment variables.

Uses pydantic-settings so every setting is type-validated at startup.
Never import os.environ directly elsewhere; always use `from app.config import settings`.
"""

from functools import lru_cache
from typing import Literal

from pydantic import AnyUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # silently drop unknown env vars
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: Literal["development", "production"] = "development"

    # ── JWT Auth ──────────────────────────────────────────────────────────────
    secret_key: str = Field(..., min_length=32)
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    debug: bool = False
    app_name: str = "Content Automation Engine"
    api_v1_prefix: str = "/api/v1"

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str  # e.g. postgresql+asyncpg://user:pass@host:5432/db
    # Connection pool settings
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30

    # ── Redis / Celery ────────────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"

    # ── FlareSolverr ──────────────────────────────────────────────────────────
    flaresolverr_url: str | None = "http://flaresolverr:8191"

    # ── Google Cloud / Vertex AI ──────────────────────────────────────────────
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"  # e.g., us-central1, europe-west1
    google_application_credentials: str = "/secrets/gcp-service-account.json"
    vertex_ai_model: str = "gemini-2.5-flash"  # Uses direct REST API
    imagen_model: str = "imagegeneration@006"
    gemini_use_rest_api: bool = True  # Use direct HTTP REST API instead of SDK

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_requests: int = 10
    rate_limit_window_seconds: int = 3600

    # ── Scraping / Security ───────────────────────────────────────────────────
    scrape_timeout_seconds: int = 20
    max_scrape_urls: int = 5
    # Proxy URL for scraping (e.g. http://user:pass@proxy_host:port) to bypass DDG rate limits
    scrape_proxy_url: str | None = None
    # Comma-separated CIDR blocks to block for SSRF protection
    ssrf_blocked_ranges: str = (
        "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,::1/128"
    )

    @field_validator("scrape_proxy_url", mode="before")
    @classmethod
    def strip_proxy_comments(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            # Split by # and take the first part, then strip whitespace
            clean = v.split("#")[0].strip()
            return clean if clean else None
        return v

    @field_validator("database_url")
    @classmethod
    def must_be_async_driver(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg"):
            raise ValueError(
                "DATABASE_URL must use the asyncpg driver: postgresql+asyncpg://..."
            )
        return v

    @property
    def ssrf_blocked_ranges_list(self) -> list[str]:
        return [r.strip() for r in self.ssrf_blocked_ranges.split(",") if r.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()


# Module-level convenience alias
settings: Settings = get_settings()
