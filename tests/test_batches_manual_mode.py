from __future__ import annotations

import os
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from postgrest.exceptions import APIError

from app.features.batches import queries as batch_queries
from app.features.batches.schemas import BatchDetailResponse, BatchResponse, CreateBatchRequest, PostDetail


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeInsertTable:
    def __init__(self, storage, table_name):
        self.storage = storage
        self.table_name = table_name
        self.payload = None

    def insert(self, payload):
        self.payload = deepcopy(payload)
        return self

    def execute(self):
        row = {"id": f"{self.table_name}-1", **(self.payload or {})}
        self.storage.setdefault(self.table_name, []).append(deepcopy(row))
        return _FakeResponse([deepcopy(row)])


class _FallbackInsertTable:
    def __init__(self, storage, table_name):
        self.storage = storage
        self.table_name = table_name
        self.payload = None
        self.insert_calls = 0

    def insert(self, payload):
        self.payload = deepcopy(payload)
        self.insert_calls += 1
        return self

    def execute(self):
        if self.insert_calls == 1 and "creation_mode" in (self.payload or {}):
            raise APIError(
                {
                    "code": "PGRST204",
                    "details": None,
                    "hint": None,
                    "message": "Could not find the 'creation_mode' column of 'batches' in the schema cache",
                }
            )
        row = {"id": f"{self.table_name}-1", **(self.payload or {})}
        self.storage.setdefault(self.table_name, []).append(deepcopy(row))
        return _FakeResponse([deepcopy(row)])


