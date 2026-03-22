"""Tests for lifestyle variant generation."""

from unittest.mock import MagicMock

from app.features.topics.variant_expansion import generate_dialog_scripts_variant


def test_generate_dialog_scripts_variant_includes_constraints(monkeypatch):
    """The variant prompt includes forced framework and hook style."""
    captured_prompt = {}

    def mock_generate(*, prompt, system_prompt=None, **kwargs):
        captured_prompt["value"] = prompt
        return (
            "## Problem-Agitieren-Lösung Ads\n\n"
            "kennst du das Gefühl, wenn alles zu viel wird? Hier ist die Lösung.\n\n"
            "## Testimonial Ads\n\n"
            "seit ich das ausprobiert habe, hat sich alles verändert.\n\n"
            "## Transformations-Geschichten Ads\n\n"
            "der Moment, als ich aufgehört habe zu zweifeln, war magisch.\n\n"
            "## Beschreibung\n\n"
            "Ein Test-Skript für Lifestyle-Inhalte.\n"
        )

    mock_llm = MagicMock()
    mock_llm.generate_gemini_json = mock_generate

    monkeypatch.setattr(
        "app.features.topics.variant_expansion.get_llm_client",
        lambda: mock_llm,
    )

    result = generate_dialog_scripts_variant(
        topic="Test topic",
        forced_framework="Testimonial",
        forced_hook_style="personal_story",
    )
    assert "Testimonial" in captured_prompt["value"]
    assert "personal_story" in captured_prompt["value"]
    assert result is not None
