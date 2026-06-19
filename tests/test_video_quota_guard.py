import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import get_settings
from app.core.errors import ErrorCode, FlowForgeException
from app.core.video_profiles import get_duration_profile
from app.features.videos.handlers import (
    BatchVideoGenerationRequest,
    VideoGenerationRequest,
    generate_all_videos,
    generate_video,
)


def _valid_16s_script() -> str:
    return (
        "Erster langer Satz erklärt ruhig den Einstieg und setzt den Kontext für die Zuschauerin heute klar. "
        "Zweiter langer Satz führt die Beobachtung weiter und bleibt im gesprochenen Rhythmus."
    )


def _valid_32s_script() -> str:
    return (
        "Erster langer Satz erklärt ruhig den Einstieg und setzt den Kontext für die Zuschauerin heute klar. "
        "Zweiter langer Satz führt die Beobachtung weiter und bleibt natürlich im gesprochenen Rhythmus stabil. "
        "Dritter langer Satz nennt den konkreten Nutzen und verbindet ihn mit einer Alltagssituation direkt. "
        "Vierter langer Satz schließt den Gedanken sauber ab, zeigt den nächsten kleinen Schritt im Alltag "
        "und bleibt ohne neue Pointe oder unnötige visuelle Ablenkung."
    )


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self._data)


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _FakeSupabaseClient:
    def __init__(self, posts):
        self._posts = posts

    def table(self, name):
        if name == "posts":
            return _FakeTable(self._posts)
        raise AssertionError(f"Unexpected table access: {name}")


class _MutableSelectQuery:
    def __init__(self, rows):
        self._rows = rows
        self._filters = {}

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def execute(self):
        matched = [
            row for row in self._rows
            if all(row.get(column) == value for column, value in self._filters.items())
        ]
        return SimpleNamespace(data=matched)


class _MutableUpdateQuery:
    def __init__(self, rows, payload):
        self._rows = rows
        self._payload = payload
        self._filters = {}

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def execute(self):
        matched = []
        for row in self._rows:
            if all(row.get(column) == value for column, value in self._filters.items()):
                row.update(self._payload)
                matched.append(row)
        return SimpleNamespace(data=matched)


class _MutablePostsTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *args, **kwargs):
        return _MutableSelectQuery(self._rows)

    def update(self, payload):
        return _MutableUpdateQuery(self._rows, payload)


class _MutableSupabaseClient:
    def __init__(self, posts):
        self._posts = posts

    def table(self, name):
        if name == "posts":
            return _MutablePostsTable(self._posts)
        raise AssertionError(f"Unexpected table access: {name}")


def test_generate_video_blocks_before_submit_when_quota_reservation_fails(monkeypatch):
    post = {
        "id": "post-1",
        "batch_id": "batch-1",
        "video_prompt_json": {"optimized_prompt": "Prompt"},
        "seed_data": {},
        "video_metadata": {},
    }
    class _VideoTable(_FakeTable):
        def update(self, _payload):
            return self

    class _VideoClient(_FakeSupabaseClient):
        def table(self, _name):
            return _VideoTable(self._posts)

    fake_supabase = SimpleNamespace(client=_VideoClient([post]))
    submit_mock = MagicMock()

    def _reject_reservation(**kwargs):
        raise FlowForgeException(
            code=ErrorCode.RATE_LIMIT,
            message="Blocked before submission",
            details={"blocked_before_submit": True},
            status_code=429,
    )

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.get_batch_by_id", lambda batch_id: {"id": batch_id, "target_length_tier": None})
    monkeypatch.setattr("app.features.videos.handlers.quota_controls_bypassed", lambda: False)
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", _reject_reservation)
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr(
        "app.features.videos.handlers._submit_video_request",
        lambda **kwargs: {
            "operation_id": "operations/test-single",
            "status": "submitted",
            "requested_size": "720x1280",
            "provider_metadata": {"operation_id": "operations/test-single"},
            "provider_model": "vertex-ai",
            "estimated_duration_seconds": 180,
        },
    )

    request = VideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=8)

    asyncio.run(generate_video("post-1", request))

    submit_mock.assert_not_called()


