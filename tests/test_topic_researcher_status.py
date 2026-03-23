"""Tests for the topic researcher cron status endpoint."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@patch("app.features.topics.queries.get_latest_cron_run")
@patch("app.features.topics.queries.get_cron_run_stats")
@patch("app.adapters.supabase_client.get_supabase")
def test_cron_status_with_data(mock_sb, mock_stats, mock_latest):
    mock_sb.return_value = MagicMock()
    mock_sb.return_value.health_check.return_value = True
    mock_latest.return_value = {
        "id": "run-1",
        "started_at": "2026-03-23T06:00:00+00:00",
        "completed_at": "2026-03-23T06:12:34+00:00",
        "status": "completed",
        "topics_completed": 4,
        "topics_failed": 1,
        "seed_source": "yaml_bank",
    }
    mock_stats.return_value = {"total_runs": 12, "total_topics_researched": 47}

    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/topics/cron-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["last_run"]["status"] == "completed"
    assert data["total_runs"] == 12


@patch("app.features.topics.queries.get_latest_cron_run")
@patch("app.features.topics.queries.get_cron_run_stats")
@patch("app.adapters.supabase_client.get_supabase")
def test_cron_status_no_runs(mock_sb, mock_stats, mock_latest):
    mock_sb.return_value = MagicMock()
    mock_sb.return_value.health_check.return_value = True
    mock_latest.return_value = None
    mock_stats.return_value = {"total_runs": 0, "total_topics_researched": 0}

    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/topics/cron-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["last_run"] is None
    assert data["total_runs"] == 0
