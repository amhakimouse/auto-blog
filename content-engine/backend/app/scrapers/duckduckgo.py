"""
app/scrapers/duckduckgo.py — DuckDuckGo organic search result scraper.

Workflow 1 (Keyword → Article) discovery step.

Strategy:
  DuckDuckGo serves a pure-HTML search page at https://html.duckduckgo.com/html/
  with no JavaScript, no captchas, and a simple POST-based interface — making it
  the most reliable way to extract organic result URLs without an API key.

Flow:
  1. POST the keyword to DuckDuckGo's HTML endpoint.
  2. Parse the response HTML with BeautifulSoup to extract result anchors.
  3. Resolve and deduplicate redirect URLs → return the top-n real URLs.

Security:
  This scraper intentionally hits only the DuckDuckGo domain. SSRF validation
  in app/security.py covers URLs returned by this class before they are fetched
  as article content by article_scraper.py.

Rate-limit handling:
  DuckDuckGo returns HTTP 202 or a CAPTCHA page when overloaded.
  Both cases raise DuckDuckGoRateLimitError so Celery can retry with backoff.
"""
from __future__ import annotations

import re
import time
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

_DDG_ENDPOINT: Final[str] = "https://html.duckduckgo.com/html/"

# Mimic a real browser to avoid bot-detection heuristics.
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://html.duckduckgo.com",
    "Referer": "https://html.duckduckgo.com/",
    "DNT": "1",
}

# DDG wraps every result URL in a redirect proxy. Strip it to get the real URL.
# Pattern: /l/?uddg=<URL-encoded-real-url>&...
_DDG_REDIRECT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:uddg|u)=([^&]+)", re.IGNORECASE
)

# Domains to skip — low-quality, login-walled, or structurally unhelpful.
_BLOCKLIST: Final[frozenset[str]] = frozenset({
    "youtube.com", "youtu.be",
    "reddit.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "pinterest.com",
    "amazon.com", "ebay.com",
    "duckduckgo.com", "bing.com", "google.com",
})

