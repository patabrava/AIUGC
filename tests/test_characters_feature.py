from __future__ import annotations

import io
import os
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "dummy")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://cdn.example.com")

from fastapi.testclient import TestClient

from app.features.characters import handlers as character_handlers
from app.features.characters import queries as character_queries
from app.features.characters.schemas import ActorIdentityRecord, CharacterRecord
from app.main import app


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self.payload = None
        self.filters = []
        self._limit = None
        self._order = None
        self._pending_insert = None
        self._pending_update = None
        self._single = False

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def maybe_single(self):
        self._single = True
        return self

    def order(self, key, desc=False):
        self._order = (key, desc)
        return self

    def limit(self, value):
        self._limit = value
        return self

    def insert(self, payload):
        self.payload = payload
        self._pending_insert = payload
        return self

    def update(self, payload):
        self.payload = payload
        self._pending_update = payload
        return self

    def execute(self):
        if self._pending_insert is not None:
            self.rows.append(self._pending_insert)
            data = self._pending_insert
        else:
            rows = [row for row in self.rows if all(row.get(key) == value for key, value in self.filters)]
            if self._pending_update is not None:
                for row in rows:
                    row.update(self._pending_update)
                data = rows
            else:
                if self._order is not None:
                    key, desc = self._order
                    rows = sorted(rows, key=lambda row: str(row.get(key) or ""), reverse=desc)
                if self._limit is not None:
                    rows = rows[: self._limit]
                data = rows[0] if (self._single or self._limit == 1) and rows else rows
        self.filters = []
        self._limit = None
        self._order = None
        self._pending_insert = None
        self._pending_update = None
        self._single = False
        return _FakeResponse(data)


def _fake_supabase(rows):
    table = _FakeTable(rows)
    return SimpleNamespace(client=SimpleNamespace(table=lambda _name: table))


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _actor_row(
    actor_id: str,
    *,
    is_active: bool = False,
    phase: str = "ready",
    progress: int = 100,
    lora_id: str | None = "lora-1",
    error: str | None = None,
    updated_at: str = "2026-05-21T00:00:00Z",
) -> dict:
    return {
        "id": actor_id,
        "name": f"Actor {actor_id}",
        "is_active": is_active,
        "provider": "magnific",
        "provider_lora_id": lora_id,
        "provider_lora_name": f"lora_{actor_id}",
        "provider_training_task_id": f"task-{actor_id}",
        "training_status": "failed" if error else phase,
        "training_phase": phase,
        "training_progress_percent": progress,
        "training_error": error,
        "training_images": ["https://cdn.example.com/a.png"] * 8,
        "consent_source": "operator",
        "created_at": "2026-05-20T00:00:00Z",
        "updated_at": updated_at,
    }


def test_get_active_character_returns_record(monkeypatch):
    rows = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "Test",
            "front_image_url": "https://cdn/front.png",
            "three_quarter_image_url": "https://cdn/3q.png",
            "profile_image_url": "https://cdn/profile.png",
            "is_active": True,
            "created_at": "2026-05-08T00:00:00Z",
            "updated_at": "2026-05-08T00:00:00Z",
        }
    ]
    monkeypatch.setattr(character_queries, "get_supabase", lambda: _fake_supabase(rows))

    result = character_queries.get_active_character()

    assert isinstance(result, CharacterRecord)
    assert result.front_image_url == "https://cdn/front.png"


def test_get_active_character_returns_none_when_table_missing(monkeypatch):
    class _MissingTable:
        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            raise APIError(
                {
                    "code": "204",
                    "message": "Missing response",
                    "details": "characters relation not found",
                    "hint": None,
                }
            )

    monkeypatch.setattr(
        character_queries,
        "get_supabase",
        lambda: SimpleNamespace(client=SimpleNamespace(table=lambda _name: _MissingTable())),
    )

    assert character_queries.get_active_character() is None


def test_actor_identity_readiness_distinguishes_training_from_active():
    from app.features.characters.actor_identity import actor_identity_is_ready, actor_identity_training_ready

    inactive_ready = ActorIdentityRecord.model_validate(_actor_row("ready-inactive", is_active=False))
    active_ready = ActorIdentityRecord.model_validate(_actor_row("ready-active", is_active=True))
    active_failed = ActorIdentityRecord.model_validate(
        _actor_row("failed-active", is_active=True, error="provider failed")
    )

    assert actor_identity_training_ready(inactive_ready) is True
    assert actor_identity_is_ready(inactive_ready) is False
    assert actor_identity_is_ready(active_ready) is True
    assert actor_identity_training_ready(active_failed) is False


