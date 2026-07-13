# Semantic UGC Production Mode Design

Date: 2026-07-13
Status: Approved for implementation

## Goal

Promote the proven duration-driven Veo 3.1 semantic UGC pilot into a production batch mode named `semantic_ugc`. The mode accepts an integer target duration from 8 through 60 seconds, produces the fewest independent Veo takes allowed by the provider's eight-second ceiling, preserves actor and location consistency from one immutable approved reference package, pauses before every unapproved paid operation, survives worker restarts, and publishes one captioned final video per post.

## Scope boundary

This design applies only to the new Semantic UGC mode. Existing `automated`, `manual`, `manual_character_consistency`, `character_consistency`, `character_consistency_mid`, and `character_consistency_light` behavior remains unchanged, including their existing duration tiers and routing.

Semantic UGC must not be added to `CHARACTER_CONSISTENCY_MODES`. That legacy predicate implies LoRA readiness and Magnific-backed scene-reference behavior. Semantic UGC has its own readiness predicate based on an approved script, exactly two ordered actor references, one actor-free location reference, and one approved Gemini master frame.

The first implementation supports 8 through 60 integer seconds. The application maximum is configurable. Database constraints enforce only the structural minimum so raising the maximum later does not require a schema migration.

## Canonical batch contract

A Semantic UGC batch stores:

- `creation_mode = 'semantic_ugc'`;
- `video_pipeline_route = 'semantic_ugc'`;
- `target_duration_seconds`, required and in the configured application range;
- `target_length_tier = NULL` to prevent accidental entry into legacy duration routing;
- the actor selected for the batch;
- the normal post counts and topic inputs used by automated batch creation.

Legacy modes continue to require and persist `target_length_tier`. Cross-field validation rejects Semantic UGC rows without `target_duration_seconds` and rejects legacy rows that try to use the new field as their duration authority.

The batch form adds `Semantic UGC - Veo 3.1` to the mode selector. Selecting it replaces the fixed 8/16/32 dropdown with a numeric seconds input and convenient 8, 16, 32, and 50 presets. The backend contract accepts every integer in range rather than enumerating the presets.

## Semantic duration contract

One immutable `SemanticDurationContract` is shared by batch validation, script generation, script validation, shot planning, cost estimation, paid approval, and worker execution.

For requested duration `D`:

```text
delivery_min_seconds = D - 1.5
delivery_max_seconds = D + 0.5
minimum_take_count = ceil(max(4, delivery_min_seconds) / 8)
safe_words_per_take = 18

minimum_words = max(
  14,
  ceil(2.0 * delivery_min_seconds),
  safe_words_per_take * (minimum_take_count - 1) + 1
)

maximum_words = min(
  safe_words_per_take * minimum_take_count,
  floor(2.4 * (D - 0.5))
)

semantic_block_count = minimum_take_count through minimum_take_count * 2
```

Representative contracts are:

| Target | Minimum takes | Word range |
|---:|---:|---:|
| 8s | 1 | 14-18 |
| 16s | 2 | 29-36 |
| 32s | 4 | 61-72 |
| 50s | 7 | 109-118 |
| 60s | 8 | 127-142 |

The contract fails closed when the input is non-integral, outside the configured application range, or produces an impossible word envelope. It exposes a canonical JSON representation and SHA-256 hash used by approvals and worker idempotency.

## Topic research and script generation

Topic research remains duration-neutral. Semantic UGC may reuse an approved topic family or research dossier without requiring an audited script-bank row at the exact requested second. This avoids an unbounded topic-bank matrix for every duration.

Script generation occurs just in time from three generic semantic prompt templates, one per post family. Each render receives the requested duration, delivery envelope, word bounds, minimum take count, semantic-block guidance, language, actor context, facts, and CTA constraints. Existing duration-specific 8/16/32 prompt files remain available exclusively to legacy modes.

Every Semantic UGC script must:

- fit the contract word envelope;
- form complete semantic beats through `plan_editorial_beats`;
- require exactly the contract's minimum take count unless a recorded semantic-boundary exception makes one extra take unavoidable;
- contain distinct complete sentences rather than repeated padding;
- preserve the normal research citations and audit provenance;
- reach `script_review_status = 'approved'` before reference or video planning.

Provider-failure fallback copy is built from distinct fact-aware semantic blocks and must pass the same contract. Unknown durations never fall back to an eight-second word envelope.

## Reference and approval workflow

