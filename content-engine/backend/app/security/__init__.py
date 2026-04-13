"""
app/security.py — URL validation (SSRF protection) and HTML sanitization (XSS).

Called by:
  - Scraper for Workflow 2 to validate user-supplied URLs
  - Worker pipeline after AI generation to sanitize output before DB storage
"""
import ipaddress
import socket
from urllib.parse import urlparse

import bleach
from bleach.css_sanitizer import CSSSanitizer

from app.config import settings
from app.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SSRF Protection
# ─────────────────────────────────────────────────────────────────────────────

class SSRFViolationError(ValueError):
    """Raised when a URL resolves to a blocked/private network range."""


def _build_blocked_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    for cidr in settings.ssrf_blocked_ranges_list:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            log.warning("security.invalid_cidr", cidr=cidr)
    return networks


_BLOCKED_NETWORKS = _build_blocked_networks()


def validate_url_for_ssrf(url: str) -> str:
    """
    Validate that `url` is safe to fetch:
      1. Must use http or https scheme.
      2. Must not point to a private/loopback/link-local IP range.

    Raises SSRFViolationError on failure, returns the original URL on success.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise SSRFViolationError(
            f"URL scheme '{parsed.scheme}' is not allowed. Use http or https."
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFViolationError("URL has no hostname.")

    # Resolve the hostname to its IP address(es)
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFViolationError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    for addr_info in addr_infos:
        raw_ip = addr_info[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue

        for network in _BLOCKED_NETWORKS:
            if ip in network:
                log.warning(
                    "security.ssrf_blocked",
                    url=url,
                    hostname=hostname,
                    resolved_ip=str(ip),
                    blocked_network=str(network),
                )
                raise SSRFViolationError(
                    f"URL resolves to a blocked network range: {network}"
                )

    log.debug("security.url_validated", url=url)
    return url


# ─────────────────────────────────────────────────────────────────────────────
# HTML Sanitization (XSS prevention for AI-generated content)
# ─────────────────────────────────────────────────────────────────────────────

# Tags allowed in the final article HTML
_ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "strong", "em", "b", "i", "u", "s", "del", "ins",
    "blockquote", "pre", "code",
    "a",
    "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "div", "span", "figure", "figcaption",
]

# Attributes allowed per tag
_ALLOWED_ATTRS: dict[str, list[str]] = {
    "*": ["class", "id"],           # global class/id for styling
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height", "loading"],
    "th": ["scope"],
    "td": ["colspan", "rowspan"],
    "blockquote": ["cite"],
}

# CSS properties allowed inside style attributes (none by default — add if needed)
_CSS_SANITIZER = CSSSanitizer(allowed_css_properties=[])


def sanitize_html(raw_html: str) -> str:
    """
    Strip any tags / attributes not in the allowlist.
    Prevents AI-generated content from injecting <script>, event handlers, etc.
    """
    clean = bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        css_sanitizer=_CSS_SANITIZER,
        strip=True,          # remove disallowed tags entirely (don't escape)
        strip_comments=True,
    )
    log.debug("security.html_sanitized", original_len=len(raw_html), clean_len=len(clean))
    return clean
