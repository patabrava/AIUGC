import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "service-key")
os.environ.setdefault("SUPABASE_KEY", "service-key")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "account-id")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "access-key")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "secret-key")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "bucket-name")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://cdn.example.com")
os.environ.setdefault("CRON_SECRET", "cron-secret")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.core.errors import FlowForgeException  # noqa: E402
from app.features.qa import handlers as qa_handlers  # noqa: E402
from app.features.qa.schemas import QAApprovalRequest  # noqa: E402
from app.features.qa.schemas import BatchQAStatusResponse  # noqa: E402


class _JsonRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class _HtmxJsonRequest(_JsonRequest):
    headers = {
        "content-type": "application/json",
        "hx-request": "true",
    }


class _ClientNavigationHtmxJsonRequest(_JsonRequest):
    headers = {
        "content-type": "application/json",
        "hx-request": "true",
        "x-delivery-navigation-owner": "client",
    }


def _stub_decision(
    monkeypatch,
    *,
    batch_state="S7_PUBLISH_PLAN",
    batch_id="batch-1",
    qa_auto_checks=None,
):
    calls = []

    async def decision(*, post_id, qa_request, correlation_id):
        calls.append(
            {
                "post_id": post_id,
                "approved": qa_request.approved,
                "notes": qa_request.notes,
                "correlation_id": correlation_id,
            }
        )
        return (
            {"qa_auto_checks": qa_auto_checks},
            batch_id,
            batch_state == "S7_PUBLISH_PLAN",
        )

    monkeypatch.setattr(qa_handlers, "_record_qa_decision", decision)
    return calls


@pytest.mark.asyncio
async def test_delivery_approval_database_work_does_not_starve_event_loop(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def bounded_decision(**_kwargs):
        started.set()
        await release.wait()
        return ({"qa_auto_checks": None}, "batch-1", True)

    monkeypatch.setattr(qa_handlers, "_record_qa_decision", bounded_decision)

    approval = asyncio.create_task(
        qa_handlers.approve_qa(
            "post-final",
            _HtmxJsonRequest({"approved": True}),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=0.1)
    event_loop_probe = asyncio.Event()
    asyncio.get_running_loop().call_soon(event_loop_probe.set)
    await asyncio.wait_for(event_loop_probe.wait(), timeout=0.1)
    release.set()

    response = await approval
    assert response.headers["hx-redirect"] == "/batches/batch-1#publish-workflow"


@pytest.mark.asyncio
async def test_semantic_delivery_approval_redirects_htmx_to_publish(monkeypatch):
    calls = _stub_decision(monkeypatch)

    response = await qa_handlers.approve_qa(
        "post-final",
        _HtmxJsonRequest({"approved": True}),
    )

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == "/batches/batch-1#publish-workflow"
    assert calls == [
        {
            "post_id": "post-final",
            "approved": True,
            "notes": None,
            "correlation_id": "qa_approve_post-final",
        }
    ]


@pytest.mark.asyncio
async def test_semantic_delivery_client_owner_receives_state_without_redirect(
    monkeypatch,
):
    _stub_decision(monkeypatch)

    response = await qa_handlers.approve_qa(
        "post-final",
        _ClientNavigationHtmxJsonRequest({"approved": True}),
    )

    assert not hasattr(response, "headers")
    assert response.data["batch_advanced"] is True


@pytest.mark.asyncio
async def test_semantic_delivery_decision_redirects_to_current_post_when_batch_waits(
    monkeypatch,
):
    _stub_decision(monkeypatch, batch_state="S6_QA")

    response = await qa_handlers.approve_qa(
        "post-first",
        _HtmxJsonRequest({"approved": True}),
    )

    assert response.status_code == 200
    assert response.headers["hx-redirect"] == (
        "/batches/batch-1#semantic-video-post-post-first"
    )


@pytest.mark.asyncio
async def test_non_htmx_delivery_rejection_returns_rpc_result(monkeypatch):
    calls = _stub_decision(monkeypatch)

    response = await qa_handlers.approve_qa(
        "post-rejected",
        _JsonRequest({"approved": False, "notes": "Bad cut"}),
    )

    assert response.data["qa_pass"] is False
    assert response.data["qa_notes"] == "Bad cut"
    assert response.data["qa_auto_checks"] is None
    assert response.data["batch_advanced"] is True
    assert calls[0]["approved"] is False


@pytest.mark.asyncio
async def test_qa_rpc_retries_idempotently_on_a_fresh_transport(monkeypatch):
    settings = SimpleNamespace(
        supabase_url="https://example.supabase.co",
        supabase_service_key="service-key",
    )
    monkeypatch.setattr(qa_handlers, "get_settings", lambda: settings)
    calls = []
    clients = []

    class _Client:
        def __init__(self, **_kwargs):
            self.index = len(clients)
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            calls.append((self.index, url, headers, json))
            if self.index == 0:
                raise httpx.ReadTimeout(
                    "temporary timeout",
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "post_id": "post-1",
                    "batch_id": "batch-1",
                    "batch_state": "S7_PUBLISH_PLAN",
                },
            )

    async def no_delay(_seconds):
        return None

    monkeypatch.setattr(qa_handlers.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(qa_handlers.asyncio, "sleep", no_delay)

    result = await qa_handlers._execute_qa_decision_rpc(
        post_id="post-1",
        qa_request=QAApprovalRequest(approved=True),
        correlation_id="qa-test",
    )

    assert result["batch_state"] == "S7_PUBLISH_PLAN"
    assert len(clients) == 2
    assert [call[0] for call in calls] == [0, 1]
    assert calls[1][1].endswith("/rest/v1/rpc/apply_post_qa_decision")
    assert calls[1][3] == {
        "p_post_id": "post-1",
        "p_approved": True,
        "p_notes": None,
    }


@pytest.mark.asyncio
async def test_qa_decision_maps_atomic_validation_result(monkeypatch):
    async def invalid_gate(**_kwargs):
        return {
            "ok": False,
            "error_code": "validation_error",
            "message": "ActorIdentity video identity gate must pass before QA approval.",
            "batch_id": "batch-1",
        }

    monkeypatch.setattr(qa_handlers, "_execute_qa_decision_rpc", invalid_gate)

    with pytest.raises(FlowForgeException, match="identity gate must pass"):
        await qa_handlers._record_qa_decision(
            post_id="post-actor",
            qa_request=QAApprovalRequest(approved=True),
            correlation_id="qa-test",
        )


def test_batch_qa_status_exposes_persisted_batch_state_for_client_reconciliation():
    payload = BatchQAStatusResponse(
        batch_id="batch-1",
        batch_state="S7_PUBLISH_PLAN",
        total_posts=1,
        posts_with_videos=1,
        posts_qa_passed=1,
        posts_qa_pending=0,
        all_passed=True,
        can_advance_to_publish=True,
    ).model_dump()

    assert payload["batch_state"] == "S7_PUBLISH_PLAN"
