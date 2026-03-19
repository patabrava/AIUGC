# FLOW-FORGE Review

Date: 2026-03-17
Review mode: LIRA senior audit for existing codebase with TikTok sandbox integration planning

## Executive Decision

Default recommendation: build TikTok as a new provider path inside the existing `publish` slice, but do not reuse the current batch-scoped `meta_connection` pattern for durable TikTok account storage.

The repo already has the right monolith shape, provider config entry points, media storage path, and S7 publish UI anchor. The blockers are structural, not foundational: no app-level auth model, no encrypted token storage, a Meta-hardcoded publish slice, and a stale claim that TikTok is supported when only Meta is implemented.

## Context Zero

### Environment Matrix

- OS from workspace context: macOS / Darwin
- Shell: `zsh`
- App runtime target: Python 3.11 from README
- API framework: `fastapi==0.104.1`
- Validation/config: `pydantic==2.5.0`, `pydantic-settings==2.1.0`
- DB client: `supabase==2.9.0`
- HTTP client: `httpx==0.27.2`
- Templates/UI: Jinja2 + HTMX + Alpine
- Background scheduling: APScheduler in-process plus `workers/video_poller.py`

### Non-Functional Constraints

- Locality budget: `{files: 10-14, LOC/file: <=350 target and <=1000 hard, deps: 0 default and max 1 if encryption cannot stay vanilla}`
- TikTok target scope: sandbox only, `user.info.basic` + `video.upload`
- Production review: out of scope for this pass
- Direct publishing with `video.publish`: explicitly deferred

## Findings

### CRITICAL: The app has no authentication boundary, but the requested TikTok model assumes durable per-user connected accounts

