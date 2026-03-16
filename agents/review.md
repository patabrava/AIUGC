# FLOW-FORGE Review

Date: 2026-03-16
Review mode: Existing codebase audit with hosting recommendation

## Executive Decision

Default recommendation: move compute to a VPS-style host, not to Cloudflare-only compute, and do not use Hostinger shared hosting.

Best fit for the current codebase:
- If your main goal is cost consolidation: use a Hostinger VPS to run both the FastAPI app and the poller, keep Supabase, and keep ImageKit for now.
- If your main goal is better video edge delivery later: add Cloudflare in front of the app or migrate video storage separately after introducing a storage adapter boundary.

Not recommended as the immediate move:
- Cloudflare-only deployment for the full app.
- Hostinger shared hosting as the single platform.
- Replacing ImageKit with Cloudflare in the same move as the compute migration.

## Findings

### IMPORTANT: The app requires a persistent worker, so a pure serverless or shared-web-host move will break the video pipeline

Current State:
- The repo explicitly documents two processes: the FastAPI server and `workers/video_poller.py` in [README.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/README.md#L46).
- The worker runs an infinite loop with a 10-second sleep in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L450).
- Batch progression to `S6_QA` depends on worker completion logic in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L361).

Assessment:
- This is the main hosting constraint.
- Vercel can host the request side, but it cannot replace the always-on poller.
- Hostinger shared hosting is also a mismatch unless it provides reliable daemon/process supervision for Python.

Severity: IMPORTANT

Remediation:
- If consolidating vendors, use a VPS with process supervision.
- If staying managed, keep a separate worker runtime.
- Do not plan around “one website host” unless it supports both ASGI serving and a persistent worker process.

### IMPORTANT: Video hosting is currently ImageKit-shaped in code, tests, and metadata

Current State:
- The only asset adapter is ImageKit in [app/adapters/imagekit_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/imagekit_client.py#L19).
- Completed videos write `imagekit_file_id`, `file_path`, `thumbnail_url`, and final `video_url` in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L314).
- Tests directly exercise ImageKit upload behavior in `tests/test_imagekit_url_upload.py` and `tests/test_veo_url_upload_flow.py`.
- QA checks depend on a reachable `video_url` and use `HEAD` against that URL in [app/features/qa/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/qa/handlers.py#L381).

Assessment:
- Cloudflare R2 or Stream can work, but this is not a drop-in config change.
- A storage migration now would compound risk because it touches worker logic, QA semantics, metadata shape, and tests.

Severity: IMPORTANT

Remediation:
- Keep ImageKit during the compute migration.
- If you later want Cloudflare for video, first introduce a `video_asset_store` interface and migrate adapters behind it.

### IMPORTANT: The repo already points to Vercel plus Railway, not Render, so the current problem is consolidation rather than replacing a present Render dependency

Current State:
- Deployment docs specify `Vercel (API) + Railway (Worker)` in [README.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/README.md#L94).
- Vercel config exists in [vercel.json](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/vercel.json#L1).
- No Render config was found in the repository scan.

Assessment:
- The codebase evidence says your active architecture is already split across request and worker runtimes, but not on Render.
- The strategic question is whether to keep split hosting or consolidate compute onto one VPS provider.

Severity: IMPORTANT

Remediation:
- Treat this as a consolidation decision:
  - `Vercel + Railway + ImageKit + Supabase`
  - or `Hostinger VPS + ImageKit + Supabase`
  - or `Hostinger VPS + Cloudflare proxy + ImageKit + Supabase`

### MINOR: Worker packaging is fragile and makes host migration harder than it needs to be

Current State:
- The worker mutates `sys.path` to import the parent app in [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L13).
- The repo maintains a second worker-specific `requirements.txt` in [workers/requirements.txt](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/requirements.txt).

Assessment:
- This is workable on a VPS, but it is brittle for deployment packaging and increases drift risk between the web app and worker.

Severity: MINOR

Remediation:
- Unify dependency management and run both processes from the repo root.
- Package the worker as a normal module entrypoint.

### MINOR: Some core files are over the repo’s own locality budget, which will slow future infra migrations

Current State:
- Large files include [app/features/videos/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py), [app/features/batches/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches/handlers.py), and [app/features/topics/agents.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/agents.py).

Assessment:
- This is not a blocker for hosting, but it increases change risk when you later refactor storage providers or alter the job model.

Severity: MINOR

Remediation:
- Split video submission, polling recovery, and storage concerns into smaller files before a provider migration.

## Hosting Evaluation

### Option A: Hostinger VPS

Fit: Strong

Why it fits this repo:
- Can run both `uvicorn` and the poller as real long-lived processes.
- Lets you eliminate the split between Vercel and Railway.
- Requires the fewest application changes.

What it does not solve by itself:
- It does not replace ImageKit’s CDN/storage role automatically.
- It does not replace Supabase.

Recommended layout:
- `systemd` service 1: FastAPI app
- `systemd` service 2: `workers/video_poller.py`
- Reverse proxy: Nginx or Caddy
- Database/state: Supabase
- Video assets: keep ImageKit initially

Risk:
- Operational burden is higher than managed hosting.
- You own uptime, restarts, logs, and SSL termination.

### Option B: Cloudflare for full hosting

Fit: Weak for the current codebase

Why:
- The current app is Python/FastAPI plus an infinite-loop worker.
- Cloudflare’s strengths are edge proxying, CDN, object storage, and edge/serverless compute, but this repo is not shaped for a no-daemon edge runtime.
- Reaching Cloudflare-only would imply a rewrite of the worker model into cron-driven jobs, queues, or another evented pattern.

Where Cloudflare does fit:
- Proxy/CDN in front of a VPS.
- Future storage migration target after adapterization.

### Option C: Hostinger shared hosting

Fit: Poor

Why:
- This repo needs ASGI app hosting and a persistent worker.
- Shared hosting plans usually optimize for PHP/static workloads, not supervised Python daemons.

### Option D: Keep split managed hosting

Fit: Strong operationally, weaker on bill simplicity

Why:
- Matches the code as written.
- Lowest migration risk.
- Higher vendor sprawl and likely higher recurring cost than a single VPS.

## Strategic Recommendation

Choose this order:

1. Move compute to a Hostinger VPS if your priority is cost reduction and fewer vendors.
2. Keep Supabase.
3. Keep ImageKit for now.
4. Put Cloudflare in front later only if you want DNS, caching, WAF, or media migration.
5. Defer any Cloudflare video-storage migration until after compute consolidation is stable.

This gives you the lowest-risk path to “stop paying for split compute” without turning the migration into an infrastructure rewrite.

## Recommended Implementation Block

Budget:
- files: 6-8 touched
- LOC/file: target under 250, no file over 700
- deps: 0 new Python deps preferred, max 1 process-management/doc concern if justified

Implementation block:
1. Add production process docs for VPS deployment.
2. Add a single root startup model for web and worker.
3. Replace deployment-specific README sections with neutral process-manager instructions.
4. Add health/readiness notes for both web and worker.
5. Add one smoke testscript for VPS deployment verification.

Testscripts:
- `deploy-smoke`
  - Objective: verify FastAPI serves, worker starts, Supabase health passes, and video polling loop boots.
  - Run: start both services, hit `/health`, inspect worker logs for `video_poller_started`.
- `video-path-smoke`
  - Objective: verify a submitted video still progresses to asset URL and QA-readable state.
  - Run: execute existing phase-4 style flow against staging.

If remediation is accepted, switch to `EYE` and execute the implementation-block in one pass. If debugging fails for two turns, create `agents/testscripts/failure_report.md`.