def test_local_quota_guard_can_be_bypassed_for_multi_key_testing(monkeypatch):
    from app.features.videos import quota_guard

    settings = get_settings()
    monkeypatch.setattr(settings, "veo_disable_local_quota_guard", True)
    monkeypatch.setattr(
        quota_guard,
        "get_quota_snapshot",
        lambda **kwargs: {
            "frozen": True,
            "minute_remaining_units": 0,
            "minute_limit": 0,
            "daily_remaining_units": 0,
            "daily_limit": 0,
        },
    )
    rpc_mock = MagicMock(side_effect=AssertionError("quota RPC should not be used when bypass is enabled"))
    monkeypatch.setattr(quota_guard, "_rpc", rpc_mock)

    snapshot = quota_guard.ensure_immediate_submit_slot(requested_units=7)
    reservation = quota_guard.reserve_quota(
        provider="veo_3_1",
        post_id="post-1",
        batch_id="batch-1",
        reservation_key="reservation-1",
        requested_units=7,
        require_immediate_slot=True,
    )
    consumed = quota_guard.consume_quota(
        reservation_key="reservation-1",
        operation_id="operation-1",
        units=1,
    )
    released = quota_guard.release_quota(
        reservation_key="reservation-1",
        reason="test",
        final_status="released",
        error_code="test",
    )
    frozen = quota_guard.freeze_provider_quota(provider="veo_3_1", reason="test")
    quota_guard.maybe_freeze_after_provider_429(provider="veo_3_1", reason="test")

    assert snapshot["frozen"] is True
    assert reservation["bypassed"] is True
    assert consumed["bypassed"] is True
    assert released["bypassed"] is True
    assert frozen["bypassed"] is True
    rpc_mock.assert_not_called()


def test_model_specific_quota_error_does_not_freeze_provider(monkeypatch):
    from app.features.videos import quota_guard

    freeze_mock = MagicMock()
    monkeypatch.setattr(quota_guard, "freeze_provider_quota", freeze_mock)

    quota_guard.maybe_freeze_after_provider_429(
        provider="vertex_ai",
        reason=(
            "Quota exceeded for aiplatform.googleapis.com/"
            "long_running_online_prediction_requests_per_base_model "
            "with base model: veo-3.1-generate-001."
        ),
    )

    freeze_mock.assert_not_called()


def test_generate_all_videos_releases_prior_reservations_if_batch_preflight_breaks(monkeypatch):
    posts = [
        {
            "id": "post-1",
            "batch_id": "batch-1",
            "video_prompt_json": {"optimized_prompt": "Prompt 1"},
            "seed_data": {"script": _valid_16s_script()},
            "video_status": "pending",
            "video_metadata": {},
        },
        {
            "id": "post-2",
            "batch_id": "batch-1",
            "video_prompt_json": {"optimized_prompt": "Prompt 2"},
            "seed_data": {"script": _valid_16s_script()},
            "video_status": "pending",
            "video_metadata": {},
        },
    ]
    fake_supabase = SimpleNamespace(client=_FakeSupabaseClient(posts))
    submit_mock = MagicMock()
    released = []
    reservations = []

    def _reserve_quota(**kwargs):
        reservations.append(kwargs["reservation_key"])
        if len(reservations) == 2:
            raise FlowForgeException(
                code=ErrorCode.RATE_LIMIT,
                message="Blocked before submission",
                details={"blocked_before_submit": True},
                status_code=429,
            )
        return {"allowed": True}

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.quota_controls_bypassed", lambda: False)
    monkeypatch.setattr(
        "app.features.videos.handlers.get_batch_by_id",
        lambda batch_id: {"id": batch_id, "target_length_tier": 16},
    )
    monkeypatch.setattr("app.features.videos.handlers.ensure_immediate_submit_slot", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", _reserve_quota)
    monkeypatch.setattr(
        "app.features.videos.handlers.release_quota",
        lambda **kwargs: released.append(kwargs["reservation_key"]) or {"allowed": True},
    )
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", submit_mock)
    monkeypatch.setattr("app.features.videos.handlers.reconcile_batch_video_pipeline_state", lambda **kwargs: None)

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=16)

    with pytest.raises(FlowForgeException):
        asyncio.run(generate_all_videos("batch-1", request))

    submit_mock.assert_not_called()
    assert len(reservations) == 2
    assert released == [reservations[0]]


