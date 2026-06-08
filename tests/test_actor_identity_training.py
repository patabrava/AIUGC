from __future__ import annotations

import io
import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "dummy")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://cdn.example.com")

from fastapi.testclient import TestClient

from app.features.characters.schemas import ActorTrainingSet
from app.main import app


def _urls(count: int) -> list[str]:
    return [f"https://cdn.example.com/actor/{idx}.png" for idx in range(count)]


def test_actor_training_set_accepts_8_to_20_public_urls():
    assert len(ActorTrainingSet(images=_urls(8)).images) == 8
    assert len(ActorTrainingSet(images=_urls(20)).images) == 20


@pytest.mark.parametrize("count", [0, 3, 7, 21])
def test_actor_training_set_rejects_invalid_image_count(count):
    with pytest.raises(ValidationError):
        ActorTrainingSet(images=_urls(count))


def test_actor_training_set_rejects_non_public_urls():
    with pytest.raises(ValidationError):
        ActorTrainingSet(images=["/local/file.png"] * 8)


def test_ready_actor_identity_requires_completed_training():
    from app.features.characters.actor_identity import actor_identity_is_ready
    from app.features.characters.schemas import ActorIdentityRecord

    base = {
        "id": "actor-1",
        "name": "AYRA",
        "is_active": True,
        "provider": "magnific",
        "provider_lora_id": "110",
        "provider_lora_name": "ayra",
        "provider_training_task_id": "train-1",
        "training_status": "completed",
        "training_phase": "ready",
        "training_progress_percent": 100,
        "training_error": None,
        "training_images": [f"https://cdn.example.com/{idx}.png" for idx in range(8)],
        "created_at": "2026-05-20T00:00:00Z",
        "updated_at": "2026-05-20T00:00:00Z",
    }
    assert actor_identity_is_ready(ActorIdentityRecord.model_validate(base)) is True
    base["provider_lora_id"] = None
    assert actor_identity_is_ready(ActorIdentityRecord.model_validate(base)) is False


def test_actor_identity_primary_image_url_prefers_explicit_preview_fields():
    from app.features.characters.schemas import ActorIdentityRecord

    actor = ActorIdentityRecord(
        id="actor-1",
        name="AYRA",
        is_active=True,
        provider="magnific",
        provider_lora_id="110",
        provider_lora_name="ayra",
        provider_training_task_id="train-1",
        portrait_image_url="https://cdn.example.com/portrait.png",
        cover_image_url="https://cdn.example.com/cover.png",
        training_status="completed",
        training_phase="ready",
        training_progress_percent=100,
        training_error=None,
        training_images=["https://cdn.example.com/fallback.png"],
        consent_source="operator",
        created_at="2026-05-20T00:00:00Z",
        updated_at="2026-05-20T00:00:00Z",
    )

    assert actor.primary_image_url == "https://cdn.example.com/portrait.png"


def test_actor_identity_primary_image_url_falls_back_to_training_images():
    from app.features.characters.schemas import ActorIdentityRecord

    actor = ActorIdentityRecord(
        id="actor-2",
        name="AYRA",
        is_active=True,
        provider="magnific",
        provider_lora_id="110",
        provider_lora_name="ayra",
        provider_training_task_id="train-1",
        training_status="completed",
        training_phase="ready",
        training_progress_percent=100,
        training_error=None,
        training_images=["https://cdn.example.com/fallback.png"],
        consent_source="operator",
        created_at="2026-05-20T00:00:00Z",
        updated_at="2026-05-20T00:00:00Z",
    )

    assert actor.primary_image_url == "https://cdn.example.com/fallback.png"


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_actor_training_endpoint_uploads_public_urls_before_magnific(monkeypatch):
    from app.features.characters import handlers as character_handlers
    from app.adapters.magnific_client import MagnificTrainingStatus
    from app.features.characters.schemas import ActorIdentityRecord

    uploaded_urls = []
    submitted = {}

    class _FakeStorage:
        def upload_image(self, **kwargs):
            url = f"https://cdn.example.com/training/{kwargs['file_name']}"
            uploaded_urls.append(url)
            return {"url": url, "storage_key": f"images/{kwargs['file_name']}"}

    class _FakeMagnific:
        def train_character_lora(self, **kwargs):
            submitted.update(kwargs)
            return MagnificTrainingStatus(
                raw_status="in_progress",
                phase="training",
                progress_percent=50,
                provider_training_task_id="train-task-1",
                provider_lora_name="AYRA",
            )

    def fake_create(**kwargs):
        return ActorIdentityRecord(
            id="actor-1",
            name=kwargs["name"],
            is_active=kwargs.get("is_active", False),
            provider="magnific",
            training_status="not_started",
            training_phase="not_started",
            training_progress_percent=0,
            training_images=kwargs["training_images"],
            consent_source=kwargs["consent_source"],
            created_at="2026-05-20T00:00:00Z",
            updated_at="2026-05-20T00:00:00Z",
        )

    updated = {}
    monkeypatch.setattr(character_handlers, "get_storage_client", lambda: _FakeStorage())
    monkeypatch.setattr("app.adapters.magnific_client.get_magnific_client", lambda: _FakeMagnific())
    monkeypatch.setattr(character_handlers.character_queries, "create_actor_identity", fake_create)
    monkeypatch.setattr(character_handlers.character_queries, "get_actor_identity_by_id", lambda actor_id: fake_create(name="AYRA", training_images=uploaded_urls, consent_source="owned training set", is_active=False, correlation_id="test") if actor_id == "actor-1" else None)
    monkeypatch.setattr(character_handlers.character_queries, "update_actor_training_status", lambda **kwargs: updated.update(kwargs))

    files = [
        ("training_images", (f"actor-{idx}.png", io.BytesIO(_png_bytes()), "image/png"))
        for idx in range(8)
    ]
    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor",
        data={
            "name": "AYRA",
            "gender": "female",
            "quality": "high",
            "consent_source": "owned training set",
        },
        files=files,
        follow_redirects=False,
    )

    assert response.status_code in {200, 303}, response.text
    assert len(uploaded_urls) == 8
    assert submitted["image_urls"] == uploaded_urls
    assert updated["provider_training_task_id"] == "train-task-1"
