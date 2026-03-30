# Script Contamination Hardening + German Nativeness Audit Agent — Design Spec

## Goal

Two complementary quality layers for the script pipeline:

1. **Pre-persistence hardening** — Stop contaminated text (research labels, citations, raw dossier prose) from entering `topic_scripts` by cleaning facts before they reach Gemini and tightening the persistence firewall.
2. **Post-persistence audit agent** — LLM-based quality gate that scores persisted scripts on German nativeness, hook quality, prompt compliance, and virality potential, writing results to the existing `quality_score`/`quality_notes` fields.

## Scope

**In scope:**
- P2: Clean-room fact extraction layer in `research_runtime.py`
- P3: Persistence firewall tightening in `queries.py` and `topic_validation.py`
- Audit worker: new `workers/audit_worker.py` with Gemini-based script evaluation
- Audit prompt: fixed checklist evaluating nativeness, hooks, compliance, virality
- Live verification: run a generation cycle and audit cycle end-to-end

**Out of scope:**
- P1 (red corpus freeze) — we write targeted test fixtures inline instead
- P4 (deterministic repair) — deferred until we see rejection rates post-hardening
- P5-P7 (replay, canary, drift) — the audit agent subsumes these functions
- New database tables or migrations
- UI changes

---

## Sub-project 1: Script Contamination Hardening (P2 + P3)

### P2: Clean-Room Fact Extraction

**Problem:** In `research_runtime.py`, the fact pool at lines 332-340 combines raw dossier facts, lane angles, and risk notes before sanitization. Contaminated fragments (labels, citations, partial sentences) enter the pool and can reach the final script via the fallback path or influence Gemini's output.

**Solution:** Add a `_clean_fact_pool()` function that:

1. Takes raw facts from lane + dossier
2. Splits each fact into individual sentences
3. Runs each sentence through `sanitize_spoken_fragment()` independently
4. Rejects any sentence that triggers `detect_spoken_copy_issues()`
5. Rejects any sentence shorter than 4 words (fragment, not a fact)
6. Returns only clean, validated sentences

**Insertion point:** Between lines 332 and 340 of `research_runtime.py`. Replace the current `sanitize_fact_fragments()` call with `_clean_fact_pool()`.

**Additionally:** In `_format_prompt1_research_context()` (prompts.py:340-368), the `source_summary` field is injected into the prompt with only length clipping. Add `sanitize_metadata_text()` to the `source_summary` before injection, and add `sanitize_spoken_fragment()` to each fact and risk_note before they enter the prompt context.

### P3: Persistence Firewall Tightening

**Problem:** `upsert_topic_script_variants()` already calls `detect_spoken_copy_issues()` and skips bad rows (lines 629-642). But it doesn't detect:
- Metadata bleed: dossier `source_summary` or `cluster_summary` text appearing verbatim in `script`
- Overly similar script-to-summary overlap (bigram Jaccard > 0.5 suggests the script IS the summary)

**Solution:**

1. **Add metadata bleed detection** to `topic_validation.py`: a function `detect_metadata_bleed(script, source_summary, cluster_summary)` that checks if the script contains long substrings (6+ consecutive words) from the summary fields.

2. **Add the bleed check to `upsert_topic_script_variants()`**: after the existing `detect_spoken_copy_issues()` gate, add the metadata bleed check. If triggered, log `"metadata_bleed_detected"` and skip the variant.

3. **Write quality_notes on cleaned scripts**: when `sanitize_spoken_fragment()` modifies the script (i.e., cleaned != raw), record what was cleaned in `quality_notes` for observability.

### Files Modified (P2+P3)

| File | Change |
|------|--------|
| `app/features/topics/research_runtime.py` | Add `_clean_fact_pool()`, replace `sanitize_fact_fragments()` call |
| `app/features/topics/prompts.py` | Sanitize facts/risks/summary in `_format_prompt1_research_context()` |
| `app/features/topics/topic_validation.py` | Add `detect_metadata_bleed()` function |
| `app/features/topics/queries.py` | Add metadata bleed gate in `upsert_topic_script_variants()`, write quality_notes on cleaned scripts |
| `tests/test_topics_gemini_flow.py` | Add tests for clean-room compiler and firewall |
| `tests/test_topic_pipeline.py` | Add tests for metadata bleed detection |

---

## Sub-project 2: German Nativeness Audit Agent

### Architecture

A background worker that evaluates persisted `topic_scripts` rows using Gemini, scoring them on a fixed checklist.

### Audit Worker (`workers/audit_worker.py`)

**Trigger:** Scheduled worker (like `expansion_worker.py`), runs every 12 hours or on-demand.

**Flow:**
1. Query `topic_scripts` where `quality_score IS NULL` (unaudited rows), limit 50 per run
2. For each row:
   a. Run deterministic checks first (`detect_spoken_copy_issues()`)
   b. If deterministic checks fail → `quality_score = 0`, `quality_notes = {status: "reject", reasons: [issues]}`
   c. If deterministic checks pass → call Gemini with the audit prompt
   d. Parse Gemini's structured response
   e. Write `quality_score` (0-100) and `quality_notes` (JSON) to the row

