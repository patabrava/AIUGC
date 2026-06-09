import base64
from pathlib import Path

import httpx
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
from app.features.videos.schemas import BatchVideoGenerationRequest, VideoGenerationRequest


def _valid_32s_script() -> str:
    return (
        "Erster langer Satz erklärt ruhig den Einstieg und setzt den Kontext für die Zuschauerin heute klar. "
        "Zweiter langer Satz führt die Beobachtung weiter und bleibt natürlich im gesprochenen Rhythmus stabil. "
        "Dritter langer Satz nennt den konkreten Nutzen und verbindet ihn mit einer Alltagssituation direkt. "
        "Vierter langer Satz schließt den Gedanken sauber ab und bleibt ohne neue Pointe."
    )


def _valid_16s_script() -> str:
    return (
        "Erster langer Satz erklärt ruhig den Einstieg und setzt den Kontext für die Zuschauerin heute klar. "
        "Zweiter langer Satz führt die Beobachtung weiter und bleibt im gesprochenen Rhythmus."
    )


def _assert_segment_budgets_pass(seg_meta: dict) -> None:
    budgets = seg_meta["veo_segment_spoken_budgets"]
    assert budgets
    assert all(item["word_count"] >= item["minimum_words"] for item in budgets)


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


def test_resolve_video_submission_plan_routes_duration_tier_to_vertex_extended():
    batch = {"id": "new-batch", "target_length_tier": 16, "video_pipeline_route": "veo_extended"}

    plan = video_handlers._resolve_video_submission_plan(
        batch=batch,
        requested_provider="sora_2",
        requested_seconds=12,
        aspect_ratio="9:16",
        resolution="1080p",
        size=None,
    )

    assert plan["provider"] == "vertex_ai"
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


def test_submit_video_request_threads_selected_vertex_model(monkeypatch):
    captured = {}

    class FakeVertexClient:
        def submit_text_video(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-lite-generate-001/operations/op-123",
                "status": "submitted",
                "provider_model": kwargs["model"],
            }

    monkeypatch.setattr(video_handlers, "get_vertex_ai_client", lambda: FakeVertexClient())
    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"vertex_ai_output_gcs_uri": ""})())

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
    assert captured["prompt"] == "Hallo Welt"
    assert result["provider_model"] == "veo-3.1-lite-generate-001"
    assert result["requested_model"] == "veo-3.1-lite-generate-001"


def test_submit_video_request_translates_vertex_http_errors(monkeypatch):
    request = httpx.Request("POST", "https://vertex.example.test")
    response = httpx.Response(
        400,
        request=request,
        json={"error": {"message": "Prompt blocked by provider policy"}},
    )

    class FakeVertexClient:
        def submit_text_video(self, **kwargs):
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

    monkeypatch.setattr(video_handlers, "get_vertex_ai_client", lambda: FakeVertexClient())
    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"vertex_ai_output_gcs_uri": ""})())

    with pytest.raises(video_handlers.FlowForgeException) as exc:
        video_handlers._submit_video_request(
            provider="vertex_ai",
            model="veo-3.1-generate-001",
            prompt_text="Hallo Welt",
            negative_prompt=None,
            aspect_ratio="9:16",
            provider_aspect_ratio="9:16",
            requested_aspect_ratio="9:16",
            resolution="720p",
            seconds=8,
            size="720x1280",
            correlation_id="corr-vertex-http-error",
        )

    assert exc.value.code == video_handlers.ErrorCode.THIRD_PARTY_FAIL
    assert exc.value.details["status_code"] == 400
    assert exc.value.details["response"]["error"]["message"] == "Prompt blocked by provider policy"


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


def test_build_veo_extended_base_prompt_rejects_underfilled_32s_chain():
    seed_data = {
        "script": (
            "Alle reden über Sport und Muskeln, wenn es um Energie im Rollstuhl geht. "
            "Aber niemand spricht darüber, wie wichtig die richtige Sitzposition wirklich ist. "
            "Ich dachte früher, das sei nur Komfort. "
            "Dabei entlastet eine optimale Haltung unglaublich und spart dir Kraft."
        ),
        "estimated_duration_s": 16,
    }

    with pytest.raises(ValidationError) as exc_info:
        video_handlers._build_veo_extended_base_prompt(
            seed_data,
            planned_extension_hops=3,
            target_length_tier=32,
        )

    details = exc_info.value.details
    assert details["target_length_tier"] == 32
    assert details["budget_seconds"] == 7
    assert details["word_count"] == 7
    assert details["minimum_words"] >= 12
    assert details["segment_index"] == 2


