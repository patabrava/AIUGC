# Script Hardening + Audit Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the script generation pipeline to prevent contaminated text from persisting, then add an LLM-based audit agent that scores persisted scripts on nativeness, hook quality, prompt compliance, and virality potential.

**Architecture:** Two phases. Phase A (Tasks 1-3): add `_clean_fact_pool()` to filter contaminated facts before Gemini sees them, sanitize dossier fields in prompt context, add `detect_metadata_bleed()` to the persistence firewall, and write quality_notes on cleaned scripts. Phase B (Tasks 4-7): create `app/features/topics/audit.py` with core audit logic, `audit_prompt.txt` with evaluation rubric, `workers/audit_worker.py` as the scheduled runner, and add DB query helpers. Live verification in Task 8.

**Tech Stack:** Python 3.11, FastAPI, Supabase, Gemini API (gemini-2.5-flash), pytest

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `app/features/topics/topic_validation.py` | Add `_clean_fact_pool()`, `detect_metadata_bleed()` | Modify |
| `app/features/topics/research_runtime.py:332-340` | Replace `sanitize_fact_fragments()` with `_clean_fact_pool()` | Modify |
| `app/features/topics/prompts.py:349-361` | Sanitize facts/risks/summary in research context | Modify |
| `app/features/topics/queries.py:629-741` | Add metadata bleed gate, write quality_notes | Modify |
| `app/features/topics/audit.py` | Core audit logic — `audit_single_script()`, response parsing | Create |
| `app/features/topics/prompt_data/audit_prompt.txt` | Gemini evaluation prompt with scoring rubric | Create |
| `workers/audit_worker.py` | Scheduled worker that audits unscored scripts | Create |
| `tests/test_script_hardening.py` | Tests for clean-room compiler + firewall | Create |
| `tests/test_audit_agent.py` | Tests for audit logic with mocked LLM | Create |

---

### Task 1: Add `_clean_fact_pool()` to topic_validation.py

**Files:**
- Modify: `app/features/topics/topic_validation.py`
- Test: `tests/test_script_hardening.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_script_hardening.py`:

```python
"""Tests for script contamination hardening."""

from app.features.topics.topic_validation import _clean_fact_pool


def test_clean_fact_pool_strips_label_fragments():
    """Facts with research labels are rejected."""
    facts = [
        "Zentrale Erkenntnisse: Barrierefreiheit fehlt.",
        "Nur 20 Prozent der Haltestellen sind barrierefrei.",
    ]
    result = _clean_fact_pool(facts)
    assert len(result) == 1
    assert "Zentrale Erkenntnisse" not in result[0]
    assert "20 Prozent" in result[0]


def test_clean_fact_pool_strips_citation_residue():
    """Facts with citations are rejected."""
    facts = [
        "Barrierefreiheit ist gesetzlich vorgeschrieben [cite: 1].",
        "Die KfW foerdert barrierefreie Umbauten.",
    ]
    result = _clean_fact_pool(facts)
    assert len(result) == 1
    assert "KfW" in result[0]


def test_clean_fact_pool_rejects_short_fragments():
    """Fragments under 4 words are dropped."""
    facts = [
        "Nur zwei Prozent.",
        "Nur zwei Prozent aller Wohnungen sind rollstuhlgerecht.",
    ]
    result = _clean_fact_pool(facts)
    assert len(result) == 1
    assert "rollstuhlgerecht" in result[0]


def test_clean_fact_pool_deduplicates():
    """Duplicate facts are removed."""
    facts = [
        "Nur 20 Prozent der Haltestellen sind barrierefrei.",
        "Nur 20 Prozent der Haltestellen sind barrierefrei.",
    ]
    result = _clean_fact_pool(facts)
    assert len(result) == 1


def test_clean_fact_pool_handles_none_and_empty():
    """None values and empty strings are silently dropped."""
    facts = [None, "", "  ", "Gute Arbeitshilfen halten dich im Job."]
    result = _clean_fact_pool(facts)
    assert len(result) == 1
    assert "Arbeitshilfen" in result[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd AIUGC && python -m pytest tests/test_script_hardening.py -v`
Expected: FAIL — `_clean_fact_pool` does not exist

- [ ] **Step 3: Implement `_clean_fact_pool()`**