def test_duration_profile_cost_switches_when_experiment_flag_enabled(monkeypatch):
    from app.features.videos.quota_guard import chain_cost_units

    settings = get_settings()
    monkeypatch.setattr(settings, "veo_enable_efficient_long_route", True)

    profile_16 = get_duration_profile(16)
    profile_32 = get_duration_profile(32)

    assert profile_16.veo_base_seconds == 8
    assert profile_32.veo_base_seconds == 8
    assert chain_cost_units(profile_16, provider="veo_3_1") == 2
    assert chain_cost_units(profile_32, provider="veo_3_1") == 4
    assert chain_cost_units(profile_32, provider="vertex_ai") == 4


def test_generate_video_keeps_text_only_path_for_veo(monkeypatch):
    post = {
        "id": "post-1",
        "batch_id": "batch-1",
        "video_prompt_json": {"veo_prompt": "Prompt"},
        "seed_data": {},
        "video_metadata": {},
    }
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient([post]))
    captured = {}

    def _fake_submit(**kwargs):
        captured.update(kwargs)
        return {
            "operation_id": "operations/test-single",
            "status": "submitted",
            "requested_size": "720x1280",
            "provider_metadata": {"operation_id": "operations/test-single"},
        }

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.get_batch_by_id", lambda batch_id: {"id": batch_id, "target_length_tier": None})
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", _fake_submit)

    request = VideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=8)
    asyncio.run(generate_video("post-1", request))

    assert captured["first_frame_image"] is None
    assert captured["provider"] == "vertex_ai"


def test_generate_video_auto_resolves_manual_short_script_to_8s(monkeypatch):
    post = {
        "id": "post-1",
        "batch_id": "batch-1",
        "video_prompt_json": {"veo_prompt": "Prompt"},
        "seed_data": {
            "manual_draft": True,
            "script_review_status": "approved",
            "script": "Ich bin sehr gluecklich dass die App wieder funktioniert. Lets gooooo hahahahha",
        },
        "video_metadata": {},
    }
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient([post]))
    captured = {}

    def _fake_submit(**kwargs):
        captured.update(kwargs)
        return {
            "operation_id": "operations/test-single",
            "status": "submitted",
            "requested_size": "720x1280",
            "provider_metadata": {"operation_id": "operations/test-single"},
        }

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr(
        "app.features.videos.handlers.get_batch_by_id",
        lambda batch_id: {"id": batch_id, "creation_mode": "manual", "target_length_tier": 16},
    )
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", _fake_submit)

    request = VideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=16)
    asyncio.run(generate_video("post-1", request))

    assert captured["seconds"] == 8
    assert captured["provider_duration_seconds"] == 8


def test_generate_video_skips_quota_ledger_calls_when_bypass_enabled(monkeypatch):
    post = {
        "id": "post-1",
        "batch_id": "batch-1",
        "video_prompt_json": {"veo_prompt": "Prompt"},
        "seed_data": {},
        "video_metadata": {},
    }
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient([post]))
    reserve_mock = MagicMock()
    consume_mock = MagicMock()

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.get_batch_by_id", lambda batch_id: {"id": batch_id, "target_length_tier": None})
    monkeypatch.setattr("app.features.videos.handlers.quota_controls_bypassed", lambda: True)
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", reserve_mock)
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", consume_mock)
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr(
        "app.features.videos.handlers._submit_video_request",
        lambda **kwargs: {
            "operation_id": "operations/test-single",
            "status": "submitted",
            "requested_size": "720x1280",
            "provider_metadata": {"operation_id": "operations/test-single"},
        },
    )

    request = VideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=8)
    asyncio.run(generate_video("post-1", request))

    reserve_mock.assert_not_called()
    consume_mock.assert_not_called()


