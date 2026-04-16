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
import random
import structlog
import httpx
import trafilatura
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

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

# Realistic desktop User-Agents for rotation to bypass basic bot protection.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

def _get_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }


# ── Core function ─────────────────────────────────────────────────────────────

async def extract_article_content(url: str) -> str:
    """
    Asynchronously fetch *url* and return the main article body as Markdown.
    """
    logger = log.bind(url=url)
    logger.info("scraper.fetch.start")

    # ── Step 1: Fetch raw HTML with retries and proxies ───────────────────────
    raw_html: str = await _fetch_html_with_retries(url, logger)

    # ── Step 2: Extract article body as Markdown ──────────────────────────────
    markdown: str = _extract_markdown(raw_html, url, logger)

    logger.info("scraper.extract.success", markdown_chars=len(markdown))
    return markdown


async def _fetch_raw_html(url: str) -> str:
    """
    Convenience wrapper: fetch a URL and return raw HTML.
    Used by keyword_pipeline._scrape_outline().
    """
    logger = log.bind(url=url, purpose="outline_extraction")
    return await _fetch_html_with_retries(url, logger)


# ── Private helpers ───────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(ArticleFetchError),
    reraise=True,
)
async def _fetch_html_with_retries(url: str, logger: structlog.BoundLogger) -> str:
    """Wrapper around _fetch_html to provide tenacity-based retries."""
    return await _fetch_html(url, logger)

async def _fetch_html(url: str, logger: structlog.BoundLogger) -> str:
    """Return raw HTML as a string, raising ArticleFetchError on any failure."""
    timeout = httpx.Timeout(
        connect=10.0,
        read=float(settings.scrape_timeout_seconds),
        write=10.0,
        pool=5.0,
    )

    # Configure proxy if available
    proxies = None
    if settings.scrape_proxy_url:
        proxies = {"all://": settings.scrape_proxy_url}

    try:
        async with httpx.AsyncClient(
            headers=_get_headers(),
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
            verify=True,
            proxy=settings.scrape_proxy_url if settings.scrape_proxy_url else None,
        ) as client:
            response = await client.get(url)
            
            # If blocked (403/429), raise so tenacity retries with a new User-Agent
            if response.status_code in (403, 429):
                logger.warning("scraper.fetch.blocked", status_code=response.status_code)
                raise ArticleFetchError(f"Site blocked access with status {response.status_code}")
                
            response.raise_for_status()

        logger.info(
            "scraper.fetch.success",
            status_code=response.status_code,
            content_length=len(response.text),
        )
        return response.text

    except httpx.HTTPStatusError as exc:
        msg = f"HTTP {exc.response.status_code} received for URL: {url}"
        logger.warning("scraper.fetch.http_error", status_code=exc.response.status_code)
        raise ArticleFetchError(msg) from exc

    except httpx.TimeoutException as exc:
        msg = f"Request timed out after {settings.scrape_timeout_seconds}s for {url}"
        logger.warning("scraper.fetch.timeout")
        raise ArticleFetchError(msg) from exc

    except Exception as exc:
        msg = f"Fetch failed for {url}: {type(exc).__name__}: {exc}"
        logger.warning("scraper.fetch.error", exc_type=type(exc).__name__)
        raise ArticleFetchError(msg) from exc


def _extract_markdown(raw_html: str, url: str, logger: structlog.BoundLogger) -> str:
    """
    Run trafilatura extraction and return a non-empty Markdown string.
    """
    try:
        # Attempt 1: Standard clean extraction
        extracted: str | None = trafilatura.extract(
            raw_html,
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_precision=False,
        )
        
        # Attempt 2: Fallback with comments (sometimes contains hidden content)
        if not extracted or len(extracted.strip()) < 200:
            logger.info("scraper.extract.trying_fallback_with_comments")
            extracted = trafilatura.extract(
                raw_html,
                url=url,
                output_format="markdown",
                include_comments=True,
                include_tables=True,
                no_fallback=False,
            )

    except Exception as exc:
        msg = f"trafilatura raised an unexpected error for {url}: {exc}"
        logger.error("scraper.extract.trafilatura_exception", error=str(exc))
        raise ArticleExtractionError(msg) from exc

    if not extracted or not extracted.strip():
        msg = (
            f"No usable content found for URL: {url}. "
            "This site might be JavaScript-rendered or heavily protected."
        )
        logger.warning("scraper.extract.empty_result")
        raise ArticleExtractionError(msg)

    return extracted.strip()