In `app/features/topics/topic_validation.py`, add after the `sanitize_fact_fragments()` function (after line 375):

```python
def _clean_fact_pool(raw_values: List[Any]) -> List[str]:
    """Clean and validate individual fact sentences before they enter the script pool.

    Each fact is split into sentences, sanitized independently, and rejected
    if it triggers spoken-copy issues or is too short to be meaningful.
    """
    clean: List[str] = []
    seen: set = set()
    for value in raw_values:
        text = str(value or "").strip()
        if not text:
            continue
        sanitized = sanitize_spoken_fragment(text, ensure_terminal=True)
        if not sanitized:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", sanitized):
            sentence = sentence.strip()
            if not sentence:
                continue
            word_count = len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", sentence))
            if word_count < 4:
                continue
            if detect_spoken_copy_issues(sentence):
                continue
            sig = sentence.lower()
            if sig in seen:
                continue
            seen.add(sig)
            clean.append(sentence)
    return clean
```

Also add `import re` at the top if not already present (it is — verify by checking existing imports).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd AIUGC && python -m pytest tests/test_script_hardening.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/topic_validation.py tests/test_script_hardening.py
git commit -m "feat(hardening): add _clean_fact_pool with per-sentence validation and contamination rejection"
```

---

### Task 2: Wire `_clean_fact_pool()` into Stage 3 and sanitize prompt context

**Files:**
- Modify: `app/features/topics/research_runtime.py:332-340`
- Modify: `app/features/topics/prompts.py:349-361`
- Test: `tests/test_script_hardening.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_script_hardening.py`:

```python
from unittest.mock import patch


def test_prompt_research_context_sanitizes_facts():
    """Facts injected into the prompt must be sanitized."""
    from app.features.topics.prompts import _format_prompt1_research_context

    dossier = {
        "topic": "Barrierefreiheit",
        "seed_topic": "OEPNV",
        "source_summary": "Zentrale Erkenntnisse: Das PBefG fordert Barrierefreiheit.",
        "framework_candidates": ["PAL"],
    }
    lane = {
        "title": "Haltestellen",
        "facts": ["Leitende Zusammenfassung: Nur 20% barrierefrei.", "Rampen fehlen oft."],
        "risk_notes": ["Demografische Dringlichkeit: Bedarf steigt."],
        "framework_candidates": ["PAL"],
    }
    context = _format_prompt1_research_context(dossier, lane)
    assert "Zentrale Erkenntnisse" not in context
    assert "Leitende Zusammenfassung" not in context
    assert "Demografische Dringlichkeit" not in context
    assert "Rampen fehlen" in context
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd AIUGC && python -m pytest tests/test_script_hardening.py::test_prompt_research_context_sanitizes_facts -v`
Expected: FAIL — labels still pass through to context

- [ ] **Step 3: Sanitize facts and risks in `_format_prompt1_research_context()`**

In `app/features/topics/prompts.py`, replace lines 349-350 with:

```python
    lane_facts = [
        f"- {sanitize_spoken_fragment(fact, ensure_terminal=True)}"
        for fact in list(lane.get("facts") or [])[:4]
        if sanitize_spoken_fragment(fact, ensure_terminal=True)
    ]
    lane_risks = [
        f"- {sanitize_spoken_fragment(risk, ensure_terminal=True)}"
        for risk in list(lane.get("risk_notes") or [])[:3]
        if sanitize_spoken_fragment(risk, ensure_terminal=True)
    ]
```

And replace line 361 (source summary injection) with:

```python
        f"Lane Source Summary: {_clip_text(sanitize_metadata_text(lane.get('source_summary') or payload.get('source_summary') or ''), 450)}",
```

Add the imports at the top of the file if not present:

```python
from app.features.topics.topic_validation import sanitize_spoken_fragment, sanitize_metadata_text
```

- [ ] **Step 4: Replace `sanitize_fact_fragments()` with `_clean_fact_pool()` in research_runtime.py**

In `app/features/topics/research_runtime.py`, replace lines 332-340:

```python
    lane_fact_texts = sanitize_fact_fragments(
        list(lane_payload.get("facts") or [])
        + list((dossier_payload or {}).get("facts") or [])
        + [
            lane_payload.get("angle"),
            *(lane_payload.get("risk_notes") or []),
            *((dossier_payload or {}).get("risk_notes") or []),
        ]
    )
