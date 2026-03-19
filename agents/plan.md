# Implementation Block Plan

Date: 2026-03-17
Scope: German TikTok engagement-bait hook rollout for topic prompts
Locality Budget: `{files: 5, LOC/file: <=180, deps: 0}`

## Rule Check

- `/AGENTS.md` constraints active
- Routing source reviewed: `bridgecode/EYE.md`
- Execution playbook reviewed: `bridgecode/plan-code-debug.md`
- Artifact review completed for `agents/canon.md`, `agents/review.md`, existing `agents/plan.md`, and current `agents/testscripts/`

## Action Mode

- Planning

## Phase Zero — Context

### Environment Matrix

- OS: Darwin 23.6.0 arm64
- Shell: `zsh`
- Python: `3.9.6`
- Commit: `a4de046`
- API stack: FastAPI + Jinja
- Database: Supabase
- LLM runtime: Gemini Deep Research for `PROMPT_1`, Gemini text for `PROMPT_2`
- Pinned deps relevant to this slice:
  - `fastapi==0.104.1`
  - `supabase==2.9.0`
  - `httpx==0.27.2`
  - `PyYAML==6.0.1`
  - `pytest==7.4.3`

### Existing Artifact Review

- `agents/canon.md`: current canon is about seeding progress, not prompt-hook strategy
- `agents/review.md`: current review is about Veo duration, not topic hooks
- Existing testscripts are focused on lifestyle dedup, script review, VEO, and audio; there is no hook-diversity or prompt-alignment testscript yet

### Current Constraints And Findings

- `PROMPT_1` value-post generation is the active path for `value` posts
- `build_prompt1(...)` currently accepts `post_type` but does not use it
- `prompt1.yaml` and `PROMPT1_SYSTEM_PROMPT` still anchor output to old hook examples
- The new hook list provided by product does not exist in the active repo files
- Validation currently enforces topic and CTA variety, not opening-hook variety
- Recent persisted `value` rows in Supabase cluster around a small set of starters such as `Weißt du`, `Check mal`, `Stell dir vor`, `Bevor du`

### Non-Functional Requirements

- No new dependencies
- Preserve current batch/topic generation architecture
- Keep prompts fully German and native to Germany TikTok, not translated US clickbait
- Increase hook diversity without increasing malformed output rate
- Keep the change localized to the topics slice plus one narrow test file
- Preserve deterministic validation and structured retries

### Product Assumptions

- Scope is primarily `value` posts, not lifestyle/community posts
- The supplied hook list is source material, not literal copy to paste unchanged
- German-native engagement bait should be assertive, high-retention, but still credible for disability-rights and wheelchair-use topics

### Risks

- Literal English-to-German hook translation will sound spammy and reduce trust
- Over-aggressive bait hooks can conflict with current anti-clickbait instructions
- If system prompt and YAML diverge again, Gemini will continue following the stronger anchor
- Without validation, the model will collapse back to familiar safe openers

## Capability Map

1. `value` posts use a German-native hook bank optimized for retention and credibility.
2. Runtime prompt sources are aligned: YAML, system prompt, and prompt builder say the same thing.
3. Hook selection is enforced by validation, not left to best effort.
4. Hook usage can be inspected in generated payloads and persisted DB rows.
5. The team has a repeatable testscript for checking hook-bank adoption before and after rollout.

## Dependency Map

- Prompt source boundary: `app/features/topics/prompt_data/prompt1.yaml`
- Prompt assembly boundary: `app/features/topics/prompts.py`
- LLM execution and validation boundary: `app/features/topics/agents.py`
- Optional secondary prompt alignment: `app/features/topics/prompt_data/prompt2.yaml`
- Regression test boundary: `tests/` plus manual testscript definitions in `agents/testscripts/`

## Boundary Map

- Boundary 1: product hook bank input -> normalized German-native template bank
- Boundary 2: prompt builder -> rendered `PROMPT_1` text for `value` posts
- Boundary 3: LLM response -> validated research batch
- Boundary 4: validated topic rows -> `posts` and `topic_registry`
- Boundary 5: post-rollout measurement -> hook-prefix distribution in persisted data

## Implementation Block

### Files To Change

```text
app/features/topics/prompt_data/prompt1.yaml
app/features/topics/prompts.py
app/features/topics/agents.py
app/features/topics/prompt_data/prompt2.yaml
tests/test_topics_prompt_hooks.py
```

