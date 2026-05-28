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


def _set_gate(reference_set_id: str = "set-1") -> dict:
    return {
        "status": "passed",
        "reason": "ok",
        "gate_type": "manual",
        "details": {
            "scene_consistency_set_approved": True,
            "reference_set_id": reference_set_id,
        },
    }


def test_creation_mode_accepts_character_consistency():
    payload = CreateBatchRequest.model_validate(
        {
            "brand": "Test Brand",
            "creation_mode": "character_consistency",
            "post_type_counts": {"value": 1, "lifestyle": 1, "product": 1},
        }
    )

    assert payload.creation_mode == "character_consistency"


def test_creation_mode_accepts_character_consistency_light():
    payload = CreateBatchRequest.model_validate(
        {
            "brand": "Test Brand",
            "creation_mode": "character_consistency_light",
            "post_type_counts": {"value": 1, "lifestyle": 1, "product": 1},
        }
    )

    assert payload.creation_mode == "character_consistency_light"


def test_creation_mode_accepts_character_consistency_mid():
    payload = CreateBatchRequest.model_validate(
        {
            "brand": "Test Brand",
            "creation_mode": "character_consistency_mid",
            "post_type_counts": {"value": 1, "lifestyle": 1, "product": 1},
        }
    )

    assert payload.creation_mode == "character_consistency_mid"


def test_creation_mode_accepts_manual_character_consistency():
    payload = CreateBatchRequest.model_validate(
        {
            "brand": "Test Brand",
            "creation_mode": "manual_character_consistency",
            "manual_post_count": 3,
        }
    )

    assert payload.creation_mode == "manual_character_consistency"
    assert payload.manual_post_count == 3
    assert payload.post_type_counts is None


def test_creation_mode_rejects_unknown_value():
    with pytest.raises(PydanticValidationError):
        CreateBatchRequest.model_validate(
            {
                "brand": "Test Brand",
                "creation_mode": "bogus",
                "post_type_counts": {"value": 1, "lifestyle": 1, "product": 1},
            }
        )


def _ready_actor_identity():
    from app.features.characters.schemas import ActorIdentityRecord

    return ActorIdentityRecord(
        id="actor-1",
        name="AYRA",
        is_active=True,
        provider="magnific",
        provider_lora_id="110",
        provider_lora_name="ayra",
        provider_training_task_id="train-1",
        training_status="completed",
        training_phase="ready",
        training_progress_percent=100,
        training_images=[f"https://cdn.example.com/{idx}.png" for idx in range(8)],
        created_at="2026-05-20T00:00:00Z",
        updated_at="2026-05-20T00:00:00Z",
        training_completed_at="2026-05-20T00:00:00Z",
    )


def test_create_batch_snapshots_ready_actor_identity(monkeypatch):
    from app.features.batches import queries as batch_queries

    captured = {}

    monkeypatch.setattr(batch_queries, "get_active_actor_identity", _ready_actor_identity)
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
    assert captured["payload"]["actor_identity_id"] == "actor-1"
    assert captured["payload"]["actor_identity_snapshot"]["provider_lora_id"] == "110"
    assert captured["payload"]["character_snapshot"] is None


def test_create_light_batch_snapshots_ready_actor_identity(monkeypatch):
    from app.features.batches import queries as batch_queries

    captured = {}

    monkeypatch.setattr(batch_queries, "get_active_actor_identity", _ready_actor_identity)

    def fake_insert(payload, legacy_payload=None):
        captured["payload"] = payload
        return {"id": "batch-1", **payload}

    monkeypatch.setattr(batch_queries, "_insert_batch_row", fake_insert)

    batch_queries.create_batch(
        brand="Test",
        post_type_counts={"value": 1, "lifestyle": 0, "product": 0},
        creation_mode="character_consistency_light",
    )

    assert captured["payload"]["creation_mode"] == "character_consistency_light"
    assert captured["payload"]["actor_identity_id"] == "actor-1"
    assert captured["payload"]["actor_identity_snapshot"]["provider_lora_id"] == "110"
    assert captured["payload"]["character_snapshot"] is None


