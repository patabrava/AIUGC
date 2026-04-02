"""Tests for audit JSON structured-output fallback behavior."""

import json
from unittest.mock import MagicMock

from app.features.topics.audit import AuditResult, audit_single_script


def test_audit_single_script_uses_structured_json_first():
    llm = MagicMock()
    llm.generate_gemini_json.return_value = {
        "german_nativeness": {"score": 24, "notes": "Sehr natürlich."},
        "hook_quality": {"score": 22, "notes": "Starker Hook."},
        "prompt_compliance": {"score": 21, "notes": "Tier passt."},
        "virality_potential": {"score": 23, "notes": "Hohe Neugier."},
        "total_score": 90,
        "status": "pass",
        "summary": "Starker, sauberer Audit.",
    }

    row = {
        "id": "abc-123",
        "script": "Dein Recht auf Mitfahrt existiert nur auf dem Papier.",
        "target_length_tier": 8,
        "title": "Test",
    }

    result = audit_single_script(row, llm=llm)

    assert isinstance(result, AuditResult)
    assert result.total_score == 90
    assert result.status == "pass"
    llm.generate_gemini_json.assert_called_once()
    llm.generate_gemini_text.assert_not_called()


def test_audit_single_script_repairs_invalid_structured_json_via_text_fallback():
    llm = MagicMock()
    llm.generate_gemini_json.side_effect = ValueError("structured output invalid")
    llm.generate_gemini_text.return_value = json.dumps(
        {
            "german_nativeness": {"score": 23, "notes": "Sehr natürlich."},
            "hook_quality": {"score": 22, "notes": "Starker Hook."},
            "prompt_compliance": {"score": 21, "notes": "Tier passt."},
            "virality_potential": {"score": 23, "notes": "Hohe Neugier."},
            "total_score": 89,
            "status": "pass",
            "summary": "Sauberer Audit nach Reparatur.",
        }
    )

    row = {
        "id": "retry-1",
        "script": "Dein Recht auf Mitfahrt existiert nur auf dem Papier.",
        "target_length_tier": 8,
        "title": "Test",
    }

    result = audit_single_script(row, llm=llm)

    assert isinstance(result, AuditResult)
    assert result.total_score == 89
    assert result.status == "pass"
    llm.generate_gemini_json.assert_called_once()
    llm.generate_gemini_text.assert_called_once()


def test_audit_single_script_rejects_only_after_text_fallback_fails():
    llm = MagicMock()
    llm.generate_gemini_json.side_effect = ValueError("structured output invalid")
    llm.generate_gemini_text.return_value = "not json at all"

    row = {
        "id": "retry-2",
        "script": "Dein Recht auf Mitfahrt existiert nur auf dem Papier.",
        "target_length_tier": 8,
        "title": "Test",
    }

    result = audit_single_script(row, llm=llm)

    assert result.status == "reject"
    assert result.total_score == 0
    assert "parse_error" in result.quality_notes
