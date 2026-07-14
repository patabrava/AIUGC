# Manual Semantic UGC Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual-script variant of Semantic UGC and expose dynamic 8-60 second video generation for both semantic modes.

**Architecture:** Define semantic-route membership separately from manual-script membership, then use those predicates at every batch, service, template, and database boundary. `manual_semantic_ugc` creates blank drafts but persists the same duration authority, actor snapshot, pipeline route, and downstream Semantic UGC behavior as `semantic_ugc`.

**Tech Stack:** FastAPI, Pydantic v2, Jinja2, Alpine.js, PostgreSQL/Supabase migrations, pytest, Playwright.

---

### Task 1: Lock the creation-mode contracts with failing tests

**Files:**
- Modify: `tests/test_semantic_batch_mode.py`
- Modify: `tests/test_batches_manual_mode.py`
- Modify: `tests/test_semantic_video_handlers.py`

- [ ] Add tests proving `manual_semantic_ugc` requires `manual_post_count` and `target_duration_seconds`, rejects post-type authority, clears `target_length_tier`, persists `video_pipeline_route = 'semantic_ugc'`, creates manual drafts without scheduling discovery, and is accepted by semantic video planning.
- [ ] Run the focused tests and confirm failures identify the missing literal/predicates rather than test setup errors.

### Task 2: Add centralized semantic and manual mode predicates

**Files:**
- Modify: `app/features/characters/actor_identity.py`
- Modify: `app/features/batches/schemas.py`
- Modify: `app/features/batches/handlers.py`
- Modify: `app/features/batches/queries.py`
- Modify: `app/features/semantic_videos/service.py`
- Modify: `app/features/topics/handlers.py`

- [ ] Define `SEMANTIC_UGC_MODES = {'semantic_ugc', 'manual_semantic_ugc'}` and `is_semantic_ugc_mode(value)` without adding either mode to `CHARACTER_CONSISTENCY_MODES`.
- [ ] Add `manual_semantic_ugc` to the manual-mode set and Pydantic literal.
- [ ] Replace semantic exact-string checks at batch persistence, duplication, detail/service authorization, and duration validation boundaries with the shared predicate.
- [ ] Keep automated semantic topic discovery specific to `semantic_ugc`; manual semantic creation must follow the existing manual-draft branch.
- [ ] Run focused tests until green.

### Task 3: Expose dynamic duration in the batch form

**Files:**
- Modify: `templates/batches/list.html`
- Modify: `templates/batches/detail.html`
- Modify: `tests/test_semantic_batch_mode.py`
- Modify: `tests/test_semantic_video_ui.py`

- [ ] Add `Manual Semantic UGC - Veo 3.1` beside the automated semantic option.
- [ ] Make the manual draft panel visible for all manual modes and the target-seconds panel visible for both semantic modes.
- [ ] Disable legacy duration fields for both semantic modes and submit `target_duration_seconds` for either.
- [ ] Render the semantic detail partial for either semantic mode.
- [ ] Run template and UI contract tests until green.

### Task 4: Extend the database contract

**Files:**
- Create: `supabase/migrations/20260714000000_manual_semantic_ugc_mode.sql`
- Modify: `tests/test_semantic_batch_mode.py`
- Modify: `tests/test_semantic_batch_migration_postgres.py`

- [ ] Add a forward migration that includes `manual_semantic_ugc` in `batches_creation_mode_check`.
- [ ] Define duration and route constraints with `creation_mode IN ('semantic_ugc', 'manual_semantic_ugc')` as the semantic authority.
- [ ] Run migration contract tests and PostgreSQL integration tests when the local database prerequisite is available.

### Task 5: Verify and publish

**Files:**
- Verify all files above plus `.github/workflows/deploy-production.yml`.

- [ ] Run focused semantic/manual tests, the broader batch and semantic-video suites, Ruff, and YAML parsing.
- [ ] Start the real app and use Playwright to verify both semantic form variants and the 50-second control.
- [ ] Commit only scoped tracked files, push `main`, watch deployment and migration workflows, and verify `https://lippelift.xyz/livez` plus `https://lippelift.xyz/health`.
