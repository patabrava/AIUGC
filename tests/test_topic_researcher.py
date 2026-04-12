"""Tests for the topic researcher worker."""
import os
import time
from datetime import datetime, timezone, timedelta
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from unittest.mock import patch, MagicMock


def test_should_run_no_previous_runs():
    """Should run immediately if no previous cron runs in DB."""
    with patch("workers.topic_researcher.get_latest_cron_run", return_value=None):
        from workers.topic_researcher import _get_last_run_timestamp
        ts = _get_last_run_timestamp()
    assert ts == 0.0


def test_should_run_recent_run():
    """Should not run if last completed run was recent."""
    from workers.topic_researcher import _get_last_run_timestamp, RESEARCH_INTERVAL_SECONDS
    recent_time = "2026-03-24T12:00:00+00:00"
    with patch("workers.topic_researcher.get_latest_cron_run", return_value={
        "completed_at": recent_time,
        "status": "completed",
    }):
        ts = _get_last_run_timestamp()
    assert ts > 0.0


def test_startup_gate_uses_active_running_wrapper():
    """A live running wrapper should suppress a duplicate startup launch."""
    from workers.topic_researcher import _get_active_cron_timestamp

    running_time = "2026-03-24T12:30:00+00:00"
    with patch("workers.topic_researcher.get_latest_cron_run", return_value={
        "created_at": running_time,
        "updated_at": running_time,
        "started_at": running_time,
        "status": "running",
    }):
        ts = _get_active_cron_timestamp()

    assert ts > 0.0


def test_research_single_topic_success():
    """Successfully researches a single topic through the 3-stage pipeline."""
    from workers.topic_researcher import _research_single_topic

    with patch("workers.topic_researcher._harvest_seed_topic_to_bank") as mock_harvest, \
         patch("workers.topic_researcher.get_all_topics_from_registry", return_value=[]):
        mock_harvest.return_value = [{"id": "topic-1", "title": "Test Topic"}]
        result = _research_single_topic(
            seed_topic="Test Seed",
            post_type="value",
            tiers=[8, 16, 32],
        )
    assert result is not None
    assert len(result) > 0
    assert mock_harvest.call_count == 1
    assert mock_harvest.call_args.kwargs["target_length_tier"] == 8


def test_research_single_topic_normalizes_harvest_summary():
    """Harvest summaries should be converted into row lists before collection."""
    from workers.topic_researcher import _research_single_topic

    with patch("workers.topic_researcher._harvest_seed_topic_to_bank") as mock_harvest, \
         patch("workers.topic_researcher.get_all_topics_from_registry", return_value=[]):
        mock_harvest.return_value = {"stored_rows": [{"id": "topic-1", "title": "Test Topic"}]}
        result = _research_single_topic(
            seed_topic="Test Seed",
            post_type="value",
            tiers=[8],
        )
    assert result == [{"id": "topic-1", "title": "Test Topic"}]


def test_research_single_topic_failure():
    """Handles failure gracefully, returns None."""
    from workers.topic_researcher import _research_single_topic

    with patch("workers.topic_researcher._harvest_seed_topic_to_bank", side_effect=Exception("Gemini error")), \
         patch("workers.topic_researcher.get_all_topics_from_registry", return_value=[]):
        result = _research_single_topic(
            seed_topic="Bad Seed",
            post_type="value",
            tiers=[8, 16, 32],
        )
    assert result is None


