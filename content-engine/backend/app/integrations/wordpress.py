"""
app/integrations/wordpress.py — WordPress REST API publisher.

Publishes fully-assembled articles (title + sanitized HTML + featured image)
to a WordPress site via the WP REST API using Application Passwords.

Flow:
  1. _upload_media()  — POST image bytes to /wp-json/wp/v2/media → media_id
  2. _create_post()   — POST article HTML to /wp-json/wp/v2/posts with media_id
  3. publish()        — orchestrates 1 + 2, returns {wp_post_id, wp_post_url}

Configuration (read from environment via app.config):
  WP_SITE_URL        — e.g. https://myblog.com  (no trailing slash)
  WP_USERNAME        — WordPress username
  WP_APP_PASSWORD    — WordPress Application Password (spaces are OK, stripped here)

Authentication:
  WordPress Application Passwords use HTTP Basic Auth.
  Generate one at: WP Admin → Users → Profile → Application Passwords.
  The password is in the format "xxxx xxxx xxxx xxxx xxxx xxxx" — spaces are fine,
  we strip them before encoding.

Design decisions:
  - Uses httpx.AsyncClient (async) with asyncio.run() bridge in the pipeline.
  - Raise WordPressPublishError for all failures so Celery can retry the task.
  - WP-specific errors (bad credentials, already-exists) surface with clear messages.
  - All operations are idempotent-safe: the pipeline stores wp_post_id before
    returning so a retry will detect the existing post and skip re-publishing.
"""
from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from datetime import datetime, timezone
from typing import Final

import httpx
import structlog

from app.config import settings
from app.logger import get_logger

log = get_logger(__name__)

# ── Custom exceptions ──────────────────────────────────────────────────────────

class WordPressPublishError(Exception):
    """
    Base exception for WordPress publish failures.

    Celery will catch this and retry with exponential back-off.
    Pipeline code that detects permanent failures (e.g. 401 Unauthorized)
    should raise ``WordPressAuthError`` instead, which callers may choose
    not to retry.
    """


class WordPressAuthError(WordPressPublishError):
    """
    Raised on HTTP 401 / 403 responses.

    These are NOT retryable — wrong credentials will never succeed.
    Catch this specifically in _finalize() to skip publishing without
    aborting the overall pipeline.
    """


class WordPressMediaUploadError(WordPressPublishError):
    """Raised when the image upload to /wp/v2/media fails."""


class WordPressPostError(WordPressPublishError):
    """Raised when creating the WP post via /wp/v2/posts fails."""


# ── Constants ──────────────────────────────────────────────────────────────────

_WP_API_BASE: Final[str] = "/wp-json/wp/v2"
_MEDIA_ENDPOINT: Final[str] = f"{_WP_API_BASE}/media"
_POSTS_ENDPOINT: Final[str] = f"{_WP_API_BASE}/posts"
_CONNECT_TIMEOUT: Final[float] = 15.0
_READ_TIMEOUT: Final[float] = 60.0   # large uploads can be slow

# WP post status — "publish" makes it live immediately.
# Use "draft" if you want human review before publishing.
_DEFAULT_POST_STATUS: Final[str] = "publish"


# ── Publisher class ────────────────────────────────────────────────────────────

