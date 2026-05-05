import base64
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.core.config import get_settings
from app.core.video_profiles import (
    VEO_EXTENDED_VIDEO_ROUTE,
    build_seed_duration_metadata,
    get_duration_profile,
    get_pollable_video_statuses,
    get_profile_request_cost_units,
    get_submission_video_status,
)
from app.features.videos import handlers as video_handlers
from app.features.videos.schemas import BatchVideoGenerationRequest


def test_resolve_video_submission_plan_preserves_legacy_batch_inputs():
    batch = {"id": "legacy-batch", "target_length_tier": None, "video_pipeline_route": None}

    plan = video_handlers._resolve_video_submission_plan(
        batch=batch,
        requested_provider="sora_2_pro",
        requested_seconds=12,
        aspect_ratio="16:9",
        resolution="1080p",
        size="1792x1024",
    )

    assert plan["provider"] == "sora_2_pro"
    assert plan["seconds"] == 12
    assert plan["provider_target_seconds"] == 12
    assert plan["resolution"] == "1080p"
    assert plan["size"] == "1792x1024"
    assert plan["profile"] is None


def test_resolve_video_submission_plan_routes_duration_tier_to_veo_extended():
    batch = {"id": "new-batch", "target_length_tier": 16, "video_pipeline_route": "veo_extended"}

    plan = video_handlers._resolve_video_submission_plan(
        batch=batch,
        requested_provider="sora_2",
        requested_seconds=12,
        aspect_ratio="9:16",
        resolution="1080p",
        size=None,
    )

    assert plan["provider"] == "veo_3_1"
    assert plan["seconds"] == 16
    assert plan["provider_target_seconds"] == 15
    assert plan["resolution"] == "720p"
    assert plan["requested_aspect_ratio"] == "9:16"
    assert plan["provider_aspect_ratio"] == "9:16"
    assert plan["requested_size"] == "720x1280"
    assert plan["provider_requested_size"] == "720x1280"
    assert plan["postprocess_crop_aspect_ratio"] is None
    assert plan["profile"].route == "veo_extended"


def test_manual_short_script_routes_to_8s_even_when_batch_was_created_as_16s():
    batch = {"id": "manual-batch", "creation_mode": "manual", "target_length_tier": 16}
    seed_data = {
        "manual_draft": True,
        "script": "Ich bin sehr gluecklich dass die App wieder funktioniert. Lets gooooo hahahahha",
    }

    plan = video_handlers._resolve_video_submission_plan(
        batch=batch,
        requested_provider="vertex_ai",
        requested_seconds=16,
        aspect_ratio="9:16",
        resolution="720p",
        size=None,
        seed_data=seed_data,
    )

    assert plan["duration_routed"] is True
    assert plan["manual_duration_auto_resolved"] is True
    assert plan["manual_requested_target_length_tier"] == 16
    assert plan["profile"].target_length_tier == 8
    assert plan["profile"].route != VEO_EXTENDED_VIDEO_ROUTE
    assert plan["seconds"] == 8


def test_resolve_video_submission_plan_preserves_vertex_provider_for_duration_routed_batches():
    batch = {"id": "vertex-batch", "target_length_tier": 32, "video_pipeline_route": "veo_extended"}

    plan = video_handlers._resolve_video_submission_plan(
        batch=batch,
        requested_provider="vertex_ai",
        requested_seconds=32,
        aspect_ratio="9:16",
        resolution="720p",
        size="720x1280",
    )

    assert plan["provider"] == "vertex_ai"
    assert plan["seconds"] == 32
    assert plan["provider_target_seconds"] == 29
    assert plan["resolution"] == "720p"
    assert plan["requested_size"] == "720x1280"
    assert plan["provider_requested_size"] == "720x1280"
    assert plan["profile"].route == "veo_extended"