def test_run_discovery_cycle():
    """Full cycle: selects seeds, researches them, tracks in DB."""
    from workers.topic_researcher import run_discovery_cycle

    with patch("workers.topic_researcher.select_seeds") as mock_select, \
         patch("workers.topic_researcher.create_cron_run") as mock_create, \
         patch("workers.topic_researcher.update_cron_run") as mock_update, \
         patch("workers.topic_researcher._research_single_topic") as mock_research, \
         patch("workers.topic_researcher.count_selectable_topic_families", return_value=0):

        mock_select.return_value = (["Topic A", "Topic B"], "yaml_bank")
        mock_create.return_value = {"id": "run-1", "status": "running"}
        mock_research.return_value = [{"id": "t-1", "title": "Topic A"}]
        mock_update.return_value = {"id": "run-1", "status": "completed"}

        run_discovery_cycle()

    mock_create.assert_called_once()
    assert mock_research.call_count == 2
    assert mock_update.call_count == 3
    update_kwargs = mock_update.call_args
    assert update_kwargs[1]["status"] == "completed"


def test_run_discovery_cycle_no_seeds():
    """When no seeds available, creates run with 0 topics."""
    from workers.topic_researcher import run_discovery_cycle

    with patch("workers.topic_researcher.select_seeds") as mock_select, \
         patch("workers.topic_researcher.create_cron_run") as mock_create, \
         patch("workers.topic_researcher.update_cron_run") as mock_update, \
         patch("workers.topic_researcher.count_selectable_topic_families", return_value=0):

        mock_select.return_value = ([], "yaml_bank")
        mock_create.return_value = {"id": "run-1", "status": "running"}
        mock_update.return_value = {"id": "run-1", "status": "completed"}

        run_discovery_cycle()

    mock_update.assert_called_once()
    assert mock_update.call_args[1]["topics_completed"] == 0


def test_run_discovery_cycle_does_not_skip_on_high_coverage():
    """High selectable coverage should not suppress the daily research cycle."""
    from workers.topic_researcher import run_discovery_cycle

    with patch("workers.topic_researcher.select_seeds") as mock_select, \
         patch("workers.topic_researcher.create_cron_run") as mock_create, \
         patch("workers.topic_researcher.update_cron_run") as mock_update, \
         patch("workers.topic_researcher._research_single_topic") as mock_research, \
         patch("workers.topic_researcher.count_selectable_topic_families", return_value=999):

        mock_select.return_value = (["Topic A"], "yaml_bank")
        mock_create.return_value = {"id": "run-1", "status": "running"}
        mock_research.return_value = [{"id": "t-1", "title": "Topic A"}]
        mock_update.return_value = {"id": "run-1", "status": "completed"}

        run_discovery_cycle()

    mock_create.assert_called_once()
    assert mock_research.call_count == 1
    assert mock_update.call_args[1]["status"] == "completed"


def test_run_discovery_cycle_marks_run_failed_on_unhandled_error():
    """Escaped exceptions should close the cron wrapper as failed."""
    from workers.topic_researcher import run_discovery_cycle

    with patch("workers.topic_researcher.select_seeds") as mock_select, \
         patch("workers.topic_researcher.create_cron_run") as mock_create, \
         patch("workers.topic_researcher.update_cron_run") as mock_update, \
         patch("workers.topic_researcher._research_single_topic", side_effect=RuntimeError("boom")), \
         patch("workers.topic_researcher.count_selectable_topic_families", return_value=0):

        mock_select.return_value = (["Topic A"], "yaml_bank")
        mock_create.return_value = {"id": "run-1", "status": "running"}
        mock_update.return_value = {"id": "run-1", "status": "failed"}

        run_discovery_cycle()

    assert mock_update.call_args[1]["status"] == "failed"
    assert mock_update.call_args[1]["error_message"] == "boom"


def test_topic_worker_tick_runs_research_before_audit_when_due():
    """The daily tick should audit first and then run discovery when due."""
    from workers import topic_worker

    calls = []

    def fake_research(now, last_research_run):
        calls.append("research")
        return now

    def fake_audit(now, last_audit_run):
        calls.append("audit")
        return now

    with patch.object(topic_worker, "_reconcile_stale_running_cron_run"), \
         patch.object(topic_worker, "_maybe_run_research", side_effect=fake_research), \
         patch.object(topic_worker, "_maybe_run_audit", side_effect=fake_audit):
        topic_worker.run_topic_worker_tick(last_audit_run=0.0, last_research_run=0.0, now=1_000.0)

    assert calls == ["audit", "research"]