def test_sort_actor_identity_roster_groups_active_ready_training_failed_by_recency():
    from app.features.characters.actor_identity import sort_actor_identity_roster

    rows = [
        _actor_row("failed", phase="failed", error="bad", updated_at="2026-05-21T04:00:00Z"),
        _actor_row("ready-old", updated_at="2026-05-21T01:00:00Z"),
        _actor_row("training", phase="training", progress=30, lora_id=None, updated_at="2026-05-21T05:00:00Z"),
        _actor_row("active", is_active=True, updated_at="2026-05-21T00:00:00Z"),
        _actor_row("ready-new", updated_at="2026-05-21T03:00:00Z"),
    ]
    roster = sort_actor_identity_roster([ActorIdentityRecord.model_validate(row) for row in rows])

    assert [identity.id for identity in roster] == ["active", "ready-new", "ready-old", "training", "failed"]


def test_list_actor_identities_returns_sorted_roster(monkeypatch):
    rows = [
        _actor_row("training", phase="training", progress=30, lora_id=None, updated_at="2026-05-21T05:00:00Z"),
        _actor_row("active", is_active=True, updated_at="2026-05-21T00:00:00Z"),
        _actor_row("ready", updated_at="2026-05-21T03:00:00Z"),
    ]
    monkeypatch.setattr(character_queries, "get_supabase", lambda: _fake_supabase(rows))

    result = character_queries.list_actor_identities()

    assert [identity.id for identity in result] == ["active", "ready", "training"]


def test_sync_actor_identity_roster_from_provider_imports_completed_lora(monkeypatch):
    created = []

    class _FakeMagnific:
        def list_loras(self, **_kwargs):
            return {
                "data": [
                    {
                        "id": "1786946",
                        "name": "ayra-actor-longchar-20260521",
                        "task_id": "task-1786946",
                        "status": "completed",
                    }
                ]
            }

    def _fake_create(**kwargs):
        created.append(kwargs)
        return ActorIdentityRecord(
            id="actor-imported",
            name=kwargs["name"],
            is_active=kwargs["is_active"],
            provider=kwargs["provider"],
            provider_lora_id=kwargs["provider_lora_id"],
            provider_lora_name=kwargs["provider_lora_name"],
            provider_training_task_id=kwargs["provider_training_task_id"],
            training_status=kwargs["training_status"],
            training_phase=kwargs["training_phase"],
            training_progress_percent=kwargs["training_progress_percent"],
            training_error=kwargs["training_error"],
            training_images=list(kwargs["training_images"]),
            consent_source=kwargs["consent_source"],
            created_at="2026-05-21T00:00:00Z",
            updated_at="2026-05-21T00:00:00Z",
            training_started_at=None,
            training_completed_at=None,
        )

    monkeypatch.setattr(character_queries, "get_magnific_client", lambda: _FakeMagnific())
    monkeypatch.setattr(character_queries, "list_actor_identities", lambda: [])
    monkeypatch.setattr(character_queries, "create_actor_identity", _fake_create)

    result = character_queries.sync_actor_identity_roster_from_provider(correlation_id="test")

    assert created[0]["provider_lora_id"] == "1786946"
    assert created[0]["provider_lora_name"] == "ayra-actor-longchar-20260521"
    assert created[0]["training_phase"] == "ready"
    assert created[0]["training_progress_percent"] == 100
    assert result[0].provider_lora_id == "1786946"


def test_refresh_actor_identity_roster_polls_inactive_training_actor(monkeypatch):
    from app.adapters.magnific_client import MagnificTrainingStatus

    training = ActorIdentityRecord.model_validate(
        _actor_row("training", is_active=False, phase="training", progress=40, lora_id=None)
    )
    refreshed = ActorIdentityRecord.model_validate(
        _actor_row("training", is_active=False, phase="ready", progress=100, lora_id="lora-ready")
    )
    updates = []

    class _FakeMagnific:
        def poll_character_lora_status(self, **kwargs):
            assert kwargs["provider_training_task_id"] == "task-training"
            return MagnificTrainingStatus(
                provider_training_task_id="task-training",
                provider_lora_id="lora-ready",
                provider_lora_name="lora_training",
                training_status="completed",
                training_phase="ready",
                training_progress_percent=100,
            )

    monkeypatch.setattr(character_queries, "get_magnific_client", lambda: _FakeMagnific())
    monkeypatch.setattr(character_queries, "update_actor_training_status", lambda **kwargs: updates.append(kwargs))
    monkeypatch.setattr(character_queries, "get_actor_identity_by_id", lambda actor_identity_id: refreshed)

    result = character_queries.refresh_actor_identity_roster_statuses([training], correlation_id="test")

    assert result[0].id == "training"
    assert result[0].training_phase == "ready"
    assert updates[0]["actor_identity_id"] == "training"
    assert updates[0]["provider_lora_id"] == "lora-ready"


