"""Tests for the cron-level expand_script_bank function."""

from app.features.topics.variant_expansion import expand_script_bank


def test_expand_script_bank_respects_max_per_run(monkeypatch):
    """Stops after max_scripts_per_cron_run."""
    topics = [
        {"id": f"topic-{i}", "title": f"Topic {i}", "post_type": "value"}
        for i in range(10)
    ]
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_all_topics_from_registry",
        lambda: topics,
    )

    call_count = {"n": 0}
    def mock_expand(**kw):
        call_count["n"] += 1
        return {"generated": 1, "total_existing": call_count["n"], "details": []}

    monkeypatch.setattr(
        "app.features.topics.variant_expansion.expand_topic_variants",
        mock_expand,
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_topic_scripts_for_registry",
        lambda tid: [],
    )

    result = expand_script_bank(
        max_scripts_per_cron_run=3,
        target_length_tiers=[8],
    )
    assert result["total_generated"] <= 3


def test_expansion_worker_can_be_disabled_by_config(monkeypatch):
    from workers import expansion_worker

    calls = []

    monkeypatch.setattr(
        expansion_worker,
        "get_settings",
        lambda: type("Settings", (), {"video_poller_enable_script_bank_expansion": False})(),
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.expand_script_bank",
        lambda **kw: calls.append(kw) or {"total_generated": 99, "topics_processed": 7},
    )

    expansion_worker.run_expansion()

    assert calls == []