def test_generate_all_videos_keeps_text_only_path_for_every_veo_submit(monkeypatch):
    posts = [
        {
            "id": "post-1",
            "batch_id": "batch-1",
            "video_prompt_json": {"veo_prompt": "Prompt 1"},
            "seed_data": {"script": "Erster Satz."},
            "video_status": "pending",
            "video_metadata": {},
        },
        {
            "id": "post-2",
            "batch_id": "batch-1",
            "video_prompt_json": {"veo_prompt": "Prompt 2"},
            "seed_data": {"script": "Zweiter Satz."},
            "video_status": "pending",
            "video_metadata": {},
        },
    ]
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient(posts))
    captured_calls = []

    def _fake_submit(**kwargs):
        captured_calls.append(kwargs)
        return {
            "operation_id": f"operations/test-{len(captured_calls)}",
            "status": "submitted",
            "requested_size": "720x1280",
            "provider_metadata": {"operation_id": f"operations/test-{len(captured_calls)}"},
        }

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.get_batch_by_id", lambda batch_id: {"id": batch_id, "target_length_tier": None})
    monkeypatch.setattr("app.features.videos.handlers.ensure_immediate_submit_slot", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.release_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers.reconcile_batch_video_pipeline_state", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", _fake_submit)

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=8)
    asyncio.run(generate_all_videos("batch-1", request))

    assert len(captured_calls) == 2
    for captured in captured_calls:
        assert captured["first_frame_image"] is None
        assert captured["provider"] == "vertex_ai"


