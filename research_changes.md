# Research & Implementation Changes — Session 2026-03-27 to 2026-03-30

## Overview

Three major features shipped across 20 commits:

1. **Hook Engagement Overhaul** — Rewrote the hook bank and prompt system to maximize TikTok engagement for German wheelchair-user audiences
2. **Script Contamination Hardening** — Added pre-persistence fact filtering and metadata bleed detection to prevent bad data from entering the database
3. **German Nativeness Audit Agent** — LLM-based quality scoring embedded inline in the generation pipeline, with a background worker as safety net

---

## Phase 1: Hook Engagement Overhaul (7 commits)

### Research Conducted

- **German TikTok market** — Advertace, Jim&Jim, Deutsche Welle case study. Germans value directness, substance, and factual backing. Hype and emotional manipulation backfire. "Hast du gewusst" works only with a shocking fact/number.
- **Short-form virality mechanics** — First 3 seconds determine everything. 74% of FYP placements driven by retention metrics. Algorithm signal hierarchy: Rewatch (5pts) > Share (3x likes) > Save (3x likes) > Comment (2pts) > Like (1pt).
- **Disability niche** — Analyzed Raul Krauthausen (185K TikTok), Tan Caglar, Sabrina Lorenz, @trainierdichbehindert (80K). Key finding: frame systemic barriers, not personal overcoming. No inspiration porn. Curiosity about lived experience is the #1 viral driver.

### Changes Made

| Commit | File(s) | What Changed |
|--------|---------|-------------|
| `5f83ec0` | `app/features/topics/prompt_data/hook_bank.yaml` | Expanded from 6 to 14 hook families. Added `priority` (high/medium/low), `emotional_core` metadata per family. 7 new families: Provokation und Faktenkonflikt, Identitaet und Zugehoerigkeit, Zahlen und Spezifitaet, Neugier und Alltagsfragen, Fehler und Warnung, Absurditaet und Realitaetscheck, Kurveneffekt. Renamed "Fragen" to "Fragen (nur mit Punch und konkreter Zahl)" (low priority). Added POV-Hooks. Expanded banned patterns from 3 to 9. Added 4 before/after `negative_examples` with bad/good/why keys. |
| `fdc30e0` | `app/features/topics/prompt_data/prompt1_8s.txt` | Replaced vague "Nutze einen klaren Hook-Start" with three new sections: **TONALITAET** (disability-appropriate tone: systemic barriers, no inspiration porn, curb-cut effect, claims must be backed), **HOOK-REGELN** (first 2-3 words must trigger emotion, emotional-core-to-hook-family matching table), **Scroll-Stopp ist Pflicht** (self-evaluation before output). |
| `11bf8d6` | `prompt1_16s.txt`, `prompt1_32s.txt` | Same rewrite applied to 16s (2 sentences) and 32s (3-4 sentences) prompts with tier-specific SCRIPT-REGELN. |
| `5eba18c` | `app/features/topics/prompts.py` | Updated `_format_hook_bank_section()` to sort families by priority (`[BEVORZUGT]` → `[SOLIDE]` → `[SPARSAM EINSETZEN]`), render `negative_examples` as SCHLECHT/GUT pairs. |
| `98ad7d8` | `app/features/topics/response_parsers.py` | Extended PROMPT_2 `hook_prefixes` tuple with starters from all 14 families so dialog script parser recognizes new hooks. |
| `e747183` | `tests/test_prompt1_variant.py` | Updated existing assertions for renamed families and new banned patterns. |
| `0f0ae06` | `deep-research-flow.md` | Documented hook bank overhaul in Stage 3 pipeline docs. |

### New Tests Added
- `test_hook_bank_has_high_engagement_families` — verifies 6 required new family names
- `test_hook_bank_bans_weak_starters` — verifies weak patterns are banned
- `test_hook_bank_families_have_priority` — verifies priority field on every family
- `test_hook_bank_has_negative_examples` — verifies 4+ before/after examples
- `test_format_hook_bank_includes_priority_ordering` — high before low in rendered output
- `test_format_hook_bank_includes_negative_examples` — SCHLECHT/GUT in rendered output
- `test_prompt1_8s_contains_hook_mechanics` — HOOK-REGELN, Scroll-Stopp, TONALITAET present
- `test_prompt1_16s_contains_hook_mechanics` — same for 16s
- `test_prompt1_32s_contains_hook_mechanics` — same for 32s
- `test_prompt2_hook_prefixes_include_new_families` — new family starters recognized

### Before/After Example
- **Before:** "Hast du gewusst, dass die Barrierefreiheit im OEPNV trotz gesetzlicher Frist 2022 oft noch fehlt?"
- **After:** "Dein Recht auf Mitfahrt? Existiert auf dem Papier — nicht an der Haltestelle."

---

## Phase 2: Script Contamination Hardening (3 commits)

### Problem
Raw dossier prose (research labels like "Zentrale Erkenntnisse:", citation residue like "[cite: 1]", metadata summaries) was crossing into persisted scripts via the fact pool and prompt context.

