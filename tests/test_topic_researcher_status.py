"""Tests for the topic researcher cron status endpoint."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")


async def _fetch_cron_status():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/topics/cron-status")


@patch("app.features.topics.queries.get_latest_cron_run")
@patch("app.features.topics.queries.get_cron_run_stats")
@patch("app.features.topics.queries.get_topic_research_cron_monitoring")
@patch("app.adapters.supabase_client.get_supabase")
def test_cron_status_with_data(mock_sb, mock_monitoring, mock_stats, mock_latest):
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
    mock_monitoring.return_value = {"state": "healthy", "missing_completed_dates_last_window": []}

    resp = asyncio.run(_fetch_cron_status())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["last_run"]["status"] == "completed"
    assert data["total_runs"] == 12
    assert data["monitoring"]["state"] == "healthy"


@patch("app.features.topics.queries.get_latest_cron_run")
@patch("app.features.topics.queries.get_cron_run_stats")
@patch("app.features.topics.queries.get_topic_research_cron_monitoring")
@patch("app.adapters.supabase_client.get_supabase")
def test_cron_status_no_runs(mock_sb, mock_monitoring, mock_stats, mock_latest):
    mock_sb.return_value = MagicMock()
    mock_sb.return_value.health_check.return_value = True
    mock_latest.return_value = None
    mock_stats.return_value = {"total_runs": 0, "total_topics_researched": 0}
    mock_monitoring.return_value = {"state": "missing", "missing_completed_dates_last_window": ["2026-04-08"]}

    resp = asyncio.run(_fetch_cron_status())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["last_run"] is None
    assert data["total_runs"] == 0
    assert data["monitoring"]["state"] == "missing"


def test_topic_research_cron_monitoring_flags_missing_days(monkeypatch):
    from app.features.topics import queries

    fixed_now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)

    class _FrozenDateTime:
        @staticmethod
        def now(tz=None):
            return fixed_now if tz else fixed_now.replace(tzinfo=None)

        @staticmethod
        def fromisoformat(value):
            return datetime.fromisoformat(value)

        @staticmethod
        def fromtimestamp(value, tz=None):
            return datetime.fromtimestamp(value, tz=tz)

    monkeypatch.setattr(queries, "datetime", _FrozenDateTime)
    monkeypatch.setattr(
        queries,
        "list_topic_research_cron_runs",
        lambda limit=50, status=None: [
            {
                "id": "run-1",
                "status": "completed",
                "completed_at": "2026-04-04T07:00:00+00:00",
            },
            {
                "id": "run-2",
                "status": "completed",
                "completed_at": "2026-04-09T21:43:08+00:00",
            },
        ],
    )

    monitoring = queries.get_topic_research_cron_monitoring(window_days=7)

    assert monitoring["state"] == "healthy"
    assert monitoring["missing_completed_dates_last_window"] == [
        "2026-04-03",
        "2026-04-05",
        "2026-04-06",
        "2026-04-07",
        "2026-04-08",
    ]