def test_generate_all_character_consistency_uses_approved_scene_reference_set_for_segmented_submit(monkeypatch):
    from app.features.characters.schemas import SceneReferenceSetSummary

    posts = [
        {
            "id": "post-1",
            "batch_id": "batch-1",
            "post_type": "value",
            "video_prompt_json": {"veo_prompt": "Prompt 1", "scene": "Bathroom"},
            "seed_data": {"script": _valid_16s_script(), "script_review_status": "approved"},
            "video_status": "pending",
            "video_metadata": {},
        },
    ]
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient(posts))
    batch = {
        "id": "batch-1",
        "target_length_tier": 16,
        "creation_mode": "character_consistency",
        "actor_identity_id": "actor-1",
    }
    reference_set = SceneReferenceSetSummary.from_rows(
        post_id="post-1",
        reference_set_id="set-1",
        rows=[
            {
                "id": "scene-front",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": "https://cdn/front.png",
                "scene_key": "home_living_room_advice_a",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {
                    "reference_set_id": "set-1",
                    "angle_key": "front_mid",
                    "identity_lock_contract": {
                        "provider": "magnific",
                        "provider_lora_id": "1786946",
                        "provider_lora_name": "ayra-actor-longchar-20260521",
                        "actor_identity_id": "actor-1",
                        "identity_strength": 100,
                        "prompt_lora_handle_required": True,
                        "styling_characters_required": True,
                    },
                    "mystic_request": {
                        "prompt": "Photorealistic still of @ayra-actor-longchar-20260521::100.",
                        "styling": {"characters": [{"id": "1786946", "strength": 100}]},
                    },
                },
                "identity_gate_result": {
                    "status": "passed",
                    "details": {
                        "scene_consistency_set_approved": True,
                        "actor_identity_match_confirmed": True,
                        "reference_set_id": "set-1",
                    },
                },
            },
            {
                "id": "scene-left",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": "https://cdn/left.png",
                "scene_key": "home_living_room_advice_a",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {
                    "reference_set_id": "set-1",
                    "angle_key": "left_three_quarter",
                    "identity_lock_contract": {
                        "provider": "magnific",
                        "provider_lora_id": "1786946",
                        "provider_lora_name": "ayra-actor-longchar-20260521",
                        "actor_identity_id": "actor-1",
                        "identity_strength": 100,
                        "prompt_lora_handle_required": True,
                        "styling_characters_required": True,
                    },
                    "mystic_request": {
                        "prompt": "Photorealistic still of @ayra-actor-longchar-20260521::100.",
                        "styling": {"characters": [{"id": "1786946", "strength": 100}]},
                    },
                },
                "identity_gate_result": {
                    "status": "passed",
                    "details": {
                        "scene_consistency_set_approved": True,
                        "actor_identity_match_confirmed": True,
                        "reference_set_id": "set-1",
                    },
                },
            },
            {
                "id": "scene-profile",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": "https://cdn/profile.png",
                "scene_key": "home_living_room_advice_a",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {
                    "reference_set_id": "set-1",
                    "angle_key": "right_profile",
                    "identity_lock_contract": {
                        "provider": "magnific",
                        "provider_lora_id": "1786946",
                        "provider_lora_name": "ayra-actor-longchar-20260521",
                        "actor_identity_id": "actor-1",
                        "identity_strength": 100,
                        "prompt_lora_handle_required": True,
                        "styling_characters_required": True,
                    },
                    "mystic_request": {
                        "prompt": "Photorealistic still of @ayra-actor-longchar-20260521::100.",
                        "styling": {"characters": [{"id": "1786946", "strength": 100}]},
                    },
                },
                "identity_gate_result": {
                    "status": "passed",
                    "details": {
                        "scene_consistency_set_approved": True,
                        "actor_identity_match_confirmed": True,
                        "reference_set_id": "set-1",
                    },
                },
            },
        ],
    )
    captured = {}

    def _fake_segmented_submit(**kwargs):
        captured.update(kwargs)
        return {
            "operation_ids": ["operations/seg-0"],
            "results": [
                {
                    "operation_id": "operations/seg-0",
                    "status": "submitted",
                    "provider_model": "veo-3.1-generate-001",
                    "provider_metadata": {
                        "operation_id": "operations/seg-0",
                        "source": "actor_identity_scene_reference_set",
                    },
                }
            ],
            "segment_count": 2,
            "prompts": ["Segment prompt 1", "Segment prompt 2"],
            "beats": ["Segment beat 1", "Segment beat 2"],
            "seed": 123,
            "i2v_locked": True,
            "i2v_model": "veo-3.1-generate-001",
            "i2v_output_gcs_uri": "gs://bucket/out/",
        }

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.get_batch_by_id", lambda batch_id: dict(batch))
    monkeypatch.setattr("app.features.videos.handlers.sync_character_consistency_batch_actor", lambda row, correlation_id: row)
    monkeypatch.setattr(
        "app.features.videos.handlers.character_queries.get_approved_video_actor_scene_reference_set_for_post",
        lambda post_id: reference_set,
    )
    monkeypatch.setattr(
        "app.core.video_profiles.get_settings",
        lambda: SimpleNamespace(veo_enable_segmented_route=True, veo_enable_efficient_long_route=True),
    )
    monkeypatch.setattr(
        "app.features.videos.handlers._load_or_build_video_prompt",
        lambda *, post, supabase_client, correlation_id, batch: post["video_prompt_json"],
    )
    monkeypatch.setattr("app.features.videos.handlers._resolve_canonical_scene_asset_for_submission", lambda **kwargs: None)
    monkeypatch.setattr(
        "app.features.videos.handlers._apply_canonical_scene_to_video_prompt",
        lambda video_prompt, seed_data, canonical_scene_asset, creation_mode: video_prompt,
    )
    monkeypatch.setattr("app.features.videos.handlers.ensure_immediate_submit_slot", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.release_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers.reconcile_batch_video_pipeline_state", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers._submit_segmented_post", _fake_segmented_submit)

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=16)
    response = asyncio.run(generate_all_videos("batch-1", request))

    assert response.data["submitted_count"] == 1
    assert captured["scene_reference_set"].reference_set_id == "set-1"
    assert captured["submission_plan"]["profile"].route == "veo_segmented"
    assert posts[0]["video_status"] == "submitted"
    assert posts[0]["video_metadata"]["video_pipeline_route"] == "veo_segmented"
    assert posts[0]["video_metadata"]["operation_ids"] == ["operations/seg-0"]
    assert posts[0]["video_metadata"]["i2v_lock"]["state"] == "pending"
    assert posts[0]["video_metadata"]["i2v_lock"]["beats"] == ["Segment beat 1", "Segment beat 2"]
    assert posts[0]["video_metadata"]["veo_segment_ops"][0]["kind"] == "anchor"
    assert posts[0]["video_metadata"]["veo_segment_ops"][1]["kind"] == "i2v"
    assert posts[0]["video_metadata"]["veo_segment_ops"][1]["operation_id"] is None