def test_submit_video_request_threads_selected_veo_model(monkeypatch):
    class FakeVeoClient:
        def __init__(self):
            self.calls = []

        def submit_video_generation(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "operation_id": "operations/model-test",
                "status": "submitted",
                "provider_model": kwargs["model"],
            }

    fake_client = FakeVeoClient()
    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: fake_client)

    result = video_handlers._submit_video_request(
        provider="veo_3_1",
        model="veo-3.1-fast-generate-001",
        prompt_text="Hallo Welt",
        negative_prompt="subtitles",
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size="720x1280",
        correlation_id="corr-model",
    )

    assert fake_client.calls[0]["model"] == "veo-3.1-fast-generate-001"
    assert result["provider_model"] == "veo-3.1-fast-generate-001"


def test_submit_video_request_threads_selected_vertex_model(monkeypatch, tmp_path):
    captured = {}

    class FakeVertexClient:
        def submit_image_video(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-lite-generate-001/operations/op-123",
                "status": "submitted",
                "provider_model": kwargs["model"],
            }

    monkeypatch.setattr(video_handlers, "get_vertex_ai_client", lambda: FakeVertexClient())
    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"vertex_ai_output_gcs_uri": ""})())
    image_path = tmp_path / "sarah.jpg"
    image_path.write_bytes(b"fake-jpeg")
    monkeypatch.setattr(video_handlers, "_GLOBAL_VEO_ANCHOR_PATH", image_path)

    result = video_handlers._submit_video_request(
        provider="vertex_ai",
        model="veo-3.1-lite-generate-001",
        prompt_text="Hallo Welt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size="720x1280",
        correlation_id="corr-vertex-model",
    )

    assert captured["model"] == "veo-3.1-lite-generate-001"
    assert captured["image_bytes"] == b"fake-jpeg"
    assert result["provider_model"] == "veo-3.1-lite-generate-001"
    assert result["requested_model"] == "veo-3.1-lite-generate-001"


def test_build_submission_metadata_initializes_extension_chain():
    batch = {"id": "new-batch", "target_length_tier": 32, "video_pipeline_route": "veo_extended"}
    plan = video_handlers._resolve_video_submission_plan(
        batch=batch,
        requested_provider=None,
        requested_seconds=None,
        aspect_ratio="9:16",
        resolution="720p",
        size=None,
    )

    metadata = video_handlers._build_submission_metadata(
        existing_metadata={},
        submission_plan=plan,
        submission_result={
            "operation_id": "operations/abc",
            "requested_size": "720x1280",
            "provider_requested_size": "720x1280",
        },
    )

    assert metadata["target_length_tier"] == 32
    assert metadata["video_pipeline_route"] == "veo_extended"
    assert metadata["provider_target_seconds"] == 29
    assert metadata["generated_seconds"] == 0
    assert metadata["operation_ids"] == ["operations/abc"]
    assert metadata["requested_aspect_ratio"] == "9:16"
    assert metadata["provider_aspect_ratio"] == "9:16"
    assert metadata["requested_size"] == "720x1280"
    assert metadata["provider_requested_size"] == "720x1280"
    assert metadata["poller_environment"] == get_settings().environment
    assert metadata["poller_scope"]
    assert "postprocess_crop_aspect_ratio" not in metadata
    assert "postprocess_strategy" not in metadata


def test_build_submission_metadata_clears_stale_polling_errors():
    metadata = video_handlers._build_submission_metadata(
        existing_metadata={
            "error": "No Google Cloud Application Default Credentials found.",
            "error_type": "ValidationError",
            "failed_at": "2026-04-12T18:00:00Z",
            "provider_status_code": 401,
            "provider_response_body": "old error",
            "last_polled_by": "worker-a",
            "last_polled_at": "2026-04-12T18:01:00Z",
            "last_poll_recovery": "stale",
        },
        submission_plan={
            "aspect_ratio": "9:16",
            "provider_aspect_ratio": "9:16",
            "resolution": "720p",
            "seconds": 8,
            "requested_size": "720x1280",
            "provider_requested_size": "720x1280",
            "profile": None,
        },
        submission_result={
            "operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-fast-generate-001/operations/op-456",
            "provider_model": "veo-3.1-fast-generate-001",
            "requested_model": "veo-3.1-fast-generate-001",
            "requested_size": "720x1280",
        },
    )

    assert "error" not in metadata
    assert "error_type" not in metadata
    assert "failed_at" not in metadata
    assert "provider_status_code" not in metadata
    assert "provider_response_body" not in metadata
    assert "last_polled_by" not in metadata
    assert "last_polled_at" not in metadata
    assert "last_poll_recovery" not in metadata
    assert metadata["provider_model"] == "veo-3.1-fast-generate-001"
    assert metadata["requested_model"] == "veo-3.1-fast-generate-001"


