from __future__ import annotations

import io
import os
from types import SimpleNamespace

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

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def maybe_single(self):
        return self

    def insert(self, payload):
        self.payload = payload
        self.rows.append(payload)
        return self

    def update(self, payload):
        self.payload = payload
        if self.rows:
            self.rows[0].update(payload)
        return self

    def execute(self):
        if self.filters:
            rows = [row for row in self.rows if all(row.get(key) == value for key, value in self.filters)]
        else:
            rows = self.rows
        return _FakeResponse(rows[0] if rows else None)


def _fake_supabase(rows):
    table = _FakeTable(rows)
    return SimpleNamespace(client=SimpleNamespace(table=lambda _name: table))


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


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
    upserts = []

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

    def _fake_upsert(**kwargs):
        upserts.append(kwargs)
        return ActorIdentityRecord(
            id="actor-1",
            name=kwargs["name"],
            is_active=True,
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
    monkeypatch.setattr(character_handlers.character_queries, "upsert_active_actor_identity", _fake_upsert)
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
    assert len(upserts) >= 2
    assert training_payloads[0]["name"] == "AYRA Actor Identity"
    assert training_payloads[0]["quality"] == "high"
    assert training_payloads[0]["gender"] == "woman"
    assert len(training_payloads[0]["image_urls"]) == 8
    assert any(item.get("provider_training_task_id") == "task-123" for item in upserts)


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
