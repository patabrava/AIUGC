# Audit JSON Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce audit parse-error rejects by asking Gemini for structured JSON first, then retrying once with a text-repair fallback before a script is rejected, and prove the behavior with a live worker smoke test against the deployed stack.

**Architecture:** Keep the audit worker as the post-persistence quality gate. Change the audit agent so it requests Gemini structured JSON on the first attempt, coerces that payload into `AuditResult`, and only falls back to the current text parsing path when the structured response is unavailable or malformed. This leaves topic generation, script selection, and the family-first bank untouched while removing most false rejects caused by malformed audit responses.

**Tech Stack:** Python 3.11, Gemini API, Supabase, pytest, existing LLM adapter

**Budget:** `{files: 4, LOC/file: 70-220, deps: 0}`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/features/topics/audit.py` | Primary structured JSON audit call, JSON coercion, text-repair fallback, and parse-error logging | Modify |
| `tests/test_audit_agent.py` | Update existing audit tests so they cover the structured JSON path | Modify |
| `tests/test_audit_json_retry.py` | New tests for structured success, repair fallback, and final reject behavior | Create |
| `docs.md` | Document the new audit parsing hierarchy and what parse errors mean operationally | Modify |

---

### Task 1: Make the audit worker use Gemini structured JSON first

**Files:**
- Modify: `app/features/topics/audit.py`
- Modify: `tests/test_audit_agent.py`

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_audit_agent.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_audit_agent.py::test_audit_single_script_uses_structured_json_first -q
```

Expected:

```text
FAIL because audit_single_script still calls generate_gemini_text directly
```

- [ ] **Step 3: Implement the structured JSON path**

Add a small schema helper and a payload coercer in `app/features/topics/audit.py`:

```python
AUDIT_JSON_SCHEMA = {
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
```

Then change `audit_single_script(...)` so it tries `llm.generate_gemini_json(...)` first:

```python
prompt = _build_audit_prompt(row)
try:
    structured = llm.generate_gemini_json(
        prompt=prompt,
        json_schema=AUDIT_JSON_SCHEMA,
        system_prompt=AUDIT_SYSTEM_PROMPT,
        max_tokens=1024,
        temperature=0.2,
    )
    return _coerce_audit_payload(structured, script_id)
except Exception as exc:
    logger.warning("audit_structured_json_failed", script_id=script_id, error=str(exc))
```

After the structured path fails, keep the existing text parser as the next attempt rather than the first attempt.

- [ ] **Step 4: Run the updated unit test**

Run:

```bash
python3 -m pytest tests/test_audit_agent.py::test_audit_single_script_uses_structured_json_first -q
```

Expected:

```text
PASS
```

- [ ] **Step 5: Commit the structured-path change**

```bash
git add app/features/topics/audit.py tests/test_audit_agent.py
git commit -m "feat(topics): use structured JSON for audit results"
```

---

### Task 2: Add a text-repair fallback and prove parse errors no longer reject good scripts immediately

**Files:**
- Modify: `app/features/topics/audit.py`
- Create: `tests/test_audit_json_retry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audit_json_retry.py`:

```python
import json
from unittest.mock import MagicMock

from app.features.topics.audit import AuditResult, audit_single_script


def test_audit_single_script_repairs_invalid_structured_json_via_text_fallback():
    llm = MagicMock()
    llm.generate_gemini_json.side_effect = ValueError("structured output invalid")
    llm.generate_gemini_text.return_value = json.dumps({
        "german_nativeness": {"score": 23, "notes": "Sehr natürlich."},
        "hook_quality": {"score": 22, "notes": "Starker Hook."},
        "prompt_compliance": {"score": 21, "notes": "Tier passt."},
        "virality_potential": {"score": 23, "notes": "Hohe Neugier."},
        "total_score": 89,
        "status": "pass",
        "summary": "Sauberer Audit nach Reparatur.",
    })

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
```

- [ ] **Step 2: Run the tests to verify the failure path**

Run:

```bash
python3 -m pytest tests/test_audit_json_retry.py -q
```

Expected:

```text
FAIL because audit_single_script still rejects malformed JSON before retrying the text path
```

- [ ] **Step 3: Implement the fallback path**

