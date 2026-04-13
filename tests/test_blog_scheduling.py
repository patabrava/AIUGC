from __future__ import annotations

import asyncio
import os

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

from fastapi.testclient import TestClient

from app.main import app
from app.features.blog import blog_runtime


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
