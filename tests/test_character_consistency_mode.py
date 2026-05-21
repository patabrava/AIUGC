from __future__ import annotations

import base64
import os

import pytest
from pydantic import ValidationError as PydanticValidationError

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "dummy")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "dummy")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://cdn.example.com")

from app.features.batches.schemas import CreateBatchRequest


def test_creation_mode_accepts_character_consistency():
    payload = CreateBatchRequest.model_validate(
        {
            "brand": "Test Brand",
            "creation_mode": "character_consistency",
            "post_type_counts": {"value": 1, "lifestyle": 1, "product": 1},
        }
    )

    assert payload.creation_mode == "character_consistency"


def test_creation_mode_rejects_unknown_value():
    with pytest.raises(PydanticValidationError):
        CreateBatchRequest.model_validate(
            {
                "brand": "Test Brand",
                "creation_mode": "bogus",
                "post_type_counts": {"value": 1, "lifestyle": 1, "product": 1},
            }
        )


def test_create_batch_snapshots_active_character(monkeypatch):
    from app.features.batches import queries as batch_queries
    from app.features.characters.schemas import CharacterRecord

    captured = {}

    monkeypatch.setattr(
        batch_queries,
        "get_active_character",
        lambda: CharacterRecord(
            id="char-1",
            name="Snapshot Test",
            front_image_url="https://cdn/front.png",
            three_quarter_image_url="https://cdn/3q.png",
            profile_image_url="https://cdn/profile.png",
            is_active=True,
            created_at="2026-05-08T00:00:00Z",
            updated_at="2026-05-08T00:00:00Z",
        ),
    )
    def fake_insert(payload, legacy_payload=None):
        captured["payload"] = payload
        return {"id": "batch-1", **payload}

    monkeypatch.setattr(batch_queries, "_insert_batch_row", fake_insert)

    batch_queries.create_batch(
        brand="Test",
        post_type_counts={"value": 1, "lifestyle": 1, "product": 1},
        creation_mode="character_consistency",
    )

    assert captured["payload"]["creation_mode"] == "character_consistency"
    assert captured["payload"]["character_snapshot"]["character_id"] == "char-1"
    assert captured["payload"]["character_snapshot"]["front_image_url"] == "https://cdn/front.png"


def test_character_mode_without_active_character_raises(monkeypatch):
    from app.core.errors import ValidationError
    from app.features.batches import queries as batch_queries

    monkeypatch.setattr(batch_queries, "get_active_character", lambda: None)

    with pytest.raises(ValidationError) as exc:
        batch_queries.create_batch(
            brand="Test",
            post_type_counts={"value": 1, "lifestyle": 0, "product": 0},
            creation_mode="character_consistency",
        )

    assert "character" in exc.value.message.lower()


def test_scene_and_negative_prompt_helpers(monkeypatch):
    from app.features.posts import prompt_builder

    class _FakeLLM:
        def generate_json(self, *args, **kwargs):
            return {"value": "kitchen", "lifestyle": "cafe", "product": "office"}

    monkeypatch.setattr(prompt_builder, "_get_llm_client", lambda: _FakeLLM())

    assert prompt_builder.propose_scene_plan(
        brand="Brand",
        topic_titles=["A"],
        correlation_id="corr",
    ) == {"value": "kitchen", "lifestyle": "cafe", "product": "office"}
    assert prompt_builder.resolve_scene_for_post(
        post_type="product",
        scene_plan={"value": "kitchen", "lifestyle": "cafe", "product": "office"},
        override=None,
    ) == "office"
    assert prompt_builder.resolve_scene_for_post(
        post_type="value",
        scene_plan={"value": "kitchen"},
        override="custom",
    ) == "custom"
    assert "different room" not in prompt_builder.build_negative_prompt(
        creation_mode="character_consistency",
        is_extension=False,
    )
    assert "different room" in prompt_builder.build_negative_prompt(
        creation_mode="character_consistency",
        is_extension=True,
    )


def test_reference_image_wrapper_does_not_override_legacy_character_prompt():
    from app.features.posts import prompt_builder

    prompt = prompt_builder.build_video_prompt_from_seed(
        {
            "script": "Ein ruhiger Satz fuer den Test.",
            "character": (
                "Same person as the uploaded @ayra character reference images: "
                "38-year-old German woman with shoulder-length light brown hair."
            ),
        }
    )

    assert "Same person as the uploaded" not in prompt["veo_prompt"]
    assert prompt_builder.DEFAULT_CHARACTER in prompt["veo_prompt"]


def test_character_consistency_prompt_uses_legacy_short_character():
    from app.features.posts import prompt_builder

    prompt = prompt_builder.build_video_prompt_from_seed(
        {
            "script": "Ein ruhiger Satz fuer den Test.",
            "character": (
                "Same person as the uploaded @ayra character reference images: "
                "38-year-old German woman with shoulder-length light brown hair."
            ),
        },
        use_legacy_short_character=True,
    )

    assert "Same person as the uploaded" not in prompt["veo_prompt"]
    assert prompt_builder.LEGACY_SHORT_CHARACTER in prompt["veo_prompt"]