def test_generate_all_character_consistency_prepares_lora_scene_reference_set_when_missing(monkeypatch):
    from app.features.characters.schemas import SceneReferenceSetSummary
    from app.features.videos import handlers as video_handlers

    posts = [
        {
            "id": "post-1",
            "batch_id": "batch-1",
            "post_type": "value",
            "video_prompt_json": {"veo_prompt": "Prompt 1", "scene": "Bathroom"},
            "seed_data": {"script": _valid_16s_script(), "script_review_status": "approved"},
            "video_status": "pending",
            "video_metadata": {},
        },
    ]
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient(posts))
    batch = {
        "id": "batch-1",
        "target_length_tier": 16,
        "creation_mode": "character_consistency",
        "actor_identity_id": "actor-1",
    }
    reference_set = SceneReferenceSetSummary.from_rows(
        post_id="post-1",
        reference_set_id="set-prepared",
        rows=[
            {
                "id": f"scene-{angle_key}",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": f"https://cdn/{angle_key}.png",
                "scene_key": "home_living_room_advice_a",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {
                    "reference_set_id": "set-prepared",
                    "angle_key": angle_key,
                    "identity_lock_contract": {
                        "provider": "magnific",
                        "provider_lora_id": "1786946",
                        "provider_lora_name": "ayra-actor-longchar-20260521",
                        "actor_identity_id": "actor-1",
                        "identity_strength": 100,
                        "prompt_lora_handle_required": True,
                        "styling_characters_required": True,
                    },
                    "mystic_request": {
                        "prompt": "Photorealistic still of @ayra-actor-longchar-20260521::100.",
                        "styling": {"characters": [{"id": "1786946", "strength": 100}]},
                    },
                },
                "identity_gate_result": {
                    "status": "passed",
                    "details": {
                        "scene_consistency_set_approved": True,
                        "actor_identity_match_confirmed": True,
                        "reference_set_id": "set-prepared",
                    },
                },
            }
            for angle_key in ("front_mid", "left_three_quarter", "right_profile")
        ],
    )
    prepared = []
    captured = {}

    def _fake_prepare(**kwargs):
        prepared.append(kwargs)
        return reference_set

    def _fake_segmented_submit(**kwargs):
        captured.update(kwargs)
        return {
            "operation_ids": ["operations/seg-0"],
            "results": [
                {
                    "operation_id": "operations/seg-0",
                    "status": "submitted",
                    "provider_model": "veo-3.1-generate-001",
                    "provider_metadata": {
                        "operation_id": "operations/seg-0",
                        "source": "actor_identity_scene_reference_set",
                    },
                }
            ],
            "segment_count": 2,
            "prompts": ["Segment prompt 1", "Segment prompt 2"],
            "beats": ["Segment beat 1", "Segment beat 2"],
            "seed": 123,
            "i2v_locked": True,
            "i2v_model": "veo-3.1-generate-001",
            "i2v_output_gcs_uri": "gs://bucket/out/",
        }

    monkeypatch.setattr(video_handlers, "get_supabase", lambda: fake_supabase)
    monkeypatch.setattr(video_handlers, "get_batch_by_id", lambda batch_id: dict(batch))
    monkeypatch.setattr(video_handlers, "sync_character_consistency_batch_actor", lambda row, correlation_id: row)
    monkeypatch.setattr(video_handlers.character_queries, "get_approved_scene_reference_set_for_post", lambda post_id: None)
    monkeypatch.setattr(video_handlers, "_ensure_actor_lora_scene_reference_set_for_video", _fake_prepare, raising=False)
    monkeypatch.setattr(
        "app.core.video_profiles.get_settings",
        lambda: SimpleNamespace(veo_enable_segmented_route=True, veo_enable_efficient_long_route=True),
    )
    monkeypatch.setattr(
        video_handlers,
        "_load_or_build_video_prompt",
        lambda *, post, supabase_client, correlation_id, batch: post["video_prompt_json"],
    )
    monkeypatch.setattr(video_handlers, "_resolve_canonical_scene_asset_for_submission", lambda **kwargs: None)
    monkeypatch.setattr(
        video_handlers,
        "_apply_canonical_scene_to_video_prompt",
        lambda video_prompt, seed_data, canonical_scene_asset, creation_mode: video_prompt,
    )
    monkeypatch.setattr(video_handlers, "ensure_immediate_submit_slot", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(video_handlers, "reserve_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(video_handlers, "consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(video_handlers, "release_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(video_handlers, "record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr(video_handlers, "reconcile_batch_video_pipeline_state", lambda **kwargs: None)
    monkeypatch.setattr(video_handlers, "_submit_segmented_post", _fake_segmented_submit)

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=16)
    response = asyncio.run(generate_all_videos("batch-1", request))

    assert response.data["submitted_count"] == 1
    assert prepared and prepared[0]["post"]["id"] == "post-1"
    assert prepared[0]["batch"]["actor_identity_id"] == "actor-1"
    assert captured["scene_reference_set"].reference_set_id == "set-prepared"


def test_generate_all_videos_persists_unexpected_segmented_submit_failure(monkeypatch):
    posts = [
        {
            "id": "post-segmented-fail",
            "batch_id": "batch-32",
            "post_type": "value",
            "video_prompt_json": {"veo_prompt": "Prompt 32", "audio": {"dialogue": _valid_32s_script()}},
            "seed_data": {"script": _valid_32s_script(), "script_review_status": "approved"},
            "video_status": "failed",
            "video_metadata": {"prior": "value"},
        },
    ]
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient(posts))

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr(
        "app.features.videos.handlers.get_batch_by_id",
        lambda batch_id: {"id": batch_id, "target_length_tier": 32, "creation_mode": "automated"},
    )
    monkeypatch.setattr(
        "app.core.video_profiles.get_settings",
        lambda: SimpleNamespace(veo_enable_segmented_route=True, veo_enable_efficient_long_route=True),
    )
    monkeypatch.setattr("app.features.videos.handlers.quota_controls_bypassed", lambda: True)
    monkeypatch.setattr("app.features.videos.handlers.release_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers.reconcile_batch_video_pipeline_state", lambda **kwargs: None)
    monkeypatch.setattr(
        "app.features.videos.handlers._submit_segmented_post",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Vertex credentials missing")),
    )

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=32)
    response = asyncio.run(generate_all_videos("batch-32", request))

    assert response.data["submitted_count"] == 0
    assert response.data["skipped_count"] == 1
    assert response.data["skipped_posts"] == [
        {
            "post_id": "post-segmented-fail",
            "reason": "unexpected_submission_error",
            "stage": "segmented_submit",
            "code": "RuntimeError",
            "message": "Vertex credentials missing",
        }
    ]
    assert posts[0]["video_status"] == "failed"
    assert posts[0]["video_provider"] == "vertex_ai"
    assert posts[0]["video_metadata"]["prior"] == "value"
    assert posts[0]["video_metadata"]["error"] == "Vertex credentials missing"
    assert posts[0]["video_metadata"]["error_code"] == "unexpected_submission_error"
    assert posts[0]["video_metadata"]["video_pipeline_route"] == "veo_segmented"


