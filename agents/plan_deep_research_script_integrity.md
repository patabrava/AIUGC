# Implementation Block Plan

Date: 2026-03-27
Scope: Prevent contaminated German `topic_scripts.script` rows in Deep Research value/product banking
Locality Budget: `{files: 6-8, LOC/file: <=260, deps: 0}`

## Rule Check

- `/AGENTS.md` constraints active
- Routing source reviewed: `bridgecode/EYE.md`
- Execution playbook reviewed: `bridgecode/plan-code-debug.md`
- Runtime contract reviewed: `deep-research-flow.md`
- Live failure evidence reviewed from Supabase `topic_scripts.script` rows

## Action Mode

- Planning

## Phase Zero - Context

### Environment Matrix

- OS: Darwin arm64
- Shell: `zsh`
- Python: `3.9.6`
- Commit: `760130e`
- App shape: FastAPI monolith, feature-local topics slice
- DB: Supabase Postgres
- LLM path:
  - Deep Research prose for Stage 1
  - Gemini text for Stage 3 script writing
- Runtime tables involved:
  - `topic_research_runs`
  - `topic_research_dossiers`
  - `topic_registry`
  - `topic_scripts`

### Non-Functional Requirements

- No new dependencies
- No schema migration by default
- Banking must stay usable under real Gemini variance
- Contaminated scripts must be blocked before `topic_scripts` persistence
- Canonical `8/16/32` coverage must still complete for accepted lanes
- Fresh live warm-up must remain bank-first and recoverable

### Observed Failure Classes

- Label leakage:
  - `Zentrale Erkenntnisse.`
  - `Leitende Zusammenfassung.`
  - `Demografische Dringlichkeit.`
- Citation residue:
  - `[cite: 1]`
- Truncated endings:
  - final clause ends mid-thought or with unfinished noun phrase
- Metadata bleed:
  - raw dossier summaries/titles crossing into spoken script
- Sentence-shaping artifacts:
  - malformed punctuation or broken inclusive forms from cleanup

### Root Cause

The defect is not one bad prompt. The failure boundary is the Stage-3 handoff from dossier context to final spoken script. Raw or semi-raw research text is still too close to the final persisted copy. As long as summaries, labels, and citations can cross that boundary, the bank will continue to accumulate contaminated scripts.

### Success Standard

The target is not "fewer bad rows." The target is "bad rows are structurally unpersistable." A fresh live run may still produce invalid drafts, but they must either be repaired into clean spoken German or quarantined and excluded from `topic_scripts`.

## Capability Map

1. Historical bad runs become a reusable red-corpus regression set.
2. Stage 3 becomes a clean-room script compiler, not a raw text pass-through.
3. Persistence becomes a hard firewall with quarantine, not best-effort cleanup.
4. Live warm-up remains able to persist canonical `8/16/32` rows for good lanes.
5. Ongoing audits detect drift before the bank silently degrades again.

## Boundary Map

- Boundary A: raw research prose -> normalized factual units
- Boundary B: factual units -> spoken sentence pool
- Boundary C: sentence pool -> tiered final script
- Boundary D: final script -> persistence gate
- Boundary E: persistence gate -> `topic_scripts` or quarantine path

## Implementation Block

### Files To Change

```text
app/features/topics/research_runtime.py
app/features/topics/topic_validation.py
app/features/topics/hub.py
tests/test_topics_gemini_flow.py
agents/testscripts/testscript_topic_script_audit.py
deep-research-flow.md
AGENTS.md
```

### P1: Freeze The Historical Failure Corpus

**Objective:**  
Turn past failures into a permanent regression asset before touching generation again.

**Deliverable Scope (vertical slice):**
- Data:
  - export all current bad `topic_scripts.script` examples
  - capture representative Stage-1 raw research artifacts and Stage-3 prompt artifacts
- Validation:
  - classify failures into fixed buckets:
    - `label_leak`
    - `citation_residue`
    - `truncation`
    - `metadata_bleed`
    - `cleanup_artifact`
- Observability:
  - one reproducible audit command reports counts by defect class

**Implementation Boundaries (contracts):**
- Boundary A:
  - input: existing DB rows plus saved artifacts
  - output: deterministic red corpus fixtures used by tests
- Boundary B:
  - every future fix must prove it neutralizes the corpus without regressions

**Testscript**
- **ID:** `TS-P1-red-corpus-build`
- **Objective:** create and validate the historical defect corpus from live evidence
- **Prerequisites:** Supabase access and local workspace
- **Setup (commands):**
  - query `topic_scripts`
  - collect stored `results_deep_research.md`, `normalization.md`, `stage3_prompt1_8s.md` artifacts when present
- **Run (commands):**
  - `python3 agents/testscripts/testscript_topic_script_audit.py`
- **Expected Observations (at boundaries):**
  - defect classes are non-empty and reproducible
  - the offending fragments are preserved as fixtures
- **Artifact Capture Points:**
  - audit JSON summary
  - SQL export snapshot
- **Cleanup:** none
- **Known Limitations:** corpus quality depends on the captured historical runs

