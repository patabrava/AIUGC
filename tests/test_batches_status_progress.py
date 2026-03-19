"""Regression tests for batch seeding status progress payloads."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.features.batches import handlers as batch_handlers
from app.features.topics import handlers as topic_handlers


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


def test_get_batch_status_requeues_stalled_s1_batch(monkeypatch):
    scheduled = []
    started = []

    monkeypatch.setattr(
        batch_handlers,
        "get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "brand": "Recover",
            "state": "S1_SETUP",
            "updated_at": "2026-03-19T21:00:00+00:00",
            "post_type_counts": {"value": 3, "lifestyle": 4, "product": 0},
        },
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_posts_summary",
        lambda batch_id: {
            "posts_count": 0,
            "posts_by_state": {},
        },
    )
    monkeypatch.setattr(batch_handlers, "get_seeding_progress", lambda batch_id: None)
    monkeypatch.setattr(batch_handlers, "is_batch_discovery_active", lambda batch_id: False)
    monkeypatch.setattr(
        batch_handlers,
        "start_seeding_interaction",
        lambda batch_id, brand, expected_posts: started.append((batch_id, brand, expected_posts)),
    )
    monkeypatch.setattr(
        batch_handlers,
        "schedule_batch_discovery",
        lambda batch_id, reason: scheduled.append((batch_id, reason)) or True,
    )

    response = asyncio.run(batch_handlers.get_batch_status("batch-recover"))

    assert response.ok is True
    assert started == [("batch-recover", "Recover", 7)]
    assert scheduled == [("batch-recover", "status_recovery")]


def test_recover_stalled_batches_only_requeues_empty_s1_batches(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        topic_handlers,
        "list_batches",
        lambda archived=False, limit=25, offset=0: (
            [
                {
                    "id": "resume-me",
                    "state": "S1_SETUP",
                    "created_at": now.isoformat(),
                },
                {
                    "id": "has-posts",
                    "state": "S1_SETUP",
                    "created_at": now.isoformat(),
                },
                {
                    "id": "done",
                    "state": "S2_SEEDED",
                    "created_at": now.isoformat(),
                },
            ],
            3,
        ),
    )
    monkeypatch.setattr(
        topic_handlers,
        "get_posts_by_batch",
        lambda batch_id: [] if batch_id == "resume-me" else [{"id": "post-1"}],
    )
    monkeypatch.setattr(topic_handlers, "get_seeding_progress", lambda batch_id: None)

    scheduled = []
    monkeypatch.setattr(
        topic_handlers,
        "schedule_batch_discovery",
        lambda batch_id, reason: scheduled.append((batch_id, reason)) or True,
    )

    recovered = topic_handlers.recover_stalled_batches(limit=10)

    assert recovered == ["resume-me"]
    assert scheduled == [("resume-me", "startup_recovery")]


def test_recover_stalled_batches_skips_old_backlog_and_caps_recovery(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        topic_handlers,
        "list_batches",
        lambda archived=False, limit=25, offset=0: (
            [
                {
                    "id": "newest",
                    "state": "S1_SETUP",
                    "created_at": now.isoformat(),
                },
                {
                    "id": "second-newest",
                    "state": "S1_SETUP",
                    "created_at": (now - timedelta(minutes=10)).isoformat(),
                },
                {
                    "id": "stale-backlog",
                    "state": "S1_SETUP",
                    "created_at": (now - timedelta(hours=12)).isoformat(),
                },
            ],
            3,
        ),
    )
    monkeypatch.setattr(topic_handlers, "get_posts_by_batch", lambda batch_id: [])
    monkeypatch.setattr(topic_handlers, "get_seeding_progress", lambda batch_id: None)

    scheduled = []
    monkeypatch.setattr(
        topic_handlers,
        "schedule_batch_discovery",
        lambda batch_id, reason: scheduled.append((batch_id, reason)) or True,
    )

    recovered = topic_handlers.recover_stalled_batches(limit=1, max_age_hours=6)

    assert recovered == ["newest"]
    assert scheduled == [("newest", "startup_recovery")]


def test_seeding_interaction_emits_resumable_events():
    topic_handlers.clear_seeding_progress("batch-events")

    started = topic_handlers.start_seeding_interaction(
        batch_id="batch-events",
        brand="Demo",
        expected_posts=4,
    )
    updated = topic_handlers.update_seeding_progress(
        "batch-events",
        stage="researching",
        stage_label="Researching current source-backed topics",
        detail_message="Collecting current value topics from the model.",
        posts_created=1,
        expected_posts=4,
        is_retrying=False,
        retry_message=None,
    )

    all_events = topic_handlers.get_seeding_events("batch-events")
    replay_events = topic_handlers.get_seeding_events(
        "batch-events",
        last_event_id=all_events[0]["event_id"],
    )

    assert started["interaction_id"].startswith("seed_")
    assert updated["interaction_id"] == started["interaction_id"]
    assert all_events[0]["event_type"] == "interaction.start"
    assert any(event["event_type"] == "progress.update" for event in all_events)
    assert any(event["event_type"] == "content.delta" for event in all_events)
    assert any(event["event_type"] == "progress.post_created" for event in all_events)
    assert all(int(event["event_id"]) > int(all_events[0]["event_id"]) for event in replay_events)
