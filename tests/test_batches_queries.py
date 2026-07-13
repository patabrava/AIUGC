import httpx
from types import SimpleNamespace

import pytest

from app.features.batches import queries as batch_queries


def test_batch_list_projection_keeps_legacy_and_semantic_duration_fields():
    fields = set(batch_queries.BATCH_LIST_FIELDS.split(","))

    assert {
        "creation_mode",
        "target_length_tier",
        "target_duration_seconds",
        "video_pipeline_route",
    } <= fields


class _RetryingQuery:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def execute(self):
        self.calls += 1
        if self.calls < 3:
            raise httpx.ReadError(
                "temporarily unavailable",
                request=httpx.Request("GET", "https://example.test/rest/v1/batches"),
            )
        return SimpleNamespace(data=self.rows, count=len(self.rows))


class _FakeClient:
    def __init__(self, batches, posts):
        self._queries = {
            "batches": _RetryingQuery(batches),
            "posts": _RetryingQuery(posts),
        }

    def table(self, name):
        return self._queries[name]


def test_get_batch_by_id_retries_transient_request_errors(monkeypatch):
    fake_adapter = SimpleNamespace(
        client=_FakeClient(
            batches=[{"id": "batch-1", "brand": "Test Batch"}],
            posts=[],
        )
    )
    sleeps = []
    monkeypatch.setattr(batch_queries, "get_supabase", lambda: fake_adapter)
    monkeypatch.setattr(batch_queries.time, "sleep", lambda seconds: sleeps.append(seconds))

    batch = batch_queries.get_batch_by_id("batch-1")

    assert batch["id"] == "batch-1"
    assert sleeps == [0.15, 0.35]


def test_get_batch_posts_summary_retries_transient_request_errors(monkeypatch):
    fake_adapter = SimpleNamespace(
        client=_FakeClient(
            batches=[],
            posts=[
                {"id": "post-1", "batch_id": "batch-1", "post_type": "value"},
                {"id": "post-2", "batch_id": "batch-1", "post_type": "lifestyle"},
            ],
        )
    )
    sleeps = []
    monkeypatch.setattr(batch_queries, "get_supabase", lambda: fake_adapter)
    monkeypatch.setattr(batch_queries.time, "sleep", lambda seconds: sleeps.append(seconds))

    summary = batch_queries.get_batch_posts_summary("batch-1")

    assert summary == {"posts_count": 2, "posts_by_state": {"value": 1, "lifestyle": 1}}
    assert sleeps == [0.15, 0.35]


class _MutableTable:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.pending_update = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        rows = [row for row in self.rows if all(row.get(key) == value for key, value in self.filters)]
        if self.pending_update is not None:
            for row in rows:
                row.update(self.pending_update)
            data = rows
        else:
            data = rows
        self.filters = []
        self.pending_update = None
        return SimpleNamespace(data=data)

    def update(self, payload):
        self.pending_update = payload
        return self


class _MutableClient:
    def __init__(self, batches, posts):
        self.tables = {
            "batches": _MutableTable(batches),
            "posts": _MutableTable(posts),
        }

    def table(self, name):
        return self.tables[name]


def _ready_actor(actor_id: str):
    from app.features.characters.schemas import ActorIdentityRecord

    return ActorIdentityRecord.model_validate(
        {
            "id": actor_id,
            "name": f"Actor {actor_id}",
            "is_active": True,
            "provider": "magnific",
            "provider_lora_id": f"lora-{actor_id}",
            "provider_lora_name": f"actor_{actor_id}",
            "provider_training_task_id": f"task-{actor_id}",
            "training_status": "completed",
            "training_phase": "ready",
            "training_progress_percent": 100,
            "training_error": None,
            "training_images": ["https://cdn.example.com/a.png"] * 8,
            "portrait_image_url": "https://cdn.example.com/a.png",
            "cover_image_url": "https://cdn.example.com/a.png",
            "consent_source": "operator",
            "created_at": "2026-05-20T00:00:00Z",
            "updated_at": "2026-05-20T00:00:00Z",
            "training_completed_at": "2026-05-20T00:00:00Z",
        }
    )


def test_sync_character_consistency_batch_actor_updates_unsubmitted_batch_and_clears_scene_reference_state(monkeypatch):
    batches = [
        {
            "id": "batch-1",
            "creation_mode": "character_consistency",
            "actor_identity_id": "actor-old",
            "actor_identity_snapshot": {"actor_identity_id": "actor-old", "provider_lora_id": "lora-actor-old"},
        }
    ]
    posts = [
        {
            "id": "post-1",
            "batch_id": "batch-1",
            "video_status": None,
            "scene_reference_image_id": "scene-ref-1",
            "identity_gate_result": {"status": "passed"},
        }
    ]
    monkeypatch.setattr(
        batch_queries,
        "get_supabase",
        lambda: SimpleNamespace(client=_MutableClient(batches, posts)),
    )

    updated = batch_queries.sync_character_consistency_batch_actor(
        batches[0],
        correlation_id="test-correlation",
        active_actor=_ready_actor("actor-new"),
    )

    assert updated["actor_identity_id"] == "actor-new"
    assert updated["actor_identity_snapshot"]["actor_identity_id"] == "actor-new"
    assert batches[0]["actor_identity_id"] == "actor-new"
    assert posts[0]["scene_reference_image_id"] is None
    assert posts[0]["identity_gate_result"] is None


def test_sync_character_consistency_batch_actor_rejects_started_batch_with_different_actor(monkeypatch):
    batches = [
        {
            "id": "batch-1",
            "creation_mode": "character_consistency",
            "actor_identity_id": "actor-old",
            "actor_identity_snapshot": {"actor_identity_id": "actor-old", "provider_lora_id": "lora-actor-old"},
        }
    ]
    posts = [
        {
            "id": "post-1",
            "batch_id": "batch-1",
            "video_status": "submitted",
            "scene_reference_image_id": "scene-ref-1",
            "identity_gate_result": {"status": "passed"},
        }
    ]
    monkeypatch.setattr(
        batch_queries,
        "get_supabase",
        lambda: SimpleNamespace(client=_MutableClient(batches, posts)),
    )

    with pytest.raises(batch_queries.ValidationError) as exc_info:
        batch_queries.sync_character_consistency_batch_actor(
            batches[0],
            correlation_id="test-correlation",
            active_actor=_ready_actor("actor-new"),
        )

    assert exc_info.value.status_code == 422
    assert "already started video generation with a different actor" in exc_info.value.message
