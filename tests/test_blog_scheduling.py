from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

import httpx
from fastapi.testclient import TestClient

from app.core.errors import ErrorCode, FlowForgeException
from app.main import app
from app.features.blog import handlers as blog_handlers
from app.features.blog import queries as blog_queries
from app.features.blog import blog_runtime
from app.features.blog.webflow_client import WebflowClient


DATABASE_ERROR_CODE = getattr(ErrorCode, "DATABASE_ERROR", ErrorCode.INTERNAL_ERROR)


def test_blog_schedule_endpoint_is_registered():
    client = TestClient(app)
    response = client.put(
        "/blog/posts/00000000-0000-0000-0000-000000000000/blog/schedule",
        json={"scheduled_at": "2026-04-01T10:00:00Z"},
    )
    assert response.status_code != 404


def test_dispatch_due_blog_posts_processes_due_rows(monkeypatch):
    monkeypatch.setattr(
        blog_runtime,
        "get_due_scheduled_blog_posts",
        lambda limit=10: [{"id": "post-1"}],
    )

    class _FakeExecute:
        data = [{"id": "post-1"}]

    class _FakeTable:
        def update(self, _payload):
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return _FakeExecute()

    class _FakeSupabase:
        client = type("Client", (), {"table": staticmethod(lambda _name: _FakeTable())})()

    monkeypatch.setattr(blog_runtime, "get_supabase", lambda: _FakeSupabase())

    published = []
    monkeypatch.setattr(
        blog_runtime,
        "publish_blog_post",
        lambda post_id, publication_date=None: published.append((post_id, publication_date)) or {"post_id": post_id},
    )

    result = asyncio.run(blog_runtime.dispatch_due_blog_posts(trigger="test"))

    assert published == [("post-1", None)]
    assert result["processed"] == 1
    assert result["published"] == 1
    assert result["failed"] == 0


def test_update_blog_status_scheduled_raises_on_first_write_error_without_fallback(monkeypatch):
    class _FailingTable:
        def __init__(self):
            self.update_calls = []

        def update(self, payload):
            self.update_calls.append(dict(payload))
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            raise Exception("database write failed")

    table = _FailingTable()

    class _FakeSupabase:
        client = type("Client", (), {"table": staticmethod(lambda _name: table)})()

    monkeypatch.setattr(blog_queries, "get_supabase", lambda: _FakeSupabase())

    scheduled_at = "2026-04-01T09:00:00Z"
    try:
        blog_queries.update_blog_status("post-1", status="scheduled", scheduled_at=scheduled_at)
    except FlowForgeException as exc:
        assert exc.code == DATABASE_ERROR_CODE
        assert exc.details["post_id"] == "post-1"
        assert "database write failed" in exc.details["error"]
    else:
        raise AssertionError("Expected FlowForgeException")

    assert table.update_calls == [
        {"blog_status": "scheduled", "blog_scheduled_at": scheduled_at}
    ]


def test_update_blog_status_scheduled_raises_when_scheduled_at_did_not_persist(monkeypatch):
    class _FakeResponse:
        data = [{"id": "post-1", "blog_status": "scheduled", "blog_scheduled_at": None}]

    class _FakeTable:
        def update(self, _payload):
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return _FakeResponse()

    class _FakeSupabase:
        client = type("Client", (), {"table": staticmethod(lambda _name: _FakeTable())})()

    monkeypatch.setattr(blog_queries, "get_supabase", lambda: _FakeSupabase())

    try:
        blog_queries.update_blog_status(
            "post-1",
            status="scheduled",
            scheduled_at="2026-04-01T09:00:00Z",
        )
    except FlowForgeException as exc:
        assert exc.code == DATABASE_ERROR_CODE
        assert exc.details["requested_blog_scheduled_at"] == "2026-04-01T09:00:00Z"
        assert exc.details["persisted_blog_scheduled_at"] is None
    else:
        raise AssertionError("Expected FlowForgeException")


def test_update_blog_status_accepts_equivalent_fractional_second_schedule(monkeypatch):
    class _FakeResponse:
        data = [
            {
                "id": "post-1",
                "blog_status": "scheduled",
                "blog_scheduled_at": "2026-06-05T14:01:19.445+00:00",
            }
        ]

    class _FakeTable:
        def update(self, _payload):
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return _FakeResponse()

    class _FakeSupabase:
        client = type("Client", (), {"table": staticmethod(lambda _name: _FakeTable())})()

    monkeypatch.setattr(blog_queries, "get_supabase", lambda: _FakeSupabase())

    updated = blog_queries.update_blog_status(
        "post-1",
        status="scheduled",
        scheduled_at="2026-06-05T14:01:19.445000+00:00",
    )

    assert updated["blog_status"] == "scheduled"


