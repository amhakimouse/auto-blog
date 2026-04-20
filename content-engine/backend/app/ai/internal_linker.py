"""
app/ai/internal_linker.py — Smart Internal Linking Engine.

Responsibilities:
  1. Fetch recent WordPress posts (title + URL) via the WP REST API.
  2. Cache the result in Redis per integration (TTL = 4 hours) to prevent a
     network round-trip for every concurrent Celery job targeting the same site.
  3. Expose a single public function — `fetch_link_candidates()` — that is
     resilient: if the WP API is unreachable the function returns an empty list
     so the caller can fall back to generating the article without links.

Cache key schema:
    wp_link_cache:<integration_id>
    Value: JSON-serialised list[{"title": ..., "url": ...}]
    TTL: 4 hours (configurable via LINK_CACHE_TTL_SECONDS env-var or default).

Design notes:
  - Uses the synchronous `redis` client (already configured in app/redis.py)
    because this module is called from inside Celery workers (no event loop).
  - HTTP calls are made with `httpx` (synchronous, not async) for the same reason.
  - All exceptions are caught and logged — the caller MUST handle an empty list.
"""

from __future__ import annotations

import base64
import json
from typing import Optional

import httpx
import structlog

from app.redis import get_redis_client

log = structlog.get_logger(__name__)

# ── Cache configuration ────────────────────────────────────────────────────────
_LINK_CACHE_TTL_SECONDS: int = 60 * 60 * 4   # 4 hours
_WP_FETCH_PER_PAGE: int = 50                  # posts per WP REST page (max 100)
_WP_MAX_PAGES: int = 3                        # fetch at most 150 posts (3 × 50)
_WP_REQUEST_TIMEOUT: float = 15.0            # seconds per HTTP call

# ── Type alias ─────────────────────────────────────────────────────────────────
LinkCandidate = dict  # {"title": str, "url": str}


def _cache_key(integration_id: str) -> str:
    return f"wp_link_cache:{integration_id}"


def _build_auth_header(wp_username: str, wp_app_password: str) -> dict[str, str]:
    """Construct the Basic-Auth header expected by the WP REST API."""
    raw_password = wp_app_password.replace(" ", "")
    credentials = f"{wp_username}:{raw_password}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "User-Agent": "ContentAutomationEngine/InternalLinker-1.0",
    }


def _fetch_from_wordpress(
    wp_url: str,
    wp_username: str,
    wp_app_password: str,
    logger: structlog.BoundLogger,
) -> list[LinkCandidate]:
    """
    Paginate through the WP REST API and collect title + URL for published posts.

    Returns an empty list on any error so the caller can fall back gracefully.
    """
    base_url = wp_url.rstrip("/")
    api_url = f"{base_url}/wp-json/wp/v2/posts"
    headers = _build_auth_header(wp_username, wp_app_password)
    candidates: list[LinkCandidate] = []

    try:
        with httpx.Client(timeout=_WP_REQUEST_TIMEOUT) as client:
            for page in range(1, _WP_MAX_PAGES + 1):
                response = client.get(
                    api_url,
                    params={
                        "status": "publish",
                        "_fields": "title,link",
                        "per_page": _WP_FETCH_PER_PAGE,
                        "page": page,
                    },
                    headers=headers,
                )
                # 400 means we've gone past the last page — WP REST quirk
                if response.status_code == 400:
                    break
                response.raise_for_status()

                posts = response.json()
                if not posts:
                    break

                for post in posts:
                    title_raw = post.get("title", {})
                    # WP returns title as {"rendered": "..."}
                    if isinstance(title_raw, dict):
                        title = title_raw.get("rendered", "").strip()
                    else:
                        title = str(title_raw).strip()
                    url = post.get("link", "").strip()
                    if title and url:
                        candidates.append({"title": title, "url": url})

                total_pages = int(response.headers.get("X-WP-TotalPages", 1))
                logger.debug(
                    "internal_linker.wp_page_fetched",
                    page=page,
                    total_pages=total_pages,
                    fetched=len(posts),
                )
                if page >= total_pages:
                    break

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "internal_linker.wp_http_error",
            status=exc.response.status_code,
            error=str(exc),
        )
    except httpx.TimeoutException:
        logger.warning("internal_linker.wp_timeout", wp_url=wp_url)
    except httpx.RequestError as exc:
        logger.warning("internal_linker.wp_request_error", error=str(exc))
    except Exception as exc:
        logger.error("internal_linker.wp_unexpected", error=str(exc))

    return candidates


def fetch_link_candidates(
    integration_id: str,
    wp_url: str,
    wp_username: str,
    wp_app_password: str,
    logger: Optional[structlog.BoundLogger] = None,
) -> list[LinkCandidate]:
    """
    Return a cached list of {"title", "url"} dicts for the given WordPress site.

    Algorithm:
      1. Check Redis cache. If hit → deserialise and return immediately.
      2. If miss → call WP REST API (paginated).
      3. Write result to Redis with TTL (even if result is empty — avoids
         stampeding on a temporarily-unreachable site).
      4. On any exception → log warning and return [] (never raise).

    Args:
        integration_id:   UUID string used as part of the Redis cache key.
        wp_url:           WordPress site URL (scheme + host, no trailing slash needed).
        wp_username:      WP application username.
        wp_app_password:  WP application password (spaces allowed — stripped internally).
        logger:           Optional bound structlog logger; creates one if omitted.

    Returns:
        list[{"title": str, "url": str}] — may be empty on failure or no posts.
    """
    if logger is None:
        logger = log.bind(integration_id=integration_id)

    redis = get_redis_client()
    cache_key = _cache_key(integration_id)

    # ── 1. Cache hit? ──────────────────────────────────────────────────────────
    try:
        cached = redis.get(cache_key)
        if cached:
            candidates = json.loads(cached)
            logger.info(
                "internal_linker.cache_hit",
                integration_id=integration_id,
                count=len(candidates),
            )
            return candidates
    except Exception as exc:
        # Redis failure must never crash the pipeline — treat as cache miss
        logger.warning("internal_linker.redis_get_failed", error=str(exc))

    # ── 2. Cache miss — fetch from WP ─────────────────────────────────────────
    logger.info("internal_linker.cache_miss", integration_id=integration_id)
    candidates = _fetch_from_wordpress(
        wp_url=wp_url,
        wp_username=wp_username,
        wp_app_password=wp_app_password,
        logger=logger,
    )

    # ── 3. Write to Redis (best-effort) ───────────────────────────────────────
    try:
        redis.setex(cache_key, _LINK_CACHE_TTL_SECONDS, json.dumps(candidates))
        logger.info(
            "internal_linker.cache_written",
            integration_id=integration_id,
            count=len(candidates),
            ttl_seconds=_LINK_CACHE_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning("internal_linker.redis_set_failed", error=str(exc))

    return candidates