def test_extended_route_uses_isolated_submission_statuses():
    assert get_submission_video_status(VEO_EXTENDED_VIDEO_ROUTE, "submitted") == "extended_submitted"
    assert get_submission_video_status(VEO_EXTENDED_VIDEO_ROUTE, "processing") == "extended_processing"
    assert "extended_submitted" in get_pollable_video_statuses()
    assert "extended_processing" in get_pollable_video_statuses()


def test_build_veo_extended_base_prompt_returns_first_segment():
    seed_data = {"script": "Erster Satz. Zweiter Satz. Dritter Satz."}
    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(seed_data)

    assert "Erster Satz." in prompt
    assert "brisk but natural pacing" in prompt
    assert "Continue directly into the next segment with no concluding pause or scene-ending hold." in prompt
    assert "mouth closes" not in prompt
    assert "subtitles, captions, watermark" not in prompt
    assert seg_meta["veo_segments"] == ["Erster Satz.", "Zweiter Satz.", "Dritter Satz."]
    assert seg_meta["veo_segments_total"] == 3
    assert seg_meta["veo_current_segment_index"] == 0
    assert seg_meta["veo_segment_time_windows"] == []


def test_build_veo_extended_base_prompt_rejects_under_segmented_32s_chain():
    seed_data = {"script": "Satz eins. Satz zwei. Satz drei.", "estimated_duration_s": 22}

    with pytest.raises(ValidationError, match="one complete dialogue segment per hop"):
        video_handlers._build_veo_extended_base_prompt(
            seed_data,
            planned_extension_hops=3,
            target_length_tier=32,
        )


def test_build_veo_extended_base_prompt_packs_to_four_segments_for_legacy_32s():
    seed_data = {
        "script": "Satz eins. Satz zwei. Satz drei. Satz vier. Satz fuenf.",
        "estimated_duration_s": 30,
    }

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert seg_meta["veo_required_segments"] == 4
    assert seg_meta["veo_planned_extension_hops_target"] == 3
    assert seg_meta["veo_extension_hops_target"] == 3
    assert seg_meta["veo_chain_shortened_to_available_segments"] is False


def test_build_veo_extended_base_prompt_packs_five_sentences_into_four_segments_for_efficient_32s():
    seed_data = {
        "script": (
            "Sorry, aber Physiotherapie ist nicht gleich Ergotherapie. "
            "Physio fokussiert auf koerperliche Funktionen und Beweglichkeit, "
            "waehrend Ergo handlungsorientiert deine Selbststaendigkeit im Alltag staerkt. "
            "Durch Alltagstraining lernst du, Routinetaetigkeiten wieder selbst zu bewaeltigen. "
            "Die Kosten uebernehmen primaer die Krankenkassen, du leistest allerdings die gesetzliche Zuzahlung. "
            "Seit 2024 bietet die Blankoverordnung Therapeuten mehr Flexibilitaet fuer deine Behandlung."
        ),
        "estimated_duration_s": 29,
    }

    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert "Sorry, aber Physiotherapie ist nicht gleich Ergotherapie. Physio fokussiert auf koerperliche Funktionen und Beweglichkeit" in prompt
    assert len(seg_meta["veo_segments"]) == 4
    assert seg_meta["veo_segments_total"] == 4
    assert seg_meta["veo_segments"] == [
        "Sorry, aber Physiotherapie ist nicht gleich Ergotherapie. Physio fokussiert auf koerperliche Funktionen und Beweglichkeit, waehrend",
        "Ergo handlungsorientiert deine Selbststaendigkeit im Alltag staerkt. Durch Alltagstraining lernst du, Routinetaetigkeiten wieder",
        "selbst zu bewaeltigen. Die Kosten uebernehmen primaer die Krankenkassen, du leistest allerdings die",
        "gesetzliche Zuzahlung. Seit 2024 bietet die Blankoverordnung Therapeuten mehr Flexibilitaet fuer deine Behandlung.",
    ]


