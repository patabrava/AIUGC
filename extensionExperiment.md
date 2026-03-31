# Quota Guard Plan

## Summary
- Action mode: planning
- Scope: Veo quota guard before any extension-duration experiment
- Budget: `{files: 6, LOC/file: <=150, deps: 0}`
- Default: ship quota admission control first; defer the `8s`-base extension experiment until the guard is live and verified

## Phase Zero — Context

### Environment Matrix
- Repo: `AIUGC`
- OS/Arch: `Darwin arm64`
- Python: `3.9.6`
- Commit: `f1afd05`
- Runtime pattern:
  - web app submits Veo requests in `/app/features/videos/handlers.py`
  - poller submits extension hops in `/workers/video_poller.py`
  - duration math lives in `/app/core/video_profiles.py`
  - settings live in `/app/core/config.py`
  - prompt audit table exists in `supabase/migrations/019_create_video_prompt_audit.sql`

### Known Provider Constraints
- Current Veo tier cost in-app:
  - `8s => 1` request
  - `16s => 3` requests
  - `32s => 5` requests
- Google quota is enforced at the project level, not per post.
- The current failure mode is partial spend:
  - base and early extensions can succeed
  - final extension can fail on `429`
  - app marks the whole post failed
  - operator sees no final video even though quota and money were already spent

### Non-Functional Requirements
- Never submit a Veo chain if the app already knows the chain cannot finish inside configured quota.
- Keep the existing video generation contract stable for now.
- No new third-party dependencies.
- Durable across app restarts and worker restarts.
- Race-safe across multiple app instances and workers.
- Operator-visible failure reason must distinguish:
  - `blocked_before_submit`
  - `provider_quota_exhausted_after_submit`
- Must protect both:
  - daily budget
  - per-minute burst budget

### Constraints / Assumptions
- The app cannot rely on a documented Google API for live remaining quota.
- Exact local accounting is only fully trustworthy if this Google project is dedicated to this app.
- If outside usage exists, the guard still needs a freeze path after an unexpected provider `429`.

## Decision

Implement a durable quota reservation slice for Veo.

The quota guard will reserve the full chain budget before the first Veo submit, consume units as each Veo operation is accepted, release unused units on early termination, and freeze further Veo submissions when Google returns an unexpected `429` despite local availability.

This is the minimum design that prevents the exact partial-spend failure already observed.

## Proposed Slice

### New Data Model
Create one new table plus one database function.

#### Table: `video_provider_quota_reservations`
- `id uuid primary key`
- `provider text not null`
- `post_id uuid references public.posts(id)`
- `batch_id uuid`
- `reservation_key text not null unique`
- `quota_day_pt date not null`
- `reserved_units integer not null`
- `consumed_units integer not null default 0`
- `released_units integer not null default 0`
- `status text not null`
- `freeze_reason text`
- `provider_last_error_code text`
- `provider_last_error_message text`
- `created_at timestamptz not null default now()`
- `updated_at timestamptz not null default now()`

#### RPC / SQL Function
`reserve_video_provider_quota(...)`

Responsibility:
- atomically evaluate current reserved+consumed usage
- enforce configured day and minute ceilings
- create reservation row on success
- return structured decision payload on success/failure

Reason:
- a Python-side count-then-insert is race-prone
- the gate must be atomic across concurrent app and worker processes

### Configuration Additions
Add settings in `/app/core/config.py`:
- `veo_daily_generation_limit`
- `veo_minute_generation_limit`
- `veo_quota_soft_buffer`
- `veo_quota_freeze_on_unexpected_429`
- `veo_quota_project_scope` (label only, for logs)

Default values for current plan:
- daily: `10`
- minute: `2`
- buffer: `0` initially

### Runtime Contract

#### Chain Cost Function
Use the existing profile math in `/app/core/video_profiles.py`.

Derived cost:
- `cost_units = 1 + veo_extension_hops`

Examples:
- `8s => 1`
- `16s => 3`
- `32s => 5`

#### Reservation Lifecycle
1. Before first Veo submission:
   - compute chain cost
   - reserve full chain cost
2. On each successful Veo submit:
   - increment `consumed_units` by `1`
3. On chain completion:
   - mark reservation `completed`
4. On chain failure before full usage:
   - release `reserved_units - consumed_units`
5. On unexpected Google `429`:
   - mark reservation failed
   - release unused units
   - optionally activate freeze for the current quota window

## Phase Breakdown

### P1: Durable Quota Ledger
**Objective:** add the DB contract for quota reservations and freeze-aware state.

**Deliverable Scope**
- Data:
  - new quota reservation table
  - new atomic reservation function
- Validation:
  - enforce non-negative counters
  - restrict valid status values
- Observability:
  - created/updated timestamps

**Files**
- new `supabase/migrations/021_add_video_provider_quota_reservations.sql`

**Testscript**
- **ID:** `TS-P1-quota-schema`
- **Objective:** prove reservation rows can be created and counters remain valid
- **Run:** apply migration on local/dev Supabase and execute a single reservation RPC manually
- **Expected Observations:**
  - reservation row exists
  - invalid negative counters are rejected
  - duplicate reservation keys are rejected