def test_build_veo_extended_base_prompt_splits_long_unpunctuated_script_for_chain():
    script = " ".join([f"wort{i}" for i in range(1, 71)])
    seed_data = {
        "script": script,
        "estimated_duration_s": 28,
    }

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert seg_meta["veo_extension_hops_target"] == 3
    assert seg_meta["veo_effective_required_segments"] == 4
    assert seg_meta["veo_segments_total"] == 4
    assert " ".join(seg_meta["veo_segments"]).split() == script.split()
    assert all(segment.strip() for segment in seg_meta["veo_segments"])


def test_build_veo_extended_base_prompt_packs_to_four_segments_for_legacy_32s():
    seed_data = {
        "script": _valid_32s_script(),
        "estimated_duration_s": 30,
    }

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert seg_meta["veo_required_segments"] == 4
    assert seg_meta["veo_planned_required_segments"] == 4
    assert seg_meta["veo_effective_required_segments"] == 4
    assert seg_meta["veo_planned_extension_hops_target"] == 3
    assert seg_meta["veo_extension_hops_target"] == 3
    assert seg_meta["veo_chain_shortened_to_available_segments"] is False
    _assert_segment_budgets_pass(seg_meta)


def test_build_veo_extended_base_prompt_packs_five_sentences_into_four_segments_for_efficient_32s():
    seed_data = {
        "script": (
            "Sorry, Physiotherapie und Ergotherapie verfolgen im Alltag unterschiedliche Ziele. "
            "Die erste hilft vor allem Bewegung, Kraft und körperliche Funktionen gezielt zu verbessern. "
            "Ergotherapie trainiert dagegen konkrete Handlungen, damit Routinen im Bad, Haushalt und Beruf wieder leichter werden. "
            "Die Krankenkasse übernimmt vieles, doch Zuzahlungen und Verordnungsdetails solltest du vorher genau prüfen lassen. "
            "Seit 2024 gibt die Blankoverordnung Therapiepraxen mehr Spielraum, damit deine Behandlung besser passt."
        ),
        "estimated_duration_s": 29,
    }

    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert "Sorry, Physiotherapie und Ergotherapie verfolgen im Alltag unterschiedliche Ziele." in prompt
    assert len(seg_meta["veo_segments"]) == 4
    assert seg_meta["veo_segments_total"] == 4
    assert seg_meta["veo_extension_hops_target"] == 3
    assert seg_meta["veo_effective_required_segments"] == 4
    assert seg_meta["veo_chain_shortened_to_available_segments"] is False
    _assert_segment_budgets_pass(seg_meta)


def test_build_veo_extended_base_prompt_keeps_live_32s_segments_on_sentence_boundaries():
    seed_data = {
        "script": (
            "Deutschland 2026. Und du suchst eine wirklich altersgerechte Wohnung. "
            "Langfristige Planung ist dabei entscheidend, besonders für Mehrgenerationen und Pflegearrangements. "
            "Der Zuschuss 455 B hilft zwar mit bis zu 2.500 Euro, deckt aber oft nur einen Bruchteil der Kosten ab. "
            "Rechtliche Aspekte wie Eigentumsverhältnisse, Regelungen für den Todesfall und die Kostenaufteilung sollten vertraglich klar geregelt werden."
        ),
        "estimated_duration_s": 22,
    }

    with pytest.raises(ValidationError) as exc_info:
        video_handlers._build_veo_extended_base_prompt(
            seed_data,
            planned_extension_hops=3,
            target_length_tier=32,
        )

    assert exc_info.value.details["veo_planned_extension_hops_target"] == 3
    assert exc_info.value.details["veo_required_segments"] == 4


def test_build_veo_extended_base_prompt_packs_to_two_segments_for_efficient_16s():
    seed_data = {
        "script": _valid_16s_script(),
        "estimated_duration_s": 15,
    }

    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=1,
        target_length_tier=16,
    )

    assert "Erster langer Satz" in prompt
    assert len(seg_meta["veo_segments"]) == 2
    assert seg_meta["veo_segments_total"] == 2
    _assert_segment_budgets_pass(seg_meta)


