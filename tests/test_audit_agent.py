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
    llm.generate_gemini_json.return_value = json.loads(_mock_llm_response(85, "pass"))
    row = {"id": "abc-123", "script": "Dein Recht auf Mitfahrt existiert nur auf dem Papier.", "target_length_tier": 8, "title": "Test"}
    result = audit_single_script(row, llm=llm)
    assert isinstance(result, AuditResult)
    assert result.total_score == 85
    assert result.status == "pass"
    assert result.script_id == "abc-123"
    llm.generate_gemini_json.assert_called_once()
    llm.generate_gemini_text.assert_not_called()


def test_audit_single_script_reject():
    """Script scoring < 40 gets status reject."""
    llm = MagicMock()
    llm.generate_gemini_json.return_value = json.loads(_mock_llm_response(25, "reject"))
    row = {"id": "abc-456", "script": "Es gibt Barrierefreiheit.", "target_length_tier": 8, "title": "Test"}
    result = audit_single_script(row, llm=llm)
    assert result.total_score == 25
    assert result.status == "reject"
    llm.generate_gemini_json.assert_called_once()
    llm.generate_gemini_text.assert_not_called()


def test_audit_single_script_deterministic_reject():
    """Script failing deterministic checks gets score 0 without LLM call."""
    llm = MagicMock()
    row = {"id": "abc-789", "script": "Zentrale Erkenntnisse: Barrierefreiheit fehlt.", "target_length_tier": 8, "title": "Test"}
    result = audit_single_script(row, llm=llm)
    assert result.total_score == 0
    assert result.status == "reject"
    assert "label_fragment" in result.quality_notes
    llm.generate_gemini_json.assert_not_called()
    llm.generate_gemini_text.assert_not_called()


def test_audit_single_script_malformed_llm_response():
    """Malformed LLM JSON falls back to reject."""
    llm = MagicMock()
    llm.generate_gemini_json.side_effect = ValueError("structured output invalid")
    llm.generate_gemini_text.return_value = "This is not JSON at all"
    row = {"id": "abc-bad", "script": "Dein Recht auf Mitfahrt.", "target_length_tier": 8, "title": "Test"}
    result = audit_single_script(row, llm=llm)
    assert result.total_score == 0
    assert result.status == "reject"
    assert "parse_error" in result.quality_notes


from unittest.mock import patch, MagicMock


def test_get_unaudited_scripts_returns_pending_audit_rows():
    """Query must filter for audit_status='pending'."""
    mock_response = MagicMock()
    mock_response.data = [
        {"id": "row-1", "script": "Test script.", "target_length_tier": 8, "title": "Test"},
    ]
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = mock_response

    mock_client = MagicMock()
    mock_client.client.table.return_value = mock_table

    with patch("app.features.topics.queries.supabase", mock_client):
        from app.features.topics.queries import get_unaudited_scripts
        rows = get_unaudited_scripts(limit=50)
    assert len(rows) == 1
    assert rows[0]["id"] == "row-1"


def test_update_script_quality_writes_score_and_notes():
    """Update must write quality_score, quality_notes, and audit_status."""
    select_response = MagicMock()
    select_response.data = [{"id": "row-1", "topic_registry_id": "topic-1", "audit_attempts": 0}]
    update_response = MagicMock()
    update_response.data = [{"id": "row-1", "quality_score": 85, "audit_status": "pass"}]
    mock_table = MagicMock()
    mock_table.select.return_value.eq.return_value.limit.return_value.execute.return_value = select_response
    mock_table.update.return_value.eq.return_value.execute.return_value = update_response

    mock_client = MagicMock()
    mock_client.client.table.return_value = mock_table

    with patch("app.features.topics.queries.supabase", mock_client):
        from app.features.topics.queries import update_script_quality
        with patch("app.features.topics.queries._sync_topic_family_status", lambda **kwargs: None):
            update_script_quality(
                script_id="row-1",
                quality_score=85,
                quality_notes='{"status": "pass"}',
                audit_status="pass",
            )

    mock_table.update.assert_called_once()
    call_args = mock_table.update.call_args[0][0]
    assert call_args["quality_score"] == 85
    assert "pass" in call_args["quality_notes"]
    assert call_args["audit_status"] == "pass"


def test_audit_worker_run_audit_cycle(monkeypatch):
    """Audit cycle must fetch unaudited rows, audit them, and write results."""
    mock_rows = [
        {"id": "r1", "script": "Dein Recht auf Mitfahrt existiert nur auf dem Papier.", "target_length_tier": 8, "title": "OEPNV"},
        {"id": "r2", "script": "Nur 2 Prozent aller Wohnungen sind rollstuhlgerecht.", "target_length_tier": 8, "title": "Wohnen"},
    ]

    updated = []

    def mock_get_unaudited(*, limit=50):
        return mock_rows

    def mock_update(*, script_id, quality_score, quality_notes, audit_status=None):
        updated.append({"id": script_id, "score": quality_score})

    monkeypatch.setattr("workers.audit_worker.get_unaudited_scripts", mock_get_unaudited)
    monkeypatch.setattr("workers.audit_worker.update_script_quality", mock_update)

    mock_llm = MagicMock()
    mock_llm.generate_gemini_json.return_value = json.loads(_mock_llm_response(80, "pass"))
    monkeypatch.setattr("workers.audit_worker.get_llm_client", lambda: mock_llm)

    from workers.audit_worker import run_audit_cycle
    run_audit_cycle()

    assert len(updated) == 2
    assert all(u["score"] == 80 for u in updated)