**Pass Gate**
- PASS if the schema supports atomic reservation bookkeeping
- FAIL if reservation state can drift or duplicate

### P2: Admission Control on Initial Submit
**Objective:** block unsafe Veo chains before any paid request is submitted.

**Deliverable Scope**
- API:
  - initial post submit path consults quota guard
  - batch submit path consults quota guard per post or for the full batch plan
- Validation/Errors:
  - fail fast with `RATE_LIMIT`
  - message must include:
    - requested chain cost
    - available quota
    - zero requests were submitted
- Observability:
  - structured log for `quota_guard_blocked_before_submit`

**Files**
- new `app/features/videos/quota_guard.py`
- `/app/features/videos/handlers.py`
- `/app/core/config.py`

**Testscript**
- **ID:** `TS-P2-preflight-block`
- **Objective:** prove a request is blocked before submit when quota is insufficient
- **Run:** simulate day budget nearly exhausted, then submit a `16s` request
- **Expected Observations:**
  - Google submit function is not called
  - response is structured `429 RATE_LIMIT`
  - message clearly states no request was sent

**Pass Gate**
- PASS if preflight blocks before any Veo call
- FAIL if the handler still leaks paid submits

### P3: Poller Consumption, Release, and Freeze
**Objective:** keep reservation state accurate as the chain progresses and stop repeat failures after an unexpected provider `429`.

**Deliverable Scope**
- Worker:
  - consume reserved unit after each successful extension submit
  - release unused units on failure
  - freeze Veo submissions on unexpected provider quota rejection
- Observability:
  - structured logs:
    - `quota_unit_consumed`
    - `quota_units_released`
    - `quota_guard_frozen_after_provider_429`

**Files**
- `/workers/video_poller.py`
- `app/features/videos/quota_guard.py`

**Testscript**
- **ID:** `TS-P3-chain-accounting`
- **Objective:** prove a partial chain releases unused quota and freezes correctly on unexpected `429`
- **Run:** simulate successful base + successful extension + failing final extension
- **Expected Observations:**
  - consumed count matches accepted provider operations
  - unused reserved units are released
  - future submits are blocked by freeze until reset/clear condition

**Pass Gate**
- PASS if reservation state matches real accepted submits
- FAIL if the ledger still overcounts or undercounts after a failed chain

### P4: Operator Feedback and Batch Safety
**Objective:** make quota outcomes visible and predictable to the operator.

**Deliverable Scope**
- API/UI behavior:
  - blocked requests are surfaced as quota-preflight failures, not generic generation failures
  - batch submissions should default to all-or-nothing admission
- Observability:
  - include chain cost and remaining quota in response details

**Files**
- `/app/features/videos/handlers.py`
- tests for handler responses

**Testscript**
- **ID:** `TS-P4-operator-feedback`
- **Objective:** verify operator-facing responses distinguish blocked-before-submit from provider failure after submit
- **Expected Observations:**
  - blocked path says no Veo request was sent
  - provider failure path says partial chain may already exist

**Pass Gate**
- PASS if operator messaging is actionable
- FAIL if both cases still look identical in the UI/API

## Implementation Rules

### Guard Rules
- Never derive preflight availability from `posts` alone.
- Never derive preflight availability from `video_prompt_audit` alone.
- Always reserve the full chain before the first Veo submit.
- Always use atomic DB logic for reservation.
- Always release unused units on early failure.
- Always distinguish:
  - blocked before submit
  - failed after partial spend

### Freeze Rules
- If Google returns `429` when the local guard believed quota was available:
  - treat that as provider drift
  - freeze new Veo submits
  - require either:
    - next quota window, or
    - explicit operator/manual reset path later

### Batch Rules
- Batch submit should default to all-or-nothing admission.
- If a batch cannot fit inside current quota, block the batch before any post starts.

## Tests to Add
- unit tests for chain-cost math
- unit tests for guard decision payloads
- handler tests for blocked-before-submit behavior
- poller tests for consume/release on extension chains
- regression test for the exact observed failure:
  - `16s`
  - `3` total cost units
  - `2` accepted submits
  - final hop `429`
  - no further submits allowed while frozen

## Risks
- If the Google project is also used manually outside the app, local counts can drift.
- Without a freeze path, that drift will still produce unexpected `429`s.
- Without all-or-nothing batch admission, partial batch spend will continue.

## Non-Goals for This Slice
- No change to current Veo duration routing.
- No change to current prompt contract.
- No extension optimization yet.
- No partial video salvage yet.

## Deferred Experiment
After quota guard ships and is verified, revisit the `8s`-base extension experiment:
- evaluate whether `16` can become an efficient `~15s` route in `2` requests
- evaluate whether `32` can become an efficient `~29s` route in `4` requests
- only compare after the quota guard is stable, so cost savings and reliability can be measured separately

## Recommendation
Implement P1 through P3 as one vertical slice, then verify with the testscripts above before touching any duration optimization.
