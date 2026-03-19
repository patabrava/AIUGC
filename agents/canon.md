# FLOW-FORGE Canon

Date: 2026-03-17
Scope: Existing codebase audit and TikTok sandbox integration target state
Locality Budget: `{files: 10-14 for TikTok slice, LOC/file: <=350 target and <=1000 hard, deps: 0 default and max 1 if encryption cannot stay vanilla}`

## Goal

Preserve the current FastAPI vertical-slice monolith while adding a TikTok sandbox integration that supports web OAuth via Login Kit, draft upload via Content Posting API using `video.upload`, durable provider account storage, durable publish job tracking, and a minimal batch-detail UI flow that fits the existing S7 publish plan stage.

## Current System

### Architecture

- Runtime: FastAPI app in [app/main.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/main.py)
- Persistence: Supabase/Postgres via the singleton adapter in [app/adapters/supabase_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/supabase_client.py)
- Frontend: server-rendered Jinja2 templates with HTMX and Alpine
- Media storage: Cloudflare R2 via [app/adapters/storage_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/storage_client.py)
- Background work: APScheduler inside the web process for publish dispatch, plus `workers/video_poller.py` for video generation completion

### Active Feature Slices

- `batches`: batch creation, state machine, HTMX views
- `topics`: Gemini topic discovery and normalization
- `posts`: script review and prompt generation
- `videos`: VEO/Sora submission and polling handoff
- `qa`: automated/manual review gates
- `publish`: Meta-only OAuth, target selection, scheduling, dispatch

### Request Flow

- Main batch creation flow:
  - `POST /batches` in [app/features/batches/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches/handlers.py)
  - `create_batch(...)` in [app/features/batches/queries.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches/queries.py)
  - background `discover_topics_for_batch(...)` in [app/features/topics/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/topics/handlers.py)
- Current publish flow:
  - S7 UI in [templates/batches/detail.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/batches/detail.html)
  - Meta connect/callback/select/confirm in [app/features/publish/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish/handlers.py)
  - scheduled dispatch from APScheduler in [app/main.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/main.py)

### Current Provider Model

- Meta is implemented as a provider-specific branch inside the `publish` slice.
- Meta connection is stored batch-scoped in `batches.meta_connection` JSONB.
- Per-post publish execution state already exists via:
  - `posts.social_networks`
  - `posts.publish_status`
  - `posts.platform_ids`
  - `posts.publish_results`

### Configuration

- Settings are centralized in [app/core/config.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/core/config.py).
- TikTok config is currently incomplete:
  - `TIKTOK_CLIENT_KEY`
  - `TIKTOK_CLIENT_SECRET`
- Missing TikTok config for target design:
  - redirect URI
  - sandbox vs production mode
  - app URL
  - privacy URL
  - terms URL
  - token encryption key
  - authorized sandbox account handle

## Audit Canon

### A1. Authentication And Authorization

- App-level auth: none
- User model: none
- Current provider connection scope: batch-scoped Meta connection on `batches.meta_connection`
- Current enforcement point: none; routers are mounted directly without auth dependencies in [app/main.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/main.py)
- Canon implication:
  - TikTok cannot assume a real authenticated `user_id` exists today.
  - First TikTok implementation must either:
    - introduce a minimal owner concept explicitly, or
    - use a nullable `user_id` plus one sandbox operator record, or
    - use app-scoped provider connections and document that choice clearly.
- Required target:
  - provider tokens must be encrypted at rest
  - OAuth state must be validated and time-bounded
  - sandbox and production credentials must stay separate

### A2. Request Flow And State Management

- Current request flow is understandable but several handlers are oversized.
- Current publish/account state is split:
  - connection data on `batches`
  - publish execution state on `posts`
- Target TikTok state model:
  - connected account stored independently from batches
  - media asset stored independently from a provider job
  - publish job stores request/response/error snapshots and links back to both account and media asset

Target contracts:

```text
connected_accounts
media_assets
publish_jobs
```

### A3. Error Handling And Recovery

- Error envelope foundation exists through `FlowForgeException` in [app/core/errors.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/core/errors.py)
- Third-party normalization exists in the Meta branch and should be copied for TikTok
- Required target:
  - preserve full TikTok API error payloads in structured logs
  - persist error payload snapshots on publish jobs
  - map OAuth failures, token expiry, sandbox authorization failures, upload validation failures, and rate limits into explicit structured app errors

### A4. Data Contracts And Schemas

- Pydantic boundary validation is already the project norm.
- Current social schema advertises TikTok in `SocialNetwork` but the implementation is Meta-only.
- Target TikTok schema placement:
  - co-located inside the publish slice or a provider-local TikTok submodule under it
- Required target models:

```text
connected_accounts
  id
  user_id
  platform = 'tiktok'
  open_id
  display_name
  avatar_url
  access_token
  refresh_token
  access_token_expires_at
  refresh_token_expires_at
  scope
  environment
  created_at
  updated_at

media_assets
  id
  user_id
  source_url
  storage_key
  mime_type
  file_size
  duration_seconds
  status
  created_at

publish_jobs
  id
  user_id
  connected_account_id
  platform = 'tiktok'
  media_asset_id
  caption
  post_mode = 'draft' | 'direct'
  tiktok_publish_id
  status
  request_payload_json
  response_payload_json
  error_message
  created_at
  updated_at
  published_at
```

