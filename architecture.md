# FLOW-FORGE Architecture

Date: 2026-03-21  
Status: Current runtime architecture

## 1. Runtime Topology

- `web` process: FastAPI app serving API + Jinja/HTMX/Alpine UI
- `worker` process: `workers/video_poller.py` for async video completion and batch QA unlock
- shared persistence: Supabase Postgres + PostgREST RPC
- shared media storage: Cloudflare R2

Core entrypoints:

- [app/main.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/main.py)
- [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py)

## 2. Application Pattern

- architecture style: vanilla vertical-slice monolith
- UI: server-rendered templates with progressive enhancement
- boundaries: Pydantic schemas + structured errors (`ok/data` and error envelopes)
- orchestration model: explicit state machine per batch

Feature slices:

- [app/features/batches](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches)
- [app/features/topics](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics)
- [app/features/posts](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts)
- [app/features/videos](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos)
- [app/features/qa](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/qa)
- [app/features/publish](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish)

## 3. Batch State Machine

Primary states:

1. `S1_SETUP`
2. `S2_SEEDED`
3. `S4_SCRIPTED`
4. `S5_PROMPTS_BUILT`
5. `S6_QA`
6. `S7_PUBLISH_PLAN`
7. `S8_COMPLETE`

Critical transitions:

- `S1_SETUP -> S2_SEEDED`: topic/script seeding
- `S2_SEEDED -> S4_SCRIPTED`: script review approval
- `S4_SCRIPTED -> S5_PROMPTS_BUILT`: prompt generation
- `S5_PROMPTS_BUILT -> S6_QA`: video completion and QA unlock
- `S6_QA -> S7_PUBLISH_PLAN`: QA approvals complete
- `S7_PUBLISH_PLAN -> S8_COMPLETE`: publish dispatch terminal states

State definitions live in [app/core/states.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/core/states.py).

## 4. Topic + Script Generation

Current family-first flow:

1. Seed selection pulls from the YAML topic bank using usage-aware ranking.
2. Topic research runs on a seed topic and writes a provisional family plus `pending` scripts.
3. `workers/audit_worker.py` audits pending scripts asynchronously.
4. Passing scripts promote their owning family to `active`.
5. Batch seeding reuses only `pass`-audited family coverage and returns `coverage_pending` when the bank is short.

Current design uses:

- topic parent bank: `public.topic_registry` as the canonical family registry
- script variant rows: `public.topic_scripts`

Important behavior:

- exact-tier reuse is preferred from audited families
- missing coverage does not trigger inline audit during batch setup
- hook-bank context is injected separately from topic seed context

Core modules:

- [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py)
- [app/features/topics/prompts.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/prompts.py)
- [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py)
- [app/features/topics/queries.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/queries.py)

## 5. Video Pipeline

Submission layer:

- [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py)
- [app/adapters/veo_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/veo_client.py)
- [app/adapters/sora_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/sora_client.py)

Completion layer:

- [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py)
- [app/adapters/storage_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/storage_client.py)

Flow:

1. submit operation ID to provider
2. persist `video_operation_id` + submitted state
3. poll provider completion asynchronously
4. download asset and upload to R2
5. persist `video_url` and mark `completed`
6. move batch from `S5` to `S6` when all active posts are completed

## 6. Publish Pipeline

Providers:

- Meta (Facebook/Instagram): schedule + dispatch
- TikTok: OAuth account handling, readiness checks, draft/direct flows

Key module:

- [app/features/publish/tiktok.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish/tiktok.py)

Guardrail:

- account readiness polling must degrade safely on provider rate limits and never crash batch detail rendering.

## 7. Data Model (Key Tables)

- `public.batches`
- `public.posts`
- `public.topic_registry`
- `public.topic_scripts`
- `public.topic_research_runs`
- `public.connected_accounts`
- `public.publish_jobs`
- `public.media_assets`

Notes:

- `posts.seed_data` is the per-post script/review payload
- `posts.video_metadata` stores provider/storage diagnostics
- `topic_scripts` stores script variants plus audit state (`pending`, `pass`, `needs_repair`, `reject`)
- `topic_registry` stores family identity and lifecycle state (`provisional`, `active`, `quarantined`, `merged`)

## 8. UI Surface

Primary operator screen:

- [templates/batches/detail.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/batches/detail.html)

Responsibilities:

- script review in `S2`
- prompt generation in `S4/S5`
- video status and QA in `S6`
- publish planning and dispatch in `S7/S8`

## 9. Operational Requirements

- both `web` and `worker` processes must run in production
- provider failures are expected and must be idempotently recoverable
- staged transitions must only count active posts (`removed`/`video_excluded` excluded)