The visual authority is an immutable reference package:

1. exactly two ordered actor identity images;
2. the actor's full long character description;
3. one actor-free location generated through Nano Banana and stored durably;
4. the supplied image prompt-writer system instruction;
5. three 9:16 master candidates generated by `gemini-3.1-flash-image`;
6. one explicitly approved master candidate.

Candidate generation and Veo generation remain separate operations. A missing approved candidate, changed reference hash, changed script, or changed duration contract invalidates the shot plan and blocks paid submission.

The approved master is snapshotted by storage URI, byte length, MIME type, and SHA-256. Deterministic crops of that master create the per-take shot deck. Generated Veo frames never become identity authority for later takes.

## Free planning and paid approval

Planning is a free, persisted operation. It compiles the approved script into semantic beats, assigns deterministic master-derived shot variants, builds every Veo request, and calculates:

- take count and provider duration for every take;
- total billable provider seconds;
- quota units, one per initial Veo submission;
- configured model and resolution;
- price per provider second;
- maximum estimated dollar cost;
- the immutable contract hash.

The UI shows those values before payment. Approval is append-only and applies only to the exact contract hash. The worker rejects stale approvals.

An initial approval covers only the planned first attempt for each take. A retry creates a new request contract and requires a new approval showing the additional quota unit and dollar cost. Retries never run automatically.

## Paid-operation safety

Every provider call follows a fail-closed two-phase record:

1. persist submission intent and its idempotency/request hash;
2. call Vertex once;
3. immediately persist the accepted operation identifier and provider model.

An ambiguous transport result becomes `submission_unknown`. Normal resume logic may not submit that take again. An operator must reconcile it as accepted with an operation ID or prove it was not accepted.

The worker enforces all of the following before submission:

- approved contract hash matches the current run;
- approved take index matches the pending request;
- reservation exists for exactly one quota unit;
- cumulative approved estimated cost remains inside the configured run budget;
- accepted or unknown operations are never resubmitted;
- per-run and global in-flight limits are respected.

## Production persistence

Add normalized persistence rather than storing the orchestration manifest in `posts.video_metadata`.

### `semantic_video_runs`

Stores the post and batch identifiers, requested duration, canonical duration contract, script snapshot and hash, actor/reference/master snapshots and hashes, current stage, plan hash, model, resolution, estimated cost, artifact prefix, lease owner/expiry, failure envelope, final URLs/checksums, timestamps, and optimistic revision.

Only one nonterminal run may exist per post.

### `semantic_video_takes`

Stores one ordered row per beat and attempt: beat text, word count, estimated speech duration, provider duration, shot transform and hash, prompt and negative-prompt hashes, model, seed, request hash, submission state, intent timestamp, operation identifier, raw artifact URI/checksum, transcript result, identity QA, voice QA contribution, retry guidance, and timestamps.

Attempt history is append-only. A retry never overwrites evidence from the failed attempt.

### `semantic_video_approvals`

Stores append-only reference, initial-plan, and retry approvals with run revision, contract hash, approved take indexes, approved provider seconds, quota units, estimated dollars, approval actor, reason, and timestamp.

Object storage is artifact truth for masters, crops, raw takes, contact sheets, audio clips, stitch outputs, captions, and final video. Database rows store URIs plus hashes. Local worker paths are disposable caches.

## Worker and state machine

A dedicated `semantic_video_worker.py` processes one idempotent stage or provider wave per cycle under a database lease. It does not block the existing video poller.

Detailed run stages are:

- `awaiting_script_approval`;
- `awaiting_reference_approval`;
- `awaiting_paid_approval`;
- `generating`;
- `transcript_qa`;
- `identity_qa`;
- `voice_qa`;
- `retry_approval_required`;
- `acoustic_qa`;
- `composing`;
- `uploading`;
- `completed`;
- `failed`.

`posts.video_status` remains the coarse compatibility projection. On success, the already-captioned semantic artifact writes directly to `posts.video_url`, `video_metadata.caption_video_url`, and `video_status = 'caption_completed'`. The normal caption worker must not process it again.

## Quality gates and composition

The existing duration-driven planner, transcript validator, master-derived shot deck, visual QA, native-voice QA, acoustic seam planner, caption composer, and verified upload are reused behind persistence-friendly stage functions.

All gates are cardinality-independent:

- transcript QA covers every take and the final ordered script;
- identity QA compares every take with the approved actor/master package;
- voice QA compares two or more takes and reports single-take runs as `not_applicable` with a passing result;
- acoustic QA evaluates every adjacent seam and reports single-take runs as `not_applicable` with a passing result;
- a failure identifies exact take indexes and never triggers an automatic paid retry;
- edit points follow transcript and semantic boundaries, avoid mid-breath cuts, preserve room tone, and use the proven native-audio micro-crossfade plan;
- captions are generated only after the final transcript passes.

## HTTP and UI boundaries

Semantic UGC uses explicit endpoints rather than the legacy generate-all endpoint:

- create or refresh a free run plan;
- list master candidates and approve one;
- fetch run/take progress;
- approve the initial paid plan;
- approve selected retry requests;
- cancel a nonterminal run.

Batch detail shows requested final duration, delivery envelope, approved master, take count, billable provider seconds, exact estimate, current stage, generated/verified counts, retry targets, and incremental retry cost. Static legacy duration text and fixed two-to-three-minute estimates are not shown for Semantic UGC.

## Migration and compatibility

The migration:

- extends the batch creation-mode and pipeline-route checks;
- adds nullable `target_duration_seconds` with a structural minimum;
- makes legacy `target_length_tier` nullable only for Semantic UGC;
- creates the three semantic production tables and indexes;
- preserves existing rows and defaults;
- leaves topic-script and dossier duration checks untouched because semantic research is duration-neutral.

Batch duplication copies the semantic target duration and route. List/detail projections expose both legacy tier and semantic duration without synthesizing one from the other.

## Test-driven implementation requirements

Every behavior is introduced red-green-refactor. Required automated proof includes:

- duration-contract properties for every integer from 8 through 60;
- exact contracts for 8, 16, 32, 50, and 60 seconds;
- rejection of values outside the configured range;
- prompt rendering at 17, 33, and 50 seconds without duration-specific files;
- semantic batch schema, form, persistence, duplication, and projection tests;
- unchanged validation for every legacy mode;
- free-plan hashing, invalidation, and exact cost tests;
- stale/missing approval rejection;
- one-submission and unknown-submission fail-closed tests;
- database lease and restart/resume tests;
- one-take voice/acoustic `not_applicable` behavior;
- 50-second seven-take planning and worker-wave tests with a fake provider;
- per-take targeted retry approval tests;
- completion projection that bypasses duplicate caption processing;
- browser verification of the 50-second form and plan/approval UI.

## Live paid proof boundary

The implementation is authorized for exactly one live Veo video-generation request and no other paid generation request.

The proof uses an existing approved master and an eight-second, one-take script so the request count is exactly one. It does not call Gemini image generation, Nano Banana, Deepgram, Gemini QA, another Veo model, storage upload, or an automatic retry. All non-Vertex proof steps use local files, FFmpeg/FFprobe, deterministic captions derived from the known approved script, and human visual inspection. The command must enforce:

- `max_submissions = 1`;
- `max_budget_usd = 17.70`;
- one output video;
- one approved request hash;
- no retry on rejection, timeout, ambiguity, QA failure, or provider error.

As verified from the official Google Cloud pricing page on 2026-07-13, Veo 3.1 video plus audio at 720p or 1080p is listed at USD 0.40 per provider second. The expected maximum for one eight-second request is USD 3.20. Pricing is still supplied by configuration and checked against the user-approved ceiling at runtime.

The operation identifier is persisted before polling. Polling and downloading address the same accepted operation. Media inspection and captions run locally and cannot invoke another paid provider. If the single request cannot be accepted or completed, the paid proof stops without a second submission.

## Acceptance criteria

1. The new mode appears in batch creation and accepts every integer from 8 through 60 while legacy modes remain fixed.
2. A 50-second Semantic UGC batch produces a valid dynamic script contract and a seven-take free plan without requiring exact-duration topic-bank coverage.
3. The production run is persisted, observable, resumable, and protected by immutable approvals and database leases.
4. Magnific and LoRA adapters are unreachable from the semantic route.
5. Only failed take indexes are eligible for an explicitly approved retry.
6. The final captioned artifact advances the post and batch through the existing completion state without double-captioning.
7. Focused and broad regression suites pass apart from documented pre-existing baseline failures.
8. Real browser verification proves the new form and approval/progress experience.
9. Exactly one live eight-second Veo request is submitted, stays below USD 17.70, and its operation and output evidence are preserved; no other paid request is made.