def test_create_mid_batch_snapshots_ready_actor_identity(monkeypatch):
    from app.features.batches import queries as batch_queries

    captured = {}

    monkeypatch.setattr(batch_queries, "get_active_actor_identity", _ready_actor_identity)

    def fake_insert(payload, legacy_payload=None):
        captured["payload"] = payload
        return {"id": "batch-1", **payload}

    monkeypatch.setattr(batch_queries, "_insert_batch_row", fake_insert)

    batch_queries.create_batch(
        brand="Test",
        post_type_counts={"value": 1, "lifestyle": 0, "product": 0},
        creation_mode="character_consistency_mid",
    )

    assert captured["payload"]["creation_mode"] == "character_consistency_mid"
    assert captured["payload"]["actor_identity_id"] == "actor-1"
    assert captured["payload"]["actor_identity_snapshot"]["provider_lora_id"] == "110"
    assert captured["payload"]["character_snapshot"] is None


def test_create_manual_character_consistency_batch_snapshots_ready_actor_identity(monkeypatch):
    from app.features.batches import queries as batch_queries
    from app.features.characters.actor_identity import is_character_consistency_mode, is_manual_creation_mode

    captured = {}

    monkeypatch.setattr(batch_queries, "get_active_actor_identity", _ready_actor_identity)

    def fake_insert(payload, legacy_payload=None):
        captured["payload"] = payload
        return {"id": "batch-1", **payload}

    monkeypatch.setattr(batch_queries, "_insert_batch_row", fake_insert)

    batch = batch_queries.create_batch(
        brand="Test",
        post_type_counts=None,
        creation_mode="manual_character_consistency",
        manual_post_count=3,
    )

    assert is_manual_creation_mode(batch["creation_mode"]) is True
    assert is_character_consistency_mode(batch["creation_mode"]) is True
    assert captured["payload"]["creation_mode"] == "manual_character_consistency"
    assert captured["payload"]["manual_post_count"] == 3
    assert captured["payload"]["post_type_counts"] == {}
    assert captured["payload"]["actor_identity_id"] == "actor-1"
    assert captured["payload"]["actor_identity_snapshot"]["provider_lora_id"] == "110"
    assert captured["payload"]["character_snapshot"] is None


def test_character_consistency_requires_ready_actor_identity_for_new_batches(monkeypatch):
    from app.core.errors import ValidationError
    from app.features.batches import queries as batch_queries

    monkeypatch.setattr(batch_queries, "get_active_actor_identity", lambda: None)

    with pytest.raises(ValidationError) as exc:
        batch_queries.create_batch(
            brand="Test",
            post_type_counts={"value": 1, "lifestyle": 0, "product": 0},
            creation_mode="character_consistency",
        )

    assert "/settings/actor" in exc.value.message
    assert "select a ready actor" in exc.value.message.lower()


def test_manual_character_consistency_requires_ready_actor_identity_for_new_batches(monkeypatch):
    from app.core.errors import ValidationError
    from app.features.batches import queries as batch_queries

    monkeypatch.setattr(batch_queries, "get_active_actor_identity", lambda: None)

    with pytest.raises(ValidationError) as exc:
        batch_queries.create_batch(
            brand="Test",
            post_type_counts=None,
            manual_post_count=3,
            creation_mode="manual_character_consistency",
        )

    assert "/settings/actor" in exc.value.message
    assert "select a ready actor" in exc.value.message.lower()


def test_existing_legacy_character_snapshot_batches_remain_valid():
    from app.features.characters.actor_identity import resolve_character_consistency_source

    source = resolve_character_consistency_source(
        batch={
            "id": "batch-legacy",
            "creation_mode": "character_consistency",
            "character_snapshot": {"character_id": "char-1", "front_image_url": "https://cdn/front.png"},
        }
    )
    assert source["source"] == "legacy_character_snapshot"


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


def test_character_consistency_prompt_omits_scene_text_when_reference_images_define_scene():
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
        prompt_style="character_consistency",
        scene_plan={
            "value": "A generated kitchen scene that must not be submitted.",
            "lifestyle": "A generated cafe scene that must not be submitted.",
            "product": "A generated office scene that must not be submitted.",
        },
    )

    assert prompt["prompt_style"] == "character_consistency"
    assert "Scene:\n" not in prompt["veo_prompt"]
    assert "A generated kitchen scene" not in prompt["veo_prompt"]
    assert prompt_builder.DEFAULT_SCENE_BODY not in prompt["veo_prompt"]
    assert prompt_builder.LEGACY_SCENE_BODY not in prompt["veo_prompt"]
    assert "approved reference images" in prompt["veo_prompt"]
    assert "Ein ruhiger Satz fuer den Test." in prompt["veo_prompt"]


