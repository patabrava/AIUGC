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


def test_actor_reference_endpoint_persists_ranked_gemini_options_without_lora(monkeypatch):
    from app.features.characters import handlers as character_handlers
    from app.features.characters.schemas import ActorIdentityRecord

    uploaded_urls = []
    created = {}

    class _FakeStorage:
        def upload_image(self, **kwargs):
            url = f"https://cdn.example.com/training/{kwargs['file_name']}"
            uploaded_urls.append(url)
            return {"url": url, "storage_key": f"images/{kwargs['file_name']}"}

    def fake_create(**kwargs):
        created.update(kwargs)
        return ActorIdentityRecord(
            id="actor-1",
            name=kwargs["name"],
            is_active=kwargs.get("is_active", False),
            provider=kwargs["provider"],
            training_status=kwargs["training_status"],
            training_phase=kwargs["training_phase"],
            training_progress_percent=kwargs["training_progress_percent"],
            training_images=kwargs["training_images"],
            consent_source=kwargs["consent_source"],
            reference_front_image_url=kwargs["reference_front_image_url"],
            reference_three_quarter_image_url=kwargs["reference_three_quarter_image_url"],
            reference_generation_metadata=kwargs["reference_generation_metadata"],
            created_at="2026-05-20T00:00:00Z",
            updated_at="2026-05-20T00:00:00Z",
        )

    monkeypatch.setattr(character_handlers, "get_storage_client", lambda: _FakeStorage())
    monkeypatch.setattr(character_handlers.character_queries, "create_actor_identity", fake_create)
    monkeypatch.setattr(
        character_handlers,
        "generate_verified_three_quarter_reference",
        lambda *_args, **_kwargs: {
            "image_bytes": b"three-quarter-2",
            "mime_type": "image/png",
            "model": "gemini-3-pro-image",
            "identity_gate_result": {"passed": True, "confidence": 0.97},
            "selected_index": 2,
            "candidates": [
                {
                    "index": index,
                    "image_bytes": f"three-quarter-{index}".encode(),
                    "mime_type": "image/png",
                    "identity_gate_result": {
                        "passed": True,
                        "confidence": confidence,
                    },
                }
                for index, confidence in ((1, 0.93), (2, 0.97), (3, 0.95))
            ],
        },
    )

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor",
        data={
            "name": "AYRA",
            "consent_source": "owned training set",
        },
        files={
            "canonical_front_image": (
                "actor-front.png",
                io.BytesIO(_png_bytes()),
                "image/png",
            )
        },
        follow_redirects=False,
    )

    assert response.status_code == 303, response.text
    assert len(uploaded_urls) == 4
    assert created["provider"] == "gemini"
    assert created["provider_lora_id"] is None
    assert created["provider_lora_name"] is None
    assert created["provider_training_task_id"] is None
    assert created["training_phase"] == "ready"
    assert created["training_progress_percent"] == 100
    assert created["reference_front_image_url"] == uploaded_urls[0]
    assert created["reference_three_quarter_image_url"] == uploaded_urls[2]
    candidates = created["reference_generation_metadata"]["candidates"]
    assert len(candidates) == 3
    assert [candidate["selected"] for candidate in candidates] == [False, True, False]