def test_topic_worker_tick_skips_research_when_same_utc_day():
    """When the last research run happened today, the worker should only audit."""
    from workers import topic_worker

    calls = []

    def fake_research(now, last_research_run):
        calls.append("research")
        return last_research_run

    def fake_audit(now, last_audit_run):
        calls.append("audit")
        return now

    same_day = datetime(2026, 4, 12, 1, 0, tzinfo=timezone.utc).timestamp()

    with patch.object(topic_worker, "_reconcile_stale_running_cron_run"), \
         patch.object(topic_worker, "_maybe_run_research", side_effect=fake_research), \
         patch.object(topic_worker, "_maybe_run_audit", side_effect=fake_audit):
        topic_worker.run_topic_worker_tick(
            last_audit_run=0.0,
            last_research_run=same_day,
            now=datetime(2026, 4, 12, 18, 0, tzinfo=timezone.utc).timestamp(),
        )

    assert calls == ["audit"]


def test_reconcile_stale_running_cron_run_finalizes_wrapper():
    """A stale running wrapper with no active child runs should be finalized."""
    from workers.topic_researcher import _reconcile_stale_running_cron_run

    stale_started = datetime.now(timezone.utc) - timedelta(hours=2)
    stale_updated = datetime.now(timezone.utc) - timedelta(hours=1)
    latest = {
        "id": "run-1",
        "started_at": stale_started.isoformat(),
        "updated_at": stale_updated.isoformat(),
        "status": "running",
        "topics_requested": 2,
        "topics_completed": 2,
        "topics_failed": 0,
    }
    completed_child = {
        "id": "child-1",
        "created_at": (stale_started + timedelta(minutes=5)).isoformat(),
        "status": "completed",
        "result_summary": {"topic_registry_id": "topic-1"},
    }

    with patch("workers.topic_researcher.get_latest_cron_run", return_value=latest), \
         patch("workers.topic_researcher.list_topic_research_runs") as mock_runs, \
         patch("workers.topic_researcher.update_cron_run") as mock_update:
        mock_runs.side_effect = [[], [completed_child], []]
        result = _reconcile_stale_running_cron_run(max_age_seconds=60)

    assert result is not None
    assert mock_update.call_args[1]["status"] == "completed"
    assert mock_update.call_args[1]["topics_completed"] == 2


def test_reconcile_stale_running_cron_run_ignores_unrelated_later_running_children():
    """Unrelated running topic research rows should not block stale cron recovery forever."""
    from workers.topic_researcher import _reconcile_stale_running_cron_run

    stale_started = datetime.now(timezone.utc) - timedelta(days=2)
    stale_updated = stale_started + timedelta(minutes=15)
    latest = {
        "id": "run-1",
        "started_at": stale_started.isoformat(),
        "updated_at": stale_updated.isoformat(),
        "status": "running",
        "topics_requested": 5,
        "topics_completed": 0,
        "topics_failed": 3,
    }
    unrelated_running_child = {
        "id": "child-running-1",
        "created_at": (stale_updated + timedelta(hours=2)).isoformat(),
        "status": "running",
        "target_length_tier": 32,
        "topic_registry_id": None,
        "result_summary": {},
    }

    with patch("workers.topic_researcher.get_latest_cron_run", return_value=latest), \
         patch("workers.topic_researcher.list_topic_research_runs") as mock_runs, \
         patch("workers.topic_researcher.update_cron_run") as mock_update:
        mock_runs.side_effect = [[unrelated_running_child], [], []]
        result = _reconcile_stale_running_cron_run(max_age_seconds=60)

    assert result is not None
    assert mock_update.call_args[1]["status"] == "failed"
    assert mock_update.call_args[1]["topics_failed"] == 3
