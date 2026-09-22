# Concurrent image and video generation — release check

BRIDGECODE_ROUTE: generation concurrency reliability → [GENERAL, RESEARCH, LIRA, EYE] | MODE: Mixed | WHY: Queue admission, worker scheduling and provider throttling jointly determine concurrency.

Status: patch prepared and verified locally; production unchanged; paid live validation pending.

## Evidence and changes

Production revision: `4f6cf55195a823a3582852fab65045ab1c7e1d22`. Both worker services are configured for two slots; image rendering permits two calls with a one-second start interval. Since September 1, all 32 inspected image jobs reached queue completion; 24 called the renderer, with median processing time 39.6 seconds and maximum 74.8 seconds. Queue completion does not imply identity approval. One of those render jobs required a second attempt. Recent image-worker logs also contain database transition timeouts; this patch does not establish or resolve their root cause.

The patch admits independent posts from one batch while preserving per-post idempotency, the global two-image claim cap, paid-attempt limits and lease fencing. Queue waiting is bounded at one hour; the eight-minute execution deadline starts on first claim and does not reset on reclaim. Only an active card's generate button is disabled; all pending cards stay mounted until the group settles. Queue status gives no invented completion estimate.

Accepted video operations poll outside the paid submission gate. Veo submissions are paced at two seconds within each process and retry explicit HTTP 429 responses at most four times with jitter and Retry-After handling. Retry waits are bounded; excessive server cooldowns fail without another submission. Transport errors, 5xx and missing operation IDs retain existing ambiguous-submission protection. This is process-local pacing, not a project-wide quota reservation.

## Completed checks

- 355 tests passed across `test_vertex_ai_client`, `test_semantic_video_worker`, `test_semantic_video_ui`, `test_semantic_video_handlers`, `test_semantic_scene_image_worker` and `test_semantic_scene_image_health`.
- Two real PostgreSQL integration tests passed on an isolated, network-disabled PostgreSQL 14 container. The suite includes ten concurrently enqueued batches sized 3, 4, 5, 6, 7, 1, 2, 3, 7 and 4; global claim capacity, idempotency, deadline refresh on first claim, immutable reclaim deadline, and existing crash/cutover regressions.
- Computer-use browser check: live delivery page and patched local Scene page. With local generation responses that cannot buy provider work, queued two sibling cards using mouse and keyboard, verified both remained pending and the third stayed usable. Reference comparison opened with focus on Close. No JavaScript errors or desktop horizontal overflow; existing Tailwind CDN warning remains. Mobile and paid end-to-end behavior remain unverified.
- Scoped release patch applies cleanly to the deployed revision; `git diff --check` passes. No unrelated checkout changes included in that patch.

## Release gate

Use `concurrent-generation-2026-09-22.patch` against a checkout of the deployed revision, or integrate the equivalent changes into a reviewed release. Apply the new migration before enabling sibling controls. Preserve the existing v3 claim wrapper and exactly one production image/video worker service. Verify schema reload, PostgreSQL notification health and application/worker health before release success.

Obtain a live-test spending cap before generating test assets. Use distinct test batches with approved scripts and immutable actor references, run ten variable image batches plus overlapping short video runs, and account for prompt, render, evaluation and video calls including retries. Stop before exceeding the cap. Record actual overlap, queue/processing latency, 429 recovery, terminal outcomes and duplicate-operation checks. Database-only stress and simulated browser responses do not prove provider throughput.

On an application rollback, leave the additive queue behavior in place until all sibling jobs drain; do not recreate the one-active-batch unique index while sibling jobs are active.

Google's capacity guidance: https://cloud.google.com/vertex-ai/generative-ai/docs/error-code-429
