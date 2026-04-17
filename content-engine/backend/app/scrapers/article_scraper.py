"""
app/scrapers/article_scraper.py — Advanced multi-tiered article content extractor.

Tiers:
  1. HTTPX (Standard) — Fast, low overhead.
  2. FlareSolverr — Bypasses Cloudflare / DDoS protection.
  3. Playwright (Headless Chromium) — Handles heavy JavaScript rendering.

Extraction:
  - trafilatura — Industry standard for boilerplate removal and Markdown conversion.
"""
import random
import asyncio
import structlog
import httpx
import trafilatura
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings

log = structlog.get_logger(__name__)

# ── Custom exceptions ──────────────────────────────────────────────────────────

class ArticleScraperError(Exception):
    """Base exception for the scraper."""

class ArticleFetchError(ArticleScraperError):
    """Raised on HTTP-level failures."""

class ArticleExtractionError(ArticleScraperError):
    """Raised when no usable content can be found."""

# ── Constants ──────────────────────────────────────────────────────────────────

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def _get_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

# ── Core function ─────────────────────────────────────────────────────────────

async def extract_article_content(url: str) -> str:
    """
    Tiered extraction strategy.
    """
    logger = log.bind(url=url)
    
    html = await _fetch_raw_html(url, logger)
    content = _extract_markdown(html, url, logger)
    if content:
        return content

    raise ArticleExtractionError(f"All scraping tiers failed for URL: {url}")

async def _fetch_raw_html(url: str, logger: structlog.BoundLogger | None = None) -> str:
    """
    Internal helper that implements tiered fetching (HTTPX -> FlareSolverr -> Playwright).
    Returns raw HTML.
    """
    if logger is None:
        logger = log.bind(url=url)

    # Tier 1: Standard HTTPX
    try:
        logger.info("scraper.tier1.httpx.start")
        return await _fetch_with_httpx(url, logger)
    except Exception as exc:
        logger.warning("scraper.tier1.failed", error=str(exc))

    # Tier 2: FlareSolverr (Cloudflare bypass)
    if settings.flaresolverr_url:
        try:
            logger.info("scraper.tier2.flaresolverr.start")
            return await _fetch_with_flaresolverr(url, logger)
        except Exception as exc:
            logger.warning("scraper.tier2.failed", error=str(exc))

    # Tier 3: Playwright (JavaScript rendering)
    try:
        logger.info("scraper.tier3.playwright.start")
        return await _fetch_with_playwright(url, logger)
    except Exception as exc:
        logger.error("scraper.tier3.failed", error=str(exc))
        raise ArticleFetchError(f"All fetching tiers failed for URL: {url}") from exc

# ── Tier 1: HTTPX ──────────────────────────────────────────────────────────────

async def _fetch_with_httpx(url: str, logger: structlog.BoundLogger) -> str:
    async with httpx.AsyncClient(
        headers=_get_headers(),
        timeout=settings.scrape_timeout_seconds,
        follow_redirects=True,
        proxy=settings.scrape_proxy_url if settings.scrape_proxy_url else None,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text

# ── Tier 2: FlareSolverr ───────────────────────────────────────────────────────

async def _fetch_with_flaresolverr(url: str, logger: structlog.BoundLogger) -> str:
    """
    Call FlareSolverr to bypass Cloudflare.
    """
    if not settings.flaresolverr_url:
        raise ArticleFetchError("FlareSolverr URL not configured")

    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000,
    }
    
    async with httpx.AsyncClient(timeout=70.0) as client:
        response = await client.post(
            f"{settings.flaresolverr_url}/v1",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        data = response.json()
        
        if data.get("status") == "ok":
            return data["solution"]["response"]
        
        raise ArticleFetchError(f"FlareSolverr failed: {data.get('message')}")

# ── Tier 3: Playwright ─────────────────────────────────────────────────────────

async def _fetch_with_playwright(url: str, logger: structlog.BoundLogger) -> str:
    """
    Use Playwright to render JavaScript-heavy sites.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=random.choice(_USER_AGENTS))
            page = await context.new_page()
            
            # Navigate and wait for content
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            # Scroll to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2) # Wait for lazy content
            
            html = await page.content()
            return html
        finally:
            await browser.close()

# ── Extraction ────────────────────────────────────────────────────────────────

def _extract_markdown(raw_html: str, url: str, logger: structlog.BoundLogger) -> str | None:
    """
    Extract clean Markdown using trafilatura.
    """
    if not raw_html or len(raw_html) < 100:
        return None

    extracted = trafilatura.extract(
        raw_html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
    )
    
    if extracted and len(extracted.strip()) > 200:
        return extracted.strip()
    
    return None