def test_generate_all_videos_routes_32s_vertex_submission_through_duration_profile(monkeypatch):
    posts = [
        {
            "id": "post-32",
            "batch_id": "batch-32",
            "video_prompt_json": {"veo_prompt": "Prompt 32"},
            "seed_data": {"script": _valid_32s_script()},
            "video_status": "pending",
            "video_metadata": {},
        },
    ]
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient(posts))
    captured_calls = []

    def _fake_submit(**kwargs):
        captured_calls.append(kwargs)
        return {
            "operation_id": "operations/test-32",
            "status": "submitted",
            "provider_model": "vertex_ai",
            "requested_size": "720x1280",
            "provider_metadata": {"operation_id": "operations/test-32"},
        }

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.get_batch_by_id", lambda batch_id: {"id": batch_id, "target_length_tier": 32})
    monkeypatch.setattr("app.features.videos.handlers.ensure_immediate_submit_slot", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.release_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers.reconcile_batch_video_pipeline_state", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", _fake_submit)

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=32)
    asyncio.run(generate_all_videos("batch-32", request))

    assert len(captured_calls) == 1
    captured = captured_calls[0]
    assert captured["provider"] == "vertex_ai"
    assert captured["seconds"] == 32
    assert captured["provider_duration_seconds"] == 8


def test_generate_all_videos_backfills_missing_prompts_from_seed_data(monkeypatch):
    posts = [
        {
            "id": "post-1",
            "batch_id": "batch-1",
            "video_prompt_json": None,
            "seed_data": {
                "script": "Erster Satz. Zweiter Satz. Dritter Satz.",
                "script_review_status": "approved",
            },
            "video_status": "pending",
            "video_metadata": {},
        },
        {
            "id": "post-2",
            "batch_id": "batch-1",
            "video_prompt_json": None,
            "seed_data": {
                "script_review_status": "removed",
                "video_excluded": True,
            },
            "video_status": "pending",
            "video_metadata": {},
        },
    ]
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient(posts))
    captured_calls = []

    def _fake_submit(**kwargs):
        captured_calls.append(kwargs)
        return {
            "operation_id": "operations/test-1",
            "status": "submitted",
            "requested_size": "720x1280",
            "provider_metadata": {"operation_id": "operations/test-1"},
        }

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.get_batch_by_id", lambda batch_id: {"id": batch_id, "target_length_tier": None})
    monkeypatch.setattr("app.features.videos.handlers.ensure_immediate_submit_slot", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.release_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers.reconcile_batch_video_pipeline_state", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", _fake_submit)

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=8)
    asyncio.run(generate_all_videos("batch-1", request))

    assert len(captured_calls) == 1
    assert posts[0]["video_prompt_json"] is not None
    assert "optimized_prompt" in posts[0]["video_prompt_json"]
    assert posts[1]["video_prompt_json"] is None


