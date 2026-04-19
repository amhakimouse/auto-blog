"""
app/integrations/wordpress.py — WordPress REST API publisher.

Flow:
  1. upload_image()     — standalone: POST image bytes → /wp-json/wp/v2/media
  2. publish()          — upload optional featured image + create post draft
  3. update_seo_meta()  — POST to /wp-json/rank-math-api/v1/update-meta/
                          (requires rank-math-api-manager plugin)

Authentication: WordPress Application Passwords (HTTP Basic Auth).
"""
from __future__ import annotations

import json
import mimetypes
from typing import Final

import httpx
import structlog

from app.logger import get_logger

log = get_logger(__name__)

# ── Custom exceptions ──────────────────────────────────────────────────────────

class WordPressPublishError(Exception):
    """Base exception — Celery will retry on this."""

class WordPressAuthError(WordPressPublishError):
    """401/403 — not retryable, wrong credentials."""

class WordPressMediaUploadError(WordPressPublishError):
    """Media endpoint failure."""

class WordPressPostError(WordPressPublishError):
    """Post creation failure."""

class WordPressSEOError(WordPressPublishError):
    """RankMath SEO meta update failure."""


# ── Constants ──────────────────────────────────────────────────────────────────

_WP_API_BASE: Final[str] = "/wp-json/wp/v2"
_MEDIA_ENDPOINT: Final[str] = f"{_WP_API_BASE}/media"
_POSTS_ENDPOINT: Final[str] = f"{_WP_API_BASE}/posts"
_RANKMATH_ENDPOINT: Final[str] = "/wp-json/rank-math-api/v1/update-meta/"
_RANKMATH_PLUGIN_URL: Final[str] = "https://github.com/Devora-AS/rank-math-api-manager"

_CONNECT_TIMEOUT: Final[float] = 15.0
_READ_TIMEOUT: Final[float] = 60.0
_DEFAULT_POST_STATUS: Final[str] = "draft"


# ── Publisher ──────────────────────────────────────────────────────────────────

