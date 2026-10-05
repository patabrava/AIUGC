"""Shared video-provider errors, task envelopes and bounded HTTPS output transport."""
from __future__ import annotations
from dataclasses import dataclass
import ipaddress
import time
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urljoin, urlparse
import httpx
from app.core.logging import get_logger
logger = get_logger(__name__)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_OUTPUT_REDIRECTS = 3
class VideoProviderError(Exception):
    """Provider failure with an explicit task-existence classification."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        status_code: Optional[int] = None,
        retry_after_seconds: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}

    @property
    def proves_no_task(self) -> bool:
        return self.kind in {"not_submitted", "rejected", "rate_limited", "configuration"}


@dataclass(frozen=True)
class VideoTask:
    task_id: str
    status: str
    progress: Optional[float]
    output_urls: Tuple[str, ...]
    failure: Optional[str]
    failure_code: Optional[str]
    estimated_credits: Optional[float]
    cost_credits: Optional[int]
    usage_tokens: Optional[int] = None


@dataclass(frozen=True)
class VideoOutput:
    content: bytes
    content_type: str
    host: str
    redirects: int


def parse_allowed_hosts(value: Any) -> Tuple[str, ...]:
    """Parse comma-separated exact hosts or ``*.suffix`` patterns."""
    entries = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    hosts = []
    for entry in entries:
        normalized = str(entry or "").strip().lower().rstrip(".")
        if normalized:
            hosts.append(normalized)
    return tuple(hosts)


def host_is_allowed(host: str, allowed_hosts: Iterable[str]) -> bool:
    normalized = str(host or "").strip().lower().rstrip(".")
    if not normalized:
        return False
    for pattern in allowed_hosts:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                return True
        elif normalized == pattern:
            return True
    return False


def safe_url_label(url: str) -> str:
    """Return host plus path without query, fragment, or credentials for logs."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return "<unparseable>"
    return f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path[:120]}"


def validate_output_url(url: str, allowed_hosts: Iterable[str]) -> str:
    """Accept only HTTPS URLs on an allowlisted host; return the normalized host."""
    try:
        parsed = urlparse(str(url or ""))
        port = parsed.port
    except ValueError as exc:
        raise VideoProviderError("Video provider output URL is malformed.", kind="unsafe_output") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise VideoProviderError("Video provider output URL must use HTTPS.", kind="unsafe_output", details={"host": host})
    if parsed.username or parsed.password:
        raise VideoProviderError("Video provider output URL must not carry credentials.", kind="unsafe_output", details={"host": host})
    if port not in (None, 443):
        raise VideoProviderError("Video provider output URL uses an unexpected port.", kind="unsafe_output", details={"host": host})
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise VideoProviderError("Video provider output URL must not use an IP literal.", kind="unsafe_output", details={"host": host})
    if not host_is_allowed(host, allowed_hosts):
        raise VideoProviderError(
            "Video provider output host is not on the configured allowlist.",
            kind="unsafe_output",
            details={"host": host},
        )
    return host


class SafeVideoOutputDownloader:
    def download_output(self, url: str, *, correlation_id: str) -> VideoOutput:
        """Download one task output with host allowlisting, redirect revalidation, and caps."""
        deadline = time.monotonic() + self._download_timeout_seconds
        current = str(url or "")
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        owned_client = self._http_client is None
        client = httpx.Client(timeout=timeout, follow_redirects=False) if owned_client else self._http_client
        try:
            for redirects in range(_MAX_OUTPUT_REDIRECTS + 1):
                host = validate_output_url(current, self._output_allowed_hosts)
                with client.stream("GET", current, timeout=timeout, follow_redirects=False) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise VideoProviderError("Video provider output redirect has no location.", kind="unsafe_output", details={"host": host})
                        current = urljoin(current, location)
                        continue
                    if response.status_code in {401, 403, 404, 410}:
                        raise VideoProviderError(
                            "Video provider output URL expired or is unavailable.",
                            kind="output_expired",
                            status_code=response.status_code,
                            details={"host": host},
                        )
                    if response.status_code >= 400:
                        raise VideoProviderError(
                            f"Video provider output download failed with HTTP {response.status_code}.",
                            kind="transport",
                            status_code=response.status_code,
                            details={"host": host},
                        )
                    content_type = str(response.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if content_type not in {"video/mp4", "application/octet-stream", "binary/octet-stream"}:
                        raise VideoProviderError(
                            "Video provider output has an unexpected content type.",
                            kind="invalid_media",
                            details={"host": host, "content_type": content_type[:80]},
                        )
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > self._output_max_bytes:
                        raise VideoProviderError("Video provider output exceeds the byte cap.", kind="invalid_media", details={"host": host, "declared_bytes": int(declared)})
                    chunks = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self._output_max_bytes:
                            raise VideoProviderError("Video provider output exceeds the byte cap.", kind="invalid_media", details={"host": host})
                        if time.monotonic() > deadline:
                            raise VideoProviderError("Video provider output download exceeded its deadline.", kind="transport", details={"host": host})
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if not content:
                        raise VideoProviderError("Video provider output body is empty.", kind="invalid_media", details={"host": host})
                    logger.info(
                        "video_provider_output_downloaded",
                        correlation_id=correlation_id,
                        source=safe_url_label(current),
                        size_bytes=len(content),
                        redirects=redirects,
                    )
                    return VideoOutput(content=content, content_type=content_type, host=host, redirects=redirects)
            raise VideoProviderError("Video provider output exceeded the redirect limit.", kind="unsafe_output")
        except httpx.HTTPError as exc:
            raise VideoProviderError("Video provider output download failed in transport.", kind="transport", details={"error_type": type(exc).__name__}) from exc
        finally:
            if owned_client:
                client.close()