def test_32s_extended_base_prompt_uses_legacy_short_character():
    from app.features.posts import prompt_builder
    from app.features.videos import handlers as video_handlers

    prompt_text, _ = video_handlers._build_veo_extended_base_prompt(
        {
            "script": (
                "Heute beginnt mit einem ruhigen Blick auf das, was direkt vor dir liegt, nicht auf alles gleichzeitig. "
                "Du wählst eine Aufgabe, machst sie sichtbar kleiner, und gibst dir genug Zeit, ohne dich innerlich zu hetzen. "
                "Wenn etwas stockt, ist das kein Beweis gegen dich, sondern nur ein Hinweis, den Schritt anzupassen. "
                "Genau so entsteht wieder Sicherheit: durch klare Wiederholung, freundliche Grenzen, und einen Alltag, der dich nicht permanent überfordert."
            )
        },
        None,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert prompt_builder.LEGACY_SHORT_CHARACTER in prompt_text
    assert "Same person as the uploaded" not in prompt_text


def test_select_veo_model_for_character_consistency_uses_full_model(monkeypatch):
    from app.adapters import veo_client

    monkeypatch.setattr(veo_client, "_resolve_default_veo_model", lambda: "veo-3.1-fast-generate-preview")

    assert veo_client.select_veo_model_id(creation_mode="character_consistency") == veo_client._VEO_MODEL_ID
    assert veo_client.select_veo_model_id(creation_mode="automated") == "veo-3.1-fast-generate-preview"


def test_load_character_snapshot_assets_uses_snapshot_urls(monkeypatch):
    from app.features.videos import handlers as video_handlers

    fetched = []
    monkeypatch.setattr(
        video_handlers,
        "_download_image_bytes",
        lambda url: fetched.append(url) or b"bytes-" + url.encode("utf-8"),
    )

    bundle = video_handlers._load_character_snapshot_assets(
        snapshot={
            "character_id": "char-1",
            "name": "Test",
            "front_image_url": "https://cdn/front.png",
            "three_quarter_image_url": "https://cdn/3q.png",
            "profile_image_url": "https://cdn/profile.png",
        },
        correlation_id="corr",
    )

    assert fetched == ["https://cdn/front.png", "https://cdn/3q.png", "https://cdn/profile.png"]
    assert [item["mime_type"] for item in bundle["reference_images"]] == ["image/png", "image/png", "image/png"]
    assert base64.b64decode(bundle["reference_images"][0]["data_base64"]).startswith(b"bytes-https://cdn/front")
    assert bundle["metadata"]["reference_images_enabled"] is True
    assert bundle["metadata"]["character_id"] == "char-1"


def test_character_consistency_legacy_veo_request_aliases_to_vertex():
    from app.features.videos import handlers as video_handlers

    plan = video_handlers._resolve_video_submission_plan(
        batch={
            "id": "batch-1",
            "creation_mode": "character_consistency",
            "target_length_tier": 8,
            "video_pipeline_route": "short",
        },
        requested_provider="veo_3_1",
        requested_seconds=8,
        aspect_ratio="9:16",
        resolution="720p",
        size=None,
    )

    assert plan["provider"] == "vertex_ai"


def test_submit_video_request_attaches_character_reference_images_to_vertex(monkeypatch):
    from app.features.videos import handlers as video_handlers

    captured = {}

    class FakeVertexClient:
        def submit_text_video(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/ref-test",
                "status": "submitted",
                "provider_model": kwargs.get("model") or "veo-3.1-generate-001",
            }

    monkeypatch.setattr(video_handlers, "get_vertex_ai_client", lambda: FakeVertexClient())
    monkeypatch.setattr(
        video_handlers,
        "get_settings",
        lambda: type("S", (), {"vertex_ai_output_gcs_uri": "gs://bucket/out/"})(),
    )
    monkeypatch.setattr(video_handlers, "_download_image_bytes", lambda url: b"image-" + url.encode("utf-8"))

    result = video_handlers._submit_video_request(
        provider="vertex_ai",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr-vertex-ref",
        provider_duration_seconds=8,
        creation_mode="character_consistency",
        character_snapshot={
            "character_id": "char-1",
            "name": "Test Character",
            "front_image_url": "https://cdn/front.png",
            "three_quarter_image_url": "https://cdn/three-quarter.png",
            "profile_image_url": "https://cdn/profile.png",
        },
    )

    assert captured["output_gcs_uri"] == "gs://bucket/out/"
    assert len(captured["reference_images"]) == 3
    assert base64.b64decode(captured["reference_images"][0]["data_base64"]) == b"image-https://cdn/front.png"
    assert result["provider_metadata"]["reference_images_enabled"] is True
    assert result["provider_metadata"]["reference_image_count"] == 3


def test_submit_video_request_skips_character_reference_images_for_vertex_4s_base(monkeypatch):
    from app.features.videos import handlers as video_handlers

    captured = {}

    class FakeVertexClient:
        def submit_text_video(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/ref-skip",
                "status": "submitted",
                "provider_model": "veo-3.1-generate-001",
            }

    monkeypatch.setattr(video_handlers, "get_vertex_ai_client", lambda: FakeVertexClient())
    monkeypatch.setattr(
        video_handlers,
        "get_settings",
        lambda: type("S", (), {"vertex_ai_output_gcs_uri": "gs://bucket/out/"})(),
    )
    monkeypatch.setattr(
        video_handlers,
        "_download_image_bytes",
        lambda url: pytest.fail("4s Vertex reference base must not download reference images"),
    )

    result = video_handlers._submit_video_request(
        provider="vertex_ai",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=32,
        size=None,
        correlation_id="corr-vertex-4s-ref-skip",
        provider_duration_seconds=4,
        creation_mode="character_consistency",
        character_snapshot={
            "character_id": "char-1",
            "name": "Test Character",
            "front_image_url": "https://cdn/front.png",
            "three_quarter_image_url": "https://cdn/three-quarter.png",
            "profile_image_url": "https://cdn/profile.png",
        },
    )

    assert captured["reference_images"] is None
    assert result["provider_metadata"]["reference_images_enabled"] is False
    assert result["provider_metadata"]["reference_images_skipped_reason"] == "vertex_reference_images_support_only_8s_base"
    assert result["provider_metadata"]["character_id"] == "char-1"