def test_build_veo_extended_base_prompt_rebalances_16s_sentence_groups_to_budget():
    seed_data = {
        "script": (
            "Niemand redet darüber, aber der Gewürzregal Trick im Supermarkt ist verboten. "
            "Seit 2025 ist diese Auszugstechnik laut Betreibern exklusiv für Personal. "
            "Bei falscher Nutzung riskierst du kaputte Regale."
        ),
        "estimated_duration_s": 11,
    }

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=1,
        target_length_tier=16,
    )

    assert len(seg_meta["veo_segments"]) == 2
    assert [item["word_count"] for item in seg_meta["veo_segment_spoken_budgets"]] == [16, 12]
    _assert_segment_budgets_pass(seg_meta)


def test_build_veo_extended_base_prompt_rejects_underlength_formatted_number_script():
    seed_data = {
        "script": (
            "Niemand redet darüber, aber dein barrierefreies Bad kostet dich locker bis zu 8.000 Euro. "
            "Ein Treppenlift startet bei 4.000 Euro. "
            "Deshalb ist deine Budgetplanung jetzt so wichtig."
        ),
        "estimated_duration_s": 11,
    }

    with pytest.raises(ValidationError) as exc:
        video_handlers._build_veo_extended_base_prompt(
            seed_data,
            planned_extension_hops=1,
            target_length_tier=16,
        )

    assert exc.value.details["word_count"] < exc.value.details["minimum_words"]


def test_build_veo_extended_base_prompt_preserves_edited_visual_contract():
    seed_data = {
        "script": _valid_16s_script(),
        "estimated_duration_s": 29,
    }
    action_with_full_script = f"She says: {seed_data['script']}"

    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        video_prompt={
            "character": "Edited character with novelty accessory",
            "style": "Style: Edited style",
            "action": action_with_full_script,
            "scene": "Scene: Edited scene",
            "cinematography": "Cinematography: Edited cinematography",
            "audio_block": "After the final word, the audio gently settles into a quiet room tone.",
            "audio": {
                "dialogue": seed_data["script"],
                "capture": "After the final word, the audio gently settles into a quiet room tone.",
            },
        },
        planned_extension_hops=1,
        target_length_tier=16,
    )

    assert "Erster langer Satz" in prompt
    assert "Edited character with novelty accessory" in prompt
    assert "Edited style" in prompt
    assert "Edited scene" in prompt
    assert "Edited cinematography" in prompt
    assert "Style:\nStyle: Edited style" in prompt
    assert "Scene:\nScene: Edited scene" in prompt
    assert "Cinematography:\nCinematography: Edited cinematography" in prompt
    assert action_with_full_script not in prompt
    assert "After the final word, the audio gently settles into a quiet room tone." not in prompt
    assert "Continue directly into the next segment" in prompt
    assert "subtitles, captions, watermark" not in prompt
    assert len(seg_meta["veo_segments"]) == 2
    _assert_segment_budgets_pass(seg_meta)


def test_extended_base_prompt_does_not_inherit_final_stop_ending():
    seed_data = {
        "script": (
            "Erster ausreichend langer Satz für den Start dieses Videos mit ruhigem Kontext und klarer Einordnung heute. "
            "Zweiter ausreichend langer Satz für die erste Erweiterung mit klarer Beobachtung und natürlichem Sprechfluss. "
            "Dritter ausreichend langer Satz für die zweite Erweiterung mit konkretem Nutzen und sauberer Verbindung. "
            "Vierter ausreichend langer Satz für die finale Erweiterung mit sauberem Abschluss und ruhiger Landung."
        )
    }
    saved_prompt = {
        "audio": {"dialogue": seed_data["script"]},
        "ending_directive": "After the final spoken word, speech stops completely.",
    }

    prompt, _metadata = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        saved_prompt,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert "After the final spoken word, speech stops completely." not in prompt
    assert "Continue directly into the next segment" in prompt


