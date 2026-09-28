# Concurrent generation — production validation, updated 28 September 2026

BRIDGECODE_ROUTE: live parallel-generation verification → [GENERAL, RESEARCH, EYE] | MODE: Mixed | WHY: Production concurrency, provider capacity failures, browser recovery and delivered media require separate evidence.

The changes are pushed to `main` and deployed. The final code revision is `5b91a18713c9aced493cea4f3f8adadd1e1e58d8`; [deployment](https://github.com/patabrava/AIUGC/actions/runs/36416915544) succeeded. Tests confirmed two image jobs processing while three distinct accepted Veo operations were active, with no duplicate video submissions. QA content remains unpublished.

## What changed

`f74ad00` removed batch-wide image admission and browser blocking while retaining one active operation per post and two global image claims. Accepted videos poll independently of the paced paid-submission gate; only explicit 429 rejections can retry submission. Ambiguous submissions remain fenced.

`90355f8` fixes the reproduced image-capacity failure: use the existing three-render budget instead of stopping at two, honor Retry-After, increase shared cooldown after repeated 429s, and prevent a successful sibling from prematurely reopening capacity. No queue/schema change or extra renderer retry layer was introduced.

`ec9e07f` fixes the live-browser case where a card opened mid-generation continued saying “Generating” after completion. `acf6d20` adds bounded transient-service retries to scene identity evaluation using the same checksummed image bytes. It preserves the identity threshold, evaluator contract, lease guard, absolute eight-minute deadline and 40-second upload/finalization reserve. A failed identity finding is still blocking.

`5b91a18` repairs a database recovery gap observed during the soak: video workers now replace the shared Supabase transport after a request error before the next polling tick. The failed claim is not replayed because it may have committed before its acknowledgement was lost. Two regression tests prove connection replacement and the no-replay boundary; six live claims in two concurrent slots against a verified nonexistent run returned empty without claiming production work or calling a media provider.

## Live image matrix

Ten fresh batches covered sizes 1–7 with the same active actor reference pair, real prompt generation, real 2K Gemini image rendering, independent identity evaluation and durable upload. Every sibling was admitted before waiting for the batch. All 31 jobs completed, using 43 authorized render attempts. This is real provider evidence, separate from the PostgreSQL integration fixtures.

| Batch | Images | Rendered/uploaded | Render attempts | Persisted identity passes | Batch wall time | Code |
|---|---:|---:|---:|---:|---:|---|
| 1 | 1 | 1/1 | 1 | 1/1 | 32.84s | `90355f8` |
| 2 | 2 | 2/2 | 3 | 2/2 | 68.06s | `90355f8` |
| 3 | 3 | 3/3 | 4 | 3/3 | 143.98s | `90355f8` |
| 4 | 4 | 4/4 | 6 | 2/4 | 381.37s | `90355f8` |
| 5 | 7 | 7/7 | 9 | 6/7 | 456.66s | `90355f8` |
| 6 | 6 | 6/6 | 9 | 6/6 | 381.88s | `acf6d20` |
| 7 | 5 | 5/5 | 7 | 5/5 | 245.17s | `acf6d20` |
| 8 | 1 | 1/1 | 1 | 1/1 | 28.14s | `acf6d20` |
| 9 | 1 | 1/1 | 2 | 1/1 | 183.63s | `acf6d20` |
| 10 | 1 | 1/1 | 1 | 1/1 | 32.46s | `acf6d20` |

The first five batches used the image-capacity fix. Their identity-service failures exposed the next defect; the final five batches ran after the identity retry and UI fixes were deployed. Across the matrix, processing median/p95 were 66.30s / 214.76s, maximum 235.86s. Submission-to-completion median/p95, including queue waiting, were 111.52s / 412.07s. Peak observed image claims were two.

Logs recorded 12 explicit image-render 429s during the matrix and 2 text-evaluator 429s. One image in the six-image batch recovered on its third authorized attempt after two real 429 responses, then passed identity evaluation. The first five batches retained three failed evaluator records. Read-only checks of those exact persisted bytes after the final fix passed two and rejected one for smoothing/beautification; they made no image-render calls or database writes. Existing failures were not relabeled as passed. The final five batches yielded 14/14 persisted identity passes.

The original pre-fix item that exhausted two image attempts was also retried through a fresh queue operation after the fixes: it rendered once, uploaded and passed identity evaluation in 30.55 seconds. Its earlier failed operation remains in the audit trail. Across today's baseline, matrix and recovery work, 45 image operations authorized 60 render attempts; the matrix above is the fresh, unreplaced subset used for comparison.

## Measured comparison

| Workload | Before | After | Observed change |
|---|---:|---:|---:|
| Two images, 22 September | 70.18s serial service sum | 37.24s parallel batch | 46.9% less waiting |
| Two 8-second source videos, 22 September | 301.33s | 259.31s | 13.9% faster batch |
| Three images, live browser on 28 September | — | 65.33s | All three completed first attempt |
| Three videos with concurrent image work, 28 September | — | 391.02s | All three delivered |

The before-image batch contained a 60-second operator pause. Its actual wall time was 130.18 seconds; the comparison excludes that pause by summing each job's submission-to-completion duration. The paired benchmark uses the same models, actor, scripts and resolutions but is a small observational sample, not a guaranteed latency improvement. The seven-image capacity-stressed batch took 456.66 seconds even with successful recovery.

All seven source-video takes across both dates had distinct provider operations and exactly one accepted attempt each. The September 28 video burst ran on `7d46cac`, which already included the video concurrency change; subsequent patches changed scene-image recovery, card feedback and lease-transport recovery. The final regression suite rechecked the video paths, and the last backend change received the live database probe described above. The three new videos were generated through the real Chrome approval flow at a combined source-video price of $9.60. All three final downloads matched their stored SHA-256 checksums and passed full FFmpeg decoding: 1080×1920 H.264, 48 kHz AAC, final adaptive durations 6.54–6.71 seconds. Two passed automated QA; one retains a final-transcript review advisory. Source takes were eight seconds each; final adaptive deliveries were shorter. No delivery or publishing approval was granted.

[Inspect the three live deliveries](https://lippelift.xyz/batches/35a5bc97-cdcc-4bf8-872c-5d99a64861f4). The earlier before/after pair each included one visual-QA advisory, so successful delivery is not equivalent to unconditional quality approval.

## Validation and practical limits

437 focused tests passed across provider, image/identity worker, video worker, handlers, UI, queries, transport recovery and readiness suites. The earlier release also passed two real PostgreSQL integration tests covering migration cutover, crash reclaim, idempotency, global claims and deadlines, including ten concurrently enqueued variable batches totaling 42 fixture jobs. Those fixtures validate database behavior and do not count as real image generation.

Computer-use verification used native Chrome on Scene, Plan, Produce and Delivery: submitted three image cards together, compared references, approved their images, built plans, approved three paid videos together, and played a captioned delivery. At 390×844, cards, controls, navigation and video fit without horizontal overflow. Keyboard focus and the reference dialog received smoke checks. The final deployed six-image page showed completed Regenerate controls alongside generating/queued siblings. No production JavaScript errors were observed; the existing Tailwind CDN production warning remains. A separate local fixture verified ready/retry labels with a sibling still active. Test tabs were closed and mobile emulation disabled afterward.

The final web, image-worker and video-worker containers report the deployed revision; the application health check reports healthy database and image-worker readiness. Final database checks found no active image jobs or video leases, 31 distinct matrix posts with exactly one candidate each, and zero PostgreSQL notification-queue usage. No local development queue consumer was connected to production. Intermittent upstream database disconnects were observed during the soak; the final patch improves recovery without claiming to eliminate those external failures. The session's conservative cost reserve was approximately $18.30: $9.60 source videos, $0.15 per authorized image attempt and $1 for evaluation overhead, less $1.30 of image-output reserve released for 13 confirmed 429 responses with no image output. Each rejected attempt still retains a $0.05 input/prompt allowance. This reconciliation uses [Google’s image-output pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing). This is a test allowance estimate, not a reconciled provider invoice; September 22 spending is separate.

Google still returned real capacity errors. The application queues and recovers from bounded transient failures, but this test does not establish unlimited concurrency or zero future 429s. Sustained provider exhaustion can still end in a retryable failure, and generated images/videos can still fail quality gates. [Google's 429 guidance](https://cloud.google.com/vertex-ai/generative-ai/docs/error-code-429) explains shared capacity, backoff and traffic smoothing.

Redacted timestamps, job outcomes, source-operation invariants, delivery checks and evaluator probes are retained in [the evidence JSON](concurrent-generation-evidence-2026-09-28.json). Historical image rows reference the current persisted run's identity result; the fresh matrix has one job per post and no replacements.