```

With:

```python
    lane_fact_texts = _clean_fact_pool(
        list(lane_payload.get("facts") or [])
        + list((dossier_payload or {}).get("facts") or [])
        + [
            lane_payload.get("angle"),
            *(lane_payload.get("risk_notes") or []),
            *((dossier_payload or {}).get("risk_notes") or []),
        ]
    )
```

And update the import at the top of `research_runtime.py` to include `_clean_fact_pool`:

```python
from app.features.topics.topic_validation import (
    ...,
    _clean_fact_pool,
)
```

- [ ] **Step 5: Run all tests**

Run: `cd AIUGC && python -m pytest tests/test_script_hardening.py tests/test_topic_prompt_templates.py tests/test_topics_gemini_flow.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/research_runtime.py app/features/topics/prompts.py tests/test_script_hardening.py
git commit -m "feat(hardening): wire clean-room fact pool into Stage 3 and sanitize prompt context"
```

---

### Task 3: Add metadata bleed detection and persistence firewall tightening

**Files:**
- Modify: `app/features/topics/topic_validation.py`
- Modify: `app/features/topics/queries.py:629-741`
- Test: `tests/test_script_hardening.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_script_hardening.py`:

```python
from app.features.topics.topic_validation import detect_metadata_bleed


def test_detect_metadata_bleed_catches_verbatim_summary():
    """Script containing 6+ consecutive words from summary is flagged."""
    script = "Das PBefG forderte vollstaendige Barrierefreiheit bis zum Januar 2022."
    summary = "Das PBefG forderte vollstaendige Barrierefreiheit bis zum Januar 2022 fuer den gesamten OEPNV."
    result = detect_metadata_bleed(script, source_summary=summary)
    assert result is not None
    assert result["kind"] == "metadata_bleed"


def test_detect_metadata_bleed_allows_partial_overlap():
    """Script sharing fewer than 6 consecutive words is OK."""
    script = "Dein Recht auf Mitfahrt existiert nur auf dem Papier."
    summary = "Das PBefG forderte vollstaendige Barrierefreiheit bis 2022."
    result = detect_metadata_bleed(script, source_summary=summary)
    assert result is None


def test_detect_metadata_bleed_checks_cluster_summary():
    """Cluster summary is also checked for bleed."""
    script = "Barrierefreiheit im OEPNV bleibt eine gesellschaftliche Herausforderung fuer alle Beteiligten."
    cluster = "Barrierefreiheit im OEPNV bleibt eine gesellschaftliche Herausforderung fuer alle Beteiligten und Verkehrsbetriebe."
    result = detect_metadata_bleed(script, cluster_summary=cluster)
    assert result is not None


def test_detect_metadata_bleed_empty_inputs():
    """Empty or None inputs return None."""
    assert detect_metadata_bleed("", source_summary="Foo bar baz.") is None
    assert detect_metadata_bleed("Script text.", source_summary="") is None
    assert detect_metadata_bleed("Script text.") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd AIUGC && python -m pytest tests/test_script_hardening.py -k metadata_bleed -v`
Expected: FAIL — `detect_metadata_bleed` does not exist

- [ ] **Step 3: Implement `detect_metadata_bleed()`**

In `app/features/topics/topic_validation.py`, add after `_clean_fact_pool()`:

```python
def detect_metadata_bleed(
    script: str,
    *,
    source_summary: str = "",
    cluster_summary: str = "",
    min_consecutive_words: int = 6,
) -> Optional[Dict[str, Any]]:
    """Detect if a script contains long verbatim runs from metadata fields.

    Returns a dict with kind='metadata_bleed' if any metadata field shares
    min_consecutive_words or more consecutive words with the script.
    Returns None if clean.
    """
    script_text = str(script or "").strip().lower()
    if not script_text:
        return None

    script_words = re.findall(r"[a-zäöüß0-9-]+", script_text)
    if len(script_words) < min_consecutive_words:
        return None

    for field_name, field_value in [("source_summary", source_summary), ("cluster_summary", cluster_summary)]:
        value = str(field_value or "").strip().lower()
        if not value:
            continue
        meta_words = re.findall(r"[a-zäöüß0-9-]+", value)
        if len(meta_words) < min_consecutive_words:
            continue
        for i in range(len(meta_words) - min_consecutive_words + 1):
            window = " ".join(meta_words[i : i + min_consecutive_words])
            if window in " ".join(script_words):
                return {
                    "kind": "metadata_bleed",
                    "field": field_name,
                    "matched_words": min_consecutive_words,
                    "window": window,
                }
    return None
