"""Tests for the topic researcher worker."""
import os
import time
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
         patch("workers.topic_researcher._research_single_topic") as mock_research:

        mock_select.return_value = (["Topic A", "Topic B"], "yaml_bank")
        mock_create.return_value = {"id": "run-1", "status": "running"}
        mock_research.return_value = [{"id": "t-1", "title": "Topic A"}]
        mock_update.return_value = {"id": "run-1", "status": "completed"}

        run_discovery_cycle()

    mock_create.assert_called_once()
    assert mock_research.call_count == 2
    mock_update.assert_called_once()
    update_kwargs = mock_update.call_args
    assert update_kwargs[1]["status"] == "completed"


def test_run_discovery_cycle_no_seeds():
    """When no seeds available, creates run with 0 topics."""
    from workers.topic_researcher import run_discovery_cycle

    with patch("workers.topic_researcher.select_seeds") as mock_select, \
         patch("workers.topic_researcher.create_cron_run") as mock_create, \
         patch("workers.topic_researcher.update_cron_run") as mock_update:

        mock_select.return_value = ([], "yaml_bank")
        mock_create.return_value = {"id": "run-1", "status": "running"}
        mock_update.return_value = {"id": "run-1", "status": "completed"}

        run_discovery_cycle()

    mock_update.assert_called_once()
    assert mock_update.call_args[1]["topics_completed"] == 0