def test_set_active_actor_identity_rejects_training_actor(monkeypatch):
    rows = [
        _actor_row("active", is_active=True),
        _actor_row("training", phase="training", progress=30, lora_id=None),
    ]
    monkeypatch.setattr(character_queries, "get_supabase", lambda: _fake_supabase(rows))

    with pytest.raises(Exception) as exc_info:
        character_queries.set_active_actor_identity(actor_identity_id="training", correlation_id="test")

    assert getattr(exc_info.value, "status_code", None) == 422
    assert [row["id"] for row in rows if row["is_active"]] == ["active"]


def test_set_active_actor_identity_rejects_missing_actor_as_validation(monkeypatch):
    rows = [_actor_row("active", is_active=True)]
    monkeypatch.setattr(character_queries, "get_supabase", lambda: _fake_supabase(rows))

    with pytest.raises(Exception) as exc_info:
        character_queries.set_active_actor_identity(actor_identity_id="missing", correlation_id="test")

    assert getattr(exc_info.value, "status_code", None) == 422
    assert [row["id"] for row in rows if row["is_active"]] == ["active"]


def test_set_active_actor_identity_switches_exactly_one_ready_actor(monkeypatch):
    rows = [
        _actor_row("old", is_active=True, updated_at="2026-05-21T01:00:00Z"),
        _actor_row("new", is_active=False, updated_at="2026-05-21T02:00:00Z"),
    ]
    monkeypatch.setattr(character_queries, "get_supabase", lambda: _fake_supabase(rows))

    result = character_queries.set_active_actor_identity(actor_identity_id="new", correlation_id="test")

    assert result.id == "new"
    assert result.is_active is True
    assert [row["id"] for row in rows if row["is_active"]] == ["new"]


def test_set_active_actor_identity_restores_previous_active_on_activation_failure(monkeypatch):
    rows = [
        _actor_row("old", is_active=True, updated_at="2026-05-21T01:00:00Z"),
        _actor_row("new", is_active=False, updated_at="2026-05-21T02:00:00Z"),
    ]

    class _FailingActivationTable(_FakeTable):
        def execute(self):
            if self._pending_update and self._pending_update.get("is_active") is True and ("id", "new") in self.filters:
                self.filters = []
                self._pending_update = None
                raise RuntimeError("activation failed")
            return super().execute()

    table = _FailingActivationTable(rows)
    monkeypatch.setattr(
        character_queries,
        "get_supabase",
        lambda: SimpleNamespace(client=SimpleNamespace(table=lambda _name: table)),
    )

    with pytest.raises(RuntimeError):
        character_queries.set_active_actor_identity(actor_identity_id="new", correlation_id="test")

    assert [row["id"] for row in rows if row["is_active"]] == ["old"]


def test_upload_character_persists_three_images(monkeypatch):
    captured_uploads = []

    class _FakeStorage:
        def upload_image(self, **kwargs):
            captured_uploads.append(kwargs)
            return {
                "url": f"https://cdn.example.com/{kwargs['file_name']}",
                "storage_key": f"images/{kwargs['file_name']}",
            }

    monkeypatch.setattr(character_handlers, "get_storage_client", lambda: _FakeStorage())
    monkeypatch.setattr(
        character_handlers.character_queries,
        "upsert_active_character",
        lambda **kwargs: CharacterRecord(
            id="00000000-0000-0000-0000-000000000001",
            name=kwargs["name"],
            front_image_url=kwargs["front_image_url"],
            three_quarter_image_url=kwargs["three_quarter_image_url"],
            profile_image_url=kwargs["profile_image_url"],
            is_active=True,
            created_at="2026-05-08T00:00:00Z",
            updated_at="2026-05-08T00:00:00Z",
        ),
    )

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/character",
        data={"name": "My Avatar"},
        files={
            "front": ("front.png", io.BytesIO(_png_bytes()), "image/png"),
            "three_quarter": ("3q.png", io.BytesIO(_png_bytes()), "image/png"),
            "profile": ("profile.png", io.BytesIO(_png_bytes()), "image/png"),
        },
        follow_redirects=False,
    )

    assert response.status_code in {200, 303}, response.text
    assert {item["file_name"] for item in captured_uploads} == {"front.png", "3q.png", "profile.png"}
    assert all(item["content_type"] == "image/png" for item in captured_uploads)


