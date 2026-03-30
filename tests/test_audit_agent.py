"""Tests for the German nativeness audit agent."""

from pathlib import Path


AUDIT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "app"
    / "features"
    / "topics"
    / "prompt_data"
    / "audit_prompt.txt"
)


def test_audit_prompt_file_exists():
    """Audit prompt template must exist."""
    assert AUDIT_PROMPT_PATH.exists(), f"Missing: {AUDIT_PROMPT_PATH}"


def test_audit_prompt_contains_scoring_dimensions():
    """Audit prompt must define all 4 scoring dimensions."""
    content = AUDIT_PROMPT_PATH.read_text(encoding="utf-8")
    assert "german_nativeness" in content
    assert "hook_quality" in content
    assert "prompt_compliance" in content
    assert "virality_potential" in content


def test_audit_prompt_contains_json_contract():
    """Audit prompt must specify the expected JSON response structure."""
    content = AUDIT_PROMPT_PATH.read_text(encoding="utf-8")
    assert "total_score" in content
    assert "status" in content
    assert "pass" in content
    assert "needs_repair" in content
    assert "reject" in content
