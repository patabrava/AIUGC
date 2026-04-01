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
    assert "postprocess_crop_aspect_ratio" not in metadata
    assert "postprocess_strategy" not in metadata


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
    assert "Do not end the speech yet." in prompt
    assert "mouth closes" not in prompt
    assert seg_meta["veo_segments"] == ["Erster Satz.", "Zweiter Satz.", "Dritter Satz."]
    assert seg_meta["veo_segments_total"] == 3
    assert seg_meta["veo_current_segment_index"] == 0


def test_build_veo_extended_base_prompt_rejects_under_segmented_32s_chain():
    seed_data = {"script": "Satz eins. Satz zwei. Satz drei. Satz vier.", "estimated_duration_s": 28}

    with pytest.raises(ValidationError, match="one complete dialogue segment per hop"):
        video_handlers._build_veo_extended_base_prompt(
            seed_data,
            planned_extension_hops=4,
            target_length_tier=32,
        )


def test_build_veo_extended_base_prompt_preserves_full_32s_chain_budget():
    seed_data = {
        "script": "Satz eins. Satz zwei. Satz drei. Satz vier. Satz fuenf.",
        "estimated_duration_s": 30,
    }

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=4,
        target_length_tier=32,
    )

    assert seg_meta["veo_required_segments"] == 5
    assert seg_meta["veo_planned_extension_hops_target"] == 4
    assert seg_meta["veo_extension_hops_target"] == 4
    assert seg_meta["veo_chain_shortened_to_available_segments"] is False


def test_batch_video_generation_request_accepts_duration_tier_seconds():
    req = BatchVideoGenerationRequest(
        provider="veo_3_1",
        seconds=16,
        target_length_tier=16,
    )

    assert req.seconds == 16
    assert req.target_length_tier == 16


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
            "veo_segments": ["S1.", "S2.", "S3.", "S4."],
            "veo_segments_total": 4,
            "veo_current_segment_index": 0,
            "veo_extension_hops_target": 3,
            "veo_planned_extension_hops_target": 4,
            "veo_chain_shortened_to_available_segments": True,
        },
    )

    assert metadata["veo_extension_hops_target"] == 3
    assert metadata["veo_planned_extension_hops_target"] == 4
    assert metadata["veo_chain_shortened_to_available_segments"] is True
    assert metadata["veo_extension_hops_completed"] == 0
    assert metadata["veo_segments"] == ["S1.", "S2.", "S3.", "S4."]
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


def test_efficient_long_route_profiles_are_default_and_can_opt_out(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "veo_enable_efficient_long_route", True)

    profile_16 = get_duration_profile(16)
    profile_32 = get_duration_profile(32)
    metadata_32 = build_seed_duration_metadata(profile_32)

    assert settings.veo_enable_efficient_long_route is True
    assert profile_16.provider_target_seconds == 15
    assert profile_16.veo_base_seconds == 8
    assert profile_16.veo_extension_hops == 1
    assert profile_32.provider_target_seconds == 29
    assert profile_32.veo_base_seconds == 8
    assert profile_32.veo_extension_hops == 3
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
    assert legacy_32.provider_target_seconds == 32
    assert legacy_32.veo_base_seconds == 4
    assert legacy_32.veo_extension_hops == 4


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
    assert anchor_bundle["metadata"]["anchor_image_storage_key"] == "flow-forge/images/anchors/sarah.jpg"


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