def test_build_veo_extended_base_prompt_32s_uses_stable_legacy_visual_contract():
    seed_data = {
        "script": _valid_32s_script(),
        "estimated_duration_s": 29,
    }

    prompt, _seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        video_prompt={
            "character": "Character: 38-year-old German woman with black sunglasses.",
            "style": "Style: Edited style",
            "action": "Action: She keeps the black sunglasses on while speaking.",
            "scene": "Scene: Edited scene",
            "cinematography": "Cinematography: Edited cinematography",
            "audio_block": "Audio: Edited audio block.",
            "audio": {
                "dialogue": seed_data["script"],
                "capture": "Audio: Edited audio block.",
            },
            "ending_directive": "Edited ending",
        },
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert "38-year-old German woman with shoulder-length light brown hair" in prompt
    assert "crow's feet at the outer corners" not in prompt
    assert "black sunglasses" not in prompt
    assert "Edited style" not in prompt
    assert "Edited scene" not in prompt
    assert "Edited cinematography" not in prompt
    assert "Edited audio block" not in prompt
    assert "Edited ending" not in prompt
    assert "black sunglasses" not in " ".join(seed_data["script"].split())


def test_build_veo_extended_base_prompt_uses_time_budgeted_packing_for_efficient_32s():
    seed_data = {
        "script": _valid_32s_script(),
        "estimated_duration_s": 29,
    }

    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert len(seg_meta["veo_segments"]) == 4
    assert seg_meta["veo_segment_time_windows"] == [
        {"segment_index": 0, "start_seconds": 0.0, "end_seconds": 8.0, "budget_seconds": 8},
        {"segment_index": 1, "start_seconds": 8.0, "end_seconds": 15.0, "budget_seconds": 7},
        {"segment_index": 2, "start_seconds": 15.0, "end_seconds": 22.0, "budget_seconds": 7},
        {"segment_index": 3, "start_seconds": 22.0, "end_seconds": 29.0, "budget_seconds": 7},
    ]
    _assert_segment_budgets_pass(seg_meta)


