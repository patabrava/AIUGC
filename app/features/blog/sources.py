"""Omit demonstrably invalid blog references without treating outages as proof."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
import ipaddress
import socket
import ssl
import time
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.core.logging import get_logger
from app.features.blog.schemas import normalize_blog_sources

logger = get_logger(__name__)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MISSING_STATUSES = {404, 410}
_PLACEHOLDER_HOSTS = {"example.com", "example.org", "example.net"}
_CHECK_SECONDS = 8.0
_MAX_REDIRECTS = 4


class _InvalidSource(ValueError):
    pass


def _public_destination(url: str) -> tuple[str, str, int, str, str]:
    """Validate each hop and return a pinned public address plus HTTP target."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or port != (443 if parsed.scheme == "https" else 80)
        ):
            raise _InvalidSource("unsafe_url")
        if any(host == item or host.endswith("." + item) for item in _PLACEHOLDER_HOSTS) or host.endswith(
            (".example", ".invalid", ".test")
        ):
            raise _InvalidSource("placeholder")
        if host == "localhost" or host.endswith((".localhost", ".local")):
            raise _InvalidSource("unsafe_url")
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise OSError("No DNS addresses")
        if any(
            not ipaddress.ip_address(item[4][0]).is_global or ipaddress.ip_address(item[4][0]).is_multicast
            for item in addresses
        ):
            raise _InvalidSource("unsafe_url")
        target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        # Connecting to this exact address avoids a second DNS lookup/rebinding.
        return parsed.scheme, host, port, addresses[0][4][0], target
    except ValueError as exc:
        if isinstance(exc, _InvalidSource):
            raise
        raise _InvalidSource("unsafe_url") from exc


def _request_status(url: str, method: str, timeout: float) -> tuple[int, Optional[str]]:
    scheme, host, port, address, target = _public_destination(url)
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.sock = socket.create_connection((address, port), timeout=timeout)
        if scheme == "https":
            # Keep certificate verification and SNI bound to the original hostname.
            connection.sock = ssl.create_default_context().wrap_socket(connection.sock, server_hostname=host)
        connection.request(method, target, headers={"User-Agent": "LippeLift-SourceCheck/1.0"})
        response = connection.getresponse()
        # Inspect headers only; never download an article/PDF body.
        return response.status, response.getheader("Location")
    finally:
        connection.close()


def _invalid_reason(url: str) -> Optional[str]:
    current = url
    method = "HEAD"
    deadline = time.monotonic() + _CHECK_SECONDS
    redirects = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            status, location = _request_status(current, method, remaining)
            if status in _REDIRECT_STATUSES:
                if not location or redirects >= _MAX_REDIRECTS:
                    logger.info("blog_source_check_inconclusive", reason="redirect_limit")
                    return None
                current = urljoin(current, location)
                redirects += 1
                continue
            # A HEAD rejection is not proof: confirm missing pages using GET.
            if method == "HEAD" and status in (_MISSING_STATUSES | {403, 405, 501}):
                method = "GET"
                continue
            if status in _MISSING_STATUSES:
                return "missing"
            if status >= 400:
                logger.info("blog_source_check_inconclusive", http_status=status)
            return None
    except _InvalidSource as exc:
        return str(exc)
    except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
        # DNS, TLS, timeouts and bot blocks do not establish that a source is fake.
        logger.info("blog_source_check_inconclusive", error_class=type(exc).__name__)
        return None


def filter_blog_sources(raw_sources: Any) -> list[dict[str, str]]:
    """Keep named citations, omitting placeholders/unsafe URLs and confirmed 404/410s.

    URL-less citations remain valid (e.g. printed references). This checks page
    availability only, not whether the source substantiates the article's claims.
    """
    sources = normalize_blog_sources(raw_sources)
    urls = list(dict.fromkeys(source["url"] for source in sources if source["url"]))
    if not urls:
        return sources
    with ThreadPoolExecutor(max_workers=4) as pool:
        reasons = dict(zip(urls, pool.map(_invalid_reason, urls)))
    retained = []
    for source in sources:
        reason = reasons.get(source["url"])
        if reason:
            logger.info("blog_source_omitted", title=source["title"], reason=reason)
        else:
            retained.append(source)
    if len(retained) != len(sources):
        logger.info("blog_sources_filtered", source_count=len(sources), omitted_count=len(sources) - len(retained))
    return retained