class _FakeQueryTable:
    def __init__(self, storage, table_name):
        self.storage = storage
        self.table_name = table_name
        self.filters = []
        self.payload = None

    def select(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self.payload = deepcopy(payload)
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        rows = [deepcopy(row) for row in self.storage.get(self.table_name, []) if all(row.get(key) == value for key, value in self.filters)]
        if self.payload is not None:
            updated = []
            for row in rows:
                row.update(deepcopy(self.payload))
                updated.append(deepcopy(row))
            return _FakeResponse(updated)
        return _FakeResponse(rows)


class _FakeClient:
    def __init__(self, storage):
        self.storage = storage

    def table(self, name):
        if name in {"batches", "posts"}:
            return _FakeInsertTable(self.storage, name)
        return _FakeQueryTable(self.storage, name)


class _FallbackClient(_FakeClient):
    def table(self, name):
        if name == "batches":
            return _FallbackInsertTable(self.storage, name)
        if name == "posts":
            return _FakeInsertTable(self.storage, name)
        return _FakeQueryTable(self.storage, name)


def _fake_supabase(storage):
    return SimpleNamespace(client=_FakeClient(storage))


def _fallback_supabase(storage):
    return SimpleNamespace(client=_FallbackClient(storage))


def test_manual_batch_request_requires_manual_post_count():
    with pytest.raises(ValidationError):
        CreateBatchRequest.model_validate(
            {
                "brand": "ACME",
                "creation_mode": "manual",
                "target_length_tier": 8,
            }
        )


def test_automated_batch_request_still_accepts_type_counts():
    payload = CreateBatchRequest.model_validate(
        {
            "brand": "ACME",
            "creation_mode": "automated",
            "post_type_counts": {"value": 2, "lifestyle": 1, "product": 0},
            "target_length_tier": 16,
        }
    )

    assert payload.creation_mode == "automated"
    assert payload.post_type_counts.total == 3


def test_create_batch_persists_creation_mode_and_manual_count(monkeypatch):
    storage = {"batches": []}
    monkeypatch.setattr(batch_queries, "get_supabase", lambda: _fake_supabase(storage))

    batch = batch_queries.create_batch(
        brand="ACME",
        post_type_counts=None,
        target_length_tier=16,
        creation_mode="manual",
        manual_post_count=3,
    )

    assert batch["creation_mode"] == "manual"
    assert batch["manual_post_count"] == 3
    assert storage["batches"][0]["creation_mode"] == "manual"
    assert storage["batches"][0]["manual_post_count"] == 3


def test_create_batch_falls_back_when_batches_schema_is_legacy(monkeypatch):
    storage = {"batches": []}
    monkeypatch.setattr(batch_queries, "get_supabase", lambda: _fallback_supabase(storage))

    batch = batch_queries.create_batch(
        brand="ACME",
        post_type_counts=None,
        target_length_tier=16,
        creation_mode="manual",
        manual_post_count=3,
    )

    assert batch["creation_mode"] == "manual"
    assert batch["manual_post_count"] == 3
    assert storage["batches"][0]["brand"] == "ACME"
    assert "creation_mode" not in storage["batches"][0]


def test_create_manual_draft_posts_uses_blank_post_type(monkeypatch):
    captured = []

    def fake_create_post_for_batch(**kwargs):
        captured.append(kwargs)
        return {"id": f"post-{len(captured)}", **kwargs}

    monkeypatch.setattr(batch_queries, "create_post_for_batch", fake_create_post_for_batch)

    created = batch_queries.create_manual_draft_posts("batch-1", 2, 16)

    assert len(created) == 2
    assert captured[0]["batch_id"] == "batch-1"
    assert captured[0]["post_type"] == "value"
    assert captured[0]["topic_title"] == "Manual Draft 1"
    assert captured[0]["seed_data"]["manual_draft"] is True
    assert captured[0]["seed_data"]["manual_post_type"] == ""


def test_duplicate_batch_preserves_manual_mode(monkeypatch):
    original = {
        "id": "batch-1",
        "brand": "ACME",
        "post_type_counts": {},
        "creation_mode": "manual",
        "manual_post_count": 4,
        "target_length_tier": 16,
    }
    created_calls = []

    monkeypatch.setattr(batch_queries, "get_batch_by_id", lambda batch_id: original)
    monkeypatch.setattr(
        batch_queries,
        "create_batch",
        lambda *args, **kwargs: created_calls.append((args, kwargs)) or {"id": "batch-2", "creation_mode": kwargs.get("creation_mode"), "manual_post_count": kwargs.get("manual_post_count")},
    )

    duplicate = batch_queries.duplicate_batch("batch-1", new_brand="ACME Copy")

    assert duplicate["creation_mode"] == "manual"
    assert duplicate["manual_post_count"] == 4
    assert created_calls[0][1]["creation_mode"] == "manual"


def test_batch_and_post_detail_models_accept_manual_mode():
    post = PostDetail(
        id="post-1",
        post_type=None,
        topic_title="Manual Draft 1",
        topic_rotation="",
        topic_cta="",
        spoken_duration=0.0,
    )
    batch = BatchResponse(
        id="batch-1",
        brand="ACME",
        state="S1_SETUP",
        creation_mode="manual",
        post_type_counts={},
        manual_post_count=3,
        created_at="2026-04-12T00:00:00Z",
        updated_at="2026-04-12T00:00:00Z",
        archived=False,
    )
    detail = BatchDetailResponse(
        id="batch-1",
        brand="ACME",
        state="S2_SEEDED",
        creation_mode="manual",
        post_type_counts={},
        manual_post_count=3,
        created_at="2026-04-12T00:00:00Z",
        updated_at="2026-04-12T00:00:00Z",
        archived=False,
        posts_count=1,
        posts_by_state={},
        posts=[post],
    )

    assert batch.creation_mode == "manual"
    assert detail.posts[0].post_type is None


@pytest.mark.anyio
async def test_create_batch_endpoint_manual_mode_creates_drafts_and_skips_discovery(monkeypatch):
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

    from app.features.batches import handlers as batch_handlers

    draft_calls = []
    scheduled_calls = []

    class _FakeRequest:
        def __init__(self, payload):
            self.headers = {"content-type": "application/json"}
            self._payload = payload

        async def json(self):
            return self._payload

    def fake_create_batch(**kwargs):
        return {
            "id": "batch-1",
            "brand": kwargs["brand"],
            "state": "S1_SETUP",
            "creation_mode": kwargs["creation_mode"],
            "post_type_counts": kwargs["post_type_counts"],
            "manual_post_count": kwargs["manual_post_count"],
            "target_length_tier": kwargs["target_length_tier"],
            "created_at": "2026-04-12T00:00:00Z",
            "updated_at": "2026-04-12T00:00:00Z",
            "archived": False,
        }

    def fake_create_manual_draft_posts(batch_id, manual_post_count, target_length_tier):
        draft_calls.append((batch_id, manual_post_count, target_length_tier))
        return [{"id": f"post-{index + 1}"} for index in range(manual_post_count)]

    def fake_update_batch_state(batch_id, target_state):
        return {
            "id": batch_id,
            "brand": "ACME",
            "state": target_state.value,
            "creation_mode": "manual",
            "post_type_counts": {},
            "manual_post_count": 3,
            "target_length_tier": 16,
            "created_at": "2026-04-12T00:00:00Z",
            "updated_at": "2026-04-12T00:00:00Z",
            "archived": False,
        }

    monkeypatch.setattr(batch_handlers, "create_batch", fake_create_batch)
    monkeypatch.setattr(batch_handlers, "create_manual_draft_posts", fake_create_manual_draft_posts)
    monkeypatch.setattr(batch_handlers, "update_batch_state", fake_update_batch_state)
    monkeypatch.setattr(batch_handlers, "start_seeding_interaction", lambda *args, **kwargs: scheduled_calls.append(("start", args, kwargs)))
    monkeypatch.setattr(batch_handlers, "schedule_batch_discovery", lambda *args, **kwargs: scheduled_calls.append(("schedule", args, kwargs)))

    response = await batch_handlers.create_batch_endpoint(
        _FakeRequest(
            {
                "brand": "ACME",
                "creation_mode": "manual",
                "manual_post_count": 3,
                "target_length_tier": 16,
            }
        )
    )

    assert response.data.state.value == "S2_SEEDED"
    assert draft_calls == [("batch-1", 3, 16)]
    assert scheduled_calls == []


@pytest.mark.anyio
async def test_create_batch_endpoint_manual_mode_redirects_to_detail_page(monkeypatch):
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

    from app.features.batches import handlers as batch_handlers

    class _FakeRequest:
        def __init__(self, payload):
            self.headers = {"content-type": "application/json", "accept": "text/html"}
            self._payload = payload

        async def json(self):
            return self._payload

    monkeypatch.setattr(batch_handlers, "create_batch", lambda **kwargs: {
        "id": "batch-1",
        "brand": kwargs["brand"],
        "state": "S1_SETUP",
        "creation_mode": kwargs["creation_mode"],
        "post_type_counts": kwargs["post_type_counts"],
        "manual_post_count": kwargs["manual_post_count"],
        "target_length_tier": kwargs["target_length_tier"],
        "created_at": "2026-04-12T00:00:00Z",
        "updated_at": "2026-04-12T00:00:00Z",
        "archived": False,
    })
    monkeypatch.setattr(batch_handlers, "create_manual_draft_posts", lambda *args, **kwargs: [])
    monkeypatch.setattr(batch_handlers, "update_batch_state", lambda *args, **kwargs: {
        "id": "batch-1",
        "brand": "ACME",
        "state": "S2_SEEDED",
        "creation_mode": "manual",
        "post_type_counts": {},
        "manual_post_count": 3,
        "target_length_tier": 16,
        "created_at": "2026-04-12T00:00:00Z",
        "updated_at": "2026-04-12T00:00:00Z",
        "archived": False,
    })
    monkeypatch.setattr(batch_handlers, "start_seeding_interaction", lambda *args, **kwargs: None)
    monkeypatch.setattr(batch_handlers, "schedule_batch_discovery", lambda *args, **kwargs: None)

    response = await batch_handlers.create_batch_endpoint(
        _FakeRequest(
            {
                "brand": "ACME",
                "creation_mode": "manual",
                "manual_post_count": 3,
                "target_length_tier": 16,
            }
        )
    )

    assert response.headers["HX-Redirect"] == "/batches/batch-1"