def test_build_veo_extended_base_prompt_uses_efficient_32s_visual_contract():
    seed_data = {
        "script": _valid_32s_script(),
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

    assert "38-year-old German woman with shoulder-length light brown hair" in prompt
    assert "crow's feet at the outer corners" not in prompt
    assert "Edited character with novelty accessory" not in prompt
    assert "Edited style" not in prompt
    assert "Edited scene" not in prompt
    assert "Edited cinematography" not in prompt
    assert len(seg_meta["veo_segments"]) == 4
    _assert_segment_budgets_pass(seg_meta)


def test_build_veo_extended_base_prompt_uses_legacy_32s_default_visual_contract():
    seed_data = {
        "script": _valid_32s_script(),
    }

    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert "38-year-old German woman with shoulder-length light brown hair" in prompt
    assert "Based on the uploaded person images" not in prompt
    assert "sunglasses" not in prompt
    assert seg_meta["veo_extension_hops_target"] == 3
    _assert_segment_budgets_pass(seg_meta)


def test_batch_video_generation_request_accepts_duration_tier_seconds():
    req = BatchVideoGenerationRequest(
        provider="vertex_ai",
        seconds=16,
        target_length_tier=16,
    )

    assert req.seconds == 16
    assert req.target_length_tier == 16


def test_video_generation_requests_accept_veo_provider_for_reference_image_path():
    single_request = VideoGenerationRequest(
        provider="veo_3_1",
        model="veo-3.1-generate-preview",
        aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
    )
    batch_request = BatchVideoGenerationRequest(
        provider="veo_3_1",
        model="veo-3.1-fast-generate-preview",
        aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
    )

    assert single_request.provider == "veo_3_1"
    assert single_request.model == "veo-3.1-generate-preview"
    assert batch_request.provider == "veo_3_1"
    assert batch_request.model == "veo-3.1-fast-generate-preview"


def test_batch_video_generation_request_rejects_gemini_provider():
    with pytest.raises(Exception):
        BatchVideoGenerationRequest(
            provider="gemini",
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
    assert plan["provider"] == "vertex_ai"
    assert plan["profile"].veo_extension_hops == 3
    assert plan["profile"].veo_base_seconds == 8
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
    assert profile_32.prompt1_min_words == 68
    assert profile_32.prompt1_max_words == 88
    assert profile_32.prompt2_min_words == 64
    assert profile_32.prompt2_max_words == 84
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
    assert video_handlers._should_assign_veo_seed(provider="vertex_ai", profile=profile_32) is True
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

    assert "64-84 Wörter und fünf bis sechs natürliche Sätze" in prompt_text


def test_reference_image_paths_parse_comma_separated_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "veo_use_reference_images", True, raising=False)
    monkeypatch.setattr(
        settings,
        "veo_reference_image_paths",
        "static/images/video-references/front.png, static/images/video-references/profile.png,static/images/video-references/full-body.png",
        raising=False,
    )

    paths = video_handlers._configured_veo_reference_image_paths(settings)

    assert paths == [
        "static/images/video-references/front.png",
        "static/images/video-references/profile.png",
        "static/images/video-references/full-body.png",
    ]


def test_load_global_veo_reference_assets_reads_three_pngs(monkeypatch, tmp_path):
    paths = []
    for name, content in [
        ("front.png", b"front-image"),
        ("profile.png", b"profile-image"),
        ("full-body.png", b"full-body-image"),
    ]:
        image_path = tmp_path / name
        image_path.write_bytes(content)
        paths.append(str(image_path))

    settings = type(
        "S",
        (),
        {
            "veo_use_reference_images": True,
            "veo_reference_image_paths": ",".join(paths),
        },
    )()
    monkeypatch.setattr(video_handlers, "get_settings", lambda: settings)

    bundle = video_handlers._load_global_veo_reference_assets(correlation_id="corr-ref", strict=True)

    assert [item["mime_type"] for item in bundle["reference_images"]] == ["image/png", "image/png", "image/png"]
    assert [base64.b64decode(item["data_base64"]) for item in bundle["reference_images"]] == [
        b"front-image",
        b"profile-image",
        b"full-body-image",
    ]
    assert bundle["metadata"]["reference_images_enabled"] is True
    assert bundle["metadata"]["reference_image_count"] == 3


def test_load_global_veo_reference_assets_rejects_more_than_three(monkeypatch, tmp_path):
    paths = []
    for index in range(4):
        image_path = tmp_path / f"ref-{index}.png"
        image_path.write_bytes(b"image")
        paths.append(str(image_path))

    settings = type(
        "S",
        (),
        {
            "veo_use_reference_images": True,
            "veo_reference_image_paths": ",".join(paths),
        },
    )()
    monkeypatch.setattr(video_handlers, "get_settings", lambda: settings)

    with pytest.raises(ValidationError) as exc:
        video_handlers._load_global_veo_reference_assets(correlation_id="corr-ref", strict=True)

    assert "at most three" in exc.value.message


def test_default_reference_image_assets_exist_and_load(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "veo_use_reference_images", True, raising=False)

    bundle = video_handlers._load_global_veo_reference_assets(correlation_id="corr-default-ref", strict=True)

    assert bundle is not None
    assert bundle["metadata"]["reference_image_count"] == 3
    assert [item["mime_type"] for item in bundle["reference_images"]] == ["image/png", "image/png", "image/png"]


def test_veo_client_payload_includes_asset_reference_images(monkeypatch):
    from app.adapters import veo_client as veo_adapter
    from app.adapters.veo_client import VeoClient

    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"name":"operations/reference-test"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"name": "operations/reference-test"}

    class FakeHttpClient:
        def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(veo_adapter, "get_settings", lambda: type("S", (), {"gemini_api_key": "test-key"})())
    VeoClient._instance = None
    client = VeoClient()
    client._http_client = FakeHttpClient()

    result = client.submit_video_generation(
        prompt="Prompt",
        negative_prompt="subtitles, watermark",
        correlation_id="corr",
        aspect_ratio="9:16",
        resolution="720p",
        duration_seconds=8,
        reference_images=[
            {"mime_type": "image/png", "data_base64": "Zmlyc3Q="},
            {"mime_type": "image/png", "data_base64": "c2Vjb25k"},
        ],
        model="veo-3.1-generate-001",
    )

    assert result["operation_id"] == "operations/reference-test"
    assert "negativePrompt" not in captured["json"]["parameters"]
    assert captured["json"]["instances"][0]["referenceImages"] == [
        {
            "image": {
                "bytesBase64Encoded": "Zmlyc3Q=",
                "mimeType": "image/png",
            },
            "referenceType": "asset",
        },
        {
            "image": {
                "bytesBase64Encoded": "c2Vjb25k",
                "mimeType": "image/png",
            },
            "referenceType": "asset",
        },
    ]


