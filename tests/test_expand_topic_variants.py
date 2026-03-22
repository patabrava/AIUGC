"""Tests for the expand_topic_variants orchestration."""

from unittest.mock import MagicMock
from types import SimpleNamespace

from app.features.topics.variant_expansion import expand_topic_variants


def test_expand_topic_variants_generates_and_stores(monkeypatch):
    """Generates a variant and calls upsert."""
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_existing_variant_pairs",
        lambda **kw: [],
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_topic_research_dossiers",
        lambda **kw: [{"id": "dossier-1", "normalized_payload": {
            "framework_candidates": ["PAL", "Testimonial"],
            "lane_candidates": [{"title": "Lane 1", "framework_candidates": ["PAL"]}],
        }}],
    )

    stored = []
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.upsert_topic_script_variants",
        lambda **kw: stored.append(kw) or [],
    )

    mock_llm = MagicMock()
    mock_llm.generate_gemini_json.return_value = [{"topic": "Test", "script": "Test script das ist ein guter Skript fuer dich.", "caption": "Cap"}]
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_llm_client",
        lambda: mock_llm,
    )

    mock_item = SimpleNamespace(
        topic="Test",
        script="Test script das ist ein guter Skript fuer dich.",
        caption="Cap",
        framework="PAL",
        source_summary="",
        estimated_duration_s=5,
        sources=[],
        tone="direkt",
        disclaimer="Keine Rechts- oder medizinische Beratung.",
    )
    mock_batch = SimpleNamespace(items=[mock_item])
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.parse_prompt1_response",
        lambda raw, profile=None, **kwargs: mock_batch,
    )

    result = expand_topic_variants(
        topic_registry_id="topic-1",
        title="Test Topic",
        post_type="value",
        target_length_tier=8,
        count=1,
    )
    assert result["generated"] == 1
    assert len(stored) == 1
    assert stored[0]["topic_research_dossier_id"] == "dossier-1"


def test_expand_topic_variants_skips_exhausted_topic(monkeypatch):
    """Returns 0 generated when all variants are used."""
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_existing_variant_pairs",
        lambda **kw: [
            {"framework": "PAL", "hook_style": "question"},
            {"framework": "PAL", "hook_style": "bold_claim"},
            {"framework": "Testimonial", "hook_style": "question"},
            {"framework": "Testimonial", "hook_style": "bold_claim"},
        ],
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_topic_research_dossiers",
        lambda **kw: [{"id": "d1", "normalized_payload": {
            "framework_candidates": ["PAL", "Testimonial"],
            "lane_candidates": [],
        }}],
    )
    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_hook_bank",
        lambda: {"families": [
            {"name": "question", "examples": ["Was wäre wenn..."]},
            {"name": "bold_claim", "examples": ["Die Wahrheit ist..."]},
        ], "banned_patterns": []},
    )

    result = expand_topic_variants(
        topic_registry_id="topic-1",
        title="Test Topic",
        post_type="value",
        target_length_tier=8,
        count=5,
    )
    assert result["generated"] == 0