**Gemini Audit Prompt** (new file: `app/features/topics/prompt_data/audit_prompt.txt`):

The prompt asks Gemini to evaluate a single script against 4 dimensions, returning a JSON response:

1. **German Nativeness (0-25):** Does it sound like native spoken German? Natural, direct, fluid? No translation artifacts? Would a native speaker actually say this?

2. **Hook Quality (0-25):** Does the opening pull attention within 2-3 words? Does it trigger emotion (Wut, Ueberraschung, Wiedererkennung, Neugier)? Does it match a high-priority hook family from the bank? Would someone scrolling TikTok stop?

3. **Prompt Compliance (0-25):** Does it respect the tier word/sentence bounds? Does it stay within the topic scope? Does it follow the TONALITAET rules (systemic barriers, no inspiration porn)?

4. **Virality Potential (0-25):** Would this drive comments, shares, or saves? Does it create a curiosity gap? Does it use the curb-cut framing when relevant? Is the tone conversational, not academic?

**Response contract (JSON):**
```json
{
  "german_nativeness": {"score": 0-25, "notes": "..."},
  "hook_quality": {"score": 0-25, "notes": "..."},
  "prompt_compliance": {"score": 0-25, "notes": "..."},
  "virality_potential": {"score": 0-25, "notes": "..."},
  "total_score": 0-100,
  "status": "pass|needs_repair|reject",
  "summary": "One-line human-readable verdict"
}
```

**Status thresholds:**
- `pass`: total_score >= 70
- `needs_repair`: total_score 40-69
- `reject`: total_score < 40

**Persistence:** Write to existing `topic_scripts` fields:
- `quality_score` = `total_score` (numeric)
- `quality_notes` = full JSON response (text, stored as JSON string)

### Audit Core Module (`app/features/topics/audit.py`)

Extracted from the worker for testability:

```
audit_single_script(script_row: Dict) -> AuditResult
audit_batch(rows: List[Dict], llm: LLMClient) -> List[AuditResult]
```

The worker calls these functions. Tests mock the LLM and test the logic independently.

### Files Created/Modified (Audit Agent)

| File | Action |
|------|--------|
| `app/features/topics/audit.py` | **Create** — core audit logic, Gemini prompt builder, response parser |
| `app/features/topics/prompt_data/audit_prompt.txt` | **Create** — fixed evaluation prompt with scoring rubric |
| `workers/audit_worker.py` | **Create** — scheduled worker that runs audit on unscored rows |
| `app/features/topics/queries.py` | **Modify** — add `get_unaudited_scripts()` and `update_script_quality()` queries |
| `tests/test_audit_agent.py` | **Create** — tests for audit logic with mocked LLM |
| `deep-research-flow.md` | **Modify** — document audit stage in pipeline |

---

## Data Flow (End to End)

```
Stage 1: Raw Research (Gemini Deep Research)
    |
Stage 2: Local Dossier Normalization
    |
Stage 3: Script Generation
    |-- [NEW P2] _clean_fact_pool() filters contaminated facts
    |-- Gemini generates script from clean facts
    |-- sanitize_spoken_fragment() cleans output
    |-- detect_spoken_copy_issues() validates output
    |
Stage 4: Persistence
    |-- [EXISTING] sanitize + detect_spoken_copy_issues gate
    |-- [NEW P3] detect_metadata_bleed() gate
    |-- [NEW P3] quality_notes records what was cleaned
    |-- Write to topic_scripts
    |
[NEW] Audit Agent (async, post-persistence)
    |-- Deterministic checks (detect_spoken_copy_issues)
    |-- LLM evaluation (nativeness, hooks, compliance, virality)
    |-- Write quality_score + quality_notes to topic_scripts
```

---

## Testing Strategy

### P2+P3 Tests
- Unit: `_clean_fact_pool()` with contaminated inputs → only clean sentences returned
- Unit: `detect_metadata_bleed()` with script containing summary verbatim → detected
- Integration: `generate_topic_script_candidate()` with contaminated dossier → clean script or hard failure
- Integration: `upsert_topic_script_variants()` with metadata bleed → row skipped

### Audit Agent Tests
- Unit: `audit_single_script()` with mocked LLM returning scores → correct status assignment
- Unit: audit prompt builder includes hook bank families and tier constraints
- Unit: response parser handles malformed Gemini JSON gracefully (fallback to reject)
- Integration: `audit_batch()` with mix of good/bad scripts → correct score distribution

### Live Verification
- Run a generation cycle (3 topics, 8s tier)
- Verify no contaminated rows in `topic_scripts`
- Run audit worker on fresh rows
- Verify `quality_score` and `quality_notes` populated
- Check that scores align with manual assessment

---

## Success Criteria

1. **Zero contaminated scripts persist** — no label leakage, citation residue, or metadata bleed in newly generated `topic_scripts` rows
2. **All persisted scripts have audit scores** — `quality_score` populated within 12 hours of persistence
3. **Audit scores correlate with quality** — `pass` scripts sound like native German TikTok hooks; `reject` scripts don't
4. **No pipeline slowdown** — hardening adds < 100ms to generation; audit runs asynchronously
5. **Existing tests still pass** — no regressions in the 51-test suite
