"""
app/scrapers/article_scraper.py — Async article content extractor.

Workflow 2 (Link → Article) ingestion step.

Flow:
  1. Fetch raw HTML from the target URL via httpx (async).
  2. Strip boilerplate and extract the main article body via trafilatura.
  3. Return clean Markdown text to the caller (the Celery pipeline).

Security note:
  SSRF validation is performed at the API layer (app/security.py) before the
  URL ever reaches this function. This module trusts the URL is safe to fetch.
"""
from __future__ import annotations

import structlog
import httpx
import trafilatura

from app.config import settings

log = structlog.get_logger(__name__)

# ── Custom exceptions ──────────────────────────────────────────────────────────

class ArticleScraperError(Exception):
    """Raised when the scraper cannot fetch or extract article content."""


class ArticleFetchError(ArticleScraperError):
    """Raised on HTTP-level failures (4xx, 5xx, timeout, network error)."""


class ArticleExtractionError(ArticleScraperError):
    """Raised when trafilatura returns None or an empty result."""


# ── Constants ──────────────────────────────────────────────────────────────────

# A realistic desktop User-Agent so sites don't serve bot-mitigation pages.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


# ── Core function ─────────────────────────────────────────────────────────────

async def extract_article_content(url: str) -> str:
    """
    Asynchronously fetch *url* and return the main article body as Markdown.

    Args:
        url: A fully-qualified HTTP/HTTPS URL that has already passed SSRF
             validation at the API layer.

    Returns:
        A non-empty Markdown string containing the extracted article text.

    Raises:
        ArticleFetchError: The URL could not be fetched (bad status, timeout,
                           network error, or SSL failure).
        ArticleExtractionError: trafilatura could not identify meaningful
                                content in the fetched document.
    """
    logger = log.bind(url=url)
    logger.info("scraper.fetch.start")

    # ── Step 1: Fetch raw HTML ────────────────────────────────────────────────
    raw_html: str = await _fetch_html(url, logger)

    # ── Step 2: Extract article body as Markdown ──────────────────────────────
    markdown: str = _extract_markdown(raw_html, url, logger)

    logger.info("scraper.extract.success", markdown_chars=len(markdown))
    return markdown


async def _fetch_raw_html(url: str) -> str:
    """
    Convenience wrapper: fetch a URL and return raw HTML.

    Used by keyword_pipeline._scrape_outline() which needs raw HTML for
    BeautifulSoup parsing rather than trafilatura-extracted Markdown.

    Raises:
        ArticleFetchError on any fetch failure.
    """
    logger = log.bind(url=url, purpose="outline_extraction")
    return await _fetch_html(url, logger)


# ── Private helpers ───────────────────────────────────────────────────────────

async def _fetch_html(url: str, logger: structlog.BoundLogger) -> str:
    """Return raw HTML as a string, raising ArticleFetchError on any failure."""
    timeout = httpx.Timeout(
        connect=10.0,
        read=float(settings.scrape_timeout_seconds),
        write=10.0,
        pool=5.0,
    )

    try:
        async with httpx.AsyncClient(
            headers=_DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
            verify=True,           # enforce valid TLS certificates
        ) as client:
            response = await client.get(url)
            response.raise_for_status()  # raises HTTPStatusError on 4xx/5xx

        logger.info(
            "scraper.fetch.success",
            status_code=response.status_code,
            content_length=len(response.text),
        )
        return response.text

    except httpx.HTTPStatusError as exc:
        msg = (
            f"HTTP {exc.response.status_code} received for URL: {url}. "
            f"Reason: {exc.response.reason_phrase}"
        )
        logger.warning("scraper.fetch.http_error", status_code=exc.response.status_code)
        raise ArticleFetchError(msg) from exc

    except httpx.TimeoutException as exc:
        msg = (
            f"Request timed out after {settings.scrape_timeout_seconds}s "
            f"while fetching: {url}"
        )
        logger.warning("scraper.fetch.timeout")
        raise ArticleFetchError(msg) from exc

    except httpx.TooManyRedirects as exc:
        msg = f"Too many redirects (>5) when fetching: {url}"
        logger.warning("scraper.fetch.too_many_redirects")
        raise ArticleFetchError(msg) from exc

    except httpx.RequestError as exc:
        # Covers: ConnectError, RemoteProtocolError, SSLError, etc.
        msg = f"Network error when fetching {url}: {type(exc).__name__}: {exc}"
        logger.warning("scraper.fetch.network_error", exc_type=type(exc).__name__)
        raise ArticleFetchError(msg) from exc


def _extract_markdown(raw_html: str, url: str, logger: structlog.BoundLogger) -> str:
    """
    Run trafilatura extraction and return a non-empty Markdown string.

    trafilatura is configured for maximum content quality:
      - output_format="markdown" → preserves headings and lists
      - include_comments=False → strips comment sections
      - include_tables=True → keeps structured data
      - no_fallback=False → tries alternative extraction heuristics if primary fails
    """
    try:
        extracted: str | None = trafilatura.extract(
            raw_html,
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_precision=False,   # favor recall; we want maximum article text
        )
    except Exception as exc:
        # trafilatura should not raise normally, but guard defensively.
        msg = f"trafilatura raised an unexpected error for {url}: {exc}"
        logger.error("scraper.extract.trafilatura_exception", error=str(exc))
        raise ArticleExtractionError(msg) from exc

    if not extracted or not extracted.strip():
        msg = (
            f"trafilatura returned no usable content for URL: {url}. "
            "The page may be behind a paywall, JavaScript-rendered, or "
            "contain insufficient article text."
        )
        logger.warning("scraper.extract.empty_result")
        raise ArticleExtractionError(msg)

    return extracted.strip()
