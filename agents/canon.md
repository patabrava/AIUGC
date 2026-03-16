# FLOW-FORGE Canon

Date: 2026-03-16
Scope: Existing codebase audit for hosting and video-delivery strategy

## System Summary

FLOW-FORGE is a Python 3.11 FastAPI monolith with server-rendered Jinja templates and a Supabase-backed state machine. The app manages UGC batch creation, topic discovery, prompt assembly, video generation submission, QA, and publish planning.

The deployed topology reflected in source is split-runtime:
- Request runtime: FastAPI app exported for Vercel serverless via `api/index.py`.
- Background runtime: long-running polling worker in `workers/video_poller.py`.
- Data system: Supabase tables used as primary operational store.
- Video delivery/storage: ImageKit is the current canonical video asset backend.

## Evidence Snapshot

- FastAPI app bootstraps all feature routers in [app/main.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/main.py#L51).
- Vercel serverless adapter is declared in [api/index.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/api/index.py) and wired in [vercel.json](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/vercel.json#L1).
- README explicitly documents a second terminal for the poller and names deployment as `Vercel (API) + Railway (Worker)` in [README.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/README.md#L46) and [README.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/README.md#L157).
- The worker polls all `submitted` and `processing` posts every 10 seconds in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L42) and [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L46).
- Completed videos are uploaded to ImageKit and persisted back to `posts.video_url` and `posts.video_metadata` in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L268).
- ImageKit is hard-wired as the asset adapter in [app/adapters/imagekit_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/imagekit_client.py#L19).
- QA depends on a publicly reachable `video_url` and issues `HEAD` requests to that URL in [app/features/qa/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/qa/handlers.py#L358).

## Runtime Model

### Request Path

Representative path:
1. `POST /videos/{post_id}/generate` enters [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py#L37).
2. The handler reads the post from Supabase, builds provider-specific prompt text, submits the generation request, then stores `video_operation_id`, `video_provider`, and initial `video_status` back into Supabase at [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py#L125).
3. The API response returns immediately; actual completion is deferred to the worker.

### Background Path

Representative path:
1. Worker polls `posts` for `submitted` and `processing` jobs in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L46).
2. Worker calls provider status endpoints in VEO or Sora adapters at [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L143) and [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L217).
3. On completion, worker uploads to ImageKit and updates `posts.video_url` in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L326).
4. Worker transitions the batch from `S5_PROMPTS_BUILT` to `S6_QA` in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L361).

This is a real operational dependency, not an optimization. Without the worker, videos do not complete into the application state machine.

## Storage and Video Assumptions

- Video submission goes to external generation providers: Google VEO and OpenAI Sora.
- Generated assets are not kept on local disk as the canonical store.
- The system assumes a stable public asset URL, provider metadata, and optional thumbnail URL.
- QA logic validates the public URL directly, so any replacement storage must preserve HTTP reachability semantics.

Current asset contract shape:
- `video_url`
- `video_metadata.imagekit_file_id`
- `video_metadata.file_path`
- `video_metadata.thumbnail_url`
- `video_metadata.size_bytes`
- `video_metadata.upload_method`

## Deployment Constraints Derived From Code

1. A serverless-only host is insufficient.
   The worker is an infinite loop with 10-second polling and must remain alive.

2. Shared hosting is insufficient unless it supports supervised long-running Python processes.
   The codebase is not CGI/PHP-style and requires an ASGI app plus worker.

3. Video hosting and app hosting are separate concerns.
   The app compute can move to one vendor while video assets stay on a CDN/storage vendor.

4. Replacing ImageKit is not a config-only swap.
   The adapter, worker, QA expectations, tests, and metadata schema are all ImageKit-shaped today.

## Codebase Shape Relevant To Hosting

- Approximate Python source size: 7.7k LOC.
- Large files exceeding locality budget:
  - [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py)
  - [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py)
  - [app/features/batches/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches/handlers.py)
- Dependency profile is moderate for a Python monolith and includes FastAPI, Supabase, OpenAI, Google GenAI, ImageKit, APScheduler, and Playwright.

## Canonical Hosting Interpretation

Near-term canonical deployment options that fit the existing code without a rewrite:

1. Single VPS deployment
- One VPS runs FastAPI behind Nginx/Caddy and the poller under `systemd` or Supervisor.
- Supabase remains managed.
- Video assets remain on ImageKit or move later behind a dedicated adapter migration.

2. Split managed deployment
- Serverless or web service for FastAPI.
- Separate worker service for the poller.
- Supabase remains managed.
- ImageKit remains asset backend.

The current repository fits option 1 or 2. It does not fit Cloudflare-only compute or Hostinger shared hosting without architectural change.
