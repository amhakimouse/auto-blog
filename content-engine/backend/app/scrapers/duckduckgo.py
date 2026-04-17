"""
app/scrapers/duckduckgo.py — DuckDuckGo organic search result scraper.

Workflow 1 (Keyword → Article) discovery step.

Strategy:
  DuckDuckGo serves a pure-HTML search page at https://lite.duckduckgo.com/lite/
  This module uses a tiered approach:
  1. Standard HTTPX with rotated User-Agents.
  2. FlareSolverr (Cloudflare bypass) if Tier 1 is blocked.

Rate-limit handling:
  DuckDuckGo returns HTTP 202 or a CAPTCHA page when overloaded.
  Both cases raise DuckDuckGoRateLimitError so Celery can retry with backoff.
"""
from __future__ import annotations

import re
import time
import random
import urllib.parse
from typing import Final

import httpx
import structlog
from bs4 import BeautifulSoup

from app.config import settings
from app.logger import get_logger

log = get_logger(__name__)

# ── Custom exceptions ──────────────────────────────────────────────────────────

class DuckDuckGoError(Exception):
    """Base exception for DuckDuckGo scraper failures."""


class DuckDuckGoRateLimitError(DuckDuckGoError):
    """
    Raised when DuckDuckGo detects bot traffic.
    Celery task layer should catch this and retry with exponential back-off.
    """


class DuckDuckGoFetchError(DuckDuckGoError):
    """Raised on network-level or HTTP failures unrelated to rate limiting."""


class DuckDuckGoParseError(DuckDuckGoError):
    """Raised when the response HTML cannot be parsed into valid result URLs."""


# ── Constants ──────────────────────────────────────────────────────────────────

_DDG_ENDPOINT: Final[str] = "https://lite.duckduckgo.com/lite/"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

def _get_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://lite.duckduckgo.com",
        "Referer": "https://lite.duckduckgo.com/",
        "DNT": "1",
    }

_DDG_REDIRECT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:uddg|u)=([^&]+)", re.IGNORECASE
)

_BLOCKLIST: Final[frozenset[str]] = frozenset({
    "youtube.com", "youtu.be",
    "reddit.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "pinterest.com",
    "amazon.com", "ebay.com",
    "duckduckgo.com", "bing.com", "google.com",
})

_RATELIMIT_SIGNALS: Final[tuple[str, ...]] = (
    "unusual traffic",
    "captcha",
    "rate limit",
    "automated requests",
    "blocked",
)


# ── Public interface ──────────────────────────────────────────────────────────

