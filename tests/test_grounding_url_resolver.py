"""Tests for the Vertex grounding redirect resolver."""

from __future__ import annotations

from typing import List

import httpx
import pytest

from app.adapters.grounding_url_resolver import (
    GROUNDING_REDIRECT_HOST,
    is_grounding_redirect_url,
    resolve_grounding_urls,
)


REDIRECT_URL_A = (
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
    "AUZIYQHpa_Rxstd5Ddw6ihYVw3oIRyRRPf9u9KgSTGpQ"
)
REDIRECT_URL_B = (
    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
    "ZYXWVUTSRQPONML"
)


def test_grounding_redirect_host_constant():
    assert GROUNDING_REDIRECT_HOST == "vertexaisearch.cloud.google.com"


@pytest.mark.parametrize(
    "url, expected",
    [
        (REDIRECT_URL_A, True),
        ("https://www.tagesschau.de/article", False),
        ("https://VertexAISearch.Cloud.Google.com/foo", True),
        ("", False),
        (None, False),
    ],
)
def test_is_grounding_redirect_url(url, expected):
    assert is_grounding_redirect_url(url) is expected


def test_resolve_grounding_urls_returns_final_destination():
    # Map redirect URL -> final destination. We return a real 302 so httpx's
    # follow_redirects can update response.url to the destination.
    final_for = {
        REDIRECT_URL_A: "https://www.tagesschau.de/article",
        REDIRECT_URL_B: "https://www.bmas.de/seite",
    }

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in final_for:
            return httpx.Response(302, headers={"Location": final_for[url]})
        return httpx.Response(200)

    client = httpx.Client(
        transport=httpx.MockTransport(redirect_handler),
        follow_redirects=True,
        timeout=5.0,
    )

    resolved = resolve_grounding_urls(
        [REDIRECT_URL_A, REDIRECT_URL_B, "https://already-real.example/x"],
        http_client=client,
    )

    assert resolved[REDIRECT_URL_A] == "https://www.tagesschau.de/article"
    assert resolved[REDIRECT_URL_B] == "https://www.bmas.de/seite"
    assert "https://already-real.example/x" not in resolved  # only redirects


def test_resolve_grounding_urls_skips_unresolvable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        timeout=5.0,
    )

    resolved = resolve_grounding_urls([REDIRECT_URL_A], http_client=client)
    assert REDIRECT_URL_A not in resolved


def test_resolve_grounding_urls_swallows_transport_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        timeout=5.0,
    )

    resolved = resolve_grounding_urls([REDIRECT_URL_A], http_client=client)
    assert resolved == {}


def test_resolve_grounding_urls_deduplicates_input():
    calls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            request=httpx.Request("GET", "https://final.example/"),
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        timeout=5.0,
    )

    resolve_grounding_urls(
        [REDIRECT_URL_A, REDIRECT_URL_A, REDIRECT_URL_A],
        http_client=client,
    )
    assert len(calls) == 1