```

Add `Optional` and `Dict` to typing imports if not already present.

- [ ] **Step 4: Wire bleed detection into `upsert_topic_script_variants()`**

In `app/features/topics/queries.py`, after the existing `detect_spoken_copy_issues()` gate (after line 642), add:

```python
        bleed_issue = detect_metadata_bleed(
            script,
            source_summary=str(variant.get("source_summary") or ""),
            cluster_summary=str(variant.get("cluster_summary") or ""),
        )
        if bleed_issue:
            logger.warning(
                "topic_script_integrity_rejected",
                topic_registry_id=topic_registry_id,
                topic_research_dossier_id=topic_research_dossier_id,
                target_length_tier=tier,
                bucket=bucket,
                lane_key=lane_key,
                reason="metadata_bleed",
                bleed_field=bleed_issue.get("field"),
                bleed_window=bleed_issue.get("window"),
                script_preview=script[:240],
            )
            continue
```

Add the import at the top of `queries.py`:

```python
from app.features.topics.topic_validation import detect_metadata_bleed
```

- [ ] **Step 5: Run all tests**

Run: `cd AIUGC && python -m pytest tests/test_script_hardening.py tests/test_topics_gemini_flow.py tests/test_topics_hub.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/features/topics/topic_validation.py app/features/topics/queries.py tests/test_script_hardening.py
git commit -m "feat(hardening): add metadata bleed detection and wire into persistence firewall"
```

---

### Task 4: Create audit prompt

**Files:**
- Create: `app/features/topics/prompt_data/audit_prompt.txt`
- Test: `tests/test_audit_agent.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_audit_agent.py`:

```python
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
    assert '"status"' in content or "status" in content
    assert "pass" in content
    assert "needs_repair" in content
    assert "reject" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd AIUGC && python -m pytest tests/test_audit_agent.py -v`
Expected: FAIL — file does not exist

- [ ] **Step 3: Create `audit_prompt.txt`**

Create `app/features/topics/prompt_data/audit_prompt.txt`:

```
AUDIT_AGENT
Du bewertest einen einzelnen TikTok-Scripttext fuer die Zielgruppe Rollstuhlnutzer:innen in Deutschland.

SCRIPT ZU BEWERTEN:
{script}

TIER: {target_length_tier}s
TOPIC: {topic}

BEWERTUNGSDIMENSIONEN (jeweils 0-25 Punkte):

1. german_nativeness (0-25):
- Klingt der Text wie natuerliches, gesprochenes Deutsch?
- Ist die Formulierung direkt, fluessig und natuerlich?
- Gibt es Uebersetzungsartefakte, steife Wendungen oder buerokratische Sprache?
- Wuerde eine Muttersprachlerin diesen Satz genau so sagen?
- 0-10: Klingt uebersetzt oder kuenstlich
- 11-18: Verstaendlich aber nicht nativ
- 19-25: Klingt wie von einer deutschen Muttersprachlerin gesprochen

2. hook_quality (0-25):
- Erzwingen die ersten 2-3 Woerter Aufmerksamkeit?
- Loest der Hook eine Emotion aus (Wut, Ueberraschung, Wiedererkennung, Neugier)?
- Wuerde jemand beim Scrollen durch TikTok stoppen?
- Passt der Hook zu einer der bevorzugten Hook-Familien: Kontrast, Provokation, Identitaet, Zahlen, Konsequenz, Neugier, Fehler/Warnung?
- 0-10: Kein Hook oder Lehrer-Energie (Hast du gewusst, Wusstest du)
- 11-18: Solider Hook, aber nicht scroll-stoppend
- 19-25: Sofortiger Scroll-Stopp, starke Emotion