def test_manual_character_consistency_prompt_omits_scene_text_when_reference_images_define_scene():
    from app.features.posts import prompt_builder

    prompt = prompt_builder.build_video_prompt_from_seed(
        {"script": "Ein manueller Satz fuer den Test."},
        use_legacy_short_character=True,
        prompt_style="manual_character_consistency",
        scene_override="A manually entered bedroom scene that must not be submitted.",
    )

    assert prompt["prompt_style"] == "manual_character_consistency"
    assert "Scene:\n" not in prompt["veo_prompt"]
    assert "A manually entered bedroom scene" not in prompt["veo_prompt"]
    assert prompt_builder.DEFAULT_SCENE_BODY not in prompt["veo_prompt"]
    assert "approved reference images" in prompt["veo_prompt"]
    assert "Ein manueller Satz fuer den Test." in prompt["veo_prompt"]


def test_character_consistency_mid_prompt_omits_scene_block_when_reference_images_define_scene():
    from app.features.posts import prompt_builder

    prompt = prompt_builder.build_video_prompt_from_seed(
        {"script": "Ein Mid Satz fuer den Test."},
        use_legacy_short_character=True,
        prompt_style="character_consistency_mid",
    )

    assert prompt["prompt_style"] == "character_consistency_mid"
    assert "Scene:\n" not in prompt["veo_prompt"]
    assert "Match the approved reference images" not in prompt["veo_prompt"]
    assert prompt_builder.DEFAULT_SCENE_BODY not in prompt["veo_prompt"]
    assert "approved reference images" in prompt["veo_prompt"]
    assert "Ein Mid Satz fuer den Test." in prompt["veo_prompt"]


def test_character_consistency_prompt_sync_removes_legacy_scene_text():
    from app.features.posts import prompt_builder

    existing_prompt = prompt_builder.build_video_prompt_from_seed(
        {"script": "Ein alter Satz fuer den Test."},
        prompt_style="standard",
    )
    existing_prompt["prompt_style"] = "character_consistency"
    existing_prompt["scene"] = "Scene: A legacy bedroom scene that must not be submitted."
    existing_prompt["veo_prompt"] = (
        "Character:\nOld character\n\n"
        "Scene:\nA legacy bedroom scene that must not be submitted.\n\n"
        "Dialogue:\nEin alter Satz fuer den Test."
    )

    synced = prompt_builder.sync_video_prompt_with_seed_data(
        existing_prompt,
        {"script": "Ein neuer Satz fuer den Test."},
        use_legacy_short_character=True,
    )

    assert synced["prompt_style"] == "character_consistency"
    assert "Scene:\n" not in synced["veo_prompt"]
    assert "legacy bedroom scene" not in synced["veo_prompt"]
    assert "approved reference images" in synced["veo_prompt"]
    assert "Ein neuer Satz fuer den Test." in synced["veo_prompt"]


def test_character_consistency_light_prompt_uses_reference_image_motion_prompt():
    from app.features.posts import prompt_builder

    prompt = prompt_builder.build_video_prompt_from_seed(
        {
            "script": "Ein erster Satz. Ein zweiter Satz.",
            "character": (
                "Same person as the uploaded @ayra character reference images: "
                "38-year-old German woman with shoulder-length light brown hair."
            ),
        },
        prompt_style="character_consistency_light",
    )

    assert prompt["prompt_style"] == "character_consistency_light"
    assert "The referenced woman sits in the referenced wheelchair setup" in prompt["veo_prompt"]
    assert "Keep her identity, wardrobe, room, lighting, camera distance, and framing matched to the reference images." in prompt["veo_prompt"]
    assert "38-year-old German woman" not in prompt["veo_prompt"]
    assert "Scene:" not in prompt["veo_prompt"]
    assert '"Ein erster Satz' not in prompt["veo_prompt"]


def test_character_consistency_mid_prompt_uses_stripped_scene_block():
    from app.features.posts import prompt_builder

    prompt = prompt_builder.build_video_prompt_from_seed(
        {
            "script": "Ein erster Satz. Ein zweiter Satz.",
            "character": (
                "Same person as the uploaded @ayra character reference images: "
                "38-year-old German woman with shoulder-length light brown hair."
            ),
        },
        use_legacy_short_character=True,
        prompt_style="character_consistency_mid",
    )

    assert prompt["prompt_style"] == "character_consistency_mid"
    assert "Character:" in prompt["veo_prompt"]
    assert prompt_builder.LEGACY_SHORT_CHARACTER in prompt["veo_prompt"]
    assert "Scene:\n" not in prompt["veo_prompt"]
    assert "approved reference images" in prompt["veo_prompt"]
    assert "A modern, tidy bedroom with blush-pink walls" not in prompt["veo_prompt"]
    assert "Dialogue:\nEin erster Satz. Ein zweiter Satz." in prompt["veo_prompt"]


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