class WordPressPublisher:
    """Async WordPress article publisher using the WP REST API."""

    def __init__(
        self,
        wp_site_url: str,
        wp_username: str,
        wp_app_password: str,
    ) -> None:
        self._wp_username = wp_username
        self._wp_app_password = wp_app_password.replace(" ", "")
        self._base_url: str = wp_site_url.rstrip("/") + "/"
        self._configured: bool = bool(wp_site_url and wp_username and wp_app_password)
        self._logger: structlog.BoundLogger = log.bind(
            wp_site=self._base_url,
            wp_user=wp_username,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _make_client(self) -> httpx.AsyncClient:
        """Create a pre-configured async httpx client."""
        timeout = httpx.Timeout(
            connect=_CONNECT_TIMEOUT,
            read=_READ_TIMEOUT,
            write=_READ_TIMEOUT,
            pool=5.0,
        )
        return httpx.AsyncClient(
            base_url=self._base_url,
            auth=(self._wp_username, self._wp_app_password),
            headers={"User-Agent": "ContentAutomationEngine/1.0 (Python/httpx)"},
            timeout=timeout,
            follow_redirects=True,
            verify=True,
        )

    def _handle_auth_errors(self, response: httpx.Response, context: str) -> None:
        if response.status_code in (401, 403):
            raise WordPressAuthError(
                f"WordPress authentication failed during {context} "
                f"(HTTP {response.status_code}). "
                "Check WP_USERNAME and WP_APP_PASSWORD. "
                f"Response: {response.text[:200]}"
            )

    # ── Public: Credential Verification ───────────────────────────────────────

    async def get_site_info(self) -> dict:
        """Verify credentials by fetching /wp-json/wp/v2/users/me."""
        if not self._configured:
            raise WordPressPublishError("WordPress is not configured.")

        async with self._make_client() as client:
            try:
                response = await client.get(f"{_WP_API_BASE}/users/me")

                # Detect JS challenge pages (anti-bot / JS injection)
                if "aes.js" in response.text or "toNumbers" in response.text:
                    raise WordPressPublishError(
                        "Connection blocked by a JavaScript security challenge. "
                        "Whitelist the WP REST API (/wp-json/*) in your firewall, "
                        "or switch to a host that allows API access."
                    )

                if not response.text:
                    raise WordPressPublishError(
                        f"WordPress returned an empty response (HTTP {response.status_code}). "
                        "Verify that the WP REST API is enabled on your site."
                    )

                self._handle_auth_errors(response, "credential verification")

                if response.status_code != 200:
                    raise WordPressPublishError(
                        f"WP site check returned HTTP {response.status_code}. "
                        f"Response: {response.text[:200]}"
                    )

                try:
                    return response.json()
                except json.JSONDecodeError:
                    raise WordPressPublishError(
                        f"WordPress returned non-JSON (HTTP {response.status_code}). "
                        f"First 100 chars: {response.text[:100]!r}. "
                        "The URL may be wrong or a security plugin is blocking the request."
                    )

            except httpx.RequestError as exc:
                raise WordPressPublishError(
                    f"Network error connecting to {self._base_url}: {exc}"
                ) from exc

    # ── Public: Standalone Image Upload ───────────────────────────────────────

    async def upload_image(
        self,
        image_bytes: bytes,
        filename: str,
        alt_text: str = "",
        caption: str = "",
        description: str = "",
    ) -> dict:
        """
        Upload image bytes to the WP Media Library.
        Creates its own httpx client — safe to call standalone without publish().

        Returns WP media dict with 'id', 'source_url', etc.
        """
        if not self._configured:
            raise WordPressPublishError("WordPress is not configured.")

        async with self._make_client() as client:
            return await self._upload_media(
                client=client,
                image_bytes=image_bytes,
                filename=filename,
                alt_text=alt_text,
                caption=caption,
                description=description,
            )

    # ── Public: Full Publish ───────────────────────────────────────────────────

    async def publish(
        self,
        title: str,
        html_content: str,
        *,
        image_bytes: bytes | None = None,
        image_filename: str = "featured-image.webp",
        image_alt: str = "",
        image_caption: str = "",
        image_description: str = "",
        featured_media_id: int | None = None,
        slug: str | None = None,
        meta_description: str | None = None,
        focus_keyword: str | None = None,
        tags: list[int] | None = None,
        categories: list[int] | None = None,
        post_status: str = _DEFAULT_POST_STATUS,
    ) -> dict:
        """Upload optional featured image and create a WordPress post draft."""
        if not self._configured:
            raise WordPressPublishError("WordPress is not configured.")

        async with self._make_client() as client:
            self._logger.info("wordpress.publish.start", title=title)

            # Step 1: Upload featured image (optional) if bytes provided and no existing ID
            featured_image_url: str | None = None

            if image_bytes and not featured_media_id:
                upload_result = await self._upload_media(
                    client=client,
                    image_bytes=image_bytes,
                    filename=image_filename,
                    alt_text=image_alt or title,
                    caption=image_caption,
                    description=image_description,
                )
                featured_media_id = upload_result["id"]
                featured_image_url = upload_result.get("source_url")
                self._logger.info(
                    "wordpress.media.uploaded",
                    media_id=featured_media_id,
                    url=featured_image_url,
                )

            # Step 2: Create the post
            post_result = await self._create_post(
                client=client,
                title=title,
                html_content=html_content,
                slug=slug,
                meta_description=meta_description,
                featured_media_id=featured_media_id,
                tags=tags or [],
                categories=categories or [1],
                post_status=post_status,
            )

            wp_post_id = post_result["id"]

            # Step 3: Update RankMath SEO meta
            if focus_keyword or meta_description:
                try:
                    await self.update_seo_meta(
                        client=client,
                        post_id=wp_post_id,
                        focus_keyword=focus_keyword,
                        meta_description=meta_description,
                        seo_title=title,
                    )
                except WordPressSEOError as exc:
                    # SEO meta failure does NOT abort the publish
                    self._logger.warning("wordpress.seo_meta.failed", error=str(exc))

        result = {
            "wp_post_id": wp_post_id,
            "wp_post_url": post_result.get("link", ""),
            "featured_image_url": featured_image_url,
        }
        self._logger.info("wordpress.publish.success", **result)
        return result

    # ── Private: Media upload ──────────────────────────────────────────────────

    async def _upload_media(
        self,
        client: httpx.AsyncClient,
        image_bytes: bytes,
        filename: str,
        alt_text: str,
        caption: str = "",
        description: str = "",
    ) -> dict:
        """Upload image bytes via multipart/form-data to /wp-json/wp/v2/media."""
        self._logger.info("wordpress.media.upload.start", filename=filename, bytes=len(image_bytes))

        mime_type, _ = mimetypes.guess_type(filename)
        mime_type = mime_type or "image/webp"

        try:
            response = await client.post(
                _MEDIA_ENDPOINT,
                content=image_bytes,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Type": mime_type,
                },
            )
            self._handle_auth_errors(response, "media upload")
            response.raise_for_status()
            data = response.json()
            media_id = data["id"]

            # Update alt text / caption / description
            await client.post(
                f"{_MEDIA_ENDPOINT}/{media_id}",
                json={"alt_text": alt_text, "caption": caption, "description": description},
            )
            self._logger.info("wordpress.media.upload.done", media_id=media_id)
            return data

        except httpx.RequestError as exc:
            raise WordPressMediaUploadError(
                f"Network error uploading media to {self._base_url}: {exc}"
            ) from exc

    # ── Private: Post creation ─────────────────────────────────────────────────

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
        """Create a new WP post via the REST API."""
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

        self._handle_auth_errors(response, "post creation")

        if response.status_code not in (200, 201):
            raise WordPressPostError(
                f"WP post creation returned HTTP {response.status_code}: {response.text[:500]}"
            )

        data = response.json()
        self._logger.info(
            "wordpress.post.create.done",
            wp_post_id=data.get("id"),
            wp_post_url=data.get("link"),
        )
        return data

    # ── Public: RankMath SEO Meta ──────────────────────────────────────────────

    async def update_seo_meta(
        self,
        client: httpx.AsyncClient | None,
        post_id: int,
        focus_keyword: str | None = None,
        meta_description: str | None = None,
        seo_title: str | None = None,
    ) -> dict:
        """
        Update SEO metadata via the RankMath REST API.

        Endpoint: POST /wp-json/rank-math-api/v1/update-meta/
        Requires the rank-math-api-manager plugin:
            https://github.com/Devora-AS/rank-math-api-manager

        Raises:
            WordPressSEOError: If the endpoint is not found (plugin not installed)
                               or any other failure occurs.
        """
        self._logger.info("wordpress.rankmath.update.start", post_id=post_id)

        payload: dict = {"post_id": str(post_id)}
        if focus_keyword:
            payload["rank_math_focus_keyword"] = focus_keyword
        if meta_description:
            payload["rank_math_description"] = meta_description
        if seo_title:
            payload["rank_math_title"] = seo_title

        _client_owner = client is None
        _client = client if client else self._make_client()

        try:
            if _client_owner:
                _client = await _client.__aenter__()

            response = await _client.post(_RANKMATH_ENDPOINT, json=payload)

            if response.status_code == 404:
                raise WordPressSEOError(
                    "RankMath API endpoint not found (HTTP 404). "
                    "You must install the 'rank-math-api-manager' plugin: "
                    f"{_RANKMATH_PLUGIN_URL}"
                )

            self._handle_auth_errors(response, "RankMath SEO update")
            response.raise_for_status()
            self._logger.info("wordpress.rankmath.update.done", post_id=post_id)
            return response.json()

        except WordPressSEOError:
            raise
        except WordPressAuthError:
            raise
        except httpx.RequestError as exc:
            raise WordPressSEOError(
                f"Network error calling RankMath API: {exc}"
            ) from exc
        except Exception as exc:
            raise WordPressSEOError(f"Unexpected RankMath error: {exc}") from exc
        finally:
            if _client_owner:
                await _client.__aexit__(None, None, None)


# ── Module-level helper ────────────────────────────────────────────────────────

def is_wordpress_configured(wp_site_url: str, wp_username: str, wp_app_password: str) -> bool:
    """Return True if all three WP credentials are non-empty."""
    return bool(wp_site_url and wp_username and wp_app_password)