3. prompt_compliance (0-25):
- Haelt der Text die Wortanzahl des Tiers ein? (8s: 12-15, 16s: 26-36, 32s: 54-74)
- Bleibt er beim Thema?
- Folgt er der Tonalitaet: locker-direkt, du-Form, empowernd?
- Zeigt er Barrieren (Systemversagen), nicht Ueberwindung (Inspiration Porn)?
- 0-10: Verletzt Tier-Regeln oder Tonalitaet
- 11-18: Technisch korrekt aber unpraezise
- 19-25: Exakt im Tier, perfekte Tonalitaet

4. virality_potential (0-25):
- Wuerde dieser Text Kommentare, Shares oder Saves ausloesen?
- Erzeugt er eine Wissensluecke (Curiosity Gap)?
- Nutzt er den Kurveneffekt (Barrierefreiheit nuetzt allen)?
- Ist der Ton wie eine Freundin, nicht wie eine Lehrerin?
- 0-10: Kein Engagement-Potenzial
- 11-18: Solide, aber keine virale Wirkung
- 19-25: Hohe Wahrscheinlichkeit fuer Shares/Saves/Kommentare

ANTWORTFORMAT:
Antworte NUR mit einem validen JSON-Objekt. Kein Zusatztext, keine Markdown-Fences.

{
  "german_nativeness": {"score": <0-25>, "notes": "<kurze Begruendung>"},
  "hook_quality": {"score": <0-25>, "notes": "<kurze Begruendung>"},
  "prompt_compliance": {"score": <0-25>, "notes": "<kurze Begruendung>"},
  "virality_potential": {"score": <0-25>, "notes": "<kurze Begruendung>"},
  "total_score": <0-100>,
  "status": "<pass|needs_repair|reject>",
  "summary": "<Ein-Satz-Zusammenfassung>"
}

STATUS-SCHWELLEN:
- "pass": total_score >= 70
- "needs_repair": total_score 40-69
- "reject": total_score < 40
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd AIUGC && python -m pytest tests/test_audit_agent.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/prompt_data/audit_prompt.txt tests/test_audit_agent.py
git commit -m "feat(audit): create audit prompt with 4-dimension scoring rubric"
```

---

### Task 5: Create audit core module

**Files:**
- Create: `app/features/topics/audit.py`
- Test: `tests/test_audit_agent.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit_agent.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd AIUGC && python -m pytest tests/test_audit_agent.py -k "audit_single" -v`
Expected: FAIL — `audit.py` does not exist

- [ ] **Step 3: Implement `app/features/topics/audit.py`**

Create `app/features/topics/audit.py`:

```python
"""German Nativeness Audit Agent — post-persistence quality gate.

Evaluates persisted topic_scripts on nativeness, hook quality,
prompt compliance, and virality potential using Gemini.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.features.topics.topic_validation import detect_spoken_copy_issues

logger = get_logger(__name__)

AUDIT_PROMPT_PATH = Path(__file__).parent / "prompt_data" / "audit_prompt.txt"

AUDIT_SYSTEM_PROMPT = (
    "You are the Lippe Lift Studio audit agent.\n"
    "Evaluate the script and return ONLY valid JSON.\n"
    "No markdown fences, no extra text.\n"
    "All notes in German."
)


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
    return template.format(
        script=str(row.get("script") or ""),
        target_length_tier=int(row.get("target_length_tier") or 8),
        topic=str(row.get("title") or row.get("topic") or ""),
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
    prompt = _build_audit_prompt(row)
    try:
        raw_response = llm.generate_gemini_text(
            prompt=prompt,
            system_prompt=AUDIT_SYSTEM_PROMPT,
            max_tokens=1000,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd AIUGC && python -m pytest tests/test_audit_agent.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/audit.py tests/test_audit_agent.py
git commit -m "feat(audit): create core audit module with deterministic pre-check and LLM evaluation"
```

---

### Task 6: Add audit DB queries

**Files:**
- Modify: `app/features/topics/queries.py`
- Test: `tests/test_audit_agent.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit_agent.py`:

```python
from unittest.mock import patch, MagicMock


def test_get_unaudited_scripts_returns_null_quality_rows():
    """Query must filter for quality_score IS NULL."""
    mock_response = MagicMock()
    mock_response.data = [
        {"id": "row-1", "script": "Test script.", "target_length_tier": 8, "title": "Test"},
    ]
    mock_table = MagicMock()
    mock_table.select.return_value.is_.return_value.limit.return_value.execute.return_value = mock_response

    mock_client = MagicMock()
    mock_client.client.table.return_value = mock_table

    with patch("app.features.topics.queries.supabase", mock_client):
        from app.features.topics.queries import get_unaudited_scripts
        rows = get_unaudited_scripts(limit=50)
    assert len(rows) == 1
    assert rows[0]["id"] == "row-1"


def test_update_script_quality_writes_score_and_notes():
    """Update must write quality_score and quality_notes."""
    mock_response = MagicMock()
    mock_response.data = [{"id": "row-1", "quality_score": 85}]
    mock_table = MagicMock()
    mock_table.update.return_value.eq.return_value.execute.return_value = mock_response

    mock_client = MagicMock()
    mock_client.client.table.return_value = mock_table

    with patch("app.features.topics.queries.supabase", mock_client):
        from app.features.topics.queries import update_script_quality
        update_script_quality(script_id="row-1", quality_score=85, quality_notes='{"status": "pass"}')

    mock_table.update.assert_called_once()
    call_args = mock_table.update.call_args[0][0]
    assert call_args["quality_score"] == 85
    assert "pass" in call_args["quality_notes"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd AIUGC && python -m pytest tests/test_audit_agent.py -k "unaudited or update_script" -v`
Expected: FAIL — functions don't exist

- [ ] **Step 3: Implement query functions**

In `app/features/topics/queries.py`, add at the end of the file (before any final blank lines):

```python
def get_unaudited_scripts(*, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch topic_scripts rows where quality_score is NULL (unaudited)."""
    response = (
        supabase.client.table("topic_scripts")
        .select("id, title, script, target_length_tier, post_type, bucket, lane_key, source_summary, cluster_id")
        .is_("quality_score", "null")
        .limit(limit)
        .execute()
    )
    return list(response.data or [])