def test_light_extended_base_prompt_uses_reference_image_motion_prompt():
    from app.features.videos import handlers as video_handlers

    prompt_text, metadata = video_handlers._build_veo_extended_base_prompt(
        {
            "script": (
                "Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht. "
                "Mit einer klaren Routine bleibst du im Alltag trotzdem deutlich entspannter. "
                "So bleibt dein Tag klarer und planbarer."
            ),
        },
        None,
        planned_extension_hops=1,
        target_length_tier=16,
        creation_mode="character_consistency_light",
    )

    assert "The referenced woman sits in the referenced wheelchair setup" in prompt_text
    assert "Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht." in prompt_text
    assert "So bleibt dein Tag klarer und planbarer." not in prompt_text
    assert "Scene:" not in prompt_text
    assert "38-year-old German woman" not in prompt_text
    assert metadata["veo_segments_total"] == 2


def test_mid_extended_base_prompt_uses_stripped_scene_block():
    from app.features.videos import handlers as video_handlers

    prompt_text, metadata = video_handlers._build_veo_extended_base_prompt(
        {
            "script": (
                "Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht. "
                "Mit einer klaren Routine bleibst du im Alltag trotzdem deutlich entspannter. "
                "So bleibt dein Tag klarer und planbarer."
            ),
        },
        None,
        planned_extension_hops=1,
        target_length_tier=16,
        creation_mode="character_consistency_mid",
    )

    assert "Character:" in prompt_text
    assert "Scene:\n" not in prompt_text
    assert "approved reference images" in prompt_text
    assert "A modern, tidy bedroom with blush-pink walls" not in prompt_text
    assert "Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht." in prompt_text
    assert "So bleibt dein Tag klarer und planbarer." not in prompt_text
    assert metadata["veo_segments_total"] == 2


def test_character_consistency_extended_base_prompt_omits_scene_text():
    from app.features.posts import prompt_builder
    from app.features.videos import handlers as video_handlers

    script = (
        "Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht. "
        "Mit einer klaren Routine bleibst du im Alltag trotzdem deutlich entspannter. "
        "So bleibt dein Tag klarer und planbarer."
    )
    video_prompt = prompt_builder.build_video_prompt_from_seed(
        {"script": script},
        prompt_style="character_consistency",
        use_legacy_short_character=True,
    )
    video_prompt["scene"] = "Scene: A stored scene that must not be submitted."

    prompt_text, metadata = video_handlers._build_veo_extended_base_prompt(
        {"script": script, "target_length_tier": 16},
        video_prompt,
        planned_extension_hops=1,
        target_length_tier=16,
        creation_mode="character_consistency",
    )

    assert metadata["veo_segments_total"] >= 2
    assert "Scene:\n" not in prompt_text
    assert "stored scene" not in prompt_text
    assert "approved reference images" in prompt_text
    assert "Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht." in prompt_text


def test_select_veo_model_for_character_consistency_uses_full_model(monkeypatch):
    from app.adapters import veo_client

    monkeypatch.setattr(veo_client, "_resolve_default_veo_model", lambda: "veo-3.1-fast-generate-preview")

    assert veo_client.select_veo_model_id(creation_mode="character_consistency") == veo_client._VEO_MODEL_ID
    assert veo_client.select_veo_model_id(creation_mode="character_consistency_mid") == veo_client._VEO_MODEL_ID
    assert veo_client.select_veo_model_id(creation_mode="character_consistency_light") == veo_client._VEO_MODEL_ID
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


def test_character_consistency_light_16s_uses_eight_second_reference_base():
    from app.features.videos import handlers as video_handlers

    plan = video_handlers._resolve_video_submission_plan(
        batch={
            "id": "batch-1",
            "creation_mode": "character_consistency_light",
            "target_length_tier": 16,
        },
        requested_provider="veo_3_1",
        requested_seconds=16,
        aspect_ratio="9:16",
        resolution="720p",
        size=None,
    )

    assert plan["provider"] == "vertex_ai"
    assert plan["profile"].target_length_tier == 16
    assert plan["profile"].veo_base_seconds == 8
    assert plan["profile"].veo_extension_hops == 1


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


