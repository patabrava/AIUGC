from app.core.video_profiles import (
    VIDEO_STATUS_CAPTION_PENDING,
    VIDEO_STATUS_CAPTION_PROCESSING,
    VIDEO_STATUS_CAPTION_COMPLETED,
    VIDEO_STATUS_CAPTION_FAILED,
    get_pollable_video_statuses,
    get_caption_pollable_statuses,
)


def test_caption_status_constants_exist():
    assert VIDEO_STATUS_CAPTION_PENDING == "caption_pending"
    assert VIDEO_STATUS_CAPTION_PROCESSING == "caption_processing"
    assert VIDEO_STATUS_CAPTION_COMPLETED == "caption_completed"
    assert VIDEO_STATUS_CAPTION_FAILED == "caption_failed"


def test_caption_statuses_not_in_video_pollable():
    pollable = get_pollable_video_statuses()
    assert "caption_pending" not in pollable
    assert "caption_processing" not in pollable
    assert "caption_completed" not in pollable
    assert "caption_failed" not in pollable


def test_get_caption_pollable_statuses():
    statuses = get_caption_pollable_statuses()
    assert "caption_pending" in statuses
    assert "caption_processing" not in statuses
