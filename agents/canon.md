# FLOW-FORGE Canon

Date: 2026-03-21
Scope: Existing codebase audit and topics hub target state
Locality Budget: `{files: 6-8 for the topics hub slice, LOC/file: <=260 target and <=500 hard, deps: 0}`

## Goal

Add a read-first topics hub that lets batch owner/editors browse existing topics, inspect scripts, review durable research-run history, and launch on-demand research for one topic without breaking the current batch workflow.

## Current System

### Architecture

- Runtime: FastAPI in [app/main.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/main.py)
- Frontend: server-rendered Jinja templates with HTMX and Alpine from CDN in [templates/base.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/base.html)
- Persistence: Supabase/Postgres through the singleton adapter in [app/adapters/supabase_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/supabase_client.py)
- Topic slice: `topics` router in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py)
- Batch detail UI: [templates/batches/detail.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/batches/detail.html)
- Batch list UI: [templates/batches/list.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/batches/list.html)
- Topic generation code: [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py)
- Topic registry queries: [app/features/topics/queries.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/queries.py)

### Current Topic Flow

- `POST /batches` creates a batch and schedules background discovery.
- `discover_topics_for_batch(...)` in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py) performs batch-scoped generation off the request event loop.
- Topic discovery writes into `topic_registry` and `posts`.
- The current topic router exposes `POST /topics/discover`, `GET /topics`, and `POST /topics/cron/discover`.
- `GET /topics` currently returns JSON list data, not an HTML hub.
- Live seeding progress is kept in process memory via `_SEEDING_PROGRESS` and `_SEEDING_EVENTS` in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py).

### Current Data Model

- `topic_registry` is the only topic index table in [supabase/migrations/001_initial_schema.sql](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/supabase/migrations/001_initial_schema.sql)
- `posts.seed_data` carries the script/review payload used downstream by posts, videos, QA, and publish
- There is no durable research-run table yet
- There is no first-class topic script index yet

### Current UI Surface

- There is no `templates/topics/` directory yet
- There is no page-local JS module for topics
- Existing topic browsing happens only through the JSON API and batch-driven testscript flows

## Target Remediated State

- Keep `/topics` as the canonical hub route, but make it dual-mode:
  - HTML for browser/HTMX requests
  - JSON for API clients and regression tests
- Add a launch path for on-demand research that does not require batch creation.
- Persist research runs durably so refresh, reload, or multi-worker deploys do not lose state.
- Expose existing scripts in the hub from a durable read model instead of a transient in-memory tracker.
- Keep batch discovery intact and separate from the new hub launch flow.
- Keep the implementation vanilla-first and localized to the topics feature slice.

## Architecture And Operations

### Route Contract

- `GET /topics` serves the hub page when the request asks for HTML and returns JSON when it asks for API data.
- `POST /topics/runs` starts an on-demand research run for one topic.
- `GET /topics/runs/{run_id}` returns run status for polling or fragment refresh.
- `POST /topics/discover` remains the existing batch-seeding path.
- `POST /topics/cron/discover` remains the background batch scheduler entry point.

### Data Contract

- `topic_registry` remains the topic inventory index.
- `topic_research_runs` stores durable research run status, timings, error state, and result summary.
- `topic_scripts` stores normalized script variants per topic and length tier.
- `posts.seed_data` remains the downstream payload used by the batch workflow.
- Hub reads must come from stored data, not in-memory progress objects.

### File Shape

```text
app/features/topics/handlers.py       # thin routing and request/response orchestration
app/features/topics/queries.py        # topic, script, and run read/write helpers
app/features/topics/service.py        # hub view-model assembly and launch workflow
templates/topics/hub.html            # page shell
templates/topics/partials/*.html     # list/detail/run fragments
static/js/topics/hub.js              # page-local filters, polling, and launch helpers
supabase/migrations/*topics*.sql      # durable run/script schema
tests/test_topics_hub.py             # page and contract tests
```

### Design Rules

- Do not introduce a new framework.
- Do not move the new feature into batch detail.
- Keep the hub read-first; launch is secondary to browsing and inspection.
- Use page-local partials and a small page-local JS module rather than a global store.
- Keep routing and data contracts explicit enough that a reader can find the full flow in one pass.

## Definition of Done

- `/topics` loads as an HTML hub in a browser and still serves JSON to API clients.
- Users can browse topics, inspect scripts, and review research-run history.
- Users can launch a research run for one topic and see durable status after refresh.
- Existing batch discovery tests continue to pass.
- The topics hub slice stays within the locality budget and uses no unnecessary dependencies.
