"""Runway REST adapter: request shape, task-existence classification, and safe output download."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.adapters.runway_client import (
    DATA_URI_MAX_CHARS,
    RUNWAY_API_VERSION,
    RunwayClient,
    RunwayError,
    build_image_data_uri,
    get_runway_client,
    host_is_allowed,
    validate_output_url,
)

API_KEY = "rw-test-key-do-not-log-0123456789"
TASK_ID = "task_0123456789abcdef"
IMAGE = build_image_data_uri(b"\x89PNG\r\n\x1a\nfake", "image/png")


def _client(handler, **kwargs) -> RunwayClient:
    return RunwayClient(
        api_key=API_KEY,
        base_url="https://api.dev.runwayml.com",
        output_allowed_hosts=kwargs.pop("hosts", ("*.runway-output.test",)),
        output_max_bytes=kwargs.pop("max_bytes", 1024),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _submit(client: RunwayClient, **overrides):
    params = {
        "prompt_image": IMAGE,
        "prompt_text": "Eine Frau erklärt ruhig den Treppenlift.",
        "ratio": "720:1280",
        "duration_seconds": 8,
        "audio": True,
        "seed": 42,
        "correlation_id": "test",
    }
    params.update(overrides)
    return client.submit_image_to_video(**params)


def test_submit_sends_exact_seedance_contract_and_hides_key():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"id": TASK_ID, "estimatedCost": {"credits": 240}})

    client = _client(handler)
    result = _submit(client)

    request = captured["request"]
    assert request.method == "POST"
    assert request.url.path == "/v1/image_to_video"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["X-Runway-Version"] == RUNWAY_API_VERSION
    body = json.loads(request.content)
    assert body == {
        "model": "seedance2_5",
        "promptImage": IMAGE,
        "promptText": "Eine Frau erklärt ruhig den Treppenlift.",
        "ratio": "720:1280",
        "duration": 8,
        "audio": True,
        "seed": 42,
    }
    assert result == {"task_id": TASK_ID, "estimated_credits": 240.0}
    assert API_KEY not in repr(client)


def test_submit_omits_seed_when_absent():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id": TASK_ID})

    _submit(_client(handler), seed=None)
    assert "seed" not in bodies[0]


@pytest.mark.parametrize(
    ("exception", "kind", "proves_no_task"),
    [
        (httpx.ConnectError("refused"), "not_submitted", True),
        (httpx.ConnectTimeout("connect timeout"), "not_submitted", True),
        (httpx.ReadTimeout("read timeout"), "ambiguous", False),
        (httpx.RemoteProtocolError("closed"), "ambiguous", False),
    ],
)
def test_submit_transport_failures_classify_task_existence(exception, kind, proves_no_task):
    def handler(request):
        raise exception

    with pytest.raises(RunwayError) as caught:
        _submit(_client(handler))
    assert caught.value.kind == kind
    assert caught.value.proves_no_task is proves_no_task


@pytest.mark.parametrize(
    ("status_code", "kind"),
    [(400, "rejected"), (401, "rejected"), (413, "rejected"), (422, "rejected"), (500, "ambiguous"), (502, "ambiguous"), (408, "ambiguous"), (409, "ambiguous")],
)
def test_submit_http_statuses_classify_task_existence(status_code, kind):
    def handler(request):
        return httpx.Response(status_code, json={"error": "nope"})

    with pytest.raises(RunwayError) as caught:
        _submit(_client(handler))
    assert caught.value.kind == kind
    assert caught.value.status_code == status_code


def test_submit_rate_limit_reads_retry_after_ms():
    def handler(request):
        return httpx.Response(429, headers={"retry-after-ms": "12500"}, json={"error": "slow down"})

    with pytest.raises(RunwayError) as caught:
        _submit(_client(handler))
    assert caught.value.kind == "rate_limited"
    assert caught.value.proves_no_task is True
    assert caught.value.retry_after_seconds == pytest.approx(12.5)


def test_rejected_details_drop_echoed_values():
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": "Validation failed",
                "issues": [{"code": "too_big", "path": ["promptText"], "message": "x", "received": "SECRET PROMPT"}],
            },
        )

    with pytest.raises(RunwayError) as caught:
        _submit(_client(handler))
    assert caught.value.details == {"error": "Validation failed", "issues": [{"code": "too_big", "path": ["promptText"]}]}
    assert "SECRET PROMPT" not in json.dumps(caught.value.details)


@pytest.mark.parametrize("payload", [{"id": "short"}, {"estimatedCost": {"credits": 1}}, ["not", "a", "dict"]])
def test_accepted_response_without_valid_task_id_is_ambiguous(payload):
    def handler(request):
        return httpx.Response(200, json=payload)

    with pytest.raises(RunwayError) as caught:
        _submit(_client(handler))
    assert caught.value.kind == "ambiguous"


def test_unreadable_accepted_body_is_ambiguous():
    def handler(request):
        return httpx.Response(200, content=b"<html>")

    with pytest.raises(RunwayError) as caught:
        _submit(_client(handler))
    assert caught.value.kind == "ambiguous"


@pytest.mark.parametrize(
    "overrides",
    [
        {"ratio": "1280:720"},
        {"duration_seconds": 3},
        {"duration_seconds": 31},
        {"prompt_text": ""},
        {"prompt_text": "x" * 15001},
        {"prompt_image": "http://insecure.test/image.png"},
        {"prompt_image": "data:image/png;base64," + "A" * DATA_URI_MAX_CHARS},
        {"seed": -1},
    ],
)
def test_local_validation_rejects_before_any_request(overrides):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": TASK_ID})

    with pytest.raises(RunwayError) as caught:
        _submit(_client(handler), **overrides)
    assert caught.value.kind == "rejected"
    assert caught.value.proves_no_task is True
    assert calls == []


def test_get_task_parses_success_output_and_cost():
    def handler(request):
        assert request.url.path == f"/v1/tasks/{TASK_ID}"
        return httpx.Response(
            200,
            json={"id": TASK_ID, "status": "SUCCEEDED", "output": ["https://cdn.runway-output.test/a.mp4"], "cost": {"credits": 240}},
        )

    task = _client(handler).get_task(TASK_ID, correlation_id="test")
    assert task.status == "SUCCEEDED"
    assert task.output_urls == ("https://cdn.runway-output.test/a.mp4",)
    assert task.cost_credits == 240


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(404), "not_found"),
        (httpx.Response(429, headers={"retry-after": "7"}), "rate_limited"),
        (httpx.Response(503), "transport"),
        (httpx.Response(200, json={"id": "task_other_123456", "status": "RUNNING"}), "invalid_response"),
        (httpx.Response(200, content=b"nope"), "invalid_response"),
    ],
)
def test_get_task_errors_are_classified(response, kind):
    with pytest.raises(RunwayError) as caught:
        _client(lambda request: response).get_task(TASK_ID, correlation_id="test")
    assert caught.value.kind == kind


def test_cancel_treats_404_as_gone_and_5xx_as_unconfirmed():
    assert _client(lambda request: httpx.Response(204)).cancel_task(TASK_ID, correlation_id="t") is True
    assert _client(lambda request: httpx.Response(404)).cancel_task(TASK_ID, correlation_id="t") is True
    assert _client(lambda request: httpx.Response(500)).cancel_task(TASK_ID, correlation_id="t") is False
    assert _client(lambda request: httpx.Response(204)).cancel_task("../bad", correlation_id="t") is False


def test_organization_probe_reports_model_access():
    def handler(request):
        assert request.method == "GET" and request.url.path == "/v1/organization"
        return httpx.Response(
            200,
            json={
                "creditBalance": 5000,
                "tier": {"maxMonthlyCreditSpend": 100000, "models": {"seedance2_5": {"maxConcurrentGenerations": 2, "maxDailyGenerations": 50}}},
                "usage": {"models": {"seedance2_5": {"dailyGenerations": 3}}},
            },
        )

    result = _client(handler).get_organization(correlation_id="t")
    assert result["seedance2_5_available"] is True
    assert result["credit_balance"] == 5000
    assert result["seedance2_5_max_concurrent_generations"] == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.runway-output.test/a.mp4",
        "https://evil.test/a.mp4",
        "https://user:pass@cdn.runway-output.test/a.mp4",
        "https://cdn.runway-output.test:8443/a.mp4",
        "https://127.0.0.1/a.mp4",
        "https://runway-output.test.evil.test/a.mp4",
    ],
)
def test_output_url_validation_rejects_unsafe_targets(url):
    with pytest.raises(RunwayError) as caught:
        validate_output_url(url, ("*.runway-output.test",))
    assert caught.value.kind == "unsafe_output"


def test_host_allowlist_matches_exact_and_subdomain_only():
    assert host_is_allowed("cdn.runway-output.test", ("*.runway-output.test",))
    assert not host_is_allowed("runway-output.test", ("*.runway-output.test",))
    assert host_is_allowed("files.example.test", ("files.example.test",))
    assert not host_is_allowed("xfiles.example.test", ("files.example.test",))


def test_download_follows_redirect_only_to_allowlisted_hosts():
    def handler(request):
        if request.url.host == "a.runway-output.test":
            return httpx.Response(302, headers={"location": "https://b.runway-output.test/final.mp4"})
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"\x00\x00\x00\x18ftypmp42")

    output = _client(handler).download_output("https://a.runway-output.test/x.mp4", correlation_id="t")
    assert output.host == "b.runway-output.test"
    assert output.redirects == 1
    assert output.content.startswith(b"\x00\x00\x00\x18ftyp")


def test_download_rejects_redirect_to_unlisted_host():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://metadata.internal.test/latest"})

    with pytest.raises(RunwayError) as caught:
        _client(handler).download_output("https://a.runway-output.test/x.mp4", correlation_id="t")
    assert caught.value.kind == "unsafe_output"
    assert calls == ["a.runway-output.test"]


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(403), "output_expired"),
        (httpx.Response(410), "output_expired"),
        (httpx.Response(500), "transport"),
        (httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"), "invalid_media"),
        (httpx.Response(200, headers={"content-type": "video/mp4", "content-length": "4096"}, content=b"x" * 4096), "invalid_media"),
        (httpx.Response(200, headers={"content-type": "video/mp4"}, content=b""), "invalid_media"),
    ],
)
def test_download_failures_are_classified(response, kind):
    with pytest.raises(RunwayError) as caught:
        _client(lambda request: response).download_output("https://a.runway-output.test/x.mp4", correlation_id="t")
    assert caught.value.kind == kind


def test_download_enforces_streamed_byte_cap_without_content_length():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=iter([b"x" * 600, b"x" * 600]))

    with pytest.raises(RunwayError) as caught:
        _client(handler).download_output("https://a.runway-output.test/x.mp4", correlation_id="t")
    assert caught.value.kind == "invalid_media"


def test_factory_requires_key_and_https_base_url():
    with pytest.raises(RunwayError) as missing:
        get_runway_client(SimpleNamespace(runway_api_key=""))
    assert missing.value.kind == "configuration"
    with pytest.raises(RunwayError) as insecure:
        get_runway_client(SimpleNamespace(runway_api_key=API_KEY, runway_api_base_url="http://api.dev.runwayml.com"))
    assert insecure.value.kind == "configuration"


def test_data_uri_accepts_only_png_and_jpeg():
    assert build_image_data_uri(b"abc", "image/jpeg").startswith("data:image/jpeg;base64,")
    with pytest.raises(RunwayError):
        build_image_data_uri(b"abc", "image/webp")


def test_sheet_reference_route_sends_one_unpositioned_image_only():
    captured = {}
    def handler(request):
        captured['request'] = request
        return httpx.Response(200, json={'id': TASK_ID, 'estimatedCost': {'credits': 160}})
    result = _client(handler).submit_reference_to_video(
        reference_image=IMAGE, prompt_text='@Image 1 defines the seated actor.',
        ratio='480:854', duration_seconds=8, audio=True, seed=42, correlation_id='sheet-test',
    )
    request = captured['request']; body = json.loads(request.content)
    assert request.url.path == '/v1/text_to_video'
    assert body['model'] == 'seedance2_5'
    assert body['references'] == [{'uri': IMAGE}]
    assert 'promptImage' not in body
    assert 'referenceVideos' not in body and 'referenceAudio' not in body
    assert body['duration'] == 8 and body['ratio'] == '480:854'
    assert result['estimated_credits'] == 160