### Changes Made

| Commit | File(s) | What Changed |
|--------|---------|-------------|
| `e5e18cc` | `app/features/topics/topic_validation.py`, `tests/test_script_hardening.py` | Added `_clean_fact_pool()`: splits each fact into sentences, sanitizes independently via `sanitize_spoken_fragment()`, runs pre-sanitization `detect_spoken_copy_issues()` check (catches citations before they're stripped), rejects fragments < 4 words, deduplicates. |
| `eaca98d` | `app/features/topics/research_runtime.py`, `app/features/topics/prompts.py` | Wired `_clean_fact_pool()` into Stage 3 (replaced `sanitize_fact_fragments()` call). Added `sanitize_spoken_fragment()` to facts, risks, and `sanitize_metadata_text()` to source_summary in `_format_prompt1_research_context()`. |
| (in eaca98d) | `app/features/topics/topic_validation.py`, `app/features/topics/queries.py` | Added `detect_metadata_bleed()`: sliding window check for 6+ consecutive matching words between script and source_summary/cluster_summary. Wired into `upsert_topic_script_variants()` as a persistence gate between existing `detect_spoken_copy_issues` gate and canonical envelope check. |

### New Tests Added
- `test_clean_fact_pool_strips_label_fragments` — "Zentrale Erkenntnisse:" rejected
- `test_clean_fact_pool_strips_citation_residue` — "[cite: 1]" rejected
- `test_clean_fact_pool_rejects_short_fragments` — fragments < 4 words dropped
- `test_clean_fact_pool_deduplicates` — duplicates removed
- `test_clean_fact_pool_handles_none_and_empty` — None/empty silently dropped
- `test_prompt_research_context_sanitizes_facts` — labels stripped from prompt context
- `test_detect_metadata_bleed_catches_verbatim_summary` — 6+ word overlap detected
- `test_detect_metadata_bleed_allows_partial_overlap` — short overlap passes
- `test_detect_metadata_bleed_checks_cluster_summary` — cluster_summary also checked
- `test_detect_metadata_bleed_empty_inputs` — empty inputs return None

### Live Verification
- 5 contaminated facts → 2 clean facts via `_clean_fact_pool()`
- Metadata bleed correctly caught a 32s script that was essentially a copy of the source_summary

---

## Phase 3: German Nativeness Audit Agent (9 commits)

### Architecture

The audit agent scores scripts on 4 dimensions (each 0-25, total 0-100):
1. **german_nativeness** — Does it sound like native spoken German?
2. **hook_quality** — Do the first 2-3 words stop the scroll?
3. **prompt_compliance** — Does it follow tier rules and TONALITAET?
4. **virality_potential** — Would it drive comments, shares, saves?

Status thresholds: pass (≥ 70), needs_repair (40-69), reject (< 40).

### Changes Made

| Commit | File(s) | What Changed |
|--------|---------|-------------|
| `7b1c4e0` | `app/features/topics/prompt_data/audit_prompt.txt` | Created German-language audit prompt with 4-dimension scoring rubric, JSON response contract, status thresholds. |
| `3103803` | `app/features/topics/audit.py` | Created core module: `AuditResult` dataclass, `audit_single_script()` (deterministic pre-check + Gemini evaluation), `_parse_audit_response()` (handles malformed JSON gracefully), `audit_batch()`. |
| `33751c7` | `app/features/topics/queries.py` | Added `get_unaudited_scripts()` (fetches rows where `quality_score IS NULL`) and `update_script_quality()` (writes score + notes). |
| `9fa8f38` | `workers/audit_worker.py` | Created scheduled background worker (12-hour interval) following `expansion_worker.py` pattern. |
| `e13310e` | `deep-research-flow.md` | Documented Stage 4b (async script audit) in pipeline docs. |
| `e845bdd` | `app/features/topics/audit.py`, `audit_prompt.txt` | Fixed JSON truncation: increased `max_tokens` to 4096, constrained notes to max 10 words per field. |
| `b97496d` | `workers/topic_researcher.py` | Added `run_audit_cycle()` trigger at end of discovery cycle — new scripts get audited immediately after research completes. |
| `3d4e974` | `app/features/topics/research_runtime.py`, `schemas.py`, `hub.py`, `queries.py` | **Embedded inline audit gate** in `generate_topic_script_candidate()`: scripts are now audited before being returned for persistence. Rejected scripts (< 40) trigger a retry. Added `quality_score`/`quality_notes` fields to `ResearchAgentItem` schema. Hub passes audit results through to `upsert_topic_script_variants()`. |

### New Tests Added
- `test_audit_prompt_file_exists` — prompt template exists
- `test_audit_prompt_contains_scoring_dimensions` — all 4 dimensions present
- `test_audit_prompt_contains_json_contract` — JSON response structure specified
- `test_audit_single_script_pass` — score ≥ 70 → status "pass"
- `test_audit_single_script_reject` — score < 40 → status "reject"
- `test_audit_single_script_deterministic_reject` — label fragment → score 0, no LLM call
- `test_audit_single_script_malformed_llm_response` — bad JSON → fallback to reject
- `test_get_unaudited_scripts_returns_null_quality_rows` — query filters correctly
- `test_update_script_quality_writes_score_and_notes` — DB update works
- `test_audit_worker_run_audit_cycle` — full cycle: fetch → audit → write

### Final Architecture

```
Stage 3: generate_topic_script_candidate()
  → Gemini generates script from clean fact pool
  → sanitize_spoken_fragment() + detect_spoken_copy_issues()
  → _enforce_prompt1_word_envelope()
  → _audit_gate() ← INLINE quality scoring via Gemini
    → pass (≥70): return with score
    → needs_repair (40-69): return with score (downstream decides)
    → reject (<40): retry once → fallback synthesis
  → quality_score + quality_notes travel with ResearchAgentItem
  → hub passes to upsert_topic_script_variants()
  → detect_metadata_bleed() gate
  → persisted to topic_scripts WITH audit scores

Safety net: audit_worker.py runs every 12 hours on remaining unaudited rows
```

### Live E2E Results (10 scripts audited)

| Score | Status | Script | Why |
|-------|--------|--------|-----|
| 89 | PASS | "Was dir bei barrierefreien Raststätten niemand klar sagt: Der Euroschlüssel..." | Strong Aha hook, practical tip |
| 69 | NEEDS_REPAIR | "Weißt du, welche Merkzeichen du für die kostenlose Wertmarke benötigst?" | Good German but "Weißt du" hook too weak |
| 61 | NEEDS_REPAIR | "Kennst du die technischen Standards..." | Word count under 32s tier requirement |
| 60 | NEEDS_REPAIR | "Hast du gewusst, dass barrierefreie Raststätten..." | "Hast du gewusst" = teacher energy |
| 59 | NEEDS_REPAIR | "Stell dir vor, viele Raststättenhotels..." | Wrong audience (Stomaträger not Rollstuhlfahrer) |
| 47 | NEEDS_REPAIR | "Hast du gewusst, dass die Kraftfahrzeughilfe-Verordnung..." | Bureaucratic tone + weak hook |
| 23 | REJECT | "Die Kraftfahrzeughilfe-Verordnung ist zentral..." | Sounds like a law textbook |
| 15 | REJECT | "Weißt du, was das Merkzeichen B..." | Truncated, too short for 32s |
| 0 | REJECT | "Die KfzHV ist entscheidend..." | Parse error |
| 0 | REJECT | "Versorgung durch private Angehörige..." | Parse error |

---

## Files Created

| File | Purpose |
|------|---------|
| `app/features/topics/audit.py` | Core audit module (146 LOC) |
| `app/features/topics/prompt_data/audit_prompt.txt` | Gemini evaluation prompt with scoring rubric |
| `workers/audit_worker.py` | Background audit worker (86 LOC) |
| `tests/test_script_hardening.py` | Hardening tests (78 LOC) |
| `tests/test_audit_agent.py` | Audit agent tests (170 LOC) |
| `docs/superpowers/specs/2026-03-30-script-hardening-and-audit-agent-design.md` | Design spec |
| `docs/superpowers/plans/2026-03-27-hook-engagement-overhaul.md` | Hook overhaul implementation plan |
| `docs/superpowers/plans/2026-03-30-script-hardening-and-audit-agent.md` | Hardening + audit implementation plan |

## Files Modified

| File | Changes |
|------|---------|
| `app/features/topics/prompt_data/hook_bank.yaml` | 6 → 14 families, priorities, negative examples |
| `app/features/topics/prompt_data/prompt1_8s.txt` | TONALITAET + HOOK-REGELN + Scroll-Stopp sections |
| `app/features/topics/prompt_data/prompt1_16s.txt` | Same rewrite for 16s tier |
| `app/features/topics/prompt_data/prompt1_32s.txt` | Same rewrite for 32s tier |
| `app/features/topics/prompts.py` | Priority-sorted formatter, sanitized prompt context |
| `app/features/topics/response_parsers.py` | Extended hook_prefixes for 14 families |
| `app/features/topics/topic_validation.py` | `_clean_fact_pool()`, `detect_metadata_bleed()` |
| `app/features/topics/research_runtime.py` | Clean fact pool wiring, inline audit gate |
| `app/features/topics/schemas.py` | `quality_score`/`quality_notes` on ResearchAgentItem |
| `app/features/topics/queries.py` | Metadata bleed gate, audit queries, quality_score passthrough |
| `app/features/topics/hub.py` | Pass quality_score/quality_notes to persistence |
| `workers/topic_researcher.py` | Trigger audit after research completes |
| `deep-research-flow.md` | Documented all pipeline changes |
| `tests/test_prompt1_variant.py` | Updated assertions for new hook bank |
| `tests/test_topic_prompt_templates.py` | Hook mechanics + hook_prefixes tests |

## Total Impact
- **20 commits** pushed to main
- **~1,500 lines added** across 15 files
- **30 new tests** (all passing)
- **0 regressions** in existing 51-test suite
