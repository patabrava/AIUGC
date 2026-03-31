import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.errors import ErrorCode, FlowForgeException
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


def test_generate_video_blocks_before_submit_when_quota_reservation_fails(monkeypatch):
    post = {
        "id": "post-1",
        "batch_id": "batch-1",
        "video_prompt_json": {"optimized_prompt": "Prompt"},
        "seed_data": {},
        "video_metadata": {},
    }
    fake_supabase = SimpleNamespace(client=_FakeSupabaseClient([post]))
    submit_mock = MagicMock()

    def _reject_reservation(**kwargs):
        raise FlowForgeException(
            code=ErrorCode.RATE_LIMIT,
            message="Blocked before submission",
            details={"blocked_before_submit": True},
            status_code=429,
        )

    monkeypatch.setattr("app.features.videos.handlers.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("app.features.videos.handlers.reserve_quota", _reject_reservation)
    monkeypatch.setattr("app.features.videos.handlers._submit_video_request", submit_mock)

    request = VideoGenerationRequest(provider="veo_3_1", aspect_ratio="9:16", resolution="720p", seconds=8)

    with pytest.raises(FlowForgeException) as exc_info:
        asyncio.run(generate_video("post-1", request))

    assert exc_info.value.code == ErrorCode.RATE_LIMIT
    assert exc_info.value.details["blocked_before_submit"] is True
    submit_mock.assert_not_called()


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

    request = BatchVideoGenerationRequest(provider="veo_3_1", aspect_ratio="9:16", resolution="720p", seconds=16)

    with pytest.raises(FlowForgeException) as exc_info:
        asyncio.run(generate_all_videos("batch-1", request))

    assert exc_info.value.code == ErrorCode.RATE_LIMIT
    assert submit_mock.call_count == 0
    assert released == [reservations[0]]