def test_generate_all_videos_aborts_before_provider_for_underlength_32s_lifestyle(monkeypatch):
    posts = [
        {
            "id": "bad-lifestyle",
            "batch_id": "batch-32",
            "post_type": "lifestyle",
            "video_prompt_json": {"audio": {"dialogue": " ".join(["wort"] * 24) + "."}},
            "seed_data": {
                "script": " ".join(["wort"] * 24) + ".",
                "target_length_tier": 32,
                "script_review_status": "approved",
            },
            "video_status": "pending",
            "video_metadata": {},
        }
    ]
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient(posts))
    captured_calls = []

    def _fake_submit(**kwargs):
        captured_calls.append(kwargs)
        return {"operation_id": "operations/should-not-run", "status": "submitted"}

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr(
        "app.features.videos.handlers.get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "target_length_tier": 32,
            "post_type_counts": {"value": 0, "lifestyle": 1, "product": 0},
        },
    )
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", _fake_submit)

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=32)

    with pytest.raises(Exception) as exc:
        asyncio.run(generate_all_videos("batch-32", request))

    assert "lifestyle 32s script has 24 words" in str(exc.value)
    assert captured_calls == []


def test_generate_all_videos_persists_failed_post_when_submit_errors(monkeypatch):
    posts = [
        {
            "id": "post-fail",
            "batch_id": "batch-1",
            "post_type": "value",
            "video_prompt_json": {"veo_prompt": "Prompt fail"},
            "seed_data": {"script": _valid_16s_script(), "script_review_status": "approved"},
            "video_status": "pending",
            "video_metadata": {},
        },
        {
            "id": "post-ok",
            "batch_id": "batch-1",
            "post_type": "lifestyle",
            "video_prompt_json": {"veo_prompt": "Prompt ok"},
            "seed_data": {"script": _valid_16s_script(), "script_review_status": "approved"},
            "video_status": "pending",
            "video_metadata": {},
        },
    ]
    fake_supabase = SimpleNamespace(client=_MutableSupabaseClient(posts))
    submit_calls = []

    def _fake_submit(**kwargs):
        submit_calls.append(kwargs["correlation_id"])
        if kwargs["correlation_id"].endswith("post-fail"):
            raise FlowForgeException(
                code=ErrorCode.THIRD_PARTY_FAIL,
                message="Vertex AI video submission failed",
                details={
                    "provider": "vertex_ai",
                    "status_code": 400,
                    "response": {"error": {"message": "Prompt blocked"}},
                    "response_body": '{"error":{"message":"Prompt blocked"}}',
                },
                status_code=503,
            )
        return {
            "operation_id": "operations/test-ok",
            "status": "submitted",
            "requested_size": "720x1280",
            "provider_metadata": {"operation_id": "operations/test-ok"},
        }

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr(
        "app.features.videos.handlers.get_batch_by_id",
        lambda batch_id: {"id": batch_id, "target_length_tier": 16},
    )
    monkeypatch.setattr("app.features.videos.handlers.ensure_immediate_submit_slot", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.release_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers.reconcile_batch_video_pipeline_state", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", _fake_submit)

    request = BatchVideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=16)
    response = asyncio.run(generate_all_videos("batch-1", request))

    assert response.data["submitted_count"] == 1
    assert response.data["skipped_count"] == 1
    assert posts[0]["video_status"] == "failed"
    assert posts[0]["video_metadata"]["provider_status_code"] == 400
    assert posts[0]["video_metadata"]["error"] == "Vertex AI video submission failed"
    assert posts[1]["video_status"] == "extended_submitted"