In `app/features/topics/audit.py`, add a small wrapper that tries structured JSON first and then one text-repair pass:

```python
def _audit_with_structured_json(row: Dict[str, Any], *, llm: Any) -> AuditResult:
    structured = llm.generate_gemini_json(
        prompt=_build_audit_prompt(row),
        json_schema=AUDIT_JSON_SCHEMA,
        system_prompt=AUDIT_SYSTEM_PROMPT,
        max_tokens=1024,
        temperature=0.2,
    )
    return _coerce_audit_payload(structured, str(row.get("id") or ""))


def audit_single_script(row: Dict[str, Any], *, llm: Any) -> AuditResult:
    script_id = str(row.get("id") or "")
    script_text = str(row.get("script") or "").strip()

    issues = detect_spoken_copy_issues(script_text)
    if issues:
        ...

    try:
        return _audit_with_structured_json(row, llm=llm)
    except Exception as exc:
        logger.warning("audit_structured_json_failed", script_id=script_id, error=str(exc))

    prompt = _build_audit_prompt(row)
    repair_prompt = (
        prompt
        + "\n\nAntworte nur mit validem JSON. Kein Zusatztext, keine Markdown-Fences."
    )
    try:
        raw_response = llm.generate_gemini_text(
            prompt=repair_prompt,
            system_prompt=AUDIT_SYSTEM_PROMPT,
            max_tokens=4096,
            temperature=0.2,
        )
    except Exception as exc:
        logger.exception("audit_llm_error", script_id=script_id, error=str(exc))
        return AuditResult(
            script_id=script_id,
            total_score=0,
            status="reject",
            quality_notes=json.dumps({"llm_error": str(exc)}, ensure_ascii=False),
        )

    return _parse_audit_response(raw_response, script_id)
```

The important rule is:
- structured JSON first
- text repair once
- parse-error reject only as the last resort

- [ ] **Step 4: Run the retry tests again**

Run:

```bash
python3 -m pytest tests/test_audit_json_retry.py -q
```

Expected:

```text
PASS
```

- [ ] **Step 5: Commit the fallback change**

```bash
git add app/features/topics/audit.py tests/test_audit_json_retry.py
git commit -m "feat(topics): add audit JSON repair fallback"
```

---

### Task 3: Document the new audit behavior and verify it against a live script

**Files:**
- Modify: `docs.md`

- [ ] **Step 1: Add the operational note**

Add a short section to `docs.md` that says:

```md
## Audit JSON Handling

The audit worker now asks Gemini for structured JSON first. If the structured call fails, it retries once with a text-repair prompt. A `parse_error` should now be treated as a last-resort failure, not the normal path.
```

- [ ] **Step 2: Run the focused test suite**

Run:

```bash
python3 -m pytest tests/test_audit_agent.py tests/test_audit_json_retry.py -q
```

Expected:

```text
PASS
```

- [ ] **Step 3: Do one live audit smoke test**

Run the audit worker against a single pending script and confirm:
- the structured JSON path is used first
- no `audit_response_parse_error` is logged for the successful row
- the audit result is written back as `pass`, `needs_repair`, or `reject`

Use the existing live worker path and inspect the newest `topic_scripts` row plus the latest `topic_research_cron_runs` row.

Capture a before/after snapshot in the live test notes:
- `topic_scripts` counts by `audit_status` before the audit run
- the pending script id selected for the smoke test
- the exact audit worker invocation used
- `topic_scripts` counts by `audit_status` after the audit run
- the newest `topic_research_cron_runs` row status and completion fields
- whether the structured JSON path or the repair fallback was used

- [ ] **Step 4: Commit the docs change**

```bash
git add docs.md
git commit -m "docs: describe audit json fallback hierarchy"
```

---

## Self-Review

- Spec coverage: structured-output primary path, text-repair fallback, and docs are all covered.
- Live validation coverage: the plan now includes a real worker smoke test with before/after database snapshots and run status checks.
- Placeholder scan: no TBD/TODO placeholders.
- Type consistency: all code snippets use existing names from `audit.py`, `AuditResult`, `audit_single_script`, and `generate_gemini_json`.
- Scope check: limited to audit parsing and its tests; no topic-generation rewrite.
