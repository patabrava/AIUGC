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

import pytest  # noqa: E402

from app.features.qa import handlers as qa_handlers  # noqa: E402


class _JsonRequest:
    headers = {"content-type": "application/json"}

    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class _FakeQuery:
    def __init__(self, table_name, db):
        self.table_name = table_name
        self.db = db
        self.selected_fields = None
        self.filters = []
        self.update_payload = None

    def select(self, fields):
        self.selected_fields = None if fields == "*" else [field.strip() for field in fields.split(",")]
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def update(self, payload):
        self.update_payload = payload
        return self

    def execute(self):
        def matches(row):
            return all(row.get(field) == value for field, value in self.filters)

        if self.update_payload is not None:
            for row in self.db[self.table_name]:
                if matches(row):
                    row.update(self.update_payload)

        rows = [row.copy() for row in self.db[self.table_name] if matches(row)]
        if self.selected_fields is not None:
            rows = [{field: row.get(field) for field in self.selected_fields} for row in rows]
        return SimpleNamespace(data=rows)


class _FakeSupabaseClient:
    def __init__(self, db):
        self.db = db

    def table(self, table_name):
        return _FakeQuery(table_name, self.db)


@pytest.mark.asyncio
async def test_approving_last_active_video_advances_when_removed_post_exists(monkeypatch):
    db = {
        "batches": [{"id": "batch-1", "state": "S6_QA"}],
        "posts": [
            {
                "id": "post-approved",
                "batch_id": "batch-1",
                "qa_pass": True,
                "seed_data": {"script_review_status": "approved"},
            },
            {
                "id": "post-final",
                "batch_id": "batch-1",
                "qa_pass": None,
                "seed_data": {"script_review_status": "approved"},
            },
            {
                "id": "post-removed",
                "batch_id": "batch-1",
                "qa_pass": None,
                "seed_data": {"script_review_status": "removed", "video_excluded": True},
            },
        ],
    }
    fake_client = _FakeSupabaseClient(db)
    monkeypatch.setattr(qa_handlers, "get_supabase", lambda: SimpleNamespace(client=fake_client))

    response = await qa_handlers.approve_qa("post-final", _JsonRequest({"approved": True}))

    assert response.data["batch_advanced"] is True
    assert db["batches"][0]["state"] == "S7_PUBLISH_PLAN"
    assert db["posts"][1]["qa_pass"] is True


@pytest.mark.asyncio
async def test_rejecting_video_excludes_it_and_advances_with_remaining_approved_posts(monkeypatch):
    db = {
        "batches": [{"id": "batch-1", "state": "S6_QA"}],
        "posts": [
            {
                "id": "post-approved",
                "batch_id": "batch-1",
                "qa_pass": True,
                "seed_data": {"script_review_status": "approved"},
            },
            {
                "id": "post-rejected",
                "batch_id": "batch-1",
                "qa_pass": None,
                "seed_data": {"script_review_status": "approved"},
            },
        ],
    }
    fake_client = _FakeSupabaseClient(db)
    monkeypatch.setattr(qa_handlers, "get_supabase", lambda: SimpleNamespace(client=fake_client))

    response = await qa_handlers.approve_qa("post-rejected", _JsonRequest({"approved": False, "notes": "Bad cut"}))

    assert response.data["batch_advanced"] is True
    assert db["batches"][0]["state"] == "S7_PUBLISH_PLAN"
    assert db["posts"][1]["qa_pass"] is False
    assert db["posts"][1]["qa_notes"] == "Bad cut"
    assert db["posts"][1]["seed_data"]["video_excluded"] is True
    assert db["posts"][1]["seed_data"]["video_review_status"] == "rejected"
