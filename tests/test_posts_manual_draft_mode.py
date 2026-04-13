from __future__ import annotations

import os
from copy import deepcopy

import pytest

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

from app.features.posts import handlers as posts_handlers


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, storage, table_name):
        self.storage = storage
        self.table_name = table_name
        self.filters = []
        self.payload = None
        self.operation = "select"

    def select(self, *_fields):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        rows = self.storage[self.table_name]
        matches = [row for row in rows if all(row.get(key) == value for key, value in self.filters)]
        if self.operation == "update":
            updated = []
            for row in matches:
                row.update(deepcopy(self.payload))
                updated.append(deepcopy(row))
            return _FakeResponse(updated)
        return _FakeResponse([deepcopy(row) for row in matches])


class _FakeClient:
    def __init__(self, storage):
        self.storage = storage

    def table(self, table_name):
        return _FakeTable(self.storage, table_name)


class _FakeSupabase:
    def __init__(self, storage):
        self.client = _FakeClient(storage)


class _FakeRequest:
    def __init__(self, payload, content_type="application/x-www-form-urlencoded"):
        self.headers = {"content-type": content_type}
        self._payload = payload

    async def json(self):
        return self._payload

    async def form(self):
        return self._payload


@pytest.mark.anyio
async def test_manual_draft_save_updates_post_type_script_and_clears_prompt(monkeypatch):
    storage = {
        "batches": [{"id": "batch-1", "creation_mode": "manual"}],
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "post_type": "",
                "seed_data": {"manual_draft": True, "script_review_status": "pending", "script": "Old"},
                "video_prompt_json": {"stale": True},
            }
        ],
    }
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    response = await posts_handlers.update_post_script(
        "post-1",
        _FakeRequest({"script_text": "New script", "post_type": "custom_kind"}),
    )

    assert response.ok is True
    assert storage["posts"][0]["post_type"] == "custom_kind"
    assert storage["posts"][0]["seed_data"]["script"] == "New script"
    assert storage["posts"][0]["video_prompt_json"] is None


@pytest.mark.anyio
async def test_automated_save_does_not_override_post_type_with_submitted_value(monkeypatch):
    storage = {
        "batches": [{"id": "batch-1", "creation_mode": "automated"}],
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "post_type": "value",
                "seed_data": {"script_review_status": "pending", "script": "Old"},
                "video_prompt_json": {"stale": True},
            }
        ],
    }
    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    response = await posts_handlers.update_post_script(
        "post-1",
        _FakeRequest({"script_text": "New script", "post_type": "hijack"}),
    )

    assert response.ok is True
    assert storage["posts"][0]["post_type"] == "value"
    assert storage["posts"][0]["seed_data"]["script"] == "New script"
    assert storage["posts"][0]["video_prompt_json"] is None