Current State:
- Every router is mounted directly with no auth dependency or session guard in [app/main.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/main.py#L163).
- Batch creation and mutation endpoints accept unauthenticated requests in [app/features/batches/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches/handlers.py#L60).
- The requested TikTok schema assumes `connected_accounts.user_id`, `media_assets.user_id`, and `publish_jobs.user_id`, but no user model exists anywhere in the repo.

Assessment:
- This is the primary architectural mismatch between the guide and the current app.
- If implemented naively, TikTok connected accounts would either be orphaned, implicitly global, or incorrectly attached to batch records.

Severity: CRITICAL

Remediation:
- Define an explicit ownership rule before coding:
  - either add a minimal authenticated user model, or
  - introduce an app-operator ownership convention for sandbox only with nullable `user_id`.
- Document that rule in schema, handlers, and README.
- Do not overload `batches.meta_connection` as a pseudo-user account store.

### CRITICAL: Provider tokens are persisted in plaintext-equivalent JSONB today, which violates the TikTok integration requirements

Current State:
- Meta tokens are stored on the batch record via `_update_batch_meta_connection(...)` in [app/features/publish/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish/handlers.py#L284).
- The callback persists `user_access_token` directly inside the connection payload in [app/features/publish/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish/handlers.py#L438).
- The schema migration explicitly stores Meta connection data in `batches.meta_connection JSONB` in [supabase/migrations/005_add_meta_publish_integration.sql](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/supabase/migrations/005_add_meta_publish_integration.sql#L5).

Assessment:
- The code sanitizes tokens before sending them back to the browser, but storage is still unencrypted.
- That is incompatible with the stated non-negotiables for TikTok OAuth and refresh-token handling.

Severity: CRITICAL

Remediation:
- Introduce a token encryption adapter before any TikTok token persistence.
- Store encrypted access and refresh tokens in a dedicated `connected_accounts` table.
- Redact token values from logs and persisted error payloads.

### IMPORTANT: The current social publishing slice is explicitly Meta-hardcoded even where schemas already advertise TikTok

Current State:
- `SocialNetwork` includes `TIKTOK` in [app/features/publish/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish/schemas.py#L16).
- The S7 UI copy, controls, and routes are entirely Meta-specific in [templates/batches/detail.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/batches/detail.html#L220).
- The only implemented OAuth and target-selection routes are Meta routes in [app/features/publish/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish/handlers.py#L388).

Assessment:
- The schema suggests a provider-agnostic publish system, but the actual implementation is a provider-specific branch.
- Adding TikTok by extending `meta_connection` semantics would deepen the coupling and make S7 harder to reason about.

Severity: IMPORTANT

Remediation:
- Keep TikTok in the `publish` slice, but split provider logic into explicit provider-local helpers.
- Replace the single Meta card with sibling provider sections.
- Only expose TikTok network choices after the TikTok backend path exists.

### IMPORTANT: The current data model is missing the job/account/media separations required by the TikTok guide

Current State:
- Post-level publish outcome fields already exist on `posts` in [supabase/migrations/001_initial_schema.sql](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/supabase/migrations/001_initial_schema.sql).
- Meta connection state is batch-scoped JSONB in [supabase/migrations/005_add_meta_publish_integration.sql](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/supabase/migrations/005_add_meta_publish_integration.sql#L5).
- There are no `connected_accounts`, `media_assets`, or `publish_jobs` tables.

Assessment:
- The current post-centric schema is usable for final outcome snapshots, but not for OAuth connection lifecycle, token refresh lifecycle, or draft upload job tracking.
- TikTok draft upload needs its own durable job record even if `posts.publish_results` stays as the denormalized UI summary.

Severity: IMPORTANT

Remediation:
- Add:
  - `connected_accounts`
  - `media_assets`
  - `publish_jobs`
- Keep `posts.publish_results` and `posts.platform_ids` as read-optimized summaries, not the source of truth.

### IMPORTANT: TikTok configuration is only partially represented, so exact redirect URI and environment separation would be easy to get wrong

Current State:
- Only `tiktok_client_key` and `tiktok_client_secret` exist in [app/core/config.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/core/config.py#L87).
- `.env.example` only lists `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` in [/.env.example](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.env.example#L34).
- No redirect URI, app URL, policy URLs, environment, or sandbox account config exists.

Assessment:
- TikTok OAuth requires exact redirect matching, so the current config surface is insufficient for a safe implementation.
- Sandbox vs production drift would be very likely without explicit settings.

Severity: IMPORTANT

Remediation:
- Extend config and `.env.example` with:
  - `TIKTOK_REDIRECT_URI`
  - `TIKTOK_ENVIRONMENT`
  - `APP_URL`
  - `PRIVACY_POLICY_URL`
  - `TERMS_URL`
  - `TIKTOK_SANDBOX_ACCOUNT`
  - `TOKEN_ENCRYPTION_KEY`
- Validate required TikTok config together, similar to `_require_meta_settings()`.

### IMPORTANT: The request flow already exceeds the locality budget in several critical handlers, which will make TikTok harder to maintain unless its scope is kept narrow

Current State:
- `app/features/publish/handlers.py` is 1019 LOC.
- `app/features/topics/handlers.py` is 982 LOC.
- `app/features/batches/handlers.py` is 802 LOC.
- `app/features/videos/handlers.py` is 745 LOC.
- The file-size scan shows several files well above the repo’s locality budget.

Assessment:
- The vertical-slice structure is good, but several slices have collapsed too many concerns into one handler file.
- TikTok should not be added as another 300-500 lines into the already oversized publish handler without local helper extraction.

Severity: IMPORTANT

Remediation:
- Add small provider-local helpers such as:
  - `app/features/publish/tiktok.py`
  - `app/features/publish/tiktok_crypto.py`
- Keep route definitions in `handlers.py`, but move request building, token exchange, and upload orchestration into adjacent helper modules.

### IMPORTANT: The current tests are uneven and partially environment-coupled, leaving the publish/auth boundary underprotected

Current State:
- The repo has pytest coverage for selected worker and provider paths, for example [tests/test_video_poller_batch_transition.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_video_poller_batch_transition.py#L1).
- Several “tests” are actually environment-coupled scripts that talk to a real Supabase project, for example [tests/test_video_submission_flow.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_video_submission_flow.py#L22).
- Existing publish coverage is not broad enough for a second provider with OAuth + upload + persistence.

Assessment:
- TikTok OAuth and draft upload should not be added without deterministic local tests that do not depend on a live Supabase environment.
- The current testing style is enough to add regression tests, but not enough to trust a token-handling integration by default.

Severity: IMPORTANT

Remediation:
- Add provider-local pytest coverage for:
  - OAuth state signing/verification
  - token encryption round-trip
  - callback token exchange normalization
  - upload-draft request shaping
  - error persistence on publish jobs
- Keep one sandbox smoke testscript separate from unit/integration coverage.

### MINOR: README and product language currently overstate TikTok support

Current State:
- README describes the app as a “Deterministic UGC video production system for TikTok and Instagram” in [README.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/README.md#L1).
- Implemented publishing is Meta-only at present.

Assessment:
- This is product/docs drift, not a runtime bug.
- It will become misleading once TikTok work begins unless the README is made explicit about sandbox scope and current provider coverage.

Severity: MINOR

Remediation:
- Update README after the TikTok slice lands to state:
  - Meta scheduling/publishing status
  - TikTok sandbox draft-upload status
  - direct-post not yet implemented

## TikTok Implementation Block

Budget: `{files: 10-14, LOC/file: <=350 target and <=1000 hard, deps: 0 default and max 1 if encryption cannot stay vanilla}`

### Capability Map

1. Accept TikTok app configuration for sandbox web OAuth.
2. Start and complete Login Kit OAuth with exact redirect URI matching and validated state.
3. Fetch and persist the connected TikTok account using `user.info.basic`.
4. Persist encrypted token material and token expiry metadata.
5. Upload a generated video as a TikTok draft using `video.upload`.
6. Persist durable publish job records and surface success/failure in S7 UI.
7. Keep Meta behavior unchanged.

### Dependency Map

- Config: [app/core/config.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/core/config.py)
- Publish slice routes and schemas:
  - [app/features/publish/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish/handlers.py)
  - [app/features/publish/schemas.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/publish/schemas.py)
- New TikTok helpers:
  - `app/features/publish/tiktok.py`
  - `app/features/publish/tiktok_crypto.py`
- UI: [templates/batches/detail.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/batches/detail.html)
- Storage reuse: [app/adapters/storage_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/storage_client.py)
- Schema: new Supabase migration(s)
- Tests: new provider-local pytest files

### Boundary Map

- OAuth boundary:
  - `GET /api/auth/tiktok/start`
  - `GET /api/auth/tiktok/callback`
- Account boundary:
  - `GET /api/tiktok/account`
- Draft-upload boundary:
  - `POST /api/tiktok/upload-draft`
- Job boundary:
  - `GET /api/tiktok/publish-jobs/:id`
- Persistence boundary:
  - `connected_accounts`
  - `media_assets`
  - `publish_jobs`
- UI boundary:
  - S7 publish plan provider section

### Implementation Pass

1. Config and contracts
- Extend settings and `.env.example` for complete TikTok sandbox config.
- Add provider-local schemas for account state and upload-draft requests.

2. Persistence
- Create `connected_accounts`, `media_assets`, and `publish_jobs`.
- Keep `posts.publish_results` and `posts.platform_ids` as the denormalized summary.

3. Security
- Add token encryption helper and exact redirect URI validation.
- Keep OAuth server-side only.

4. TikTok provider helper
- Build authorization URL.
- Exchange code for tokens.
- Fetch user profile with `user.info.basic`.
- Upload draft with `video.upload`.
- Persist request/response/error payloads with redaction.

5. UI
- Add TikTok connection and draft-upload status card to S7 as a sibling to Meta.
- Show:
  - not connected
  - connecting
  - connected
  - uploading
  - success
  - failure
  - reconnect required

6. Tests
- Add deterministic pytest coverage for provider-local helpers and route handlers.
- Add one sandbox smoke testscript for a single authorized TikTok account.

### Exact App Config Pack To Feed The Coding LLM

```text
Implement a TikTok web integration in sandbox mode using Login Kit and Content Posting API.

Requirements:
- Web app
- OAuth login with TikTok
- Scopes: user.info.basic and video.upload
- Use sandbox environment first
- Store connected TikTok account, tokens, open_id, and profile info
- Allow uploading a video as a TikTok draft
- Build backend endpoints for auth start, auth callback, upload draft, and account status
- Use server-side token exchange
- Encrypt tokens at rest
- Validate OAuth state
- Log and persist TikTok API responses and errors
- Build a minimal frontend flow with Connect TikTok, connected state, upload video, and success/failure state

App config:
- Product: Login Kit + Content Posting API
- Client key: [PASTE]
- Client secret: [PASTE]
- Redirect URI: [PASTE]
- App URL: [PASTE]
- Privacy Policy URL: [PASTE]
- Terms URL: [PASTE]
- Environment: sandbox
- Authorized sandbox account: [PASTE HANDLE]
- Scopes: user.info.basic, video.upload

Also generate:
- database schema
- env var template
- API client module
- auth service
- posting service
- migration files
- minimal frontend UI
- README with setup steps
```

### Pass / Fail Criteria

- OAuth start builds the exact TikTok sandbox authorization URL and validates state on callback.
- Connected TikTok account data persists with encrypted token fields.
- Draft upload creates a durable publish job and stores TikTok request/response snapshots.
- S7 UI surfaces connect, upload, success, failure, and reconnect-required states.
- Existing Meta routes and UI continue to work unchanged.

## Testscripts

### `tiktok_oauth_local`

- Objective: validate OAuth URL generation, state verification, and callback persistence logic locally
- Prerequisites: app config set, mocked TikTok HTTP responses
- Setup:
  - configure sandbox env vars
  - seed one sandbox operator/account owner if needed
- Run:
  - execute provider-local pytest coverage for start and callback handlers
- Expected observations:
  - exact redirect URI used
  - invalid state rejected
  - account row created/updated with encrypted token fields

### `tiktok_draft_upload_local`

- Objective: validate media selection, draft upload request shaping, and publish job persistence
- Prerequisites: one connected TikTok account fixture, one generated media asset fixture
- Setup:
  - create connected account fixture
  - create media asset fixture pointing to stored R2 object or staged file
- Run:
  - execute provider-local pytest coverage for `POST /api/tiktok/upload-draft`
- Expected observations:
  - job row created
  - request/response payloads persisted
  - success and failure states mapped predictably

### `tiktok_sandbox_smoke`

- Objective: validate one end-to-end sandbox flow with the authorized TikTok test account
- Prerequisites: real sandbox app config and one test video asset
- Setup:
  - connect the sandbox TikTok account
  - choose one valid MP4 asset already stored by the app
- Run:
  - start OAuth
  - complete callback
  - submit draft upload
- Expected observations:
  - account becomes connected
  - publish job reaches submitted/successful draft state
  - UI reflects job status without exposing tokens
- Artifact capture:
  - structured logs
  - DB snapshots for `connected_accounts` and `publish_jobs`
  - screenshot of the S7 TikTok state

## Handoff

`agents/canon.md` and `agents/review.md` have been updated for this audit.

Switch to `EYE` and use `bridgecode/plan-code-debug.md` to execute the TikTok implementation block and testscripts.

If after trying to debug for two turns the tests still fail, generate [agents/testscripts/failure_report.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/agents/testscripts/failure_report.md) with the failing script id, environment matrix, artifacts, suspected boundary, attempted fixes, and next targeted observations.
