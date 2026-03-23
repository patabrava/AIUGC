"""Tests for topic_research_cron_runs CRUD functions."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from unittest.mock import MagicMock, patch


def _mock_supabase():
    mock = MagicMock()
    mock.client.table.return_value = mock.client.table
    mock.client.table.insert.return_value = mock.client.table
    mock.client.table.update.return_value = mock.client.table
    mock.client.table.select.return_value = mock.client.table
    mock.client.table.eq.return_value = mock.client.table
    mock.client.table.order.return_value = mock.client.table
    mock.client.table.limit.return_value = mock.client.table
    mock.client.table.execute.return_value = MagicMock(data=[{
        "id": "run-1",
        "started_at": "2026-03-24T00:00:00Z",
        "completed_at": None,
        "status": "running",
        "topics_requested": 5,
        "topics_completed": 0,
        "topics_failed": 0,
        "seed_source": "yaml_bank",
        "topic_ids": [],
        "error_message": None,
        "details": {},
        "created_at": "2026-03-24T00:00:00Z",
    }])
    return mock


@patch("app.features.topics.queries.get_supabase")
def test_create_cron_run(mock_get_sb):
    mock_get_sb.return_value = _mock_supabase()
    from app.features.topics.queries import create_cron_run
    result = create_cron_run(topics_requested=5, seed_source="yaml_bank")
    assert result["status"] == "running"
    assert result["topics_requested"] == 5


@patch("app.features.topics.queries.get_supabase")
def test_update_cron_run(mock_get_sb):
    mock_sb = _mock_supabase()
    mock_sb.client.table.execute.return_value = MagicMock(data=[{
        "id": "run-1",
        "status": "completed",
        "topics_completed": 4,
        "topics_failed": 1,
        "completed_at": "2026-03-24T00:10:00Z",
        "topics_requested": 5,
        "started_at": "2026-03-24T00:00:00Z",
        "seed_source": "yaml_bank",
        "topic_ids": ["t1", "t2", "t3", "t4"],
        "error_message": None,
        "details": {},
        "created_at": "2026-03-24T00:00:00Z",
    }])
    mock_get_sb.return_value = mock_sb
    from app.features.topics.queries import update_cron_run
    result = update_cron_run(
        run_id="run-1",
        status="completed",
        topics_completed=4,
        topics_failed=1,
        topic_ids=["t1", "t2", "t3", "t4"],
    )
    assert result["status"] == "completed"


@patch("app.features.topics.queries.get_supabase")
def test_get_latest_cron_run(mock_get_sb):
    mock_get_sb.return_value = _mock_supabase()
    from app.features.topics.queries import get_latest_cron_run
    result = get_latest_cron_run()
    assert result is not None
    assert result["id"] == "run-1"


@patch("app.features.topics.queries.get_supabase")
def test_get_latest_cron_run_empty(mock_get_sb):
    mock_sb = _mock_supabase()
    mock_sb.client.table.execute.return_value = MagicMock(data=[])
    mock_get_sb.return_value = mock_sb
    from app.features.topics.queries import get_latest_cron_run
    result = get_latest_cron_run()
    assert result is None


@patch("app.features.topics.queries.get_supabase")
def test_get_cron_run_stats(mock_get_sb):
    mock_sb = _mock_supabase()
    mock_sb.client.table.execute.return_value = MagicMock(data=[
        {"status": "completed", "topics_completed": 4},
        {"status": "completed", "topics_completed": 3},
    ])
    mock_get_sb.return_value = mock_sb
    from app.features.topics.queries import get_cron_run_stats
    result = get_cron_run_stats()
    assert "total_runs" in result
    assert "total_topics_researched" in result