def test_build_veo_extended_base_prompt_packs_to_two_segments_for_efficient_16s():
    seed_data = {
        "script": (
            "Erster kurzer Satz. Zweiter kurzer Satz. "
            "Dritter Satz mit etwas mehr Inhalt."
        ),
        "estimated_duration_s": 15,
    }

    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=1,
        target_length_tier=16,
    )

    assert "Erster kurzer Satz. Zweiter kurzer Satz." in prompt
    assert len(seg_meta["veo_segments"]) == 2
    assert seg_meta["veo_segments_total"] == 2
    assert seg_meta["veo_segments"] == [
        "Erster kurzer Satz. Zweiter kurzer Satz. Dritter",
        "Satz mit etwas mehr Inhalt.",
    ]


def test_build_veo_extended_base_prompt_uses_canonical_segment_visual_contract():
    seed_data = {
        "script": "Erster Satz. Zweiter Satz. Dritter Satz.",
        "estimated_duration_s": 29,
    }

    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        video_prompt={
            "character": "Edited character with novelty accessory",
            "style": "Style: Edited style",
            "action": "She says: Erster Satz. Zweiter Satz. Dritter Satz.",
            "scene": "Scene: Edited scene",
            "cinematography": "Cinematography: Edited cinematography",
            "audio_block": "After the final word, the audio gently settles into a quiet room tone.",
            "audio": {
                "dialogue": "Erster Satz. Zweiter Satz. Dritter Satz.",
                "capture": "After the final word, the audio gently settles into a quiet room tone.",
            },
        },
        planned_extension_hops=1,
        target_length_tier=16,
    )

    assert "Erster Satz." in prompt
    assert "Edited character with novelty accessory" not in prompt
    assert "Edited style" not in prompt
    assert "Edited scene" not in prompt
    assert "Edited cinematography" not in prompt
    assert "Style:\nStyle:" not in prompt
    assert "Scene:\nScene:" not in prompt
    assert "Cinematography:\nCinematography:" not in prompt
    assert "She says: Erster Satz. Zweiter Satz. Dritter Satz." not in prompt
    assert "After the final word, the audio gently settles into a quiet room tone." not in prompt
    assert "no settling room tone" in prompt
    assert "subtitles, captions, watermark" not in prompt
    assert seg_meta["veo_segments"] == ["Erster Satz. Zweiter", "Satz. Dritter Satz."]


def test_build_veo_extended_base_prompt_uses_time_budgeted_packing_for_efficient_32s():
    seed_data = {
        "script": (
            "Als Rollstuhlnutzer kennst du das: Das gesuchte Produkt steht unerreichbar hoch im Supermarktregal. "
            "Spezielle Hubrollstühle mit stufenloser Gasdruckfederung und ergonomische Greifzangen erleichtern den Zugriff enorm. "
            "Für enge Gänge sind ankoppelbare Rollstuhl Einkaufswagen die beste Wahl. "
            "Seit 2025 verbessert das Barrierefreiheitsstärkungsgesetz Terminals, aber Übergangsfristen bremsen die Inklusion noch. "
            "Mit diesen Hacks meisterst du vertikale Barrieren beim Einkaufen."
        ),
        "estimated_duration_s": 29,
    }

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert seg_meta["veo_segments"] == [
        "Als Rollstuhlnutzer kennst du das: Das gesuchte Produkt steht unerreichbar hoch im Supermarktregal. Spezielle Hubrollstühle mit",
        "stufenloser Gasdruckfederung und ergonomische Greifzangen erleichtern den Zugriff enorm. Für enge Gänge sind",
        "ankoppelbare Rollstuhl Einkaufswagen die beste Wahl. Seit 2025 verbessert das Barrierefreiheitsstärkungsgesetz Terminals, aber",
        "Übergangsfristen bremsen die Inklusion noch. Mit diesen Hacks meisterst du vertikale Barrieren beim Einkaufen.",
    ]
    assert seg_meta["veo_segment_time_windows"] == [
        {"segment_index": 0, "start_seconds": 0.0, "end_seconds": 8.0, "budget_seconds": 8},
        {"segment_index": 1, "start_seconds": 8.0, "end_seconds": 15.0, "budget_seconds": 7},
        {"segment_index": 2, "start_seconds": 15.0, "end_seconds": 22.0, "budget_seconds": 7},
        {"segment_index": 3, "start_seconds": 22.0, "end_seconds": 29.0, "budget_seconds": 7},
    ]