def test_veo_client_rejects_first_frame_and_reference_images_together(monkeypatch):
    from app.adapters import veo_client as veo_adapter
    from app.adapters.veo_client import VeoClient

    class FakeResponse:
        status_code = 200
        text = '{"name":"operations/illegal-test"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"name": "operations/illegal-test"}

    class FakeHttpClient:
        def post(self, url, headers, json):
            return FakeResponse()

    monkeypatch.setattr(veo_adapter, "get_settings", lambda: type("S", (), {"gemini_api_key": "test-key"})())
    VeoClient._instance = None
    client = VeoClient()
    client._http_client = FakeHttpClient()

    with pytest.raises(ValueError) as exc:
        client.submit_video_generation(
            prompt="Prompt",
            negative_prompt=None,
            correlation_id="corr",
            aspect_ratio="9:16",
            resolution="720p",
            duration_seconds=8,
            first_frame_image={"mime_type": "image/png", "data_base64": "aW1hZ2U="},
            reference_images=[{"mime_type": "image/png", "data_base64": "cmVm"}],
            model="veo-3.1-generate-001",
        )

    assert "referenceImages cannot be combined" in str(exc.value)


def test_veo_client_payload_logging_redacts_reference_image_base64(monkeypatch):
    from app.adapters import veo_client as veo_adapter
    from app.adapters.veo_client import VeoClient

    monkeypatch.setattr(veo_adapter, "get_settings", lambda: type("S", (), {"gemini_api_key": "test-key"})())
    VeoClient._instance = None
    client = VeoClient()

    logged_payload = client._payload_for_logging(
        {
            "instances": [
                {
                    "prompt": "Prompt",
                    "referenceImages": [
                        {
                            "image": {
                                "bytesBase64Encoded": "Zmlyc3Q=",
                                "mimeType": "image/png",
                            },
                            "referenceType": "asset",
                        }
                    ],
                }
            ],
            "parameters": {"aspectRatio": "9:16"},
        }
    )

    assert logged_payload["instances"][0]["referenceImages"][0]["image"]["bytesBase64Encoded"] == (
        "<redacted_base64:8_chars>"
    )


def test_submit_video_request_attaches_reference_images_to_veo_base(monkeypatch, tmp_path):
    captured = {}
    image_path = tmp_path / "front.png"
    image_path.write_bytes(b"front-image")

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "operations/ref-base",
                "status": "submitted",
                "provider_model": kwargs.get("model"),
            }

    settings = type(
        "S",
        (),
        {
            "veo_use_reference_images": True,
            "veo_reference_image_paths": str(image_path),
        },
    )()
    monkeypatch.setattr(video_handlers, "get_settings", lambda: settings)
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
        correlation_id="corr-ref-base",
        provider_duration_seconds=8,
    )

    assert captured["first_frame_image"] is None
    assert len(captured["reference_images"]) == 1
    assert base64.b64decode(captured["reference_images"][0]["data_base64"]) == b"front-image"
    assert result["provider_metadata"]["reference_image_count"] == 1
    assert result["provider_metadata"]["reference_images_enabled"] is True


def test_submit_video_request_skips_reference_images_when_disabled(monkeypatch, tmp_path):
    captured = {}
    image_path = tmp_path / "front.png"
    image_path.write_bytes(b"front-image")

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {"operation_id": "operations/text-base", "status": "submitted"}

    settings = type(
        "S",
        (),
        {
            "veo_use_reference_images": False,
            "veo_reference_image_paths": str(image_path),
        },
    )()
    monkeypatch.setattr(video_handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())

    video_handlers._submit_video_request(
        provider="veo_3_1",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr-text-base",
        provider_duration_seconds=8,
    )

    assert captured["reference_images"] is None
    assert captured["first_frame_image"] is None


