# FLOW-FORGE Review

Date: 2026-03-21
Review mode: LIRA senior audit for `app/features/topics/agents.py` and `architecture.md`
Scope locality budget: `{files: 6, LOC/file: <=260 target and <=500 hard, deps: 0}`

## Executive Summary

Overall health: **CRITICAL**

Finding count:
- 🔴 2 critical
- 🟡 4 important
- 🟢 1 minor
- ✅ 2 passing

Top 3 urgent issues:
1. `app/features/topics/prompts.py` currently imports a missing `ResearchDossier` contract, so the topic-generation slice does not import cleanly.
2. `app/features/topics/agents.py` is 1429 LOC and mixes parser, validator, runtime orchestration, mapper, and lifestyle-generation concerns in one file.
3. `architecture.md` documents `topic_scripts` and `topic_research_runs` as current runtime truth, but the active code path still uses `topic_registry` plus `posts.seed_data` and does not touch those tables.

## Context Zero

### Environment Matrix

- OS: macOS / Darwin
- Shell: `zsh`
- Commit: `b3ed102`
- Verified local interpreter during audit: `python3 3.9.6` at `/usr/bin/python3`
- Documented target interpreter: Python 3.11+ in [README.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/README.md)
- API framework: `fastapi==0.104.1`
- Validation: `pydantic==2.5.0`, `pydantic-settings==2.1.0`
- HTTP: `httpx==0.27.2`
- LLM adapter: [app/adapters/llm_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/llm_client.py)

### Working Tree Note

The repository is dirty. Untracked migrations and tests exist for the topic-script bank. Those files were treated as evidence of intended direction, not proof of live runtime adoption.

## Detailed Findings

### 🔴 CRITICAL: The topic-generation slice does not import cleanly because `prompts.py` references a missing schema contract

**Current State:**
- [app/features/topics/prompts.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/prompts.py#L14) imports `ResearchDossier` from `app.features.topics.schemas`.
- [app/features/topics/prompts.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/prompts.py#L172) and [app/features/topics/prompts.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/prompts.py#L215) keep using that missing symbol in type checks.
- [app/features/topics/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/schemas.py) contains no `ResearchDossier` model.
- Direct verification with `python3` produced: `ImportError cannot import name 'ResearchDossier' from 'app.features.topics.schemas'`.

**Assessment:**
The slice is already in contract drift before any refactor starts. A structural split on top of a broken import boundary will compound failures and make regression signals noisy.

**Severity:** 🔴 CRITICAL

**Remediation:**
- First repair the schema boundary before moving code.
- Either add the real `ResearchDossier` model to [app/features/topics/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/schemas.py) or remove the stale import and type references from [app/features/topics/prompts.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/prompts.py).
- Add an import-smoke test for `app.features.topics.prompts` and `app.features.topics.agents` so this cannot regress silently.

### 🔴 CRITICAL: `generate_dialog_scripts(...)` has a type-unsafe terminal error path that can mask the real failure

**Current State:**
- [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L1145) stores the PROMPT_2 JSON response in `response`.
- [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L1205) logs `last_response=response[:1000]` after all retries.
- In the success/retry path, `response` is a dict returned by `generate_gemini_json(...)`, so `response[:1000]` is invalid for that type.

**Assessment:**
This is not a style issue. It is a broken failure-path implementation that can raise a new `TypeError` while trying to report the original validation error. That destroys observability exactly when the slice is already failing.

**Severity:** 🔴 CRITICAL

**Remediation:**
- Fix the error path before or during the refactor by logging a safe serialized preview, not a dict slice.
- Move PROMPT_2 runtime logic into a dedicated runtime module so terminal failure handling is tested in isolation.
- Add a regression test that forces three failed PROMPT_2 attempts and asserts the final raised error remains the intended validation error.

### 🟡 IMPORTANT: `agents.py` is a God file with at least five distinct reasons to change

**Current State:**
- [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py) is 1429 LOC.
- PROMPT_1 parsing and semantic validation live in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L95) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L611).
- PROMPT_2 parsing lives in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L614) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L824).
- Seed mapping lives in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L827) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L936).
- Research runtime orchestration lives in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L939) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L1207).
- Lifestyle generation and strict extraction live in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L1210) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L1429).

**Assessment:**
The file violates the repo’s locality budget and is the main reason the slice has become hard to change safely. It is too large for confident edits, and changes to one concern force rereading unrelated concerns.

**Severity:** 🟡 IMPORTANT

**Remediation:**
- Keep `agents.py` only as a compatibility facade.
- Extract four internal modules: runtime, response parsing/validation, seed builders, lifestyle generation.
- Keep existing function names stable during the first pass so handlers and tests do not churn.

### 🟡 IMPORTANT: Validation logic is duplicated across parse and runtime layers, which makes rule changes error-prone

**Current State:**
- `parse_prompt1_response(...)` validates duration, summary, German-only content, source accessibility, round-robin balance, and CTA uniqueness in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L604) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L610).
- `_generate_prompt1_batch(...)` repeats the same validations on the same items in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L1050) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L1059).