def test_actor_identity_batch_blocks_video_without_approved_scene_reference():
    from app.core.errors import FlowForgeException
    from app.features.characters.actor_identity import ensure_video_scene_reference_ready

    batch = {"id": "batch-1", "creation_mode": "character_consistency", "actor_identity_id": "actor-1"}
    post = {"id": "post-1", "batch_id": "batch-1", "scene_reference_image_id": None}
    with pytest.raises(FlowForgeException) as exc:
        ensure_video_scene_reference_ready(batch=batch, post=post, scene_reference=None, route="short")
    assert "approved SceneReferenceImage" in exc.value.message


def test_actor_identity_batch_blocks_video_without_complete_reference_set():
    from app.core.errors import FlowForgeException
    from app.features.characters.actor_identity import ensure_video_scene_reference_set_ready
    from app.features.characters.schemas import SceneReferenceSetSummary

    batch = {"id": "batch-1", "creation_mode": "character_consistency", "actor_identity_id": "actor-1"}
    post = {"id": "post-1", "batch_id": "batch-1"}
    summary = SceneReferenceSetSummary.from_rows(
        post_id="post-1",
        reference_set_id="set-1",
        rows=[
            {
                "id": "ref-1",
                "status": "approved",
                "image_url": "https://cdn.example.com/front.png",
                "provider_metadata": {"angle_key": "front_mid"},
                "identity_gate_result": _set_gate(),
            }
        ],
    )

    with pytest.raises(FlowForgeException) as exc:
        ensure_video_scene_reference_set_ready(batch=batch, post=post, scene_reference_set=summary, route="short")

    assert "three approved SceneReferenceImages" in exc.value.message


def test_light_actor_identity_batch_blocks_video_without_complete_reference_set():
    from app.core.errors import FlowForgeException
    from app.features.characters.actor_identity import ensure_video_scene_reference_set_ready

    batch = {"id": "batch-1", "creation_mode": "character_consistency_light", "actor_identity_id": "actor-1"}
    post = {"id": "post-1", "batch_id": "batch-1"}
    with pytest.raises(FlowForgeException) as exc:
        ensure_video_scene_reference_set_ready(batch=batch, post=post, scene_reference_set=None, route="extended")

    assert "three approved SceneReferenceImages" in exc.value.message


def test_mid_actor_identity_batch_blocks_video_without_complete_reference_set():
    from app.core.errors import FlowForgeException
    from app.features.characters.actor_identity import ensure_video_scene_reference_set_ready

    batch = {"id": "batch-1", "creation_mode": "character_consistency_mid", "actor_identity_id": "actor-1"}
    post = {"id": "post-1", "batch_id": "batch-1"}
    with pytest.raises(FlowForgeException) as exc:
        ensure_video_scene_reference_set_ready(batch=batch, post=post, scene_reference_set=None, route="extended")

    assert "three approved SceneReferenceImages" in exc.value.message


def test_submit_video_request_attaches_actor_scene_reference_to_vertex(monkeypatch):
    from app.features.videos import handlers as video_handlers

    captured = {}

    class FakeVertexClient:
        def submit_text_video(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/actor-ref",
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
        correlation_id="corr-actor-ref",
        provider_duration_seconds=8,
        creation_mode="character_consistency",
        scene_reference={
            "id": "scene-1",
            "actor_identity_id": "actor-1",
            "image_url": "https://cdn/scene.png",
            "scene_key": "bathroom_adaptation",
            "wardrobe_key": "everyday_sweater",
            "identity_gate_result": {"status": "passed"},
            "status": "approved",
        },
    )

    assert len(captured["reference_images"]) == 1
    assert base64.b64decode(captured["reference_images"][0]["data_base64"]) == b"image-https://cdn/scene.png"
    assert result["provider_metadata"]["source"] == "actor_identity_scene_reference"
    assert result["provider_metadata"]["scene_reference_image_id"] == "scene-1"