### P1: Hook Bank Design And Prompt Alignment

**Objective:**  
Replace the vague family guidance with a ranked, German-native hook bank for `value` posts and align every runtime prompt source.

**Deliverable Scope (vertical slice):**
- UI: none
- API: none
- Data: none
- Validation/Errors: prompt contract only
- Observability: log which hook family validator matched when retrying

**Implementation Boundaries (contracts):**
- Boundary A: raw product hook list -> normalized hook bank
  - convert literal English-style hooks into credible German-native templates
  - group into five families:
    - Fehler/Vermeiden
    - Mythos/Luege
    - Konsequenz/Verlust
    - Aha/Umschalten
    - Provokation mit Substanz
- Boundary B: `value` post prompt contract
  - prefer stronger hooks first
  - demote `Weißt du`, `Check mal`, `Stell dir vor` to fallback-only
  - keep explicit bans for low-trust clickbait

**Concrete prompt direction to implement:**
- Preferred high-retention templates:
  - `Mach diesen Fehler bei {topic} nicht, sonst {loss}.`
  - `Wenn du {topic} ignorierst, verschenkst du {benefit}.`
  - `Die größte Lüge über {topic} ist, dass {myth}.`
  - `Die meisten scheitern bei {topic}, weil sie {detail} nie prüfen.`
  - `Was dir bei {topic} fast niemand klar sagt: {truth}.`
  - `Bevor du {action} machst, prüf erst {detail}.`
  - `Dieser kleine Fehler kostet dich bei {topic} unnötig {time_money_energy}.`
  - `Der unangenehme Grund, warum {pain_point} bei {topic} oft passiert.`
  - `Finger weg von {thing}, bevor du {detail} verstanden hast.`
  - `Alles ändert sich, wenn du bei {topic} dieses Detail kennst.`
- Fallback-only starters:
  - `Weißt du ...`
  - `Check mal ...`
  - `Stell dir vor ...`

**Testscript**
- **ID:** `TS-P1-hook-bank-copy`
- **Objective:** verify the active prompt sources contain the new hook bank and no stale conflicting anchor set
- **Prerequisites:** workspace only
- **Setup (commands):**
  - inspect `prompt1.yaml`
  - inspect `agents.py`
- **Run (commands):**
  - `rg -n "Weißt du|Check mal|Stell dir vor|Die meisten scheitern|Finger weg|Der unangenehme Grund|Die größte Lüge" app/features/topics -S`
- **Expected Observations (at boundaries):**
  - strong templates appear in active prompt files
  - stale anchors are reduced or marked fallback-only
- **Artifact Capture Points:**
  - prompt excerpts with line numbers in `agents/plan.md` execution notes
- **Cleanup:** none
- **Known Limitations:** static inspection only

**Pass/Fail Gate**
- PASS if `PROMPT_1` prompt sources clearly prioritize strong German-native engagement hooks
- FAIL if YAML and system prompt still disagree or old anchors remain primary

### P2: Post-Type-Specific Prompt Wiring

**Objective:**  
Make `value` hook strategy explicit in prompt construction instead of relying on a generic shared prompt.

**Deliverable Scope (vertical slice):**
- UI: none
- API: none
- Data: none
- Validation/Errors: prompt-assembly contract
- Observability: optional debug log showing prompt flavor by `post_type`

**Implementation Boundaries (contracts):**
- Boundary A: `post_type` -> prompt variant
  - `value`: strong engagement-bait hook bank
  - `lifestyle`: softer community-led hooks
- Boundary B: prompt assembly
  - no branching hidden in YAML prose only
  - prompt builder must inject the right section deterministically

**Testscript**
- **ID:** `TS-P2-post-type-prompt-shape`
- **Objective:** verify `build_prompt1(post_type="value")` differs materially from `build_prompt1(post_type="lifestyle")`
- **Prerequisites:** local Python
- **Setup (commands):**
  - activate workspace Python
- **Run (commands):**
  - small Python assertion calling `build_prompt1(...)` for both types
- **Expected Observations (at boundaries):**
  - `value` prompt contains the high-retention hook bank
  - `lifestyle` prompt does not inherit aggressive value-post language
- **Artifact Capture Points:**
  - printed diff snippet
