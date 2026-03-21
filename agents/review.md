# FLOW-FORGE Review

Date: 2026-03-21
Review mode: LIRA senior audit for existing codebase with topics hub architecture

## Executive Decision

Build the topics hub as a new read-first slice inside the existing FastAPI/Jinja/HTMX app, but make `/topics` dual-mode so the browser gets HTML and API clients keep getting JSON. Add durable research-run tracking before exposing any on-demand research button. Do not route the hub through batch detail.

## Context Zero

### Environment Matrix

- OS from workspace context: macOS / Darwin
- Shell: `zsh`
- App runtime target: Python 3.11 from [README.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/README.md)
- API framework: `fastapi==0.104.1`
- Validation/config: `pydantic==2.5.0`, `pydantic-settings==2.1.0`
- DB client: `supabase==2.9.0`
- HTTP client: `httpx==0.27.2`
- Templates/UI: Jinja2 + HTMX + Alpine
- Background scheduling: APScheduler in-process plus `workers/video_poller.py`

### Non-Functional Constraints

- Locality budget: `{files: 6-8, LOC/file: <=260 target and <=500 hard, deps: 0}`
- No new framework
- No new dependency by default
- Preserve the current batch workflow
- Preserve the existing phase-2 topic discovery regression path

## Findings

### CRITICAL: `/topics` is already a JSON API route, so the hub cannot be added safely without a route contract change

Current State:
- The topic router currently exposes `@router.get("")` for JSON list output in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L1054).
- The same router also exposes `POST /topics/discover` for batch discovery in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L1012).
- The current phase-2 testscript already depends on `GET /topics` returning JSON list data in [tests/testscript_phase2.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/testscript_phase2.py#L197).

Assessment:
- The requested hub path and the current API path are the same.
- A browser-only page cannot be added by simply creating another route at `/topics`; the route contract must become dual-mode or the API path must move.
- This is the first blocker for the new feature.

Severity: CRITICAL

Remediation:
- Change `GET /topics` to content-negotiate:
  - HTML when the request asks for `text/html` or comes from HTMX
  - JSON for API clients and regression scripts
- Keep the JSON payload stable for the current testscript.
- Add a new hub template and page-local fragments behind the same route.

### IMPORTANT: The user-facing research state is still in-memory, so the hub would lose status on refresh or across workers

Current State:
- `_SEEDING_PROGRESS`, `_SEEDING_EVENTS`, and `_DISCOVERY_TASKS` are process-local in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L45).
- `start_seeding_interaction(...)` and `update_seeding_progress(...)` only persist state in memory and emit event snapshots in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py#L77).
- The batch discovery workflow writes final posts and topic rows, but there is no durable research-run read model in the database.

Assessment:
- This is acceptable for the existing batch bootstrap flow, but not for a user-facing topic hub that promises run tracking.
- The hub needs a durable run table and a polling/read endpoint, or it will show stale or empty state after refresh.

Severity: IMPORTANT

Remediation:
- Add a `topic_research_runs` table.
- Add read/write helpers for creating, updating, and fetching runs.
- Add `POST /topics/runs` and `GET /topics/runs/{run_id}`.
- Stop relying on `_SEEDING_PROGRESS` as the source of truth for the new hub.

### IMPORTANT: The current data model exposes only a single topic registry row and post-level seed data, so "existing scripts" are not first-class yet

Current State:
- `topic_registry` is the only topic index table in [supabase/migrations/001_initial_schema.sql](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/supabase/migrations/001_initial_schema.sql#L15).
- `topic_registry` stores `title`, `rotation`, `cta`, `use_count`, and timestamps, but not a normalized script bank.
- Downstream scripts live in `posts.seed_data` and are normalized during post review in [app/features/posts/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/handlers.py#L33).

Assessment:
- The hub can browse topics today, but "existing scripts" are only indirectly visible through posts.
- That is enough for a first pass if the hub is read-only, but it is not a clean long-term script index.

Severity: IMPORTANT

Remediation:
- First pass: aggregate scripts from `posts.seed_data` into the hub view-model.
- Second pass or backlog: add a normalized `topic_scripts` table or a `topic_registry_id` relation if script browsing needs to become a durable first-class feature.

### IMPORTANT: The topics handler is oversized and mixes UI-adjacent concerns with batch discovery orchestration

Current State:
- `app/features/topics/handlers.py` is 1135 LOC.
- The same file contains progress tracking, discovery orchestration, cron entry points, JSON list output, and background task management.
- Topic generation entry points already exist in [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L939) and [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py#L1132), but they are not separated into a hub view-model service.

Assessment:
- The topics slice is functionally correct for batch seeding, but it is too monolithic for a second user-facing surface.
- The hub should not add more orchestration logic to this file without extracting a smaller service layer.

Severity: IMPORTANT

Remediation:
- Move hub view-model assembly into a small adjacent service module.
- Keep route functions thin.
- Keep the batch discovery workflow intact and separate from the hub launch workflow.

### MINOR: There is no topics hub template or page-local JS yet

Current State:
- `templates/` contains only batch templates and shared components.
- There is no `templates/topics/` directory.
- There is no `static/js/topics/` module.

Assessment:
- This is expected given the feature does not exist yet.
- It becomes a maintainability risk only if the new hub is bolted into batch detail instead of receiving its own slice.

Severity: MINOR

Remediation:
- Add `templates/topics/hub.html`.
- Add small topic partials for list, detail, and run status.
- Add `static/js/topics/hub.js` for filters and polling.

## Prioritized Remediation Plan

### Implementation Block

1. Make `/topics` dual-mode so the hub and the existing JSON API can share the same route contract.
2. Add durable topic-run persistence and a launch endpoint for one-topic research runs.
3. Add a topic hub page shell plus fragments for topic list, script detail, and run history.
4. Add a small service/helper layer for hub view-model assembly so `handlers.py` stays thin.
5. Add tests for route negotiation, durable run state, and the hub page smoke path.

### Files

- `app/features/topics/handlers.py`
- `app/features/topics/queries.py`
- `app/features/topics/service.py` or `app/features/topics/hub.py`
- `templates/topics/hub.html`
- `templates/topics/partials/*.html`
- `static/js/topics/hub.js`
- `supabase/migrations/*topics*.sql`
- `tests/test_topics_hub.py`
- `tests/testscript_phase2.py` if route negotiation requires regression updates

### Testscripts

- Add a focused hub smoke script for the browser path.
- Re-run the phase-2 topic discovery script to prove the JSON API path still works.
- If one debugging line of work fails twice, write `agents/testscripts/failure_report.md` before looping further.

## Decision

The feature is viable and low-risk if the first pass stays read-first, keeps `/topics` contract-compatible, and adds durable run state before exposing the launch button.
