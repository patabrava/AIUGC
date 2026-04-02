# Topic Quality Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one shared pre-persistence quality gate for all topic-writing paths so malformed, dash-heavy, too-short, or time-stale topic text gets repaired or rejected before it reaches `topic_scripts`.

**Architecture:** Keep the current async audit worker as the final arbiter, but add a deterministic normalization and validation layer before persistence. The shared gate lives in `app/features/topics/topic_validation.py`, is wired through research generation, manual hub persistence, and variant expansion, and is backed by prompt-level constraints so the models emit cleaner text before the gate has to repair anything.

**Tech Stack:** Python 3.11, FastAPI, Supabase, Gemini API, pytest

**Budget:** {files: 16, LOC/file: 60-260, deps: 0}

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/features/topics/topic_validation.py` | Shared dash normalization, temporal normalization, tier-aware pre-persistence payload validation | Modify |
| `app/features/topics/research_runtime.py` | Apply shared gate to stage-3 research script candidates before `ResearchAgentItem` leaves generation | Modify |
| `app/features/topics/queries.py` | Persistence firewall; re-run shared gate before `topic_scripts` insert/update | Modify |
| `app/features/topics/hub.py` | Ensure manual hub persistence calls the same shared gate | Modify |
| `app/features/topics/variant_expansion.py` | Run shared gate on value and lifestyle expansion payloads before upsert | Modify |
| `app/features/topics/prompts.py` | Inject 2026/current-date and no-dash prompt constraints into stage-1/stage-3 prompts | Modify |
| `app/features/topics/prompt_data/prompt1_8s.txt` | Increase 8s script word budget, add no-dash and 2026 phrasing rules | Modify |
| `app/features/topics/prompt_data/prompt1_16s.txt` | Add no-dash and 2026 phrasing rules | Modify |
| `app/features/topics/prompt_data/prompt1_32s.txt` | Add no-dash and 2026 phrasing rules | Modify |
| `app/features/topics/prompt_data/prompt1_research.txt` | Tell Deep Research to reason from the current date context (2026) | Modify |
| `app/features/topics/prompt_data/hook_bank.yaml` | Remove dash-style examples and stale 2025-only phrasing from hook exemplars | Modify |
| `app/features/topics/prompt_data/audit_prompt.txt` | Keep audit word bounds aligned with the new 8s range | Modify |
| `tests/test_topic_quality_gate.py` | Unit tests for shared pre-persistence normalization and validation | Create |
| `tests/test_topic_prompt_templates.py` | Prompt-level regression tests for no-dash and 2026 instructions | Modify |
| `tests/test_topics_gemini_flow.py` | Integration coverage for research path normalization before persistence | Modify |
| `tests/test_expand_topic_variants.py` | Integration coverage for expansion path normalization before persistence | Modify |
| `docs.md` | Document the new pre-persistence gate and final audit responsibility split | Modify |

---

### Task 1: Add the shared deterministic quality gate

**Files:**
- Modify: `app/features/topics/topic_validation.py`
- Create: `tests/test_topic_quality_gate.py`

- [ ] **Step 1: Write the failing unit tests**

Create `tests/test_topic_quality_gate.py`:

```python
from app.core.errors import ValidationError
from app.features.topics.topic_validation import (
    normalize_dash_separators,
    normalize_temporal_reference,
    validate_pre_persistence_topic_payload,
)


def test_normalize_dash_separators_removes_long_dash_but_keeps_hyphenated_words():
    text = "Deutschland 2026 — und rollstuhl-gerecht bleibt ein echtes Problem."
    result = normalize_dash_separators(text)
    assert "—" not in result
    assert "rollstuhl-gerecht" in result
    assert "Deutschland 2026 und" in result


def test_normalize_temporal_reference_rewrites_stale_ab_phrase():
    result = normalize_temporal_reference(
        "Ab 2025 gibt es mehr Leistungen fuer barrierefreie Dienste.",
        current_year=2026,
    )
    assert "Ab 2025" not in result
    assert "Seit 2025" in result