- **Cleanup:** none
- **Known Limitations:** prompt-shape verification, not model output

**Pass/Fail Gate**
- PASS if `post_type` finally changes prompt behavior
- FAIL if `build_prompt1(...)` remains generic

### P3: Hook Diversity Validation

**Objective:**  
Reject `PROMPT_1` batches that overuse old safe starters or collapse to one family.

**Deliverable Scope (vertical slice):**
- UI: none
- API: existing topic generation retry path
- Data: none
- Validation/Errors:
  - opening-hook detection
  - family distribution checks
  - banned/fallback overuse checks
- Observability:
  - validation error details include matched family and repeated prefixes

**Implementation Boundaries (contracts):**
- Boundary A: script opener -> normalized hook family
- Boundary B: batch-level validator
  - fail if more than 2 scripts reuse the same family in a batch
  - fail if fallback starters dominate
  - fail if the same first 2-4 words recur too often
- Structured error envelope:
  - `{ status_code, message, context, correlation_id }` via existing validation path

**Testscript**
- **ID:** `TS-P3-validator-regression`
- **Objective:** prove strong hooks pass and repetitive `Weißt du` batches fail
- **Prerequisites:** pytest
- **Setup (commands):**
  - add focused unit fixtures
- **Run (commands):**
  - `python3 -m pytest tests/test_topics_prompt_hooks.py`
- **Expected Observations (at boundaries):**
  - batch with varied strong families passes
  - batch dominated by `Weißt du` or `Check mal` fails with explicit reason
- **Artifact Capture Points:**
  - pytest output
- **Cleanup:** none
- **Known Limitations:** validator quality depends on family matcher quality

**Pass/Fail Gate**
- PASS if validation now protects diversity and keeps retries targeted
- FAIL if repetitive starter batches still validate cleanly

### P4: Measurement And Live Output Review

**Objective:**  
Confirm the new bank shows up in persisted `value` rows and not just in prompt text.

**Deliverable Scope (vertical slice):**
- UI: none
- API: none
- Data: existing Supabase `posts` / `topic_registry`
- Validation/Errors: none
- Observability: small operator query workflow, no schema change

**Implementation Boundaries (contracts):**
- Boundary A: generation run -> persisted `posts.topic_rotation`
- Boundary B: DB review -> family distribution report

**Testscript**
- **ID:** `TS-P4-db-hook-distribution`
- **Objective:** verify new value-post rows shift away from fallback starters
- **Prerequisites:** Supabase credentials in `.env`, one fresh `value` generation run
- **Setup (commands):**
  - trigger a small batch with only `value` posts
- **Run (commands):**
  - query recent `posts` rows and count prefixes
- **Expected Observations (at boundaries):**
  - stronger families appear in fresh rows
  - `Weißt du` / `Check mal` are no longer dominant
- **Artifact Capture Points:**
  - prefix frequency table
  - 5-10 recent row samples with timestamps
- **Cleanup:** none
- **Known Limitations:** requires live model behavior, not just static repo checks

**Pass/Fail Gate**
- PASS if persisted data reflects the new bank within one fresh batch
- FAIL if DB rows still cluster around old anchors despite aligned prompts and validator

## Regression Rule

At every new phase gate:
- re-run all prior phase testscripts
- do not advance unless all prior gates pass

## Observation Checklist

- environment details confirmed
- exact files changed
- prompt-source alignment confirmed
- `value` vs `lifestyle` prompt difference confirmed
- validator failures are explicit and actionable
- fresh DB sample collected after rollout
- prefix distribution compared before vs after
- reproducibility rate recorded

## Debug Scopes

1. Prompt-source mismatch
   - YAML and system prompt say different things
2. Prompt-builder mismatch
   - `post_type` not wired to prompt output
3. Validator mismatch
   - strong hooks are rejected or repetitive hooks still pass
4. Model-behavior mismatch
   - prompt is aligned but Gemini still collapses to fallback starters
5. DB-observability mismatch
   - generated output differs from persisted rows

## Handoff

- Next execution step after approval:
  1. implement P1 and P2 together
  2. add P3 validator and test
  3. run one fresh `value` batch and complete P4 measurement
- If the live model still collapses after P1-P3, switch to Debug mode and isolate the opener-matching and retry feedback loop before widening the prompt bank further
