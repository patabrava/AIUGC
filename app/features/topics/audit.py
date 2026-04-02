"""German Nativeness Audit Agent — post-persistence quality gate.

Evaluates persisted topic_scripts on nativeness, hook quality,
prompt compliance, and virality potential using Gemini.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from app.core.logging import get_logger
from app.features.topics.topic_validation import detect_spoken_copy_issues

logger = get_logger(__name__)

AUDIT_PROMPT_PATH = Path(__file__).parent / "prompt_data" / "audit_prompt.txt"

AUDIT_SYSTEM_PROMPT = (
    "You are the Flow Forge audit agent.\n"
    "Evaluate the script and return ONLY valid JSON.\n"
    "No markdown fences, no extra text.\n"
    "All notes in German."
)

AUDIT_JSON_SCHEMA: Dict[str, Any] = {
    "name": "topic_audit_result",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "german_nativeness": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "score": {"type": "integer"},
                    "notes": {"type": "string"},
                },
                "required": ["score", "notes"],
            },
            "hook_quality": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "score": {"type": "integer"},
                    "notes": {"type": "string"},
                },
                "required": ["score", "notes"],
            },
            "prompt_compliance": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "score": {"type": "integer"},
                    "notes": {"type": "string"},
                },
                "required": ["score", "notes"],
            },
            "virality_potential": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "score": {"type": "integer"},
                    "notes": {"type": "string"},
                },
                "required": ["score", "notes"],
            },
            "total_score": {"type": "integer"},
            "status": {"type": "string", "enum": ["pass", "needs_repair", "reject"]},
            "summary": {"type": "string"},
        },
        "required": [
            "german_nativeness",
            "hook_quality",
            "prompt_compliance",
            "virality_potential",
            "total_score",
            "status",
            "summary",
        ],
    },
}


@dataclass
class AuditResult:
    script_id: str
    total_score: int
    status: str  # pass, needs_repair, reject
    quality_notes: str  # JSON string of full evaluation
    german_nativeness: int = 0
    hook_quality: int = 0
    prompt_compliance: int = 0
    virality_potential: int = 0


def _load_audit_prompt() -> str:
    with AUDIT_PROMPT_PATH.open("r", encoding="utf-8") as fp:
        return fp.read().strip()


def _build_audit_prompt(row: Dict[str, Any]) -> str:
    template = _load_audit_prompt()
    # Use .replace() instead of .format() because the template contains
    # literal JSON braces that would conflict with str.format().
    result = template.replace("{script}", str(row.get("script") or ""))
    result = result.replace("{target_length_tier}", str(int(row.get("target_length_tier") or 8)))
    result = result.replace("{topic}", str(row.get("title") or row.get("topic") or ""))
    return result


def _coerce_audit_payload(data: Dict[str, Any], script_id: str) -> AuditResult:
    total = int(data.get("total_score") or 0)
    status = str(data.get("status") or "reject")
    if status not in ("pass", "needs_repair", "reject"):
        if total >= 70:
            status = "pass"
        elif total >= 40:
            status = "needs_repair"
        else:
            status = "reject"

    return AuditResult(
        script_id=script_id,
        total_score=total,
        status=status,
        quality_notes=json.dumps(data, ensure_ascii=False),
        german_nativeness=int((data.get("german_nativeness") or {}).get("score") or 0),
        hook_quality=int((data.get("hook_quality") or {}).get("score") or 0),
        prompt_compliance=int((data.get("prompt_compliance") or {}).get("score") or 0),
        virality_potential=int((data.get("virality_potential") or {}).get("score") or 0),
    )


def _parse_audit_response(raw: str, script_id: str) -> AuditResult:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning("audit_response_parse_error", script_id=script_id, raw_preview=cleaned[:200])
        return AuditResult(
            script_id=script_id,
            total_score=0,
            status="reject",
            quality_notes=json.dumps({"parse_error": True, "raw_preview": cleaned[:200]}),
        )

    total = int(data.get("total_score") or 0)
    status = str(data.get("status") or "reject")
    if status not in ("pass", "needs_repair", "reject"):
        if total >= 70:
            status = "pass"
        elif total >= 40:
            status = "needs_repair"
        else:
            status = "reject"

    data["total_score"] = total
    data["status"] = status
    return _coerce_audit_payload(data, script_id)


def _audit_with_structured_json(row: Dict[str, Any], *, llm: Any) -> AuditResult:
    structured = llm.generate_gemini_json(
        prompt=_build_audit_prompt(row),
        json_schema=AUDIT_JSON_SCHEMA,
        system_prompt=AUDIT_SYSTEM_PROMPT,
        max_tokens=1024,
        temperature=0.2,
    )
    return _coerce_audit_payload(structured, str(row.get("id") or ""))


def _audit_with_text_fallback(row: Dict[str, Any], *, llm: Any, script_id: str) -> AuditResult:
    prompt = _build_audit_prompt(row)
    repair_prompt = (
        prompt
        + "\n\nAntworte nur mit validem JSON. Kein Zusatztext, keine Markdown-Fences."
    )

    raw_response = llm.generate_gemini_text(
        prompt=repair_prompt,
        system_prompt=AUDIT_SYSTEM_PROMPT,
        max_tokens=4096,
        temperature=0.2,
    )
    return _parse_audit_response(raw_response, script_id)


def audit_single_script(row: Dict[str, Any], *, llm: Any) -> AuditResult:
    """Audit a single topic_scripts row. Returns AuditResult."""
    script_id = str(row.get("id") or "")
    script_text = str(row.get("script") or "").strip()

    # Deterministic checks first — no LLM needed for structural failures
    issues = detect_spoken_copy_issues(script_text)
    if issues:
        issue_kinds = [issue.get("kind", "unknown") for issue in issues]
        logger.info("audit_deterministic_reject", script_id=script_id, issues=issue_kinds)
        return AuditResult(
            script_id=script_id,
            total_score=0,
            status="reject",
            quality_notes=json.dumps({"deterministic_reject": True, "issues": issue_kinds}, ensure_ascii=False),
        )

    # LLM evaluation
    try:
        return _audit_with_structured_json(row, llm=llm)
    except Exception as exc:
        logger.warning("audit_structured_json_failed", script_id=script_id, error=str(exc))

    try:
        return _audit_with_text_fallback(row, llm=llm, script_id=script_id)
    except Exception as exc:
        logger.exception("audit_llm_error", script_id=script_id, error=str(exc))
        return AuditResult(
            script_id=script_id,
            total_score=0,
            status="reject",
            quality_notes=json.dumps({"llm_error": str(exc)}, ensure_ascii=False),
        )


def audit_batch(rows: List[Dict[str, Any]], *, llm: Any) -> List[AuditResult]:
    """Audit a batch of topic_scripts rows."""
    results: List[AuditResult] = []
    for row in rows:
        result = audit_single_script(row, llm=llm)
        results.append(result)
        logger.info(
            "audit_script_evaluated",
            script_id=result.script_id,
            total_score=result.total_score,
            status=result.status,
        )
    return results