### A5. Critical User Journey

- Current S7 journey is Meta-specific in copy and controls.
- Target TikTok journey:
  - user enters S7 publish plan
  - sees TikTok connection card
  - starts OAuth via backend redirect
  - callback persists encrypted sandbox account connection
  - user triggers draft upload for a generated video
  - backend creates/updates media asset and publish job
  - UI shows submitted / failed / reconnect-required state

## Interface Canon

### B1. Design System Foundation

- Keep the existing server-rendered HTMX/Jinja approach.
- Do not add a new frontend framework for TikTok.
- Keep the change localized to the existing publish-plan area in [templates/batches/detail.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/batches/detail.html) plus any minimal partials needed.

### B2. UI States Required

- Not connected
- Connecting
- Connected account
- Uploading video
- Draft upload success
- Draft upload failure
- Token expired / reconnect required

### B3. UI Integration Rule

- TikTok UI must not be forced into the existing Meta card.
- Use sibling provider blocks within S7 instead of extending `meta_connection` semantics to TikTok.
- Network toggles must only expose providers with an implemented backend path.

### B4. Accessibility

- Keep keyboard-accessible buttons and form controls.
- Preserve visible focus rings already used in templates.
- Use textual status labels, not color alone.

## Architecture Canon

### C1. Environment And Configuration

- `.env.example` must be extended to include:
  - `TIKTOK_REDIRECT_URI`
  - `TIKTOK_ENVIRONMENT`
  - `APP_URL`
  - `PRIVACY_POLICY_URL`
  - `TERMS_URL`
  - `TIKTOK_SANDBOX_ACCOUNT`
  - `TOKEN_ENCRYPTION_KEY`
- Config must validate the redirect URI and URLs on startup when TikTok is enabled.

### C2. Repository Structure

- Keep TikTok inside the existing publish vertical slice.
- Preferred file shape:

```text
app/features/publish/handlers.py
app/features/publish/schemas.py
app/features/publish/tiktok.py
app/features/publish/tiktok_crypto.py
templates/batches/detail.html
supabase/migrations/<timestamp>_add_tiktok_connected_accounts.sql
tests/test_publish_tiktok_oauth.py
tests/test_publish_tiktok_upload.py
agents/canon.md
agents/review.md
```

- Avoid creating a large cross-cutting `services/` layer.

### C3. Dependency Management

- Default: 0 new dependencies.
- Encryption default: use a small, explicit adapter if the standard library is insufficient for authenticated encryption.
- Do not add a TikTok SDK unless the REST surface proves incomplete.

### C4. Build And Development

- Existing commands remain:
  - `pip install -r requirements.txt`
  - `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`
  - `python3 -m pytest tests/`
- TikTok sandbox implementation must be testable without production review or `video.publish`.

### C5. Testing Infrastructure

- Current tests are a mix of pytest unit tests and environment-coupled scripts.
- TikTok target tests:
  - schema/config smoke
  - OAuth state and callback handling
  - token encryption round-trip
  - draft upload request building and response persistence
  - failure-path coverage for expired token, unauthorized sandbox account, and media validation errors

### C6. Logging And Observability

- Use structured logging with correlation IDs already present in [app/core/logging.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/core/logging.py)
- Required target log events:
  - `tiktok_oauth_started`
  - `tiktok_oauth_callback_received`
  - `tiktok_token_exchange_failed`
  - `tiktok_account_connected`
  - `tiktok_upload_started`
  - `tiktok_upload_failed`
  - `tiktok_publish_job_updated`

### C7. Security Baseline

- No client secret in frontend code
- Server-side OAuth only
- Exact redirect URI matching
- encrypted token storage
- environment separation for sandbox vs production
- persisted API error payloads with token redaction

## Target TikTok Plan

### Product Scope

- Web integration
- Sandbox only
- Products:
  - `Login Kit`
  - `Content Posting API`
- Scopes:
  - `user.info.basic`
  - `video.upload`
- Deferred:
  - `video.publish`
  - direct autopost
  - URL-pull mode unless domain verification is complete

### Endpoints

```text
GET    /api/auth/tiktok/start
GET    /api/auth/tiktok/callback
POST   /api/tiktok/upload-draft
GET    /api/tiktok/account
GET    /api/tiktok/publish-jobs/:id
```

### Default Storage Decision

- Use `StorageClient` + existing R2 asset URLs as the source of truth for generated videos.
- Prefer file upload / server-side transfer first.
- Add URL-pull later only if the TikTok sandbox setup confirms verified-domain support.

### Non-Negotiables

- Do not overload `batches.meta_connection` for TikTok.
- Do not expose TikTok in the publish UI until the corresponding backend path exists.
- Do not claim `user_id` semantics unless a real app user model exists; document the interim ownership rule explicitly.
