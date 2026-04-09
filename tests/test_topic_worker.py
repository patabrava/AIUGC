"""Tests for the unified topic worker."""

from __future__ import annotations

from datetime import datetime, timezone


def test_run_topic_worker_tick_runs_audit_and_discovery(monkeypatch):
    """A due tick should drain audits and then trigger discovery without double-auditing."""
    from workers import topic_worker

    events = []

    monkeypatch.setattr(topic_worker, "AUDIT_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(topic_worker, "RESEARCH_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(topic_worker, "_reconcile_stale_running_cron_run", lambda: events.append("reconcile"))
    monkeypatch.setattr(topic_worker, "run_audit_cycle", lambda: events.append("audit"))
    monkeypatch.setattr(
        topic_worker,
        "run_discovery_cycle",
        lambda *, audit_after_discovery=False: events.append(("research", audit_after_discovery)),
    )

    last_audit_run, last_research_run = topic_worker.run_topic_worker_tick(
        last_audit_run=0.0,
        last_research_run=0.0,
        now=120.0,
    )

    assert events == ["reconcile", "audit", ("research", False)]
    assert last_audit_run == 120.0
    assert last_research_run == 120.0


def test_run_topic_worker_tick_skips_research_on_same_utc_day(monkeypatch):
    """The worker should only trigger discovery once per UTC calendar day."""
    from workers import topic_worker

    events = []

    monkeypatch.setattr(topic_worker, "AUDIT_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(topic_worker, "RESEARCH_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(topic_worker, "_reconcile_stale_running_cron_run", lambda: events.append("reconcile"))
    monkeypatch.setattr(topic_worker, "run_audit_cycle", lambda: events.append("audit"))
    monkeypatch.setattr(
        topic_worker,
        "run_discovery_cycle",
        lambda *, audit_after_discovery=False: events.append(("research", audit_after_discovery)),
    )

    same_day_now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    same_day_last_research = datetime(2026, 4, 10, 1, 0, tzinfo=timezone.utc).timestamp()

    last_audit_run, last_research_run = topic_worker.run_topic_worker_tick(
        last_audit_run=same_day_now - 10.0,
        last_research_run=same_day_last_research,
        now=same_day_now,
    )

    assert events == ["reconcile"]
    assert last_audit_run == same_day_now - 10.0
    assert last_research_run == same_day_last_research


def test_run_topic_worker_tick_triggers_research_on_new_utc_day(monkeypatch):
    """A missed calendar day should trigger discovery even if less than 24h elapsed."""
    from workers import topic_worker

    events = []

    monkeypatch.setattr(topic_worker, "AUDIT_INTERVAL_SECONDS", 60)
    monkeypatch.setattr(topic_worker, "RESEARCH_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(topic_worker, "_reconcile_stale_running_cron_run", lambda: events.append("reconcile"))
    monkeypatch.setattr(topic_worker, "run_audit_cycle", lambda: events.append("audit"))
    monkeypatch.setattr(
        topic_worker,
        "run_discovery_cycle",
        lambda *, audit_after_discovery=False: events.append(("research", audit_after_discovery)),
    )

    last_audit_run, last_research_run = topic_worker.run_topic_worker_tick(
        last_audit_run=100.0,
        last_research_run=datetime(2026, 4, 9, 23, 59, tzinfo=timezone.utc).timestamp(),
        now=datetime(2026, 4, 10, 0, 1, tzinfo=timezone.utc).timestamp(),
    )

    assert events == ["reconcile", "audit", ("research", False)]
    assert last_audit_run > 0.0
    assert last_research_run > 0.0


def test_resolve_startup_research_timestamp_reconciles_stale_wrapper_first(monkeypatch):
    """Startup should reconcile stale wrappers before gating the next daily discovery run."""
    from workers import topic_worker

    state = {"reconciled": False}

    def _reconcile():
        state["reconciled"] = True

    monkeypatch.setattr(topic_worker, "_reconcile_stale_running_cron_run", _reconcile)
    monkeypatch.setattr(topic_worker, "_get_last_run_timestamp", lambda: 100.0)
    monkeypatch.setattr(
        topic_worker,
        "_get_active_cron_timestamp",
        lambda: 0.0 if state["reconciled"] else 200.0,
    )

    resolved = topic_worker._resolve_startup_research_timestamp()

    assert resolved == 100.0
