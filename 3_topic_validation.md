# Mixed Batch Validation Run Log

Date: 2026-04-02

## What Was Tested

Live UI generation for a mixed batch with:

- 2 value scripts
- 3 lifestyle scripts
- 3 product scripts

Tested through the browser at `http://localhost:8000/batches` using the Playwright CLI flow.

## Outcome

The batch now reaches `S2_SEEDED` successfully in the UI.

Observed batch:

- Batch name: `playwright mixed 2-3-3 live rerun`
- Batch ID: `8c1f5c0f-9e0b-44ac-8aad-e299be24409a`
- Final visible state: `Ready for Script Approval`

## What Changed

### 1. Lifestyle validation routing

The main fix was in `app/features/topics/topic_validation.py`.

I changed pre-persistence validation so `post_type="lifestyle"` uses Prompt 2 word bounds instead of Prompt 1 bounds.

Why this mattered:

- Lifestyle scripts were being generated correctly
- But 32s lifestyle scripts were failing the persistence gate because they were checked against the stricter value-script envelope
- That made the batch fail after generation even though the content itself was valid

### 2. Regression coverage

I added a regression test in `tests/test_topic_quality_gate.py` to ensure lifestyle scripts are validated with Prompt 2 bounds.

This is meant to prevent the same mixed-batch failure from reappearing later.

### 3. Repo-level prevention note

I added a compact prevention rule to `AGENTS.md` so the mixed-batch validation boundary is documented for future work.

## Verification Results

The live batch completed, but one issue remains:

- Requested mix: `2 value + 3 lifestyle + 3 product = 8`
- Persisted result: `11 total posts`
- Observed split: `4 value`, `4 product`, `3 lifestyle`

So generation is working, but the batch seeding/counting path is still over-creating posts.

## Likely Follow-Up

Trace the batch seeding pipeline to find why the persisted post count is higher than the requested counts.