**Pass/Fail Gate**
- PASS if every known defect class is represented in fixtures
- FAIL if the historical failures remain only anecdotal and untestable

### P2: Convert Stage 3 Into A Clean-Room Script Compiler

**Objective:**  
Stop passing raw dossier prose into final scripts.

**Deliverable Scope (vertical slice):**
- API:
  - keep `generate_topic_script_candidate(...)` signature stable
- Logic:
  - split Stage 3 into:
    - factual extraction
    - sentence shaping
    - tier assembly
- Validation:
  - only cleaned factual units may enter the sentence pool

**Implementation Boundaries (contracts):**
- Boundary A: dossier -> fact units
  - strip labels, citations, headings, markdown, URLs, parenthetical source notes
  - discard unusable fragments instead of trying to save everything
- Boundary B: fact units -> spoken sentences
  - each sentence must be standalone spoken German
  - no headings, no citation syntax, no list markers
- Boundary C: spoken sentences -> final script
  - final script is assembled from validated sentences only
  - `source_summary` remains metadata, never raw script material

**Design Rule**
- Gemini may draft sentence text, but final script assembly must be local and sentence-based.
- No raw `cluster_summary`, `source_summary`, or lane caption may cross directly into the persisted script.

**Testscript**
- **ID:** `TS-P2-clean-room-compiler`
- **Objective:** prove label/citation contaminated dossier inputs cannot survive into final scripts
- **Prerequisites:** local pytest
- **Setup (commands):**
  - add fixture dossiers containing historical bad strings
- **Run (commands):**
  - `python3 -m pytest tests/test_topics_gemini_flow.py -k spoken`
- **Expected Observations (at boundaries):**
  - output scripts preserve meaning
  - forbidden fragments never appear in final `script`
- **Artifact Capture Points:**
  - pytest output
- **Cleanup:** none
- **Known Limitations:** sentence naturalness still depends partly on prompt quality

**Pass/Fail Gate**
- PASS if contaminated dossier text produces clean spoken output or hard failure
- FAIL if any known label or citation fragment still reaches final `script`

### P3: Add A Persistence Firewall And Quarantine Path

**Objective:**  
Make contaminated scripts impossible to write into `topic_scripts`.

**Deliverable Scope (vertical slice):**
- Data:
  - `topic_scripts` accepts only clean scripts
- Validation:
  - pre-persist gate with hard failure on contamination classes
- Observability:
  - rejected candidates are logged with defect class and lane context

**Implementation Boundaries (contracts):**
- Boundary D: final script -> persistence gate
  - fail on:
    - label fragment
    - citation residue
    - incomplete trailing clause
    - malformed artifact tail
    - broken inclusive-form cleanup artifact
- Boundary E: rejected script -> quarantine artifact
  - write diagnostic context to logs and saved artifacts
  - do not poison the bank

**Design Rule**
- Quarantine is cheaper than bad persistence. If the lane cannot produce clean copy after one deterministic repair pass, skip the lane and record why.

**Testscript**
- **ID:** `TS-P3-persistence-firewall`
- **Objective:** verify contaminated drafts are rejected and not inserted
- **Prerequisites:** local DB test path plus live Supabase verification
- **Setup (commands):**
  - run one seeded insertion path with intentionally bad drafts
- **Run (commands):**
  - focused pytest for persistence gate
  - live audit query against `topic_scripts`
- **Expected Observations (at boundaries):**
  - rejected rows never appear in `topic_scripts`
  - logs show defect class and lane key
- **Artifact Capture Points:**
  - pytest output
  - Supabase row-count before/after
- **Cleanup:** clear only test-created rows
- **Known Limitations:** quarantine uses artifacts/logs unless a dedicated table is later justified

**Pass/Fail Gate**
- PASS if persistence is impossible for dirty scripts
- FAIL if `topic_scripts` still contains any row matching the red corpus patterns

### P4: Add Deterministic Repair Before Final Rejection

**Objective:**  
Recover good lanes without letting bad text through.

**Deliverable Scope (vertical slice):**
- Logic:
  - one bounded repair pass after failed first draft
- Validation:
  - repair may only use cleaned fact units and finite sentence templates
- Observability:
  - record whether a row was direct-pass, repaired, or quarantined

**Implementation Boundaries (contracts):**
- Repair input:
  - only sanitized fact sentences
- Repair output:
  - must pass the same persistence firewall as any fresh draft
- No unbounded retries:
  - one text retry
  - one deterministic repair
  - then quarantine

**Testscript**
- **ID:** `TS-P4-repair-path`
- **Objective:** prove contaminated but recoverable inputs become clean scripts, while unrecoverable ones are skipped
- **Prerequisites:** local pytest
- **Setup (commands):**
  - fixtures for both recoverable and unrecoverable drafts
- **Run (commands):**
  - `python3 -m pytest tests/test_topics_gemini_flow.py -k repair`
- **Expected Observations (at boundaries):**
  - recoverable cases persist
  - unrecoverable cases do not persist
- **Artifact Capture Points:**
  - pytest output