def test_build_veo_extended_base_prompt_uses_efficient_32s_visual_contract():
    seed_data = {
        "script": "Erster Satz. Zweiter Satz. Dritter Satz. Vierter Satz. Fuenfter Satz.",
    }

    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        video_prompt={
            "character": "Edited character with novelty accessory",
            "style": "Edited style",
            "scene": "Edited scene",
            "cinematography": "Edited cinematography",
        },
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert (
        "38-year-old German woman with shoulder-length light brown hair with subtle blonde "
        "highlights, hazel eyes, and a warm light-medium skin tone. Friendly oval face and natural "
        "expression."
    ) in prompt
    assert (
        "Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, "
        "soft flattering indoor light, and natural skin texture."
    ) in prompt
    assert "Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance." in prompt
    assert "Edited character with novelty accessory" not in prompt
    assert "Edited scene" not in prompt
    assert seg_meta["veo_segments"] == [
        "Erster Satz. Zweiter Satz.",
        "Dritter Satz.",
        "Vierter Satz.",
        "Fuenfter Satz.",
    ]


def test_batch_video_generation_request_accepts_duration_tier_seconds():
    req = BatchVideoGenerationRequest(
        provider="vertex_ai",
        seconds=16,
        target_length_tier=16,
    )

    assert req.seconds == 16
    assert req.target_length_tier == 16


def test_batch_video_generation_request_rejects_gemini_provider():
    with pytest.raises(Exception):
        BatchVideoGenerationRequest(
            provider="veo_3_1",
            seconds=16,
            target_length_tier=16,
        )


def test_resolve_plan_for_32s_batch_initializes_full_chain_metadata():
    batch = {"id": "b-32", "target_length_tier": 32}

    plan = video_handlers._resolve_video_submission_plan(
        batch=batch,
        requested_provider=None,
        requested_seconds=None,
        aspect_ratio="9:16",
        resolution="720p",
        size=None,
    )

    assert plan["duration_routed"] is True
    assert plan["provider"] == "veo_3_1"
    assert plan["profile"].veo_extension_hops == 3
    assert plan["resolution"] == "720p"

    metadata = video_handlers._build_submission_metadata(
        existing_metadata={},
        submission_plan=plan,
        submission_result={"operation_id": "op-1", "requested_size": "720x1280"},
        segment_metadata={
            "veo_segments": ["S1. S2.", "S3.", "S4.", "S5."],
            "veo_segments_total": 4,
            "veo_current_segment_index": 0,
            "veo_extension_hops_target": 3,
            "veo_planned_extension_hops_target": 3,
            "veo_chain_shortened_to_available_segments": False,
        },
    )

    assert metadata["veo_extension_hops_target"] == 3
    assert metadata["veo_planned_extension_hops_target"] == 3
    assert metadata["veo_chain_shortened_to_available_segments"] is False
    assert metadata["veo_extension_hops_completed"] == 0
    assert metadata["veo_segments"] == ["S1. S2.", "S3.", "S4.", "S5."]
    assert metadata["chain_status"] == "submitted"
    assert metadata["provider_aspect_ratio"] == "9:16"


def test_submit_video_request_passes_explicit_veo_duration_seconds(monkeypatch):
    captured = {}

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {"operation_id": "operations/test", "status": "submitted"}

    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())

    result = video_handlers._submit_video_request(
        provider="veo_3_1",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=32,
        size=None,
        correlation_id="corr",
        provider_duration_seconds=4,
    )

    assert captured["duration_seconds"] == 4
    assert result["operation_id"] == "operations/test"