def test_validate_pre_persistence_topic_payload_extends_short_8s_script():
    payload = validate_pre_persistence_topic_payload(
        {
            "topic": "Pflegegrad pruefen",
            "title": "Pflegegrad pruefen",
            "script": "Pruef deinen Pflegegrad jetzt sofort.",
            "caption": "Pflegegrad rechtzeitig pruefen spart Rueckfragen im Alltag.",
            "source_summary": "Pflegegrad rechtzeitig pruefen spart Rueckfragen im Alltag.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        },
        target_length_tier=8,
        current_year=2026,
    )
    assert len(payload["script"].split()) >= 14
    assert payload["script"].endswith(".")


def test_validate_pre_persistence_topic_payload_strips_dash_from_all_text_fields():
    payload = validate_pre_persistence_topic_payload(
        {
            "topic": "MSZ — Rechte",
            "title": "MSZ — Rechte",
            "script": "Deutschland 2026 — dein Anspruch gilt seit 2025 fuer Hilfe am Bahnhof.",
            "caption": "MSZ — Hilfe am Bahnhof.",
            "source_summary": "MSZ — Hilfe am Bahnhof.",
            "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        },
        target_length_tier=16,
        current_year=2026,
    )
    for key in ("topic", "title", "script", "caption", "source_summary"):
        assert "—" not in payload[key]


def test_validate_pre_persistence_topic_payload_rejects_unrepairable_short_fragment():
    try:
        validate_pre_persistence_topic_payload(
            {
                "topic": "Bahnhof",
                "title": "Bahnhof",
                "script": "Nur Chaos.",
                "caption": "",
                "source_summary": "",
                "disclaimer": "Keine Rechts- oder medizinische Beratung.",
            },
            target_length_tier=8,
            current_year=2026,
        )
    except ValidationError as exc:
        assert exc.code == "TOPIC_QUALITY_GATE_FAILED"
    else:
        raise AssertionError("Expected ValidationError")
```

- [ ] **Step 2: Run the unit tests and confirm failure**

Run:

```bash
python3 -m pytest tests/test_topic_quality_gate.py -q
```

Expected:

```text
FAIL tests/test_topic_quality_gate.py
E   ImportError: cannot import name 'normalize_dash_separators'
```

- [ ] **Step 3: Add the shared helper functions in `topic_validation.py`**

Add the new constants near the existing prompt bounds and add the helpers below `sanitize_metadata_text(...)`:

```python
CURRENT_TOPIC_CONTEXT_YEAR = 2026

PROMPT1_WORD_BOUNDS = {
    8: (14, 18),
    16: (26, 36),
    32: (54, 74),
}

_DASH_SEPARATOR_TABLE = str.maketrans({
    "—": " ",
    "–": " ",
    "―": " ",
    "−": " ",
})


def normalize_dash_separators(text: Any) -> str:
    cleaned = str(text or "").translate(_DASH_SEPARATOR_TABLE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def normalize_temporal_reference(text: Any, *, current_year: int = CURRENT_TOPIC_CONTEXT_YEAR) -> str:
    cleaned = normalize_dash_separators(text)

    def _replace_ab_year(match: re.Match[str]) -> str:
        year = int(match.group("year"))
        return f"Seit {year}" if year < current_year else f"Ab {year}"

    cleaned = re.sub(
        r"\bAb\s+(?P<year>20\d{2})\b",
        _replace_ab_year,
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def validate_pre_persistence_topic_payload(
    payload: Dict[str, Any],
    *,
    target_length_tier: int,
    current_year: int = CURRENT_TOPIC_CONTEXT_YEAR,
) -> Dict[str, Any]:
    normalized = dict(payload)
    metadata_fields = ("topic", "title", "caption", "source_summary", "disclaimer", "rotation", "cta")

    for field in metadata_fields:
        if field in normalized:
            normalized[field] = sanitize_metadata_text(
                normalize_temporal_reference(normalized.get(field), current_year=current_year),
                max_chars=500 if field in {"caption", "source_summary"} else None,
            )

    script = sanitize_spoken_fragment(
        normalize_temporal_reference(normalized.get("script"), current_year=current_year),
        ensure_terminal=True,
    )
    min_words, max_words = get_prompt1_word_bounds(target_length_tier)
    script_words = len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", script))

    if target_length_tier == 8 and script_words < min_words:
        addon_source = normalized.get("source_summary") or normalized.get("caption") or ""
        addon = sanitize_spoken_fragment(addon_source, ensure_terminal=False)
        if addon:
            addon_clause = addon.rstrip(".!?")
            script = f"{script.rstrip('.!?')}, {addon_clause}.".strip()
            script = sanitize_spoken_fragment(script, ensure_terminal=True)
            script_words = len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", script))

    if script_words < min_words or script_words > max_words:
        raise ValidationError(
            message=f"Script failed tier envelope for {target_length_tier}s",
            code="TOPIC_QUALITY_GATE_FAILED",
            details={"target_length_tier": target_length_tier, "word_count": script_words},
        )

    normalized["script"] = script
    return normalized
```

- [ ] **Step 4: Re-run the unit tests**

Run:

```bash
python3 -m pytest tests/test_topic_quality_gate.py -q
```

Expected:

```text
5 passed
```

- [ ] **Step 5: Commit the shared gate**

```bash
git add app/features/topics/topic_validation.py tests/test_topic_quality_gate.py
git commit -m "feat(topics): add shared pre-persistence quality gate"
```

---

### Task 2: Push the rules into the prompts so the models start cleaner

**Files:**
- Modify: `app/features/topics/prompts.py`
- Modify: `app/features/topics/prompt_data/prompt1_8s.txt`
- Modify: `app/features/topics/prompt_data/prompt1_16s.txt`
- Modify: `app/features/topics/prompt_data/prompt1_32s.txt`
- Modify: `app/features/topics/prompt_data/prompt1_research.txt`
- Modify: `app/features/topics/prompt_data/hook_bank.yaml`
- Modify: `app/features/topics/prompt_data/audit_prompt.txt`
- Modify: `tests/test_topic_prompt_templates.py`

- [ ] **Step 1: Add the failing prompt/template regression tests**

Append to `tests/test_topic_prompt_templates.py`:

```python
def test_prompt1_8s_contains_new_word_range_and_no_dash_rule():
    prompt = build_prompt1(post_type="value", desired_topics=1)
    assert "14-18 Woerter" in prompt
    assert "Keine Gedankenstriche" in prompt
    assert "Seit 2025" in prompt


def test_prompt1_research_mentions_current_year_context():
    from app.features.topics.prompts import build_topic_research_prompt

    prompt = build_topic_research_prompt(
        seed_topic="BFSG",
        post_type="value",
        target_length_tier=8,
    )
    assert "Heute ist 2026" in prompt
    assert "seit 2025" in prompt.lower()


def test_hook_bank_examples_no_longer_contain_long_dashes():
    hook_bank = (PROMPT_DATA_DIR / "hook_bank.yaml").read_text(encoding="utf-8")
    assert "—" not in hook_bank
    assert "Ab 2025 gibt's" not in hook_bank
```

- [ ] **Step 2: Run the prompt regression tests and confirm failure**

Run:

```bash
python3 -m pytest tests/test_topic_prompt_templates.py -q
```

Expected:

```text
FAIL test_prompt1_8s_contains_new_word_range_and_no_dash_rule
FAIL test_prompt1_research_mentions_current_year_context
FAIL test_hook_bank_examples_no_longer_contain_long_dashes
```

- [ ] **Step 3: Update prompt templates and hook examples**

Make these concrete changes:

In `app/features/topics/prompt_data/prompt1_8s.txt`, replace the 8s script rule block with:

```text
- `script` muss GENAU 14-18 Woerter haben.
- `script` muss genau EIN vollstaendiger Satz sein.
- `script` muss mit `.`, `!` oder `?` enden.
- `script` muss vollstaendig auf Deutsch sein.
- Keine Gedankenstriche oder langen Dash-Zeichen. Verwende nie `—`, `–`, `―` oder `−`.
- Heute ist 2026. Wenn etwas seit 2025 gilt, schreibe `Seit 2025`, nicht `Ab 2025`.
- Kein Clickbait, keine Panikmache, keine Passiv-Starts wie `Ab 2025 gibt es`.
```

In `app/features/topics/prompt_data/prompt1_16s.txt` and `app/features/topics/prompt_data/prompt1_32s.txt`, add these two bullets into the script rules:

```text
- Keine Gedankenstriche oder langen Dash-Zeichen. Verwende nie `—`, `–`, `―` oder `−`.
- Heute ist 2026. Wenn etwas seit 2025 gilt, schreibe `Seit 2025`, nicht `Ab 2025`.
```

In `app/features/topics/prompt_data/prompt1_research.txt`, add this block under the current-web-sources paragraph:

```text
ZEITKONTEXT:
- Heute ist Maerz 2026.
- Ordne Fristen und Regelungen relativ zu 2026 ein.
- Wenn eine Aenderung bereits 2025 in Kraft getreten ist, beschreibe sie als bereits geltend, nicht als zukuenftige Ankuendigung.
```

In `app/features/topics/prompt_data/hook_bank.yaml`, replace the stale examples:

```yaml
- "Deutschland 2026 und du kommst trotzdem nicht in den Bus."
- "Wie kann es sein, dass ... im Jahr 2026 immer noch fehlt?"
- "Seit 2025 gibt es..."
```

In `app/features/topics/prompt_data/audit_prompt.txt`, change the 8s range from `12-15` to `14-18`.

In `app/features/topics/prompts.py`, append this helper and inject it in both `build_prompt1(...)` and `build_prompt1_variant(...)`:

```python
def _build_current_date_guardrail() -> str:
    return _join_sections(
        "ZEIT- UND FORMAT-GUARDRAILS:",
        "- Heute ist Maerz 2026.",
        "- Wenn eine Regel seit 2025 gilt, formuliere mit `Seit 2025`, nicht mit `Ab 2025`.",
        "- Verwende niemals Gedankenstriche oder lange Dash-Zeichen wie `—`, `–`, `―` oder `−`.",
    )
```

Then append `current_date_guardrail=_build_current_date_guardrail()` into the template render path and add `{current_date_guardrail}` directly below `{hook_bank_section}` in all three `prompt1_*s.txt` files.

- [ ] **Step 4: Re-run the prompt tests**

Run:

```bash
python3 -m pytest tests/test_topic_prompt_templates.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 5: Commit the prompt hardening**

```bash
git add app/features/topics/prompts.py app/features/topics/prompt_data/prompt1_8s.txt app/features/topics/prompt_data/prompt1_16s.txt app/features/topics/prompt_data/prompt1_32s.txt app/features/topics/prompt_data/prompt1_research.txt app/features/topics/prompt_data/hook_bank.yaml app/features/topics/prompt_data/audit_prompt.txt tests/test_topic_prompt_templates.py
git commit -m "feat(topics): harden prompt templates for dash-free 2026-aware scripts"
```

---

### Task 3: Wire the shared gate into research generation and manual hub persistence

**Files:**
- Modify: `app/features/topics/research_runtime.py`
- Modify: `app/features/topics/hub.py`
- Modify: `tests/test_topics_gemini_flow.py`

- [ ] **Step 1: Add failing integration tests for the research/manual paths**

Append to `tests/test_topics_gemini_flow.py`:

```python
def test_generate_topic_script_candidate_normalizes_dash_and_stale_time(monkeypatch):
    from app.features.topics.research_runtime import generate_topic_script_candidate

    class FakeLLM:
        def generate_gemini_text(self, *args, **kwargs):
            return "Ab 2025 gibt es mehr Hilfe — dein Anspruch gilt am Bahnhof."

    item = generate_topic_script_candidate(
        post_type="value",
        target_length_tier=16,
        dossier={
            "topic": "MSZ",
            "seed_topic": "MSZ",
            "source_summary": "Seit 2025 gelten neue Regeln fuer Hilfe am Bahnhof.",
            "facts": ["Seit 2025 gelten neue Regeln fuer Hilfe am Bahnhof."],
            "risk_notes": [],
            "framework_candidates": ["PAL"],
            "sources": [],
        },
        lane_candidate={
            "title": "MSZ",
            "source_summary": "Seit 2025 gelten neue Regeln fuer Hilfe am Bahnhof.",
            "facts": ["Seit 2025 gelten neue Regeln fuer Hilfe am Bahnhof."],
            "risk_notes": [],
            "framework_candidates": ["PAL"],
        },
        llm_factory=lambda: FakeLLM(),
    )

    assert "—" not in item.script
    assert "Ab 2025" not in item.script
    assert "Seit 2025" in item.script
```

Append to `tests/test_topics_hub.py`:

```python
def test_persist_topic_bank_row_applies_pre_persistence_quality_gate(monkeypatch):
    from app.features.topics import hub as topic_hub

    captured = {}

    def fake_store_topic_bank_entry(**kwargs):
        captured["topic_script"] = kwargs["topic_script"]
        return {"id": "topic-1", "title": kwargs["title"], "topic_research_dossier_id": "dossier-1"}

    monkeypatch.setattr(topic_hub, "store_topic_bank_entry", fake_store_topic_bank_entry)
    monkeypatch.setattr(topic_hub, "upsert_topic_script_variants", lambda **kwargs: [])

    topic_hub._persist_topic_bank_row(
        title="MSZ",
        target_length_tier=16,
        research_dossier={"topic": "MSZ", "source_summary": "Seit 2025 gilt die Hilfe am Bahnhof."},
        prompt1_item=type("Item", (), {"script": "Ab 2025 gibt es Hilfe — am Bahnhof.", "caption": "Seit 2025 gilt die Hilfe am Bahnhof.", "source_summary": "Seit 2025 gilt die Hilfe am Bahnhof."})(),
        dialog_scripts=None,
        post_type="value",
        seed_payload={},
        variants=[],
    )

    assert "—" not in captured["topic_script"]
    assert "Ab 2025" not in captured["topic_script"]
```

- [ ] **Step 2: Run the failing integration tests**

Run:

```bash
python3 -m pytest tests/test_topics_gemini_flow.py::test_generate_topic_script_candidate_normalizes_dash_and_stale_time tests/test_topics_hub.py::test_persist_topic_bank_row_applies_pre_persistence_quality_gate -q
```

Expected:

```text
2 failed
```

- [ ] **Step 3: Apply the shared gate in `research_runtime.py` and `hub.py`**

In `app/features/topics/research_runtime.py`, import the new helper and normalize the final stage-3 payload immediately before the `ResearchAgentItem` return:

```python
from app.features.topics.topic_validation import validate_pre_persistence_topic_payload
```

Add this block after `script_text` and metadata are assembled:

```python
validated_payload = validate_pre_persistence_topic_payload(
    {
        "topic": lane_title or dossier_payload.get("topic") or "Thema",
        "title": lane_title or dossier_payload.get("topic") or "Thema",
        "script": script_text,
        "caption": lane_caption or dossier_source_summary or script_text,
        "source_summary": dossier_source_summary or lane_caption or script_text,
        "disclaimer": lane_payload.get("disclaimer") or dossier_payload.get("disclaimer") or "Keine Rechts- oder medizinische Beratung.",
    },
    target_length_tier=target_length_tier,
)

script_text = validated_payload["script"]
lane_title = validated_payload["title"]
lane_caption = validated_payload["caption"]
dossier_source_summary = validated_payload["source_summary"]
```

In `app/features/topics/hub.py`, import the same helper and wrap the script before persistence in `_persist_topic_bank_row(...)`:

```python
validated_payload = validate_pre_persistence_topic_payload(
    {
        "title": title,
        "topic": title,
        "script": prompt1_item.script,
        "caption": getattr(prompt1_item, "caption", "") or getattr(prompt1_item, "source_summary", ""),
        "source_summary": getattr(prompt1_item, "source_summary", "") or getattr(prompt1_item, "caption", ""),
        "disclaimer": getattr(prompt1_item, "disclaimer", "") or "Keine Rechts- oder medizinische Beratung.",
    },
    target_length_tier=target_length_tier,
)

stored_row = store_topic_bank_entry(
    title=validated_payload["title"],
    topic_script=validated_payload["script"],
    post_type=post_type,
    target_length_tier=target_length_tier,
    research_payload=research_dossier,
    origin_kind=origin_kind,
)
```

- [ ] **Step 4: Re-run the two integration tests**

Run:

```bash
python3 -m pytest tests/test_topics_gemini_flow.py::test_generate_topic_script_candidate_normalizes_dash_and_stale_time tests/test_topics_hub.py::test_persist_topic_bank_row_applies_pre_persistence_quality_gate -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit the research/manual path integration**

```bash
git add app/features/topics/research_runtime.py app/features/topics/hub.py tests/test_topics_gemini_flow.py tests/test_topics_hub.py
git commit -m "feat(topics): enforce shared quality gate in research and hub paths"
```

---

### Task 4: Wire the same gate into variant expansion and the persistence firewall

**Files:**
- Modify: `app/features/topics/variant_expansion.py`
- Modify: `app/features/topics/queries.py`
- Modify: `tests/test_expand_topic_variants.py`
- Modify: `tests/test_topic_researcher_queries.py`

- [ ] **Step 1: Add failing tests for expansion and query persistence**

Append to `tests/test_expand_topic_variants.py`:

```python
def test_expand_topic_variants_normalizes_scripts_before_upsert(monkeypatch):
    from app.features.topics import variant_expansion as expansion

    monkeypatch.setattr(expansion, "get_existing_variant_pairs", lambda **kwargs: [])
    monkeypatch.setattr(expansion, "get_topic_research_dossiers", lambda *args, **kwargs: [{
        "id": "dossier-1",
        "normalized_payload": {
            "topic": "MSZ",
            "source_summary": "Seit 2025 gelten neue Regeln fuer Hilfe am Bahnhof.",
            "framework_candidates": ["PAL"],
            "lane_candidates": [{"title": "MSZ", "framework_candidates": ["PAL"], "facts": [], "risk_notes": []}],
            "sources": [],
        },
    }])
    monkeypatch.setattr(expansion, "_get_hook_style_names", lambda: ["default"])
    monkeypatch.setattr(expansion, "pick_next_variant", lambda **kwargs: ("PAL", "default"))
    monkeypatch.setattr(expansion, "build_prompt1_variant", lambda **kwargs: "prompt")
    monkeypatch.setattr(expansion, "get_llm_client", lambda: type("FakeLLM", (), {"generate_gemini_text": lambda self, **kwargs: "Ab 2025 gibt es Hilfe — am Bahnhof."})())

    captured = {}
    monkeypatch.setattr(expansion, "upsert_topic_script_variants", lambda **kwargs: captured.update(kwargs) or [])

    expansion.expand_topic_variants(
        topic_registry_id="topic-1",
        title="MSZ",
        post_type="value",
        target_length_tier=16,
        count=1,
    )

    assert "—" not in captured["variants"][0]["script"]
    assert "Ab 2025" not in captured["variants"][0]["script"]
```

Append to `tests/test_topic_researcher_queries.py`:

```python
def test_upsert_topic_script_variants_applies_pre_persistence_quality_gate(monkeypatch):
    from app.features.topics import queries as topic_queries

    class FakeTable:
        def __init__(self):
            self.rows = []
            self._payload = None

        def table(self, *_args, **_kwargs):
            return self

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def execute(self):
            return type("Resp", (), {"data": self.rows})()

        def insert(self, payload):
            self._payload = payload
            self.rows = [dict(payload, id="script-1")]
            return self

    fake = FakeTable()
    monkeypatch.setattr(topic_queries, "_get_supabase_adapter", lambda: type("SB", (), {"client": fake})())

    stored = topic_queries.upsert_topic_script_variants(
        topic_registry_id="topic-1",
        title="MSZ",
        post_type="value",
        target_length_tier=16,
        variants=[{"script": "Ab 2025 gibt es Hilfe — am Bahnhof.", "framework": "PAL", "hook_style": "default", "bucket": "pal"}],
    )

    assert "—" not in stored[0]["script"]
    assert "Ab 2025" not in stored[0]["script"]
```

- [ ] **Step 2: Run the failing expansion/query tests**

Run:

```bash
python3 -m pytest tests/test_expand_topic_variants.py::test_expand_topic_variants_normalizes_scripts_before_upsert tests/test_topic_researcher_queries.py::test_upsert_topic_script_variants_applies_pre_persistence_quality_gate -q
```

Expected:

```text
2 failed
```

- [ ] **Step 3: Apply the shared gate before upsert and inside the query firewall**

In `app/features/topics/variant_expansion.py`, import `validate_pre_persistence_topic_payload` and normalize the variant payload before `variant_data` is built:

```python
validated_payload = validate_pre_persistence_topic_payload(
    {
        "topic": str((lane or {}).get("title") or dossier_payload.get("topic") or title or "").strip() or "Thema",
        "title": title,
        "script": script_text,
        "caption": str((lane or {}).get("source_summary") or dossier_payload.get("source_summary") or "").strip() or script_text,
        "source_summary": str((lane or {}).get("source_summary") or dossier_payload.get("source_summary") or "").strip() or script_text,
        "disclaimer": str((lane or {}).get("disclaimer") or dossier_payload.get("disclaimer") or "Keine Rechts- oder medizinische Beratung.").strip(),
    },
    target_length_tier=target_length_tier,
)
script_text = validated_payload["script"]
```

In `app/features/topics/queries.py`, run the same helper immediately after `sanitize_spoken_fragment(...)` and before duplicate checks:

```python
validated_payload = validate_pre_persistence_topic_payload(
    {
        "topic": title,
        "title": title,
        "script": script,
        "caption": variant.get("caption") or variant.get("source_summary") or script,
        "source_summary": variant.get("source_summary") or variant.get("caption") or script,
        "disclaimer": variant.get("disclaimer") or "Keine Rechts- oder medizinische Beratung.",
        "rotation": variant.get("rotation") or "",
        "cta": variant.get("cta") or "",
    },
    target_length_tier=tier,
)
script = validated_payload["script"]
```

Use the sanitized fields again in the insert payload so `caption`, `source_summary`, `rotation`, and `cta` are also dash-free.

- [ ] **Step 4: Re-run the expansion/query tests**

Run:

```bash
python3 -m pytest tests/test_expand_topic_variants.py::test_expand_topic_variants_normalizes_scripts_before_upsert tests/test_topic_researcher_queries.py::test_upsert_topic_script_variants_applies_pre_persistence_quality_gate -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit the expansion/persistence firewall changes**

```bash
git add app/features/topics/variant_expansion.py app/features/topics/queries.py tests/test_expand_topic_variants.py tests/test_topic_researcher_queries.py
git commit -m "feat(topics): enforce shared quality gate in expansion and persistence"
```

---

### Task 5: Run the full focused regression suite and update operator docs

**Files:**
- Modify: `docs.md`

- [ ] **Step 1: Add the docs update**

In `docs.md`, add a short section under the topic-system flow:

```markdown
## Pre-Persistence Topic Quality Gate

- Every topic-writing path uses the same deterministic gate before persistence:
  - deep research stage-3 writes
  - variant expansion writes
  - manual hub / search writes
- The gate strips long dash separators from all persisted topic text fields.
- The gate normalizes stale forward-looking phrasing against the 2026 context.
- The gate stretches underpowered 8-second scripts to the new 14-18 word envelope when a safe deterministic repair is available.
- If a candidate still fails the envelope after one repair attempt, it is rejected before insert.
- The audit worker still runs after persistence and remains the final arbiter for `pass`, `needs_repair`, and `reject`.
```

- [ ] **Step 2: Run the focused regression suite**

Run:

```bash
python3 -m pytest tests/test_topic_quality_gate.py tests/test_topic_prompt_templates.py tests/test_topics_gemini_flow.py tests/test_expand_topic_variants.py tests/test_topic_researcher_queries.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 3: Run one syntax-only safety pass**

Run:

```bash
python3 -m compileall app/features/topics/topic_validation.py app/features/topics/research_runtime.py app/features/topics/queries.py app/features/topics/hub.py app/features/topics/variant_expansion.py app/features/topics/prompts.py
```

Expected:

```text
Compiling 'app/features/topics/topic_validation.py'...
Compiling 'app/features/topics/research_runtime.py'...
...
```

- [ ] **Step 4: Manual operator verification**

Run one manual topic generation from the hub or REPL and verify:

```bash
python3 - <<'PY'
from app.features.topics.topic_validation import validate_pre_persistence_topic_payload

payload = validate_pre_persistence_topic_payload(
    {
        "topic": "MSZ — Hilfe",
        "title": "MSZ — Hilfe",
        "script": "Ab 2025 gibt es Hilfe — am Bahnhof fuer deine Fahrt.",
        "caption": "MSZ — Hilfe am Bahnhof.",
        "source_summary": "Seit 2025 gelten neue Regeln fuer die Hilfe am Bahnhof.",
        "disclaimer": "Keine Rechts- oder medizinische Beratung.",
    },
    target_length_tier=16,
)
print(payload["script"])
print(payload["title"])
PY
```

Expected:

```text
Seit 2025 gibt es Hilfe am Bahnhof fuer deine Fahrt.
MSZ Hilfe
```

- [ ] **Step 5: Commit the docs and final verification**

```bash
git add docs.md
git commit -m "docs(topics): document shared pre-persistence quality gate"
```

---

## Final Validation Checklist

- [ ] `tests/test_topic_quality_gate.py` passes
- [ ] `tests/test_topic_prompt_templates.py` passes
- [ ] `tests/test_topics_gemini_flow.py` targeted new test passes
- [ ] `tests/test_expand_topic_variants.py` targeted new test passes
- [ ] `tests/test_topic_researcher_queries.py` targeted new test passes
- [ ] `compileall` passes on all touched topic modules
- [ ] Prompt templates mention 2026 and the no-dash rule
- [ ] `PROMPT1_WORD_BOUNDS[8]` and `audit_prompt.txt` both reflect `14-18`
- [ ] Manual hub persistence and automated workers share the same pre-persistence helper
- [ ] Audit remains post-persistence only
