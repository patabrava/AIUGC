# VEO Requests

## Current Finding

The live database is not consistently storing every Veo request in `video_prompt_audit`.

Observed on April 2, 2026:
- `video_prompt_audit` contains `3` Veo audit rows for today
- `posts.video_metadata.operation_ids` shows `4` Veo operations for the same post
- Missing audit row is the base Veo submission
- Present audit rows are the `3` extension hops

Affected post:
- `383ee5e2-6333-4aab-b28f-be72987766ea`

## Root Cause

`record_prompt_audit(...)` conditionally adds `seed` to the insert payload:
- [prompt_audit.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/prompt_audit.py#L14)

Base Veo submissions pass `seed` into that function:
- single-post path: [handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py#L461)
- batch path: [handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py#L860)

The live Supabase table `video_prompt_audit` does **not** have a `seed` column.

Confirmed by direct REST query:
- selecting `seed` from `video_prompt_audit` returns `400`
- error: `column video_prompt_audit.seed does not exist`

Because `record_prompt_audit(...)` is non-blocking, the insert fails silently for seeded base submissions and the Veo request still goes through.

## Why Extension Hops Still Appear

Extension hop audit inserts currently succeed because the extension audit path does not persist `seed` into `video_prompt_audit`.

That is why today we see:
- `3` extension audit rows
- `0` base audit rows
- but `4` operation IDs in the post metadata

## Recommended Fix

Choose one of these and apply it end-to-end:

### Option A
Add a nullable `seed` column to `video_prompt_audit`.

This is the cleanest fix if we want seed auditability in the database.

Required follow-up:
- add migration for `video_prompt_audit.seed`
- verify inserts succeed for both base and extension requests
- optionally start persisting extension seeds too for symmetry

### Option B
Stop inserting `seed` into `video_prompt_audit` until the schema supports it.

This is the fastest hotfix if immediate audit completeness matters more than seed persistence.

Required follow-up:
- keep `seed` in application logs and in `posts.video_metadata`
- remove `seed` from `record_prompt_audit(...)` row construction or gate it behind schema support

## Validation After Fix

Submit one new Veo extended request and confirm:
- base request creates one `video_prompt_audit` row
- each extension hop creates one `video_prompt_audit` row
- total audit rows equals `len(video_metadata.operation_ids)`

For an efficient `32s` run, expected total:
- `4` rows = `1` base + `3` extensions

For an efficient `16s` run, expected total:
- `2` rows = `1` base + `1` extension

## Useful Query Reference

Berlin-day count for April 2, 2026:
- start boundary in UTC: `2026-04-01T22:00:00+00:00`

Audit count query logic:
- table: `video_prompt_audit`
- filter: `provider = 'veo_3_1'`
- filter: `created_at >= 2026-04-01T22:00:00+00:00`

Cross-check source of truth:
- table: `posts`
- field: `video_metadata.operation_ids`

## Suggested Next Turn

Implement the audit fix first, then rerun one live Veo request and compare:
- number of `video_prompt_audit` rows
- number of `operation_ids`
- whether base and extension prompts are all present