**Assessment:**
The slice has no single semantic-validation boundary. Any future rule change requires editing multiple paths and rechecking retry semantics by hand. This is a direct contributor to refactor risk.

**Severity:** 🟡 IMPORTANT

**Remediation:**
- Centralize semantic validation in one parser/validator module.
- Make runtime code responsible only for calling the parser and reacting to one validation result.
- Add targeted parser tests for validation failures without invoking provider code.

### 🟡 IMPORTANT: Pure mapping code is performing hidden network I/O and rechecking sources that were already validated

**Current State:**
- `validate_sources_accessible(...)` checks every source URL in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L356) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L375).
- `build_seed_payload(...)` calls `_validate_url_accessible(...)` again for the primary source in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L915).

**Assessment:**
A seed-builder function should be a deterministic mapper, not a second network boundary. The current design doubles external I/O and makes payload assembly slower and harder to test.

**Severity:** 🟡 IMPORTANT

**Remediation:**
- Remove URL reachability checks from `build_seed_payload(...)`.
- Carry any accessibility status into the builder as validated metadata if the payload still needs it.
- Treat mapper functions as pure and unit-test them without HTTP.

### 🟡 IMPORTANT: `architecture.md` describes a runtime that the active code path does not yet implement

**Current State:**
- [architecture.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/architecture.md#L64) through [architecture.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/architecture.md#L73) says the current design uses `public.topic_scripts` as the canonical script store and prefers exact-tier reuse.
- [architecture.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/architecture.md#L119) through [architecture.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/architecture.md#L134) lists `topic_scripts` and `topic_research_runs` as key runtime tables.
- The active write path still inserts only `title`, `rotation`, `cta`, and post seed data in [app/features/topics/queries.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/queries.py#L23) through [app/features/topics/queries.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/queries.py#L145).
- The batch workflow in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L585) through [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L827) does not read or write `topic_scripts` or `topic_research_runs`.
- Relevant migrations are currently untracked in git status.

**Assessment:**
The documentation is ahead of the live runtime. That is acceptable as a proposal, but not acceptable as “Current runtime architecture.” It increases refactor risk because engineers will split code against a storage model the active slice is not actually using.

**Severity:** 🟡 IMPORTANT

**Remediation:**
- Update `architecture.md` only after the runtime path actually uses those tables.
- During the `agents.py` refactor, document the active truth as `topic_registry` + `posts.seed_data`.
- Treat migration adoption as a separate implementation block after the file split is stable.

### 🟡 IMPORTANT: The refactor must preserve the current monkeypatch seams or tests will fail for the wrong reason

**Current State:**
- [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L20) through [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L27) import module-level functions directly from `agents.py`.
- Existing tests monkeypatch those functions heavily, including [tests/test_lifestyle_generation_regression.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_lifestyle_generation_regression.py#L241) through [tests/test_lifestyle_generation_regression.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_lifestyle_generation_regression.py#L247).

**Assessment:**
A direct import rewrite across handlers and tests would create avoidable churn. The low-risk path is facade first, internals second.

**Severity:** 🟡 IMPORTANT

**Remediation:**
- Keep the existing public symbols in `agents.py` for the first refactor pass.
- Move internal implementations behind those exports.
- Only after the facade is stable should handlers or tests switch to narrower imports.

### 🟢 MINOR: `agents.py` still carries legacy surface area and unused imports that add noise

**Current State:**
- [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L22) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L25) import `DiscoverTopicsRequest`, `TopicListResponse`, and `TopicResponse`, but those contracts are not used in this file.
- `parse_prompt2_response(...)` is referenced by tests but not by the live handlers path according to repo search results.

**Assessment:**
This is not breaking runtime behavior, but it does make the file harder to read and obscures the true public API.

**Severity:** 🟢 MINOR

**Remediation:**
- Remove unused imports during the facade pass.
- Decide whether `parse_prompt2_response(...)` remains a supported compatibility function or is explicitly deprecated after tests are updated.

### ✅ PASS: The slice already has meaningful semantic validation worth preserving

**Current State:**
- PROMPT_1 rules enforce duration, German-only output, summary overlap, topic rotation, and CTA uniqueness in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L261) through [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L611).

**Assessment:**
The code is too large, but the semantic rule set is valuable. The refactor should preserve these rules and move them intact into a single validation module rather than rewriting them opportunistically.

**Severity:** ✅ PASS

**Remediation:**
None. Preserve and isolate.

### ✅ PASS: The topics slice already has real regression coverage that can anchor the refactor

**Current State:**
- Gemini flow coverage exists in [tests/test_topics_gemini_flow.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_topics_gemini_flow.py).
- Lifestyle regression coverage exists in [tests/test_lifestyle_generation_regression.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_lifestyle_generation_regression.py).
- Prompt-template checks exist in [tests/test_topic_prompt_templates.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_topic_prompt_templates.py).

**Assessment:**
The test foundation is good enough to support an incremental facade-first refactor. The work should extend this harness, not replace it.

**Severity:** ✅ PASS

**Remediation:**
None. Extend with import-smoke and failure-path coverage.

## Remediation Plan (Implementation Block & Testscripts)

### Implementation-Block Critical-Set

Scope: restore a stable contract boundary before structural splitting.
Locality budget: `{files: 3-4, LOC/file: <=220, deps: 0}`

Files:
- [app/features/topics/prompts.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/prompts.py)
- [app/features/topics/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/schemas.py)
- [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py)
- one focused test file or additions to [tests/test_topic_prompt_templates.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_topic_prompt_templates.py)

Deliverables:
1. Repair `ResearchDossier` import drift.
2. Fix the terminal PROMPT_2 error path so failure logging cannot raise a new exception.
3. Add import-smoke coverage for `prompts` and `agents`.

### Implementation-Block Important-Set

Scope: make `agents.py` lean without changing the public interface.
Locality budget: `{files: 6, LOC/file: <=260 target and <=500 hard, deps: 0}`

Files:
- [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py)
- `app/features/topics/research_runtime.py`
- `app/features/topics/response_parsers.py`
- `app/features/topics/seed_builders.py`
- `app/features/topics/lifestyle.py`
- [tests/test_topics_gemini_flow.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_topics_gemini_flow.py)
- [tests/test_lifestyle_generation_regression.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_lifestyle_generation_regression.py)

Deliverables:
1. Move PROMPT_1/PROMPT_2 provider calls and retry loops into `research_runtime.py`.
2. Move raw parsing and semantic validation into `response_parsers.py`.
3. Move topic/seed payload mapping into `seed_builders.py` and make those functions pure.
4. Move lifestyle generation into `lifestyle.py`.
5. Reduce `agents.py` to a stable facade that re-exports the existing function names.

### Implementation-Block Polish-Set

Scope: align docs and remove noise after the slice is stable.
Locality budget: `{files: 2-3, LOC/file: <=220, deps: 0}`

Files:
- [architecture.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/architecture.md)
- [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py)
- optional test additions

Deliverables:
1. Update `architecture.md` so “current runtime” describes only the live code path.
2. Remove unused imports and explicitly decide whether `parse_prompt2_response(...)` remains public.
3. If script-bank tables are adopted later, document them as a new block after runtime adoption, not before.

## Refactor Sequence

1. Repair contracts first.
2. Freeze current public API by turning `agents.py` into a facade boundary.
3. Extract pure parsing and validation next.
4. Extract provider runtime logic after parsers are stable.
5. Extract seed builders and lifestyle helpers last.
6. Update docs only after code matches the documented architecture.

## Testscripts

### TS0: Import Smoke

- Objective: prove the topic-generation slice imports cleanly before deeper tests.
- Prerequisites: activated project environment.
- RUN:
  - `python3 - <<'PY'`
  - `import app.features.topics.prompts`
  - `import app.features.topics.agents`
  - `print('ok')`
  - `PY`
- OBSERVE:
  - both imports succeed without `ImportError`
- COLLECT:
  - interpreter version
  - exact traceback if import fails
- REPORT:
  - pass/fail plus failing module and symbol

### TS1: Parser And Failure-Path Regression

- Objective: prove parser extraction and terminal error handling remain deterministic.
- Prerequisites: pytest available.
- RUN:
  - `python3 -m pytest tests/test_topic_prompt_templates.py tests/test_topics_gemini_flow.py -q`
- OBSERVE:
  - prompt parser tests still pass
  - forced PROMPT_2 failure path raises the intended validation error, not a type error in logging
- COLLECT:
  - pytest output
- REPORT:
  - list of failing tests with first relevant traceback only

### TS2: Lifestyle And Seed Regression

- Objective: prove lifestyle generation and seed creation survive the split.
- Prerequisites: pytest available.
- RUN:
  - `python3 -m pytest tests/test_lifestyle_generation_regression.py -q`
- OBSERVE:
  - derived lifestyle titles remain content-led
  - requested post-type counts still gate batch finalization
- COLLECT:
  - pytest output
- REPORT:
  - pass/fail and first failing assertion

### TS3: Whole-Slice Regression

- Objective: verify the facade-first refactor did not break batch discovery orchestration.
- Prerequisites: all prior scripts passing.
- RUN:
  - `python3 -m pytest tests/test_topics_gemini_flow.py tests/test_lifestyle_generation_regression.py tests/test_topic_prompt_templates.py -q`
- OBSERVE:
  - handlers continue calling the same public functions
  - monkeypatch-based regressions still operate through `agents.py`
- COLLECT:
  - combined pytest output
- REPORT:
  - status of every suite and first regression boundary hit

### Regression Rule

After each meaningful fix in the critical-set or important-set, re-run all previously passing testscripts before continuing.

### Failure Clause

If after trying to debug for two turns or more the tests still fail, generate `agents/testscripts/failure_report.md` with the failing script id, environment matrix, reproduction steps, observed behavior, expected behavior, suspected boundary, and collected artifacts before attempting another broad refactor step.

## Handoff

`agents/canon.md` and `agents/review.md` are updated for this refactor scope.

Next step: switch to `EYE` and use `bridgecode/plan-code-debug.md` to execute the remediation implementation-blocks in this order:
1. critical-set
2. important-set
3. polish-set
