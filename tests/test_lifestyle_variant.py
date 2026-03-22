"""Tests for lifestyle variant generation."""

from unittest.mock import MagicMock

from app.features.topics.variant_expansion import generate_dialog_scripts_variant


def test_generate_dialog_scripts_variant_includes_constraints(monkeypatch):
    """The variant prompt includes forced framework and hook style."""
    captured_prompt = {}

    def mock_generate(*, prompt, json_schema=None, system_prompt=None, **kwargs):
        captured_prompt["value"] = prompt
        return {
            "problem_agitate_solution": ["Test script."],
            "testimonial": ["Test testimonial."],
            "transformation": ["Test transformation."],
            "description": "Ein ausfuehrliches Test-Skript fuer Lifestyle-Inhalte mit genug Zeichen.",
        }

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
    assert len(result.problem_agitate_solution) >= 1