- **Cleanup:** none
- **Known Limitations:** repair must stay simple or it becomes another hidden generator

**Pass/Fail Gate**
- PASS if repair improves yield without lowering persistence quality
- FAIL if repair reintroduces contaminated copy

### P5: Replay Historical Runs Before Any Fresh Harvest

**Objective:**  
Prove the fix against the failures we already paid for.

**Deliverable Scope (vertical slice):**
- Data:
  - replay stored artifacts from previous runs
- Validation:
  - every historical failure class either repairs cleanly or quarantines
- Observability:
  - produce a replay report with before/after outcomes

**Implementation Boundaries (contracts):**
- Input:
  - prior raw research artifacts and saved Stage-3 inputs
- Output:
  - deterministic replay result:
    - `clean`
    - `repaired`
    - `quarantined`

**Testscript**
- **ID:** `TS-P5-historical-replay`
- **Objective:** run the new pipeline over the historical bad corpus before live traffic
- **Prerequisites:** local workspace with stored artifacts
- **Setup (commands):**
  - load replay fixtures
- **Run (commands):**
  - dedicated replay script under `agents/testscripts/`
- **Expected Observations (at boundaries):**
  - zero historical defect patterns reach final persistence
- **Artifact Capture Points:**
  - replay report markdown/json
- **Cleanup:** none
- **Known Limitations:** only covers captured history, not unseen future patterns

**Pass/Fail Gate**
- PASS if the historical corpus is fully neutralized
- FAIL if any known bad pattern survives replay

### P6: Fresh Live Canary On A Clean Supabase Bank

**Objective:**  
Verify the hardened path under real Gemini/Supabase conditions.

**Deliverable Scope (vertical slice):**
- Data:
  - clean bank tables
  - one fresh warm-up run
- Validation:
  - inspect all persisted scripts after the run
- Observability:
  - capture run summary and Supabase audit summary

**Implementation Boundaries (contracts):**
- Warm-up contract:
  - exactly `3` Deep Research calls
  - `3` unique seed topics
  - persisted canonical `8/16/32` coverage for accepted lanes
- Quality contract:
  - no persisted script may match any contamination detector

**Testscript**
- **ID:** `TS-P6-live-clean-canary`
- **Objective:** prove the fresh live bank contains only clean spoken German
- **Prerequisites:** Gemini + Supabase env vars, permission to wipe topic-bank tables
- **Setup (commands):**
  - truncate topic-bank tables
- **Run (commands):**
  - `python3 agents/testscripts/testscript_deep_research_trace.py`
  - `python3 agents/testscripts/testscript_topic_bank_e2e.py`
  - `python3 agents/testscripts/testscript_topic_script_audit.py`
- **Expected Observations (at boundaries):**
  - warm-up succeeds
  - audit returns zero defect rows
- **Artifact Capture Points:**
  - run summaries
  - Supabase audit export
- **Cleanup:** none
- **Known Limitations:** live provider variance can reduce yield, but not quality

**Pass/Fail Gate**
- PASS if live persistence yields zero contaminated rows
- FAIL if any persisted row still contains a known defect class

### P7: Install Ongoing Drift Detection

**Objective:**  
Prevent silent regressions after rollout.

**Deliverable Scope (vertical slice):**
- Observability:
  - one recurring audit query or testscript run
- Validation:
  - release gate blocks rollout if defect count is non-zero
- Documentation:
  - runtime contract and AGENTS rules updated

**Implementation Boundaries (contracts):**
- Every future prompt or sanitizer change must re-run:
  - historical replay
  - fresh Supabase audit
- Bank expansion or cron jobs must use the same persistence firewall as warm-up

**Testscript**
- **ID:** `TS-P7-drift-audit`
- **Objective:** catch regressions after future prompt/runtime changes
- **Prerequisites:** deployed or local env with Supabase access
- **Setup (commands):**
  - none beyond environment
- **Run (commands):**
  - `python3 agents/testscripts/testscript_topic_script_audit.py`
- **Expected Observations (at boundaries):**
  - zero contamination findings
- **Artifact Capture Points:**
  - audit output
- **Cleanup:** none
- **Known Limitations:** audit only catches what detectors know how to classify

**Pass/Fail Gate**
- PASS if drift audit stays green after future changes
- FAIL if contamination reappears without blocking deploy

## Release Order

1. P1 red corpus
2. P2 clean-room compiler
3. P3 persistence firewall
4. P4 deterministic repair
5. P5 historical replay
6. P6 live clean canary
7. P7 drift audit

## Regression Rule

At every gate after P2, re-run the prior gates:

- P2 changes re-run P1 audit
- P3 changes re-run P1 and P2
- P4 changes re-run P1 through P3
- P5 changes re-run all unit and replay checks
- P6 changes re-run all prior local checks before live warm-up
- P7 changes re-run everything

## Handoff

This plan is designed to eliminate the class of defect by architecture, not by prompt optimism. The essential move is to stop treating Stage-3 output as directly bankable prose. Once Stage 3 becomes a clean-room compiler with a persistence firewall, bad scripts can still be generated, but they cannot enter `topic_scripts`.
