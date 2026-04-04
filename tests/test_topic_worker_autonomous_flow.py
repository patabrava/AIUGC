from __future__ import annotations


def test_run_topic_worker_tick_triggers_research_when_interval_elapsed(monkeypatch):
    from workers import topic_worker

    calls = []

    monkeypatch.setattr(topic_worker, "_reconcile_stale_running_cron_run", lambda: None)
    monkeypatch.setattr(topic_worker, "run_audit_cycle", lambda: calls.append("audit"))
    monkeypatch.setattr(
        topic_worker,
        "run_discovery_cycle",
        lambda audit_after_discovery=False: calls.append(("research", audit_after_discovery)),
    )

    last_audit, last_research = topic_worker.run_topic_worker_tick(
        last_audit_run=0.0,
        last_research_run=0.0,
        now=24 * 60 * 60 + 1,
    )

    assert calls == ["audit", ("research", False)]
    assert last_audit > 0.0
    assert last_research > 0.0


def test_run_discovery_cycle_does_not_skip_on_high_coverage(monkeypatch):
    from workers import topic_researcher
    import workers.audit_worker as audit_worker

    calls = []

    monkeypatch.setattr(topic_researcher, "count_selectable_topic_families", lambda **kwargs: 999)
    monkeypatch.setattr(topic_researcher, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_researcher, "select_seeds", lambda max_topics, niche: (["Seed A"], "yaml_bank"))
    monkeypatch.setattr(topic_researcher, "create_cron_run", lambda **kwargs: {"id": "run-1"})
    monkeypatch.setattr(topic_researcher, "_research_single_topic", lambda **kwargs: [{"id": "topic-1"}])
    monkeypatch.setattr(topic_researcher, "_heartbeat_cron_run", lambda **kwargs: None)
    monkeypatch.setattr(
        topic_researcher,
        "update_cron_run",
        lambda *args, **kwargs: calls.append(("update", kwargs)),
    )
    monkeypatch.setattr(audit_worker, "run_audit_cycle", lambda: calls.append("audit"))

    topic_researcher.run_discovery_cycle(audit_after_discovery=True)

    assert any(item == "audit" for item in calls)
    assert any(item[0] == "update" for item in calls)
