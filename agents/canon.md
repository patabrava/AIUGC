# Lippe Lift Studio Topic Generation Canon

Date: 2026-03-21
Scope: `app/features/topics/agents.py` refactor canon for the existing topic-generation slice
Prime Directive: `agents/canon.md` is the source of truth for the target shape of this slice while the refactor is executed.
Locality Budget: `{files: 6, LOC/file: <=260 target and <=500 hard, deps: 0}`

## Project Summary

Lippe Lift Studio is a FastAPI + Jinja + HTMX vertical-slice monolith that generates topic-backed UGC post seeds, then advances batches through script review, prompt generation, QA, and publishing. The requested refactor scope is narrow: make the topic-generation slice lean enough to continue development without rewriting the surrounding batch workflow.

## Current Actual Runtime

### Verified Runtime Facts

- Main entrypoint is [app/main.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/main.py).
- Batch seeding orchestration lives in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py).
- Topic generation internals currently live almost entirely in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py).
- The database write path used by the active batch-seeding flow still writes `title`, `rotation`, and `cta` through [app/features/topics/queries.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/queries.py).
- The local verification shell resolved `python3` to `3.9.6`, while project docs still target Python 3.11+.
- The working tree is dirty. Untracked topic-schema migrations and tests exist, so documentation must distinguish between proposed schema and applied runtime behavior.

### Request Flow

Primary happy path today:

1. `discover_topics_for_batch(...)` in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L1007)
2. `_discover_topics_for_batch_sync(...)` in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L413)
3. `generate_topics_research_agent(...)` / `generate_lifestyle_topics(...)` in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L939) and [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L1210)
4. `generate_dialog_scripts(...)`, `extract_seed_strict_extractor(...)`, `build_seed_payload(...)`, `build_lifestyle_seed_payload(...)` in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py)
5. `add_topic_to_registry(...)` and `create_post_for_batch(...)` in [app/features/topics/queries.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/queries.py)

This is a valid vertical slice, but the call graph crosses too many unrelated concerns through one oversized file.

### Current Responsibility Breakdown Of `agents.py`

`app/features/topics/agents.py` currently mixes all of the following:

- PROMPT_1 normalization and raw parsing
- PROMPT_1 semantic validation rules
- PROMPT_2 raw parsing and fallback shaping
- Gemini provider orchestration and retry logic
- strict-seed extraction
- topic-to-seed mapping and social description assembly
- lifestyle topic generation and title derivation

This file is functioning as parser, validator, service, mapper, and orchestration layer at once.

### Current Data Truth

The actual runtime truth for the active batch path is:

- `topic_registry` plus `posts.seed_data` remain the live storage surfaces used by the code path.
- `topic_scripts` and `topic_research_runs` are not yet the runtime truth for this slice because the active code path does not read or write them.
- `architecture.md` must therefore be treated as partially aspirational in the topic-generation section until the code path is updated.

## Target Remediated State

### Architectural Direction

Refactor the slice by keeping the public function API stable and moving internals behind a small compatibility facade. The first pass is not a feature rewrite. It is a locality and reliability refactor.

### File Shape

Target files for the slice:

1. [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py)
   Compatibility facade only. Re-export the stable public functions currently used by handlers and tests. Target: `<=150 LOC`.
2. `app/features/topics/research_runtime.py`
   Provider calls, retry loops, and strict extractor runtime.
3. `app/features/topics/response_parsers.py`
   PROMPT_1/PROMPT_2 raw parsing, JSON/YAML repair, and semantic validation helpers.
4. `app/features/topics/seed_builders.py`
   `convert_research_item_to_topic`, `build_seed_payload`, `build_lifestyle_seed_payload`, description helpers.
5. `app/features/topics/lifestyle.py`
   `generate_lifestyle_topics`, hook tracking, and `_derive_lifestyle_title`.
6. [app/features/topics/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/schemas.py)
   Canonical contracts only. Repair stale imports here first or remove invalid references.

### Public Contract Rules

The first-pass refactor must preserve these call sites so surrounding code does not need to change immediately:

- `generate_topics_research_agent(...)`
- `generate_dialog_scripts(...)`
- `extract_seed_strict_extractor(...)`
- `convert_research_item_to_topic(...)`
- `build_seed_payload(...)`
- `generate_lifestyle_topics(...)`
- `build_lifestyle_seed_payload(...)`
- `parse_prompt1_response(...)`
- `parse_prompt2_response(...)`

Handlers may keep importing from `agents.py` during the first pass. `agents.py` becomes a facade, not the implementation home.

### Contract And Boundary Rules

- Schema/module drift is not allowed. If `prompts.py` names a contract such as `ResearchDossier`, that contract must exist in `schemas.py` or the import must be removed before further refactoring.
- Semantic validation rules live in one place only. Do not validate the same research item in both parser and runtime paths.
- Seed-builder functions must be pure mappers. They must not perform fresh network calls.
- Provider retry logic must live in the runtime module, not inside parser or mapper code.
- Architecture documentation must describe only what the live code path actually uses. Proposed schema is explicitly labeled as proposed until reads/writes exist.

### Testing Rules

- Preserve existing regression tests in [tests/test_topics_gemini_flow.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_topics_gemini_flow.py), [tests/test_lifestyle_generation_regression.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_lifestyle_generation_regression.py), and [tests/test_topic_prompt_templates.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_topic_prompt_templates.py).
- Add one import-smoke test for `app.features.topics.prompts` and `app.features.topics.agents` so contract drift fails fast.
- Refactor behind the facade first, then narrow tests to the new internal modules only after the facade path is stable.

## Detailed Decisions

### Contracts

- Pydantic models in [app/features/topics/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/schemas.py) remain the only runtime contract layer for the slice.
- If dossier-driven generation remains part of the design, add a real `ResearchDossier` model to `schemas.py`. If it is not yet part of the live runtime, remove the stale import and type references from `prompts.py`.

### Persistence

- Do not switch the runtime to `topic_scripts` or `topic_research_runs` in the same pass as the file split unless the handlers and queries path is updated end-to-end.
- Keep `queries.py` behavior stable during the first split. Documentation drift is repaired first; schema migration adoption is a separate implementation block.

### Observability

- Logging remains structured via `app.core.logging`.
- Error paths must not contain type-unsafe debug formatting that can mask the original failure.
- Retry metadata belongs in runtime orchestration logs, not in parsing helpers.

### Dependency Policy

- No new dependencies.
- Reuse `json`, `re`, `math`, `secrets`, `httpx`, `yaml`, and existing Pydantic/FastAPI infrastructure only where each concern already requires it.

## Constitution For This Slice

### Locality & Indirection

Keep the topic-generation slice understandable in one pass. Each file must have one dominant reason to change. Prefer a small facade and adjacent focused modules over another catch-all service file. Do not introduce class-heavy indirection for a functional pipeline that is currently imported as module-level functions.

### Testing & Debugging

Run the import-smoke check first, then the focused topic regression tests, then the batch discovery regression. When a refactor failure appears, change one boundary at a time: contracts first, pure parsing next, runtime orchestration next, mappers last. If after two debugging turns the tests still fail, generate `agents/testscripts/failure_report.md` before continuing.