def test_actor_settings_page_renders_active_actor(monkeypatch):
    actor = ActorIdentityRecord(
        id="actor-1",
        name="AYRA Actor Identity",
        is_active=True,
        provider="magnific",
        provider_lora_id="1785341",
        provider_lora_name="ayra_actor_codex_20260520205336",
        provider_training_task_id="task-1",
        training_status="ready",
        training_phase="ready",
        training_progress_percent=100,
        training_started_at="2026-05-20T21:00:00Z",
        training_completed_at="2026-05-20T21:10:18Z",
        training_error=None,
        training_images=["https://cdn.example.com/a.png"],
        consent_source="operator",
        created_at="2026-05-20T21:00:00Z",
        updated_at="2026-05-20T21:10:18Z",
    )
    monkeypatch.setattr(character_queries, "refresh_active_actor_identity_status", lambda correlation_id: actor)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: actor)

    response = TestClient(app, base_url="http://localhost").get("/settings/actor")

    assert response.status_code == 200
    assert "AYRA Actor Identity" in response.text
    assert "1785341" in response.text
    assert "100%" in response.text


def test_actor_settings_page_renders_ready_selector_and_full_roster(monkeypatch):
    active = ActorIdentityRecord.model_validate(
        _actor_row("active", is_active=True, updated_at="2026-05-21T10:00:00Z")
    )
    ready = ActorIdentityRecord.model_validate(
        _actor_row("ready", is_active=False, updated_at="2026-05-21T12:00:00Z")
    )
    training = ActorIdentityRecord.model_validate(
        _actor_row("training", phase="training", progress=40, lora_id=None, updated_at="2026-05-21T13:00:00Z")
    )
    sync_calls = []
    monkeypatch.setattr(character_queries, "sync_actor_identity_roster_from_provider", lambda correlation_id: sync_calls.append(correlation_id))
    monkeypatch.setattr(character_queries, "refresh_active_actor_identity_status", lambda correlation_id: active)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: active)
    monkeypatch.setattr(character_queries, "list_actor_identities", lambda: [active, ready, training])

    response = TestClient(app, base_url="http://localhost").get("/settings/actor")

    assert response.status_code == 200
    assert 'name="actor_identity_id"' in response.text
    assert 'value="active" selected' in response.text
    assert 'value="ready"' in response.text
    assert 'value="training"' not in response.text
    assert "Actor training" in response.text
    assert sync_calls