def test_efficient_long_route_applies_to_16s_and_32s(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "veo_enable_efficient_long_route", True)

    profile_16 = get_duration_profile(16)
    profile_32 = get_duration_profile(32)
    metadata_32 = build_seed_duration_metadata(profile_32)

    assert settings.veo_enable_efficient_long_route is True
    assert profile_16.provider_target_seconds == 15
    assert profile_16.veo_base_seconds == 8
    assert profile_16.veo_extension_hops == 1
    assert profile_16.prompt1_sentence_guidance == "ZWEI natuerliche Sprechbloecke"
    assert profile_16.prompt2_sentence_guidance == "2 Sprechbloecke"
    assert profile_32.provider_target_seconds == 29
    assert profile_32.veo_base_seconds == 8
    assert profile_32.veo_extension_hops == 3
    assert profile_32.prompt1_sentence_guidance == "VIER natuerliche Sprechbloecke"
    assert profile_32.prompt2_sentence_guidance == "4 Sprechbloecke"
    assert metadata_32["veo_base_seconds"] == 8
    assert metadata_32["veo_extension_hops"] == 3
    assert get_profile_request_cost_units(profile_16) == 2
    assert get_profile_request_cost_units(profile_32) == 4

    monkeypatch.setattr(settings, "veo_enable_efficient_long_route", False)

    legacy_16 = get_duration_profile(16)
    legacy_32 = get_duration_profile(32)

    assert legacy_16.provider_target_seconds == 18
    assert legacy_16.veo_base_seconds == 4
    assert legacy_16.veo_extension_hops == 2
    assert legacy_32.provider_target_seconds == 29
    assert legacy_32.veo_base_seconds == 8
    assert legacy_32.veo_extension_hops == 3


def test_efficient_long_route_stays_enabled_for_32s(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "veo_enable_efficient_long_route", False)

    profile_16 = get_duration_profile(16)
    profile_32 = get_duration_profile(32)

    assert video_handlers._uses_actual_efficient_long_route(profile_16) is False
    assert video_handlers._uses_actual_efficient_long_route(profile_32) is True


def test_veo_seed_is_only_used_for_actual_efficient_long_route(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "veo_enable_efficient_long_route", True)

    profile_16 = get_duration_profile(16)
    profile_32 = get_duration_profile(32)

    assert video_handlers._should_assign_veo_seed(provider="veo_3_1", profile=profile_16) is True
    assert video_handlers._should_assign_veo_seed(provider="veo_3_1", profile=profile_32) is True
    assert video_handlers._should_assign_veo_seed(provider="sora", profile=profile_16) is False


def test_prompt3_32s_template_uses_current_sentence_budget_language():
    prompt_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "features"
        / "topics"
        / "prompt_data"
        / "prompt3_32s.txt"
    )

    prompt_text = prompt_path.read_text(encoding="utf-8")

    assert "40-66 Wörter und fünf bis sechs natürliche Sätze" in prompt_text


def test_resolve_global_veo_anchor_image_reads_repo_asset(monkeypatch):
    class FakeStorageClient:
        def ensure_image(self, **kwargs):
            return {
                "storage_key": kwargs["object_key"],
                "url": f"https://example.r2.dev/{kwargs['object_key']}",
            }

    monkeypatch.setattr(video_handlers, "get_storage_client", lambda: FakeStorageClient())

    anchor_bundle = video_handlers._resolve_global_veo_anchor_image("corr-anchor")
    expected_bytes = Path(video_handlers._GLOBAL_VEO_ANCHOR_PATH).read_bytes()

    assert Path(video_handlers._GLOBAL_VEO_ANCHOR_PATH).name == "sarah.jpg"
    assert base64.b64decode(anchor_bundle["first_frame_image"]["data_base64"]) == expected_bytes
    assert anchor_bundle["metadata"]["anchor_image_enabled"] is True
    assert anchor_bundle["metadata"]["anchor_image_source_path"] == "static/images/sarah.jpg"
    assert anchor_bundle["metadata"]["anchor_image_storage_key"] == "Lippe Lift Studio/images/anchors/sarah.jpg"