# Phrases in the page body that confirm a rate-limit / CAPTCHA page.
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
    Synchronous DuckDuckGo HTML search scraper.

    Designed to run inside a Celery worker (sync context). Uses `httpx` in
    synchronous mode — no async event loop required.

    Usage::

        scraper = DuckDuckGoScraper()
        urls = scraper.search("best python async frameworks", n=5)
    """

    def __init__(
        self,
        timeout_seconds: float | None = None,
        polite_delay_ms: int = 500,
    ) -> None:
        """
        Args:
            timeout_seconds: HTTP timeout. Defaults to settings.scrape_timeout_seconds.
            polite_delay_ms: Milliseconds to wait before firing the request (politeness
                             delay). Avoids hammering DDG during retry storms.
        """
        self._timeout = httpx.Timeout(
            connect=10.0,
            read=float(timeout_seconds or settings.scrape_timeout_seconds),
            write=10.0,
            pool=5.0,
        )
        self._polite_delay_s: float = polite_delay_ms / 1000.0

    # ── Public method ─────────────────────────────────────────────────────────

    def search(self, keyword: str, n: int = 5) -> list[str]:
        """
        Query DuckDuckGo for *keyword* and return the top *n* organic URLs.

        Args:
            keyword: Search query string (e.g. ``"best python web frameworks 2024"``).
            n: Maximum number of result URLs to return (capped at 10).

        Returns:
            A list of up to *n* fully-qualified HTTP/S URLs from organic
            results, with DDG redirect wrappers stripped and blocklisted
            domains removed.

        Raises:
            DuckDuckGoRateLimitError: DDG detected bot activity — caller should retry.
            DuckDuckGoFetchError: Network or HTTP error unrelated to rate limiting.
            DuckDuckGoParseError: No usable URLs found in the response.
        """
        n = min(n, 10)
        logger = log.bind(keyword=keyword, n=n)
        logger.info("duckduckgo.search.start")

        if self._polite_delay_s > 0:
            time.sleep(self._polite_delay_s)

        raw_html = self._post_search(keyword, logger)
        self._check_for_rate_limit(raw_html, logger)
        urls = self._parse_results(raw_html, n, logger)

        logger.info("duckduckgo.search.done", returned=len(urls), urls=urls)
        return urls

    # ── Private: HTTP layer ───────────────────────────────────────────────────

    def _post_search(self, keyword: str, logger: structlog.BoundLogger) -> str:
        """POST to DDG HTML endpoint and return raw response HTML."""
        payload = {
            "q": keyword,
            "b": "",          # start at page 1
            "kl": "us-en",    # locale
        }

        try:
            with httpx.Client(
                headers=_HEADERS,
                timeout=self._timeout,
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                response = client.post(_DDG_ENDPOINT, data=payload)

            # DDG returns 202 Accepted when rate-limiting — treat as error.
            if response.status_code == 202:
                raise DuckDuckGoRateLimitError(
                    f"DuckDuckGo returned HTTP 202 (rate limit) for keyword: {keyword!r}"
                )

            response.raise_for_status()
            logger.debug(
                "duckduckgo.fetch.success",
                status=response.status_code,
                bytes=len(response.content),
            )
            return response.text

        except DuckDuckGoRateLimitError:
            raise  # re-raise; already correct exception type

        except httpx.HTTPStatusError as exc:
            msg = (
                f"HTTP {exc.response.status_code} from DuckDuckGo "
                f"for keyword {keyword!r}: {exc.response.reason_phrase}"
            )
            logger.warning("duckduckgo.fetch.http_error", status=exc.response.status_code)
            raise DuckDuckGoFetchError(msg) from exc

        except httpx.TimeoutException as exc:
            msg = f"DuckDuckGo request timed out for keyword: {keyword!r}"
            logger.warning("duckduckgo.fetch.timeout")
            raise DuckDuckGoFetchError(msg) from exc

        except httpx.RequestError as exc:
            msg = f"Network error reaching DuckDuckGo: {type(exc).__name__}: {exc}"
            logger.warning("duckduckgo.fetch.network_error", exc_type=type(exc).__name__)
            raise DuckDuckGoFetchError(msg) from exc

    # ── Private: rate-limit detection ─────────────────────────────────────────

    def _check_for_rate_limit(
        self, html: str, logger: structlog.BoundLogger
    ) -> None:
        """Scan the response body for CAPTCHA / rate-limit signals."""
        lower = html.lower()
        for signal in _RATELIMIT_SIGNALS:
            if signal in lower:
                logger.warning("duckduckgo.rate_limit_detected", signal=signal)
                raise DuckDuckGoRateLimitError(
                    f"DuckDuckGo rate-limit page detected (signal: {signal!r})"
                )

    # ── Private: HTML parsing ─────────────────────────────────────────────────

    def _parse_results(
        self, html: str, n: int, logger: structlog.BoundLogger
    ) -> list[str]:
        """
        Extract up to *n* organic result URLs from DDG's HTML result page.

        DDG's HTML layout (as of 2024):
          <div class="result__body">
            <a class="result__url" href="/l/?uddg=<encoded-real-url>&...">
        """
        soup = BeautifulSoup(html, "lxml")
        urls: list[str] = []
        seen: set[str] = set()

        # Primary selector: result URL anchors
        for anchor in soup.select("a.result__url, a.result__a"):
            if len(urls) >= n:
                break

            href: str | None = anchor.get("href", "")  # type: ignore[assignment]
            if not href:
                continue

            real_url = self._resolve_ddg_redirect(href)
            if real_url is None:
                # Last resort: check if the href itself is a direct URL
                if href.startswith("http") and "duckduckgo.com" not in href:
                    real_url = href
                else:
                    continue

            real_url = real_url.strip()
            if not self._is_acceptable(real_url, seen):
                continue

            seen.add(real_url)
            urls.append(real_url)
            logger.debug("duckduckgo.result_found", url=real_url)

        if not urls:
            # Fallback: try generic anchor tags that look like result links
            urls = self._fallback_parse(soup, n, logger)

        if not urls:
            raise DuckDuckGoParseError(
                "DuckDuckGo returned no parseable organic result URLs. "
                "The page layout may have changed or the query returned no results."
            )

        return urls

    def _fallback_parse(
        self,
        soup: BeautifulSoup,
        n: int,
        logger: structlog.BoundLogger,
    ) -> list[str]:
        """
        Emergency fallback: scan ALL anchors for DDG redirect patterns.
        Used when the primary CSS selectors produce nothing (layout changes).
        """
        logger.warning("duckduckgo.parse.fallback_mode")
        urls: list[str] = []
        seen: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            if len(urls) >= n:
                break
            href = anchor["href"]
            real_url = self._resolve_ddg_redirect(href)
            if real_url and self._is_acceptable(real_url, seen):
                seen.add(real_url)
                urls.append(real_url)

        return urls

    # ── Private: URL utilities ────────────────────────────────────────────────

    @staticmethod
    def _resolve_ddg_redirect(href: str) -> str | None:
        """
        Strip DDG's redirect proxy to get the real destination URL.

        DDG encodes result URLs as:
          /l/?uddg=https%3A%2F%2Fexample.com%2Farticle&...

        Returns None if the href doesn't contain a recognisable redirect pattern.
        """
        if not href:
            return None

        # Check for the uddg / u parameter in the query string
        match = _DDG_REDIRECT_RE.search(href)
        if match:
            try:
                return urllib.parse.unquote(match.group(1))
            except Exception:
                return None

        # Some DDG result anchors are fully-formed URLs already
        if href.startswith("http") and "duckduckgo.com" not in href:
            return href

        return None

    @staticmethod
    def _is_acceptable(url: str, seen: set[str]) -> bool:
        """
        Return True if *url* should be included in search results.

        Filters:
          - Must be http:// or https://
          - Must not already be in *seen* (deduplication)
          - Domain must not be in the blocklist
        """
        if url in seen:
            return False
        if not url.startswith(("http://", "https://")):
            return False
        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.lower().removeprefix("www.")
            # Check domain and its parent (e.g. "blog.youtube.com" → "youtube.com")
            parts = domain.split(".")
            for i in range(len(parts) - 1):
                if ".".join(parts[i:]) in _BLOCKLIST:
                    return False
        except Exception:
            return False
        return True