def test_actor_settings_active_post_calls_activation_helper(monkeypatch):
    captured = {}

    def fake_activate(**kwargs):
        captured.update(kwargs)
        return ActorIdentityRecord.model_validate(_actor_row(kwargs["actor_identity_id"], is_active=True))

    monkeypatch.setattr(character_queries, "set_active_actor_identity", fake_activate)

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor/active",
        data={"actor_identity_id": "ready"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings/actor?active_actor_updated=1"
    assert captured["actor_identity_id"] == "ready"
    assert captured["correlation_id"]


def test_actor_settings_active_post_rejects_non_ready_actor(monkeypatch):
    def reject(**_kwargs):
        raise ValueError("Only ready ActorIdentity rows can be activated")

    monkeypatch.setattr(character_queries, "set_active_actor_identity", reject)

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor/active",
        data={"actor_identity_id": "training"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "Only ready ActorIdentity rows can be activated" in response.text


def test_refresh_active_actor_identity_status_recovers_from_poll_failure(monkeypatch):
    actor = ActorIdentityRecord(
        id="actor-1",
        name="AYRA Actor Identity",
        is_active=True,
        provider="magnific",
        provider_lora_id="1785341",
        provider_lora_name="ayra_actor_codex_20260520205336",
        provider_training_task_id="task-1",
        training_status="processing",
        training_phase="processing",
        training_progress_percent=55,
        training_started_at="2026-05-20T21:00:00Z",
        training_completed_at=None,
        training_error=None,
        training_images=["https://cdn.example.com/a.png"],
        consent_source="operator",
        created_at="2026-05-20T21:00:00Z",
        updated_at="2026-05-20T21:05:00Z",
    )
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: actor)

    class _BrokenMagnific:
        def poll_character_lora_status(self, **_kwargs):
            raise RuntimeError("magnific unavailable")

    monkeypatch.setattr(character_queries, "get_magnific_client", lambda: _BrokenMagnific())

    refreshed = character_queries.refresh_active_actor_identity_status(correlation_id="actor-test")

    assert refreshed == actor


def test_upload_actor_identity_submits_training_set(monkeypatch):
    uploaded = []
    training_payloads = []
    creations = []
    status_updates = []

    class _FakeStorage:
        def upload_image(self, **kwargs):
            uploaded.append(kwargs)
            return {
                "url": f"https://cdn.example.com/{kwargs['file_name']}",
                "storage_key": f"images/{kwargs['file_name']}",
            }

    class _FakeMagnific:
        def train_character_lora(self, **kwargs):
            training_payloads.append(kwargs)
            from app.adapters.magnific_client import MagnificTrainingStatus

            return MagnificTrainingStatus(
                provider_training_task_id="task-123",
                provider_lora_id="lora-123",
                provider_lora_name="ayra_actor_test",
                training_status="queued",
                training_phase="queued",
                training_progress_percent=10,
            )

    def _fake_create(**kwargs):
        creations.append(kwargs)
        return ActorIdentityRecord(
            id="actor-1",
            name=kwargs["name"],
            is_active=kwargs.get("is_active", False),
            provider=kwargs["provider"],
            provider_lora_id=kwargs["provider_lora_id"],
            provider_lora_name=kwargs["provider_lora_name"],
            provider_training_task_id=kwargs["provider_training_task_id"],
            training_status=kwargs["training_status"],
            training_phase=kwargs["training_phase"],
            training_progress_percent=kwargs["training_progress_percent"],
            training_started_at="2026-05-21T00:00:00Z",
            training_completed_at=kwargs.get("training_completed_at"),
            training_error=kwargs.get("training_error"),
            training_images=list(kwargs["training_images"]),
            consent_source=kwargs.get("consent_source"),
            created_at="2026-05-21T00:00:00Z",
            updated_at="2026-05-21T00:00:00Z",
        )

    monkeypatch.setattr(character_handlers, "get_storage_client", lambda: _FakeStorage())
    monkeypatch.setattr(character_handlers.character_queries, "create_actor_identity", _fake_create)
    monkeypatch.setattr(character_handlers.character_queries, "update_actor_training_status", lambda **kwargs: status_updates.append(kwargs))
    monkeypatch.setattr(character_handlers.character_queries, "get_actor_identity_by_id", lambda _actor_id: None)
    monkeypatch.setattr(character_handlers.character_queries, "get_active_actor_identity", lambda: None)
    monkeypatch.setattr("app.adapters.magnific_client.get_magnific_client", lambda: _FakeMagnific())

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor",
        data={
            "name": "AYRA Actor Identity",
            "quality": "high",
            "gender": "woman",
            "consent_source": "operator",
            "description": "Test run",
        },
        files=[
            ("training_images", ("img1.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img2.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img3.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img4.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img5.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img6.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img7.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img8.png", io.BytesIO(_png_bytes()), "image/png")),
        ],
        follow_redirects=False,
    )

    assert response.status_code in {200, 303}, response.text
    assert len(uploaded) == 8
    assert len(training_payloads) == 1
    assert len(creations) == 1
    assert len(status_updates) == 1
    assert training_payloads[0]["name"] == "AYRA Actor Identity"
    assert training_payloads[0]["quality"] == "high"
    assert training_payloads[0]["gender"] == "woman"
    assert len(training_payloads[0]["image_urls"]) == 8
    assert creations[0]["is_active"] is False
    assert status_updates[0]["provider_training_task_id"] == "task-123"


def test_upload_actor_identity_rejects_too_few_images():
    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor",
        data={
            "name": "AYRA Actor Identity",
            "quality": "high",
            "gender": "woman",
            "consent_source": "operator",
        },
        files=[
            ("training_images", ("img1.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img2.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img3.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img4.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img5.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img6.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img7.png", io.BytesIO(_png_bytes()), "image/png")),
        ],
        follow_redirects=False,
    )

    assert response.status_code == 422
