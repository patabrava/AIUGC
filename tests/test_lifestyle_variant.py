"""Tests for lifestyle variant generation."""

from unittest.mock import MagicMock

from app.features.topics.variant_expansion import generate_dialog_scripts_variant


def test_generate_dialog_scripts_variant_includes_constraints(monkeypatch):
    """The variant prompt includes forced framework and hook style."""
    captured_prompt = {}

    def mock_generate_text(*, prompt, system_prompt=None, **kwargs):
        captured_prompt["value"] = prompt
        return (
            "Problem-Agitieren-Lösung Ads\n\n"
            "Kennst du den Moment, wenn eine ungeplante Stufe deinen Ausflug kippt und du sofort neu planen musst?\n\n"
            "Beschreibung\n\n"
            "Ein ausführlicher Erfahrungsbericht über alltägliche Planung, mögliche Hindernisse und hilfreiche Absprachen für mehr Selbstständigkeit unterwegs. #Rollstuhl #Alltag #Mobilität"
        )

    mock_llm = MagicMock()
    mock_llm.generate_gemini_text = mock_generate_text

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