def test_submit_video_request_passes_anchor_image_to_veo_client(monkeypatch):
    captured = {}
    first_frame_image = {"mime_type": "image/jpeg", "data_base64": "YWJj"}

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {"operation_id": "operations/test", "status": "submitted"}

    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())

    result = video_handlers._submit_video_request(
        provider="veo_3_1",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr",
        first_frame_image=first_frame_image,
        provider_duration_seconds=8,
    )

    assert captured["first_frame_image"] == first_frame_image
    assert result["operation_id"] == "operations/test"


def test_submit_video_request_auto_attaches_anchor_image_to_veo_client(monkeypatch, tmp_path):
    captured = {}
    image_path = tmp_path / "sarah.jpg"
    image_path.write_bytes(b"veo-anchor")

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {"operation_id": "operations/test", "status": "submitted"}

    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())
    monkeypatch.setattr(video_handlers, "_GLOBAL_VEO_ANCHOR_PATH", image_path)
    monkeypatch.setattr(
        video_handlers,
        "get_settings",
        lambda: type("S", (), {"veo_use_reference_image": True})(),
    )

    result = video_handlers._submit_video_request(
        provider="veo_3_1",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr",
        provider_duration_seconds=8,
    )

    assert base64.b64decode(captured["first_frame_image"]["data_base64"]) == b"veo-anchor"
    assert captured["first_frame_image"]["mime_type"] == "image/jpeg"
    assert result["operation_id"] == "operations/test"


def test_submit_video_request_skips_anchor_image_when_toggle_disabled(monkeypatch, tmp_path):
    captured = {}
    image_path = tmp_path / "sarah.jpg"
    image_path.write_bytes(b"veo-anchor")

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {"operation_id": "operations/test", "status": "submitted"}

    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())
    monkeypatch.setattr(video_handlers, "_GLOBAL_VEO_ANCHOR_PATH", image_path)
    monkeypatch.setattr(
        video_handlers,
        "get_settings",
        lambda: type("S", (), {"veo_use_reference_image": False})(),
    )

    result = video_handlers._submit_video_request(
        provider="veo_3_1",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr",
        provider_duration_seconds=8,
    )

    assert captured["first_frame_image"] is None
    assert result["operation_id"] == "operations/test"


def test_submit_video_request_falls_back_when_anchor_unreadable(monkeypatch):
    captured = {"veo_first_frame_image": "unset", "vertex_mode": None}

    class UnreadableAnchorPath:
        name = "sarah.jpg"

        @staticmethod
        def exists() -> bool:
            return True

        @staticmethod
        def read_bytes() -> bytes:
            raise OSError("permission denied")

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured["veo_first_frame_image"] = kwargs.get("first_frame_image")
            return {"operation_id": "operations/veo", "status": "submitted"}

    class FakeVertexClient:
        def submit_image_video(self, **kwargs):
            captured["vertex_mode"] = "image"
            return {"operation_id": "operations/image", "status": "submitted", "provider_model": kwargs.get("model")}

        def submit_text_video(self, **kwargs):
            captured["vertex_mode"] = "text"
            return {"operation_id": "operations/text", "status": "submitted", "provider_model": kwargs.get("model")}

    monkeypatch.setattr(video_handlers, "_GLOBAL_VEO_ANCHOR_PATH", UnreadableAnchorPath())
    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())
    monkeypatch.setattr(video_handlers, "get_vertex_ai_client", lambda: FakeVertexClient())
    monkeypatch.setattr(
        video_handlers,
        "get_settings",
        lambda: type("S", (), {"veo_use_reference_image": True, "vertex_ai_output_gcs_uri": ""})(),
    )

    _ = video_handlers._submit_video_request(
        provider="veo_3_1",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr-veo-fallback",
        provider_duration_seconds=8,
    )
    _ = video_handlers._submit_video_request(
        provider="vertex_ai",
        model="veo-3.1-lite-generate-001",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size="720x1280",
        correlation_id="corr-vertex-fallback",
    )

    assert captured["veo_first_frame_image"] is None
    assert captured["vertex_mode"] == "text"
