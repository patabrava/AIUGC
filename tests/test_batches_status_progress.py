"""Regression tests for batch seeding status progress payloads."""

import asyncio

from app.features.batches import handlers as batch_handlers


def test_get_batch_status_includes_live_progress(monkeypatch):
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "state": "S1_SETUP",
            "updated_at": "2026-03-16T10:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_posts_summary",
        lambda batch_id: {
            "posts_count": 2,
            "posts_by_state": {"value": 2},
        },
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_seeding_progress",
        lambda batch_id: {
            "stage": "collecting",
            "stage_label": "Collecting distinct topic candidates",
            "detail_message": "Filtering duplicate topics before writing posts.",
            "posts_created": 2,
            "expected_posts": 7,
            "current_post_type": "value",
            "attempt": 2,
            "max_attempts": 5,
            "is_retrying": False,
            "retry_message": None,
            "last_updated_at": "2026-03-16T10:00:05+00:00",
        },
    )

    response = asyncio.run(batch_handlers.get_batch_status("batch-123"))

    assert response.ok is True
    assert response.data["progress"]["stage"] == "collecting"
    assert response.data["progress"]["expected_posts"] == 7
    assert response.data["posts_count"] == 2


def test_get_batch_status_returns_none_when_no_live_progress(monkeypatch):
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "state": "S2_SEEDED",
            "updated_at": "2026-03-16T10:03:00+00:00",
        },
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_posts_summary",
        lambda batch_id: {
            "posts_count": 4,
            "posts_by_state": {"value": 2, "lifestyle": 2},
        },
    )
    monkeypatch.setattr(batch_handlers, "get_seeding_progress", lambda batch_id: None)

    response = asyncio.run(batch_handlers.get_batch_status("batch-456"))

    assert response.ok is True
    assert response.data["state"] == "S2_SEEDED"
    assert response.data["progress"] is None