def test_veo_extension_request_uses_video_without_reference_images(monkeypatch):
    from app.adapters import veo_client as veo_adapter
    from app.adapters.veo_client import VeoClient

    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"name":"operations/extension-test"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"name": "operations/extension-test"}

    class FakeHttpClient:
        def post(self, url, headers, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(veo_adapter, "get_settings", lambda: type("S", (), {"gemini_api_key": "test-key"})())
    VeoClient._instance = None
    client = VeoClient()
    client._http_client = FakeHttpClient()

    result = client.submit_video_extension(
        prompt="Continue the same presenter.",
        video_uri="gs://bucket/base.mp4",
        correlation_id="corr-extension",
        aspect_ratio="9:16",
        resolution="720p",
        duration_seconds=7,
        negative_prompt=None,
    )

    assert result["operation_id"] == "operations/extension-test"
    assert "durationSeconds" not in captured["json"]["parameters"]
    instance = captured["json"]["instances"][0]
    assert instance["video"]["uri"] == "gs://bucket/base.mp4"
    assert "referenceImages" not in instance
    assert "image" not in instance
    assert "lastFrame" not in instance



# --- Manual auto-derive (word-count -> tier) tests --------------------------
# These cover the bug where two-sentence long scripts were silently capped at
# tier 16 (~14.5s) and where >32s was unreachable.

@pytest.mark.parametrize(
    "word_count, expected_tier",
    [
        (1, 8),     # tiny script -> tier 8
        (20, 8),    # 8s of speech -> tier 8 (provider_target=8)
        (21, 16),   # ~8.4s of speech -> tier 16 (provider_target=15)
        (37, 16),   # ~14.8s of speech -> still tier 16 (15s)
        (38, 32),   # ~15.2s of speech -> tier 32 (29s)
        (72, 32),   # ~28.8s of speech -> tier 32
        (73, 48),   # ~29.2s of speech -> tier 48 (43s)
        (107, 48),  # ~42.8s of speech -> tier 48
        (108, 64),  # ~43.2s of speech -> tier 64 (57s)
        (142, 64),  # ~56.8s of speech -> tier 64
        (200, 64),  # overflow -> capped at tier 64 (max)
    ],
)
def test_manual_auto_derive_picks_tier_from_word_count(word_count, expected_tier):
    script = " ".join(["wort"] * word_count)
    seed_data = {"manual_draft": True, "script": script}
    tier = video_handlers._resolve_manual_target_length_tier(seed_data)
    assert tier == expected_tier, f"word_count={word_count}: expected {expected_tier}, got {tier}"


def test_manual_long_two_sentence_script_no_longer_caps_at_tier_16():
    """Regression: 60-word, two-sentence script was capped at tier 16 (~14.5s).
    With auto-derive it now picks tier 32 (~28.5s)."""
    script = (
        "Hallo Leute heute zeige ich euch wie ich in dreissig Tagen mein "
        "Leben komplett auf den Kopf gestellt habe und richtig krasse "
        "Ergebnisse erzielt habe in nur kurzer Zeit. Wenn ihr wirklich "
        "Erfolg haben wollt dann passt jetzt sehr gut auf weil ich euch "
        "alles erklaere was ihr wissen muesst um anzufangen heute."
    )
    seed_data = {"manual_draft": True, "script": script}
    tier = video_handlers._resolve_manual_target_length_tier(seed_data)
    assert tier == 32  # was 16 before the fix


def test_manual_long_single_sentence_script_picks_a_real_tier():
    """Regression: long script with no periods was capped at tier 8 by the
    old sentence-counter heuristic."""
    script = " ".join(["wort"] * 80)  # ~32s of speech, no periods
    seed_data = {"manual_draft": True, "script": script}
    tier = video_handlers._resolve_manual_target_length_tier(seed_data)
    assert tier == 48  # was 8 before the fix


def test_manual_empty_or_missing_script_returns_default_tier():
    assert video_handlers._resolve_manual_target_length_tier(None) == 8
    assert video_handlers._resolve_manual_target_length_tier({}) == 8
    assert video_handlers._resolve_manual_target_length_tier({"script": ""}) == 8
    assert video_handlers._resolve_manual_target_length_tier({"script": "   "}) == 8


def test_new_tier_profiles_are_resolvable():
    """Tier 48 and 64 must be valid duration profiles."""
    p48 = get_duration_profile(48)
    assert p48.target_length_tier == 48
    assert p48.veo_extension_hops == 5
    assert p48.provider_target_seconds == 43
    p64 = get_duration_profile(64)
    assert p64.target_length_tier == 64
    assert p64.veo_extension_hops == 7
    assert p64.provider_target_seconds == 57
