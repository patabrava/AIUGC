"""Tests for topic seed selection logic."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from unittest.mock import patch, MagicMock
import pytest


def test_load_seed_topics_from_yaml():
    """Loads and flattens topic_bank.yaml categories."""
    from workers.topic_seed_selector import load_seed_topics_from_yaml
    topics = load_seed_topics_from_yaml()
    assert isinstance(topics, list)
    assert len(topics) > 0
    assert all(isinstance(t, str) for t in topics)


def test_load_seed_topics_missing_file(tmp_path, monkeypatch):
    """Returns empty list when YAML file is missing."""
    monkeypatch.setattr(
        "workers.topic_seed_selector.TOPIC_BANK_PATH",
        str(tmp_path / "nonexistent.yaml"),
    )
    from workers.topic_seed_selector import load_seed_topics_from_yaml
    topics = load_seed_topics_from_yaml()
    assert topics == []


def test_load_seed_topics_malformed_yaml(tmp_path, monkeypatch):
    """Returns empty list on malformed YAML."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("{{{{not yaml")
    monkeypatch.setattr(
        "workers.topic_seed_selector.TOPIC_BANK_PATH",
        str(bad_yaml),
    )
    from workers.topic_seed_selector import load_seed_topics_from_yaml
    topics = load_seed_topics_from_yaml()
    assert topics == []


@patch("workers.topic_seed_selector.get_researched_topic_texts")
@patch("workers.topic_seed_selector.get_all_topics_from_registry")
def test_filter_unresearched_seeds(mock_get_topics, mock_get_researched):
    """Filters out seeds that already have registry entries."""
    mock_get_topics.return_value = [
        {"title": "Topic A", "id": "1"},
        {"title": "Topic B", "id": "2"},
    ]
    mock_get_researched.return_value = []
    from workers.topic_seed_selector import filter_unresearched_seeds
    seeds = ["Topic A", "Topic C", "Topic D"]
    result = filter_unresearched_seeds(seeds)
    assert "Topic A" not in result
    assert "Topic C" in result
    assert "Topic D" in result


@patch("workers.topic_seed_selector.get_researched_topic_texts")
@patch("workers.topic_seed_selector.get_all_topics_from_registry")
def test_filter_unresearched_seeds_excludes_previously_researched_topics(mock_get_topics, mock_get_researched):
    mock_get_topics.return_value = []
    mock_get_researched.return_value = [
        "Barrierefreiheit im OePNV und Regionalverkehr",
        "Merkzeichen B im Nahverkehr",
    ]
    from workers.topic_seed_selector import filter_unresearched_seeds
    result = filter_unresearched_seeds([
        "Barrierefreiheit im OePNV und Regionalverkehr",
        "Neuer Anspruch bei Rollstuhlrampe im Bus",
    ])
    assert result == ["Neuer Anspruch bei Rollstuhlrampe im Bus"]


@patch("workers.topic_seed_selector.get_researched_topic_texts")
@patch("workers.topic_seed_selector.get_all_topics_from_registry")
def test_select_seeds_phase1_sufficient(mock_get_topics, mock_get_researched):
    """When YAML bank has enough unresearched seeds, uses only Phase 1."""
    mock_get_topics.return_value = []
    mock_get_researched.return_value = []
    from workers.topic_seed_selector import select_seeds
    with patch("workers.topic_seed_selector.load_seed_topics_from_yaml") as mock_yaml:
        mock_yaml.return_value = [f"Seed {i}" for i in range(20)]
        seeds, source = select_seeds(max_topics=5)
    assert len(seeds) == 5
    assert source == "yaml_bank"


@patch("workers.topic_seed_selector.get_researched_topic_texts")
@patch("workers.topic_seed_selector.get_all_topics_from_registry")
def test_select_seeds_phase2_fallback(mock_get_topics, mock_get_researched):
    """When YAML bank is empty, falls back to LLM generation."""
    mock_get_topics.return_value = [
        {"title": f"Seed {i}", "id": str(i)} for i in range(20)
    ]
    mock_get_researched.return_value = []
    from workers.topic_seed_selector import select_seeds
    with patch("workers.topic_seed_selector.load_seed_topics_from_yaml") as mock_yaml:
        mock_yaml.return_value = [f"Seed {i}" for i in range(20)]
        with patch("workers.topic_seed_selector._generate_llm_seeds") as mock_llm:
            mock_llm.return_value = ["New LLM Topic 1", "New LLM Topic 2"]
            seeds, source = select_seeds(max_topics=2)
    assert source in ("llm_generated", "mixed")
    assert len(seeds) <= 2


@patch("workers.topic_seed_selector.get_researched_topic_texts")
@patch("workers.topic_seed_selector.get_all_topics_from_registry")
def test_select_seeds_uses_fallback_bank_when_llm_returns_empty(mock_get_topics, mock_get_researched):
    """If Gemini produces nothing, the selector should still return deterministic fallback seeds."""
    mock_get_topics.return_value = []
    mock_get_researched.return_value = []
    from workers.topic_seed_selector import select_seeds

    with patch("workers.topic_seed_selector.load_seed_topics_from_yaml") as mock_yaml, \
         patch("workers.topic_seed_selector._generate_llm_seeds", return_value=[]):
        mock_yaml.return_value = []
        seeds, source = select_seeds(max_topics=3, niche="Barrierefreiheit im Alltag")

    assert len(seeds) == 3
    assert source == "fallback_bank"
    assert all(isinstance(seed, str) and seed.strip() for seed in seeds)