def test_get_due_scheduled_blog_posts_raises_on_query_error(monkeypatch):
    class _FailingQuery:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def lte(self, *_args):
            return self

        def order(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            raise Exception("supabase select failed")

    class _FakeSupabase:
        client = type("Client", (), {"table": staticmethod(lambda _name: _FailingQuery())})()

    monkeypatch.setattr(blog_queries, "get_supabase", lambda: _FakeSupabase())

    try:
        blog_queries.get_due_scheduled_blog_posts(limit=10)
    except FlowForgeException as exc:
        assert exc.code == DATABASE_ERROR_CODE
        assert exc.message == "Failed to query due scheduled blog posts"
        assert exc.details["limit"] == 10
        assert "checked_at" in exc.details
        assert "supabase select failed" in exc.details["error"]
    else:
        raise AssertionError("Expected FlowForgeException")


def test_schedule_blog_endpoint_returns_persisted_schedule(monkeypatch):
    scheduled_dt = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    scheduled = scheduled_dt.isoformat()

    monkeypatch.setattr(
        blog_handlers,
        "_load_post_for_blog",
        lambda _post_id: {
            "id": "post-1",
            "blog_enabled": True,
            "blog_status": "draft",
            "blog_content": {
                "name": "Test Blog",
                "slug": "test-blog",
                "body_html": "<p>Body</p>",
                "summary_html": "<p>Summary</p>",
            },
        },
    )
    monkeypatch.setattr(
        blog_handlers,
        "update_blog_status",
        lambda post_id, **_kwargs: {
            "id": post_id,
            "blog_status": "scheduled",
            "blog_scheduled_at": scheduled,
        },
    )

    saved_updates = []
    monkeypatch.setattr(
        blog_handlers,
        "update_blog_content_fields",
        lambda post_id, *, updates: saved_updates.append((post_id, updates)) or {"id": post_id},
    )

    response = asyncio.run(
        blog_handlers.schedule_blog_publish(
            "post-1",
            blog_handlers.BlogScheduleRequest(scheduled_at=scheduled_dt),
        )
    )

    assert response.data["post_id"] == "post-1"
    assert response.data["blog_status"] == "scheduled"
    assert response.data["blog_scheduled_at"] == scheduled
    assert saved_updates == [("post-1", {"publication_date": scheduled})]


def test_run_scheduled_blog_publish_job_reports_due_query_error(monkeypatch):
    monkeypatch.setattr(
        blog_runtime,
        "get_due_scheduled_blog_posts",
        lambda limit=10: (_ for _ in ()).throw(
            FlowForgeException(
                code=blog_runtime.ErrorCode.INTERNAL_ERROR,
                message="Failed to query due scheduled blog posts",
                details={"error": "missing blog_scheduled_at"},
            )
        ),
    )

    result = asyncio.run(blog_runtime.run_scheduled_blog_publish_job())

    assert result["processed"] == 0
    assert result["published"] == 0
    assert result["failed"] == 0
    assert result["error"] == "Failed to query due scheduled blog posts"


def test_webflow_publish_item_uses_v2_collection_item_publish_endpoint():
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.text = '{"publishedItemIds":["wf-item-123"],"errors":[]}'
    mock_response.json.return_value = {"publishedItemIds": ["wf-item-123"], "errors": []}

    with patch.object(httpx.Client, "request", return_value=mock_response) as mock_request:
        client = WebflowClient(api_token="test-token", collection_id="col-1", site_id="site-1")
        result = client.publish_item("wf-item-123")

    assert result is True
    assert mock_request.call_args.args[0] == "POST"
    assert mock_request.call_args.args[1] == "/collections/col-1/items/publish"
    assert mock_request.call_args.kwargs["json"] == {"itemIds": ["wf-item-123"]}


def test_blog_cron_dispatch_bypasses_global_auth_with_cron_bearer(monkeypatch):
    async def _fake_dispatch_due_blog_posts(trigger="scheduler"):
        return {"processed": 0, "published": 0, "failed": 0, "trigger": trigger}

    monkeypatch.setattr(blog_runtime, "dispatch_due_blog_posts", _fake_dispatch_due_blog_posts)

    client = TestClient(app)
    response = client.post(
        "/blog/cron/dispatch",
        headers={"Authorization": "Bearer test-cron-secret"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["trigger"] == "cron"
