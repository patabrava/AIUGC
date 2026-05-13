"""Resolve Vertex AI grounding redirect URLs to their final destination.

Vertex Gemini emits links of the form
``https://vertexaisearch.cloud.google.com/grounding-api-redirect/<token>``
inside grounded research responses. These redirects expire (~30 days), so we
must resolve them to the real source URL at generation time and persist the
resolved value instead.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, Optional
from urllib.parse import urlsplit

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

GROUNDING_REDIRECT_HOST = "vertexaisearch.cloud.google.com"
_RESOLVE_TIMEOUT_SECONDS = 8.0
_RESOLVE_MAX_WORKERS = 8


def is_grounding_redirect_url(url: Optional[str]) -> bool:
    """Return True if *url* is a Vertex grounding redirect URL."""
    if not url or not isinstance(url, str):
        return False
    try:
        host = urlsplit(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return False
    return host == GROUNDING_REDIRECT_HOST


def _resolve_one(url: str, http_client: httpx.Client) -> Optional[str]:
    """Follow *url* and return the final destination, or None on failure."""
    try:
        response = http_client.get(url)
    except httpx.HTTPError as exc:
        logger.warning(
            "grounding_redirect_resolve_failed",
            url=url[:120],
            error_class=type(exc).__name__,
            error=str(exc)[:200],
        )
        return None
    final_url = str(response.url)
    if response.status_code >= 400 or is_grounding_redirect_url(final_url):
        logger.warning(
            "grounding_redirect_resolve_unresolved",
            url=url[:120],
            status_code=response.status_code,
            final_url=final_url[:200],
        )
        return None
    return final_url


def resolve_grounding_urls(
    urls: Iterable[str],
    *,
    http_client: Optional[httpx.Client] = None,
) -> Dict[str, str]:
    """Resolve every grounding redirect URL in *urls* to its final destination.

    Non-redirect URLs are skipped. Failures are logged and omitted from the
    returned mapping; callers should treat absence as "could not resolve".
    """
    unique_redirects = []
    seen: set[str] = set()
    for url in urls:
        if not is_grounding_redirect_url(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        unique_redirects.append(url)

    if not unique_redirects:
        return {}

    owns_client = http_client is None
    if http_client is None:
        http_client = httpx.Client(
            follow_redirects=True,
            timeout=_RESOLVE_TIMEOUT_SECONDS,
            headers={"User-Agent": "LippeLift-GroundingResolver/1.0"},
        )

    resolved: Dict[str, str] = {}
    try:
        worker_count = min(_RESOLVE_MAX_WORKERS, len(unique_redirects))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(_resolve_one, url, http_client): url
                for url in unique_redirects
            }
            for future in as_completed(futures):
                url = futures[future]
                final = future.result()
                if final:
                    resolved[url] = final
    finally:
        if owns_client:
            http_client.close()

    return resolved