class WordPressPublisher:
    """
    Async WordPress article publisher using the WP REST API.

    Intended to be called from inside a Celery pipeline via ``asyncio.run()``.

    Usage::

        publisher = WordPressPublisher()
        result = asyncio.run(publisher.publish(
            title="My Article",
            html_content="<h2>Hello</h2><p>World</p>",
            image_bytes=png_bytes,          # optional
            image_filename="hero.png",      # optional
            image_alt="Hero image",         # optional
            slug="my-article",             # optional
            meta_description="...",        # optional
            tags=[],                       # optional WP tag IDs
            categories=[],                # optional WP category IDs
        ))
        # result = {"wp_post_id": 42, "wp_post_url": "https://..."}
    """

    def __init__(
        self,
        wp_site_url: str,
        wp_username: str,
        wp_app_password: str,
    ) -> None:
        # Strip spaces from Application Password (WP generates them with spaces)
        raw_password = wp_app_password.replace(" ", "")
        credentials = f"{wp_username}:{raw_password}"
        encoded = base64.b64encode(credentials.encode()).decode()

        self._base_url: str = wp_site_url.rstrip("/")
        self._auth_header: str = f"Basic {encoded}"
        self._configured: bool = bool(
            wp_site_url
            and wp_username
            and wp_app_password
        )

        self._logger: structlog.BoundLogger = log.bind(
            wp_site=self._base_url,
            wp_user=wp_username,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def publish(
        self,
        title: str,
        html_content: str,
        *,
        image_bytes: bytes | None = None,
        image_filename: str = "featured-image.png",
        image_alt: str = "",
        slug: str | None = None,
        meta_description: str | None = None,
        tags: list[int] | None = None,
        categories: list[int] | None = None,
        post_status: str = _DEFAULT_POST_STATUS,
    ) -> dict:
        """
        Upload an optional featured image and create a new WordPress post.

        Args:
            title: Plain-text article title (maps to WP post title).
            html_content: Sanitized HTML body (maps to WP post content).
            image_bytes: Raw bytes of the featured image (PNG/JPEG). If None,
                         the post is created without a featured image.
            image_filename: Filename to use when uploading to WP Media Library.
            image_alt: Alt text for the featured image.
            slug: Custom WP post slug. Derived from title if not provided.
            meta_description: Used to set the Yoast / RankMath SEO excerpt field.
            tags: List of existing WP tag IDs to attach.
            categories: List of existing WP category IDs. Defaults to [1]
                        (Uncategorized) if not provided.
            post_status: "publish", "draft", or "pending". Defaults to "publish".

        Returns:
            Dict with:
              - ``wp_post_id``  (int) — WordPress post database ID
              - ``wp_post_url`` (str) — Public permalink of the new post

        Raises:
            WordPressAuthError: Invalid credentials (401/403) — non-retryable.
            WordPressMediaUploadError: Image upload failed.
            WordPressPostError: Post creation failed.
            WordPressPublishError: Any other WP API error.
        """
        if not self._configured:
            raise WordPressPublishError(
                "WordPress is not configured. Set WP_SITE_URL, WP_USERNAME, "
                "and WP_APP_PASSWORD in your .env file."
            )

        timeout = httpx.Timeout(connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT,
                                write=_READ_TIMEOUT, pool=5.0)

        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": self._auth_header,
                "User-Agent": "ContentAutomationEngine/1.0 (Python/httpx)",
            },
            timeout=timeout,
            follow_redirects=True,
        ) as client:

            # ── Step 1: Upload featured image (optional) ──────────────────────
            featured_media_id: int | None = None
            featured_image_url: str | None = None

            if image_bytes:
                upload_result = await self._upload_media(
                    client=client,
                    image_bytes=image_bytes,
                    filename=image_filename,
                    alt_text=image_alt or title,
                )
                featured_media_id = upload_result["id"]
                featured_image_url = upload_result.get("source_url")
                self._logger.info(
                    "wordpress.media.uploaded",
                    media_id=featured_media_id,
                    url=featured_image_url,
                )

            # ── Step 2: Create the post ───────────────────────────────────────
            post_result = await self._create_post(
                client=client,
                title=title,
                html_content=html_content,
                slug=slug,
                meta_description=meta_description,
                featured_media_id=featured_media_id,
                tags=tags or [],
                categories=categories or [1],  # 1 = Uncategorized
                post_status=post_status,
            )

        result = {
            "wp_post_id": post_result["id"],
            "wp_post_url": post_result.get("link", ""),
            "featured_image_url": featured_image_url,
        }

        self._logger.info(
            "wordpress.publish.success",
            wp_post_id=result["wp_post_id"],
            wp_post_url=result["wp_post_url"],
        )
        return result

    # ── Private: Media upload ─────────────────────────────────────────────────

    async def _upload_media(
        self,
        client: httpx.AsyncClient,
        image_bytes: bytes,
        filename: str,
        alt_text: str,
    ) -> dict:
        """
        Upload image bytes to the WP Media Library via multipart/form-data.

        Args:
            client: Shared httpx.AsyncClient with auth headers.
            image_bytes: Raw image bytes (PNG or JPEG).
            filename: e.g. "hero-image.png".
            alt_text: Alt text string set on the WP attachment.

        Returns:
            Dict from WP API: ``{id, source_url, …}``.

        Raises:
            WordPressAuthError: 401/403 response.
            WordPressMediaUploadError: Any other failure.
        """
        self._logger.info(
            "wordpress.media.upload.start",
            filename=filename,
            bytes=len(image_bytes),
        )

        mime_type, _ = mimetypes.guess_type(filename)
        mime_type = mime_type or "image/png"

        try:
            response = await client.post(
                _MEDIA_ENDPOINT,
                content=image_bytes,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Type": mime_type,
                },
                params={"alt_text": alt_text},
            )
        except httpx.RequestError as exc:
            raise WordPressMediaUploadError(
                f"Network error uploading media to {self._base_url}: {exc}"
            ) from exc

        self._handle_auth_errors(response, context="media upload")

        if response.status_code not in (200, 201):
            raise WordPressMediaUploadError(
                f"WP media upload returned HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        data: dict = response.json()
        self._logger.info(
            "wordpress.media.upload.done",
            media_id=data.get("id"),
            source_url=data.get("source_url"),
        )
        return data

    # ── Private: Post creation ────────────────────────────────────────────────

    async def _create_post(
        self,
        client: httpx.AsyncClient,
        title: str,
        html_content: str,
        slug: str | None,
        meta_description: str | None,
        featured_media_id: int | None,
        tags: list[int],
        categories: list[int],
        post_status: str,
    ) -> dict:
        """
        Create a new WP post via the REST API.

        Returns:
            Dict from WP API: ``{id, link, …}``.

        Raises:
            WordPressAuthError: 401/403 response.
            WordPressPostError: Any other failure.
        """
        self._logger.info("wordpress.post.create.start", title=title)

        payload: dict = {
            "title": title,
            "content": html_content,
            "status": post_status,
            "categories": categories,
            "tags": tags,
        }

        if slug:
            payload["slug"] = slug

        if featured_media_id is not None:
            payload["featured_media"] = featured_media_id

        # WP stores the meta description in the "excerpt" field.
        # Compatible with both Yoast SEO and RankMath when excerpt is set.
        if meta_description:
            payload["excerpt"] = meta_description

        try:
            response = await client.post(
                _POSTS_ENDPOINT,
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
        except httpx.RequestError as exc:
            raise WordPressPostError(
                f"Network error creating WP post at {self._base_url}: {exc}"
            ) from exc

        self._handle_auth_errors(response, context="post creation")

        if response.status_code not in (200, 201):
            raise WordPressPostError(
                f"WP post creation returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        data: dict = response.json()
        self._logger.info(
            "wordpress.post.create.done",
            wp_post_id=data.get("id"),
            wp_post_url=data.get("link"),
        )
        return data

    # ── Private: Error handling ────────────────────────────────────────────────

    def _handle_auth_errors(self, response: httpx.Response, context: str) -> None:
        """Raise ``WordPressAuthError`` on credential failures."""
        if response.status_code in (401, 403):
            raise WordPressAuthError(
                f"WordPress authentication failed during {context} "
                f"(HTTP {response.status_code}). "
                "Check WP_USERNAME and WP_APP_PASSWORD in your .env. "
                f"Response: {response.text[:200]}"
            )


# ── Module-level convenience function ─────────────────────────────────────────

def is_wordpress_configured(wp_site_url: str, wp_username: str, wp_app_password: str) -> bool:
    """
    Return True if all three WP credentials are non-empty.

    Use this as an early guard in _finalize() to skip publishing silently
    when WP credentials have not been set yet (e.g. during development).
    """
    return bool(
        wp_site_url
        and wp_username
        and wp_app_password
    )
