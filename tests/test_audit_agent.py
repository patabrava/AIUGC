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


import json
from unittest.mock import MagicMock

from app.features.topics.audit import audit_single_script, AuditResult


def _mock_llm_response(total_score: int, status: str) -> str:
    return json.dumps({
        "german_nativeness": {"score": total_score // 4, "notes": "Test note."},
        "hook_quality": {"score": total_score // 4, "notes": "Test note."},
        "prompt_compliance": {"score": total_score // 4, "notes": "Test note."},
        "virality_potential": {"score": total_score - 3 * (total_score // 4), "notes": "Test note."},
        "total_score": total_score,
        "status": status,
        "summary": "Test summary.",
    })


def test_audit_single_script_pass():
    """Script scoring >= 70 gets status pass."""
    llm = MagicMock()
    llm.generate_gemini_text.return_value = _mock_llm_response(85, "pass")
    row = {"id": "abc-123", "script": "Dein Recht auf Mitfahrt existiert nur auf dem Papier.", "target_length_tier": 8, "title": "Test"}
    result = audit_single_script(row, llm=llm)
    assert isinstance(result, AuditResult)
    assert result.total_score == 85
    assert result.status == "pass"
    assert result.script_id == "abc-123"


def test_audit_single_script_reject():
    """Script scoring < 40 gets status reject."""
    llm = MagicMock()
    llm.generate_gemini_text.return_value = _mock_llm_response(25, "reject")
    row = {"id": "abc-456", "script": "Es gibt Barrierefreiheit.", "target_length_tier": 8, "title": "Test"}
    result = audit_single_script(row, llm=llm)
    assert result.total_score == 25
    assert result.status == "reject"


def test_audit_single_script_deterministic_reject():
    """Script failing deterministic checks gets score 0 without LLM call."""
    llm = MagicMock()
    row = {"id": "abc-789", "script": "Zentrale Erkenntnisse: Barrierefreiheit fehlt.", "target_length_tier": 8, "title": "Test"}
    result = audit_single_script(row, llm=llm)
    assert result.total_score == 0
    assert result.status == "reject"
    assert "label_fragment" in result.quality_notes
    llm.generate_gemini_text.assert_not_called()


def test_audit_single_script_malformed_llm_response():
    """Malformed LLM JSON falls back to reject."""
    llm = MagicMock()
    llm.generate_gemini_text.return_value = "This is not JSON at all"
    row = {"id": "abc-bad", "script": "Dein Recht auf Mitfahrt.", "target_length_tier": 8, "title": "Test"}
    result = audit_single_script(row, llm=llm)
    assert result.total_score == 0
    assert result.status == "reject"
    assert "parse_error" in result.quality_notes