class DuckDuckGoScraper:
    """
    Synchronous DuckDuckGo HTML search scraper with FlareSolverr fallback.
    """

    def __init__(
        self,
        timeout_seconds: float | None = None,
        polite_delay_ms: int = 500,
    ) -> None:
        self._timeout = httpx.Timeout(
            connect=10.0,
            read=float(timeout_seconds or settings.scrape_timeout_seconds),
            write=10.0,
            pool=5.0,
        )
        self._polite_delay_s: float = polite_delay_ms / 1000.0
        self._proxy = settings.scrape_proxy_url

    def search(self, keyword: str, n: int = 5) -> list[str]:
        n = min(n, 10)
        logger = log.bind(keyword=keyword, n=n)
        logger.info("duckduckgo.search.start")

        if self._polite_delay_s > 0:
            time.sleep(self._polite_delay_s)

        # Tier 1: Direct HTTPX
        try:
            raw_html = self._post_search_httpx(keyword, logger)
            self._check_for_rate_limit(raw_html, logger)
            urls = self._parse_results(raw_html, n, logger)
            if urls:
                logger.info("duckduckgo.search.done", returned=len(urls), tier="httpx")
                return urls
        except DuckDuckGoRateLimitError:
            logger.warning("duckduckgo.tier1.blocked")
        except Exception as exc:
            logger.warning("duckduckgo.tier1.error", error=str(exc))

        # Tier 2: FlareSolverr
        if settings.flaresolverr_url:
            try:
                logger.info("duckduckgo.tier2.flaresolverr.start")
                raw_html = self._post_search_flaresolverr(keyword, logger)
                self._check_for_rate_limit(raw_html, logger)
                urls = self._parse_results(raw_html, n, logger)
                if urls:
                    logger.info("duckduckgo.search.done", returned=len(urls), tier="flaresolverr")
                    return urls
            except Exception as exc:
                logger.error("duckduckgo.tier2.failed", error=str(exc))

        raise DuckDuckGoParseError(
            f"DuckDuckGo search failed for keyword: {keyword!r}. All tiers exhausted."
        )

    def _post_search_httpx(self, keyword: str, logger: structlog.BoundLogger) -> str:
        payload = {"q": keyword, "b": "", "kl": "us-en"}
        with httpx.Client(
            headers=_get_headers(),
            timeout=self._timeout,
            follow_redirects=True,
            proxy=self._proxy if self._proxy else None,
        ) as client:
            response = client.post(_DDG_ENDPOINT, data=payload)
            if response.status_code == 202:
                raise DuckDuckGoRateLimitError("DDG 202 Rate Limit")
            response.raise_for_status()
            return response.text

    def _post_search_flaresolverr(self, keyword: str, logger: structlog.BoundLogger) -> str:
        # FlareSolverr lite.duckduckgo.com/lite/ POST
        # Payload for FlareSolverr to perform a POST request
        post_data = urllib.parse.urlencode({"q": keyword, "b": "", "kl": "us-en"})
        payload = {
            "cmd": "request.post",
            "url": _DDG_ENDPOINT,
            "postData": post_data,
            "maxTimeout": 60000,
        }
        with httpx.Client(timeout=70.0) as client:
            response = client.post(
                f"{settings.flaresolverr_url}/v1",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "ok":
                return data["solution"]["response"]
            raise DuckDuckGoFetchError(f"FlareSolverr DDG failed: {data.get('message')}")

    def _check_for_rate_limit(self, html: str, logger: structlog.BoundLogger) -> None:
        lower = html.lower()
        for signal in _RATELIMIT_SIGNALS:
            if signal in lower:
                raise DuckDuckGoRateLimitError(f"DDG Signal: {signal}")

    def _parse_results(self, html: str, n: int, logger: structlog.BoundLogger) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        urls: list[str] = []
        seen: set[str] = set()

        for anchor in soup.select("a.result__url, a.result__a"):
            if len(urls) >= n: break
            href = anchor.get("href", "")
            if not href: continue
            real_url = self._resolve_ddg_redirect(href)
            if not real_url:
                if href.startswith("http") and "duckduckgo.com" not in href:
                    real_url = href
                else: continue
            
            real_url = real_url.strip()
            if self._is_acceptable(real_url, seen):
                seen.add(real_url)
                urls.append(real_url)

        if not urls:
            urls = self._fallback_parse(soup, n, logger)
        return urls

    def _fallback_parse(self, soup: BeautifulSoup, n: int, logger: structlog.BoundLogger) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            if len(urls) >= n: break
            real_url = self._resolve_ddg_redirect(anchor["href"])
            if real_url and self._is_acceptable(real_url, seen):
                seen.add(real_url)
                urls.append(real_url)
        return urls

    @staticmethod
    def _resolve_ddg_redirect(href: str) -> str | None:
        match = _DDG_REDIRECT_RE.search(href)
        if match:
            try: return urllib.parse.unquote(match.group(1))
            except: return None
        if href.startswith("http") and "duckduckgo.com" not in href:
            return href
        return None

    @staticmethod
    def _is_acceptable(url: str, seen: set[str]) -> bool:
        if url in seen or not url.startswith(("http://", "https://")): return False
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower().removeprefix("www.")
            parts = domain.split(".")
            for i in range(len(parts) - 1):
                if ".".join(parts[i:]) in _BLOCKLIST: return False
            return True
        except: return False
