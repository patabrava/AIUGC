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


def test_generate_all_videos_releases_prior_reservations_if_batch_preflight_breaks(monkeypatch):
    posts = [
        {
            "id": "post-1",
            "batch_id": "batch-1",
            "video_prompt_json": {"optimized_prompt": "Prompt 1"},
            "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
            "video_status": "pending",
            "video_metadata": {},
        },
        {
            "id": "post-2",
            "batch_id": "batch-1",
            "video_prompt_json": {"optimized_prompt": "Prompt 2"},
            "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
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

    response = asyncio.run(generate_all_videos("batch-1", request))

    assert response.data["provider"] == "vertex_ai"
    assert submit_mock.call_count == 2
    assert released == []


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

    monkeypatch.setattr(
        "app.features.videos.handlers._resolve_global_veo_anchor_image",
        lambda correlation_id: (_ for _ in ()).throw(AssertionError("anchor image path must stay disabled")),
    )

    def _fake_submit(**kwargs):
        captured.update(kwargs)
        return {
            "operation_id": "operations/test-single",
            "status": "submitted",
            "requested_size": "720x1280",
            "provider_metadata": {"operation_id": "operations/test-single"},
        }

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.consume_quota", lambda **kwargs: {"ok": True})
    monkeypatch.setattr("app.features.videos.handlers.record_prompt_audit", lambda **kwargs: None)
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", _fake_submit)

    request = VideoGenerationRequest(provider="vertex_ai", aspect_ratio="9:16", resolution="720p", seconds=8)
    asyncio.run(generate_video("post-1", request))

    assert captured["first_frame_image"] is None
    assert captured["provider"] == "vertex_ai"


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

    monkeypatch.setattr(
        "app.features.videos.handlers._resolve_global_veo_anchor_image",
        lambda correlation_id: (_ for _ in ()).throw(AssertionError("anchor image path must stay disabled")),
    )

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


def test_generate_all_videos_routes_32s_vertex_submission_through_duration_profile(monkeypatch):
    posts = [
        {
            "id": "post-32",
            "batch_id": "batch-32",
            "video_prompt_json": {"veo_prompt": "Prompt 32"},
            "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz. Vierter Satz."},
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

    monkeypatch.setattr(
        "app.features.videos.handlers._resolve_global_veo_anchor_image",
        lambda correlation_id: (_ for _ in ()).throw(AssertionError("anchor image path must stay disabled")),
    )

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