def update_script_quality(*, script_id: str, quality_score: int, quality_notes: str) -> None:
    """Write audit results to a topic_scripts row."""
    supabase.client.table("topic_scripts").update(
        {"quality_score": quality_score, "quality_notes": quality_notes}
    ).eq("id", script_id).execute()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd AIUGC && python -m pytest tests/test_audit_agent.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/features/topics/queries.py tests/test_audit_agent.py
git commit -m "feat(audit): add get_unaudited_scripts and update_script_quality queries"
```

---

### Task 7: Create audit worker

**Files:**
- Create: `workers/audit_worker.py`
- Test: `tests/test_audit_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audit_agent.py`:

```python
def test_audit_worker_run_audit_cycle(monkeypatch):
    """Audit cycle must fetch unaudited rows, audit them, and write results."""
    mock_rows = [
        {"id": "r1", "script": "Dein Recht auf Mitfahrt existiert nur auf dem Papier.", "target_length_tier": 8, "title": "OEPNV"},
        {"id": "r2", "script": "Nur 2 Prozent aller Wohnungen sind rollstuhlgerecht.", "target_length_tier": 8, "title": "Wohnen"},
    ]

    updated = []

    def mock_get_unaudited(*, limit=50):
        return mock_rows

    def mock_update(*, script_id, quality_score, quality_notes):
        updated.append({"id": script_id, "score": quality_score})

    monkeypatch.setattr("workers.audit_worker.get_unaudited_scripts", mock_get_unaudited)
    monkeypatch.setattr("workers.audit_worker.update_script_quality", mock_update)

    mock_llm = MagicMock()
    mock_llm.generate_gemini_text.return_value = _mock_llm_response(80, "pass")
    monkeypatch.setattr("workers.audit_worker.get_llm_client", lambda: mock_llm)

    from workers.audit_worker import run_audit_cycle
    run_audit_cycle()

    assert len(updated) == 2
    assert all(u["score"] == 80 for u in updated)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd AIUGC && python -m pytest tests/test_audit_agent.py::test_audit_worker_run_audit_cycle -v`
Expected: FAIL — `workers/audit_worker.py` does not exist

- [ ] **Step 3: Create `workers/audit_worker.py`**

```python
"""Audit Worker — scores persisted scripts on nativeness, hooks, compliance, virality.

Runs as a scheduled background worker (like expansion_worker.py).
Fetches unaudited topic_scripts rows and evaluates them using Gemini.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.logging import configure_logging, get_logger
from app.adapters.llm_client import get_llm_client
from app.features.topics.audit import audit_batch
from app.features.topics.queries import get_unaudited_scripts, update_script_quality

configure_logging()
logger = get_logger(__name__)

AUDIT_INTERVAL_SECONDS = 12 * 60 * 60  # 12 hours
MAX_SCRIPTS_PER_RUN = 50


def run_audit_cycle() -> None:
    """Run one audit cycle: fetch unaudited scripts, evaluate, write results."""
    rows = get_unaudited_scripts(limit=MAX_SCRIPTS_PER_RUN)
    if not rows:
        logger.info("audit_cycle_no_pending_scripts")
        return

    logger.info("audit_cycle_starting", pending_count=len(rows))
    llm = get_llm_client()
    results = audit_batch(rows, llm=llm)

    for result in results:
        update_script_quality(
            script_id=result.script_id,
            quality_score=result.total_score,
            quality_notes=result.quality_notes,
        )

    pass_count = sum(1 for r in results if r.status == "pass")
    repair_count = sum(1 for r in results if r.status == "needs_repair")
    reject_count = sum(1 for r in results if r.status == "reject")

    logger.info(
        "audit_cycle_complete",
        total=len(results),
        passed=pass_count,
        needs_repair=repair_count,
        rejected=reject_count,
    )


def main() -> None:
    logger.info(
        "audit_worker_started",
        interval_hours=AUDIT_INTERVAL_SECONDS / 3600,
        max_scripts=MAX_SCRIPTS_PER_RUN,
    )

    while True:
        try:
            run_audit_cycle()
        except KeyboardInterrupt:
            logger.info("audit_worker_stopped_by_user")
            break
        except Exception:
            logger.exception("audit_worker_error")

        logger.info(
            "audit_worker_sleeping",
            next_run_in_hours=AUDIT_INTERVAL_SECONDS / 3600,
        )
        try:
            time.sleep(AUDIT_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            logger.info("audit_worker_stopped_by_user")
            break


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd AIUGC && python -m pytest tests/test_audit_agent.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add workers/audit_worker.py tests/test_audit_agent.py
git commit -m "feat(audit): create audit worker with scheduled evaluation cycle"
```

---

### Task 8: Full regression + live verification

**Files:**
- Modify: `deep-research-flow.md` (documentation update)
- No other code changes — verification only

- [ ] **Step 1: Run full test suite**

Run: `cd AIUGC && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS (including new test_script_hardening.py and test_audit_agent.py)

- [ ] **Step 2: Run ruff linting**

Run: `cd AIUGC && ruff check app/features/topics/audit.py app/features/topics/topic_validation.py app/features/topics/queries.py app/features/topics/prompts.py workers/audit_worker.py`
Expected: No new errors

- [ ] **Step 3: Live generation test — verify hardening**

Run a script generation cycle and check no contaminated rows persist:

```bash
cd AIUGC && python -c "
import logging; logging.disable(logging.CRITICAL)
from app.adapters.llm_client import get_llm_client
from app.features.topics.prompts import build_prompt1
from app.core.video_profiles import get_duration_profile
from app.features.topics import prompts as _p
from app.features.topics.topic_validation import detect_spoken_copy_issues, detect_metadata_bleed, _clean_fact_pool

_p._load_hook_bank_payload.cache_clear()
_p.get_hook_bank.cache_clear()

# Test _clean_fact_pool with contaminated input
contaminated = [
    'Zentrale Erkenntnisse: Barrierefreiheit fehlt.',
    'Nur 20 Prozent der Haltestellen sind barrierefrei.',
    'Demografische Dringlichkeit: Bedarf steigt.',
    'Guter Fakt ohne Kontamination.',
    '[cite: 1] Quellenrest.',
]
clean = _clean_fact_pool(contaminated)
print(f'Clean fact pool: {len(contaminated)} input -> {len(clean)} output')
for f in clean:
    print(f'  OK: {f}')

# Test metadata bleed detection
script = 'Das PBefG forderte vollstaendige Barrierefreiheit bis zum Januar 2022.'
summary = 'Das PBefG forderte vollstaendige Barrierefreiheit bis zum Januar 2022 fuer den gesamten OEPNV.'
bleed = detect_metadata_bleed(script, source_summary=summary)
print(f'\nMetadata bleed test: {\"DETECTED\" if bleed else \"CLEAN\"} — {bleed}')

# Generate a script and verify it's clean
llm = get_llm_client()
profile = get_duration_profile(8)
prompt = build_prompt1(
    post_type='value', desired_topics=1, profile=profile,
    dossier={'topic': 'OEPNV', 'seed_topic': 'Barrierefreiheit', 'facts': contaminated,
             'sources': [{'title': 'PBefG', 'url': 'https://example.com'}], 'framework_candidates': ['PAL'],
             'source_summary': summary},
    lane_candidate={'title': 'Haltestellen', 'facts': contaminated[:2], 'framework_candidates': ['PAL']},
)
response = llm.generate_gemini_text(prompt=prompt, system_prompt='Return only German script text.', max_tokens=200)
issues = detect_spoken_copy_issues(response.strip())
print(f'\nGenerated script: {response.strip()}')
print(f'Copy issues: {issues if issues else \"NONE — CLEAN\"}')
"
```

Verify:
- `_clean_fact_pool` reduces 5 contaminated inputs to 2 clean facts
- `detect_metadata_bleed` catches the verbatim summary
- Generated script has zero copy issues

- [ ] **Step 4: Live audit test — verify audit agent**

Run the audit agent on a few scripts:

```bash
cd AIUGC && python -c "
import logging; logging.disable(logging.CRITICAL)
from app.adapters.llm_client import get_llm_client
from app.features.topics.audit import audit_single_script

llm = get_llm_client()

scripts = [
    {'id': 'test-1', 'script': 'Dein Recht auf Mitfahrt? Existiert auf dem Papier — nicht an der Haltestelle.', 'target_length_tier': 8, 'title': 'OEPNV Barrierefreiheit'},
    {'id': 'test-2', 'script': 'Nur 2 Prozent aller Wohnungen in Deutschland sind rollstuhlgerecht.', 'target_length_tier': 8, 'title': 'Barrierefreies Wohnen'},
    {'id': 'test-3', 'script': 'Hast du gewusst, dass es Barrierefreiheit gibt?', 'target_length_tier': 8, 'title': 'Bad Hook Test'},
]

for row in scripts:
    result = audit_single_script(row, llm=llm)
    print(f'[{result.status.upper():12s}] Score: {result.total_score:3d} | {row[\"script\"][:60]}...')
    print(f'  Nativeness: {result.german_nativeness}, Hook: {result.hook_quality}, Compliance: {result.prompt_compliance}, Virality: {result.virality_potential}')
    print()
"
```

Verify:
- Good hooks (test-1, test-2) score >= 70 and status "pass"
- Weak hook (test-3) scores lower
- All 4 dimension scores are populated

- [ ] **Step 5: Update `deep-research-flow.md`**

Append to Stage 3 section (after the hook bank bullets added in the previous plan):

```
- Pre-prompt fact pool now uses `_clean_fact_pool()` which validates each sentence independently: strips labels/citations/markdown, rejects fragments < 4 words, rejects sentences with spoken-copy issues
- Persistence firewall includes `detect_metadata_bleed()` check: rejects scripts containing 6+ consecutive words from `source_summary` or `cluster_summary`
```

Add a new section after Stage 4:

```
### Stage 4b — Script audit (async, post-persistence)
- Code: `app/features/topics/audit.audit_single_script` (called via `workers/audit_worker.py`)
- Worker runs every 12 hours on `topic_scripts` rows where `quality_score IS NULL`
- Deterministic pre-check: `detect_spoken_copy_issues()` rejects structural failures (score 0) without LLM call
- LLM evaluation: Gemini scores 4 dimensions (german_nativeness, hook_quality, prompt_compliance, virality_potential), each 0-25
- Status thresholds: pass (>= 70), needs_repair (40-69), reject (< 40)
- Results written to `topic_scripts.quality_score` (numeric) and `topic_scripts.quality_notes` (JSON)
```

- [ ] **Step 6: Commit**

```bash
git add deep-research-flow.md
git commit -m "docs: update deep-research-flow with hardening and audit agent details"
```
