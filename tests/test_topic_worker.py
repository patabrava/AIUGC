"""Tests for the unified topic worker."""

from __future__ import annotations


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


def test_run_topic_worker_tick_honors_intervals(monkeypatch):
    """When the intervals have not elapsed, the worker should stay idle."""
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
        last_research_run=100.0,
        now=120.0,
    )

    assert events == ["reconcile"]
    assert last_audit_run == 100.0
    assert last_research_run == 100.0