def test_submit_video_request_attaches_three_actor_scene_references_to_vertex(monkeypatch):
    from app.features.characters.schemas import SceneReferenceSetSummary
    from app.features.videos import handlers as video_handlers

    captured = {}

    class FakeVertexClient:
        def submit_text_video(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/actor-ref-set",
                "status": "submitted",
                "provider_model": kwargs.get("model") or "veo-3.1-generate-001",
            }

    monkeypatch.setattr(video_handlers, "get_vertex_ai_client", lambda: FakeVertexClient())
    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"vertex_ai_output_gcs_uri": "gs://bucket/out/"})())
    monkeypatch.setattr(video_handlers, "_download_image_bytes", lambda url: b"image-" + url.encode("utf-8"))

    reference_set = SceneReferenceSetSummary.from_rows(
        post_id="post-1",
        reference_set_id="set-1",
        rows=[
            {
                "id": "scene-front",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": "https://cdn/front.png",
                "scene_key": "bathroom_adaptation",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {"reference_set_id": "set-1", "angle_key": "front_mid"},
                "identity_gate_result": _set_gate(),
            },
            {
                "id": "scene-left",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": "https://cdn/left.png",
                "scene_key": "bathroom_adaptation",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {"reference_set_id": "set-1", "angle_key": "left_three_quarter"},
                "identity_gate_result": _set_gate(),
            },
            {
                "id": "scene-profile",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": "https://cdn/profile.png",
                "scene_key": "bathroom_adaptation",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {"reference_set_id": "set-1", "angle_key": "right_profile"},
                "identity_gate_result": _set_gate(),
            },
        ],
    )

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
        correlation_id="corr-actor-ref-set",
        provider_duration_seconds=8,
        creation_mode="character_consistency",
        scene_reference_set=reference_set,
    )

    assert len(captured["reference_images"]) == 3
    assert result["provider_metadata"]["source"] == "actor_identity_scene_reference_set"
    assert result["provider_metadata"]["scene_reference_image_ids"] == ["scene-front", "scene-left", "scene-profile"]
    assert result["provider_metadata"]["reference_image_count"] == 3


def test_character_consistency_provider_payload_keeps_reference_images_with_no_scene_prompt(monkeypatch):
    from app.features.characters.schemas import SceneReferenceSetSummary
    from app.features.videos import handlers as video_handlers

    captured = {}

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "op-reference-scene",
                "status": "queued",
                "provider_model": "veo-test",
            }

    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())
    monkeypatch.setattr(
        video_handlers,
        "_load_scene_reference_set_assets",
        lambda *, scene_reference_set, correlation_id: {
            "reference_images": [
                {"mime_type": "image/png", "data": "aaa", "description": "front"},
                {"mime_type": "image/png", "data": "bbb", "description": "left"},
                {"mime_type": "image/png", "data": "ccc", "description": "right"},
            ],
            "metadata": {
                "reference_images_enabled": True,
                "reference_image_count": 3,
                "source": "actor_identity_scene_reference_set",
            },
        },
    )

    scene_reference_set = SceneReferenceSetSummary(
        post_id="post-1",
        reference_set_id="set-1",
        actor_identity_id="actor-1",
        approved_rows=[
            {"id": "ref-1", "provider_metadata": {"angle_key": "front_mid"}},
            {"id": "ref-2", "provider_metadata": {"angle_key": "left_three_quarter"}},
            {"id": "ref-3", "provider_metadata": {"angle_key": "right_profile"}},
        ],
        missing_angle_keys=[],
    )

    result = video_handlers._submit_video_request(
        provider="veo_3_1",
        model=None,
        prompt_text=(
            "Action:\nUse the approved reference images as the only scene source.\n\n"
            "Dialogue:\nEin Satz."
        ),
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="1080p",
        seconds=8,
        size=None,
        correlation_id="test-reference-scene",
        provider_duration_seconds=8,
        creation_mode="character_consistency",
        character_snapshot=None,
        scene_reference_set=scene_reference_set,
    )

    assert result["operation_id"] == "op-reference-scene"
    assert captured["reference_images"] and len(captured["reference_images"]) == 3
    assert "Scene:\n" not in captured["prompt"]
    assert "approved reference images" in captured["prompt"]


def test_actor_scene_reference_download_follows_provider_redirects(monkeypatch):
    from app.features.videos import handlers as video_handlers

    captured = {}

    class FakeResponse:
        content = b"image-bytes"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(video_handlers.httpx, "get", fake_get)

    assert video_handlers._download_image_bytes("https://ai-statics.freepik.com/ref.png") == b"image-bytes"
    assert captured["follow_redirects"] is True


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
