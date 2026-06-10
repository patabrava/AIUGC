# Analysis — Segmented Reference-Anchored Video + Stitch Pipeline

`BRIDGECODE_ROUTE: define architecture for drift-free multi-segment video (8s reference-anchored segments + ffmpeg stitch) → [GENERAL, LIRA] | MODE: Lira (Architect + Implementation-Block) | WHY: highest-cost failure is committing the wrong segmentation/stitch architecture before definition; LIRA defines, EYE implements.`

## Task Signal

Replace Veo "extend"-chaining (root cause of compounding character drift) with a new route that generates each segment as an **independent 8-second Veo reference-image generation anchored to the same actor reference bundle**, then **stitches the segments with ffmpeg** into the final video. This is plan definition only — no implementation in this pass.

## Why this fixes the drift (mechanism)

Today (route `veo_extended`): the 3 actor reference images are attached **only to the base clip** (`submit_video_generation`, `handlers.py`). Every 7s hop calls `submit_video_extension` with only `video.uri` + text + seed — the Veo API forbids `referenceImages` alongside a `video`/`image`/`lastFrame` input (`veo_client.py:110-111`). So each hop re-renders the face from the *previous hop's tail frame* → photocopy-of-a-photocopy → texture/identity drift that compounds per hop. A single hop (16s) stays acceptable; 3 hops (≈28s) visibly drift.

New route (`veo_segmented`): **every** segment is a fresh `submit_video_generation` carrying the **same** reference bundle + shared seed. No segment is conditioned on a drifted frame, so identity is re-anchored N times instead of decaying. Segment boundaries become hard jump-cuts — the native grammar of talking-head UGC, and further masked by the existing burned-in word captions.

## Repo Evidence (verified)

- **ffmpeg/ffprobe already in-repo, no new dep.** `workers/video_poller.py`: `_trim_tail` (628-695), `_maybe_postprocess_video_bytes` crop/scale (698-790), `_probe_video_dimensions/_probe_video_duration` (587-625). Captions: `app/adapters/caption_renderer.py:burn_captions` (251-357) uses `-filter_complex` overlay. `requirements.txt` has no ffmpeg pkg — system binary assumed in PATH. **Concat reuses this pattern.**
- **Storage = Cloudflare R2.** `app/adapters/storage_client.py`: `upload_video(video_bytes,...)` (70-129), `upload_video_from_url` (255-290), `download_video` (292-306). Returns `{storage_provider, storage_key, url, ...}`.
- **Final-assembly + status flow.** `video_poller.py:_store_completed_video` (1438-1562) uploads bytes and sets `video_status = VIDEO_STATUS_CAPTION_PENDING` (1544) + `video_url` (1545) + merged `video_metadata`. Caption worker then runs (`caption_pending → caption_processing → caption_completed`). **Captions are transcribed (Deepgram) + burned in-app on the final video — stitch MUST precede captioning so word timings span the whole video.**
- **Status enum.** `app/core/video_profiles.py:40-50`: submitted/processing/completed/failed/extended_*/caption_*. Pollable set via `get_pollable_video_statuses()` (421-427).
- **Submission entry + reference loaders.** `app/features/videos/handlers.py`: `generate_video` (1631-1970), `generate_all_videos` (2038-2507); `_load_global_veo_reference_assets` (118-222), `_load_actor_identity_anchor_assets` (377-466, loads 2-3 actor anchors + appends **canonical scene image** as 3rd anchor → scene consistency, not just face); `_build_submission_metadata` (909-988) seeds `veo_base_seconds/veo_extension_seconds/veo_extension_hops_target/_completed`, `video_pipeline_route`, `veo_segments`.
- **Extend hop machinery (to be bypassed, not deleted).** `video_poller.py`: `_needs_extension_hop` (1710-1718), `_submit_extension_hop` (1721-1934), `_build_veo_extension_prompt` (1624-1707). Decision point after base completes: 1203-1209.
- **Reference-image API constraints (`docs/character_consistency.md`).** `referenceImages` requires `durationSeconds: 8` (4s/6s rejected, line 113); max 3 asset images (114); cannot combine with `image` first-frame (116); historically 16:9-only with 9:16 "forthcoming" (112/119). **User reports 9:16 reference images now work and `VEO_USE_REFERENCE_IMAGES` is wired (`config.py:253`) — this MUST be empirically re-confirmed before rollout (see Risk R1).**
- **Existing seed threading.** `veo_seed` reused across submissions/extensions (`video_poller.py:1835/1847`, `docs/character_consistency.md:302-318`).
- **Profiles.** `video_profiles.py`: `_BASE_PROFILES` (73-168) and `_EFFICIENT_LONG_ROUTE_PROFILES` (170-207), selected by `veo_enable_efficient_long_route` (`_profiles()` 210-222). Cost units = `1 + hops` (`get_profile_request_cost_units`).

## Architecture Decision (chosen path)

Add a **parallel, flag-gated route `veo_segmented`** beside `veo_extended`. The extend path stays fully intact for instant rollback and A/B comparison.

1. **Segment unit = exactly 8s, reference-anchored, self-contained.** Each segment is an independent `submit_video_generation(prompt, reference_images=<same 3-anchor bundle>, duration_seconds=8, seed=<shared veo_seed>, negative_prompt=None)`. Per-segment prompt carries the **full** character + scene description (NOT "continue from previous") plus that segment's dialogue beat. `negative_prompt` is omitted when reference images are present (existing rule, `veo_client.py:145`).
2. **Fan-out submission, not sequential hops.** Segments are independent by design, so submit all N up front and poll each concurrently. Track `veo_segment_ops: [{index, operation_id, status, video_uri}]`. Faster wall-clock and a simpler state model than the hop chain. Reserve N quota units up front; release unused on failure.
3. **Stitch = hard-cut ffmpeg concat with normalization.** When all N segments complete: download each segment's bytes, normalize (consistent fps/SAR/codec) and concat. **Hard cut, no crossfade** — a crossfade would ghost two different poses; a jump-cut reads as native UGC and is covered by captions. Produce final bytes → hand to existing `_store_completed_video` → `caption_pending` (captioning unchanged). New intermediate status `VIDEO_STATUS_STITCHING` between all-segments-complete and caption_pending.
4. **Duration math changes to multiples of 8.** Segmented tier N = `ceil(N/8)` segments × 8s (tier 16 → 2 seg, tier 32 → 4 seg). `veo_segments` length must equal segment count; reuse `split_dialogue_sentences` + per-segment spoken-word budget (~8s ≈ 20-22 German words) so each beat fills ≈8s of speech. Cost parity note: tier 32 extend = 1+3 = 4 units; segmented = 4 units → **comparable cost, no drift** (good rollout argument).

Rejected: (a) keeping extend + injecting refs per hop — impossible, API forbids it; (b) first-frame re-anchoring per segment — weaker than 3-angle asset lock, kept only as R1 fallback; (c) crossfade stitching — worse than hard cut for independent takes; (d) LoRA — best long-term for hero actors but large lift, out of scope for this route.

## Contracts to Preserve

- `posts` columns: `video_status`, `video_url`, `video_metadata` (shape unchanged; only new metadata keys added).
- Status enum + `get_pollable_video_statuses()` must include `VIDEO_STATUS_STITCHING` and any new submitted/processing variants, and `schemas.py` allowed-status sets.
- Caption pipeline contract: it receives exactly one final video at `caption_pending`. Do not change it.
- `submit_video_generation` signature and the reference-bundle dict shape from the loaders.
- Idempotency-Key behavior on submission endpoints; quota reservation/consumption semantics.
- Batch state machine S1–S8 is untouched (this lives inside video generation under S5/S6).
- `veo_extended` route and all its tests remain green (flag-gated coexistence).

## Data / State Changes (additive metadata only)

- `video_pipeline_route = "veo_segmented"` (new constant `VEO_SEGMENTED_VIDEO_ROUTE`).
- `veo_segment_count`, `veo_segments` (already exists), `veo_segment_spoken_budgets` (already exists).
- `veo_segment_ops: [{index, operation_id, status: submitted|processing|completed|failed, video_uri}]`.
- `veo_seed` (reuse), `actor_identity_*`/reference audit (reuse existing keys).
- New status: `VIDEO_STATUS_STITCHING = "stitching"`; new submitted/processing variants only if the poller needs to distinguish segmented polling (prefer reusing `submitted`/`processing` filtered by `video_pipeline_route`).

## Implementation Block (for EYE)

```md
# Implementation Block

Task signal: New flag-gated route veo_segmented — N independent 8s reference-anchored Veo generations stitched by ffmpeg; eliminates extend-chain character drift.

Goal: Drift-free videos >16s with identity re-anchored on every segment, comparable cost, captions unchanged.

User-visible behavior: Same UI/states; longer videos hold the actor's face/texture across the full duration; segment joins read as UGC jump-cuts under captions.

{files, LOC/file, deps}:
- app/core/video_profiles.py  (+ VEO_SEGMENTED_VIDEO_ROUTE, VIDEO_STATUS_STITCHING, segmented profiles + selection; ~+80) — no new deps
- app/core/config.py          (+ veo_enable_segmented_route flag; ~+6)
- app/features/videos/handlers.py (segmented submission fan-out reusing reference loaders; ~+150)
- app/features/posts/prompt_builder.py (self-contained per-segment prompt builder, no "continue from previous"; ~+60)
- app/adapters/video_stitcher.py (NEW: ffmpeg concat/normalize of segment bytes → final bytes; ~150) — system ffmpeg only
- workers/video_poller.py      (segmented branch: per-segment completion tracking → STITCHING → stitch → reuse _store_completed_video; ~+180)
- app/features/videos/schemas.py (allow new statuses)
- tests/ (segment planner, concat builder, poller branch, e2e smoke; ~+200)

Capability slices (ship in order, each independently testable):
1. Config flag + VEO_SEGMENTED_VIDEO_ROUTE + VIDEO_STATUS_STITCHING + segmented duration profiles (ceil(N/8)×8s) + profile selection. Validate: profile unit tests.
2. Segment planner: split script into N self-contained 8s beats (reuse split_dialogue_sentences + per-segment word budget); per-segment prompt = full character+scene + beat dialogue. Validate: planner unit tests (count==N, budgets sane).
3. Submission fan-out in handlers: for veo_segmented, reserve N quota units, build the shared reference bundle ONCE (reuse _load_actor_identity_anchor_assets), submit N x submit_video_generation(duration=8, reference_images=bundle, seed=shared, negative_prompt=None); persist veo_segment_ops; status=processing. Validate: mocked-client test asserts N calls all carry identical referenceImages + same seed + durationSeconds=8.
4. Poller branch: poll each segment op; on each completion update veo_segment_ops; when all completed → set VIDEO_STATUS_STITCHING and invoke stitcher; on any terminal failure → fail post + release quota. Validate: poller unit test with fake ops.
5. video_stitcher adapter: download each segment (storage_client/_decode_vertex_video_uri), ffmpeg normalize (fps/SAR/pix_fmt) + concat demuxer (re-encode libx264, -c:a aac, +faststart), return bytes; then call existing _store_completed_video (→ caption_pending). Validate: integration test concats two sample 8s clips → one valid mp4 (ffprobe duration≈16s, dims 9:16).

Contracts to preserve: posts.video_status/video_url/video_metadata; caption pipeline input (one video at caption_pending); submit_video_generation signature + reference bundle shape; idempotency + quota semantics; veo_extended route + tests; S1–S8 untouched.

Data/state changes: additive metadata (veo_segment_ops, veo_segment_count, video_pipeline_route=veo_segmented); reuse veo_segments/veo_seed/actor refs; add VIDEO_STATUS_STITCHING.

Validation and errors: per-segment failure fails the post with a clear code and releases reserved quota; partial completion never stitches; ffmpeg nonzero exit → failed status + structured log with stderr (redacted of URIs as needed). Uniform error envelope preserved.

Observability: structlog at submission fan-out (n_segments, seed, ref_image_count), each segment completion, stitch start/end (input count, output bytes, duration), with correlation_id.

Tests/browser checks: unit (profiles, planner, concat-cmd builder), integration (real ffmpeg concat of 2 sample clips), e2e smoke (one 2-segment post end-to-end with mocked Veo returning two canned 8s clips). Behavior diff: same script through veo_extended vs veo_segmented, compare identity stability on the final frames.

Pass/fail criteria:
- PASS: 16s+ segmented video produced; every segment submission carried identical 3-image reference bundle + same seed + durationSeconds=8; final mp4 is valid 9:16 with summed duration; captions burn correctly on stitched video; veo_extended route + existing tests still green; flag off = zero behavior change.
- FAIL: any segment generated without reference images; stitch on partial set; caption pipeline broken; existing tests regressed.

Risks and assumptions: see Risks below.

Next route: EYE (implement slices 1→5 in order, validating each).
```

## Validation Path (summary)

Smoke first (2-segment / tier-16) with mocked Veo, then one real generation behind the flag, then the **behavior diff** required by repo standards: run the *same* script through `veo_extended` and `veo_segmented` and compare actor identity/texture on the last frames of the final clip. Existing `veo_extended` tests must stay green with the flag off.

## Risks & Assumptions

- **R1 (blocking, verify FIRST):** 9:16 `referenceImages` at 8s must actually be accepted by the Veo API now. Docs say 16:9-only/8s; user says 9:16 works + flag exists. EYE must run one real 9:16 8s reference generation before building slices 3-5. If rejected → fallback to per-segment **first-frame anchoring** (image-to-video works in 9:16, `docs:123`) using a canonical actor still; same stitch path, weaker lock. This is a RESEARCH/EYE smoke gate.
- **R2 (inherent tradeoff):** independent takes vary more *between* segments (pose, exact framing, micro-background) than within one clip. Mitigated by: shared 3-anchor bundle incl. canonical **scene** image (`_load_actor_identity_anchor_assets`), identical scene text per segment, shared seed, hard-cut + caption masking. Accept jump-cuts as UGC-native; this is the deliberate cost vs. seamless-but-drifting extend.
- **R3:** concat codec/fps/SAR mismatch between segments → re-encode-normalize in the stitcher (don't rely on stream-copy concat).
- **R4:** quota — N parallel reservations up front; ensure release on any failure path. Cost ≈ extend route (1+hops ≈ ceil(N/8)).
- **R5:** segment script beats must fill ≈8s of speech; under/overrun makes pacing uneven. Tune per-segment word budget; reuse existing `veo_segment_spoken_budgets`.

## Handoff

Worktree `AIUGC-segment-stitch` (branch `segment-stitch-pipeline`) created off `main`. This file is the LIRA definition. **Next route: EYE**, starting with the R1 smoke gate (confirm 9:16 8s reference generation), then slices 1→5 in order, validating each.

---

# EYE Execution Status (this run)

`BRIDGECODE_ROUTE: implement defined block → [GENERAL, EYE] | MODE: Eye B`

## Done and PROVEN (24 new tests pass, 0 regressions)

- **Slice 1 — foundation.** `app/core/config.py`: `veo_enable_segmented_route` flag (default False). `app/core/video_profiles.py`: `VEO_SEGMENTED_VIDEO_ROUTE`, `VIDEO_STATUS_STITCHING`, `SEGMENTED_SEGMENT_SECONDS=8`, `segment_count_for_tier()`, `_segmented_profile()`, segmented selection in `_profiles()`, and segmented precedence in `get_duration_profile_for_creation_mode()`. Tier→segments: 8→1 (stays SHORT), 16→2, 32→4, 48→6, 64→8. Cost units == segment count. Tests: `tests/test_segmented_profiles.py` (5).
- **Slice 2 — segment planner.** `app/features/posts/prompt_builder.py:build_segment_prompts()` — one self-contained prompt per beat (full character/scene every segment; ending only on the last). Tests: `tests/test_segment_planner.py` (5).
- **Slice 5 — stitcher.** `app/adapters/video_stitcher.py:stitch_segments()` — single ffmpeg concat-filter pass, normalizes scale/SAR/fps/pix_fmt, hard cuts, re-encode libx264+aac+faststart, single-segment passthrough. Tests (REAL ffmpeg): `tests/test_video_stitcher.py` (4) — proves duration summation and resolution-mismatch normalization.
- **Slices 3+4 — pure orchestration.** `app/features/videos/segmented_pipeline.py` — `plan_segment_submissions()`, `build_initial_segment_ops()`, `record_segment_result()` (pure), `segment_stitch_ready()`, `all_segments_completed()`, `any_segment_failed()`, `ordered_completed_segment_uris()`. Tests: `tests/test_segmented_pipeline.py` (10).

Run all: `APP_ENV_FILE=../AIUGC/.env GOOGLE_APPLICATION_CREDENTIALS=<any-file> ../AIUGC/.venv/bin/python -m pytest tests/test_video_stitcher.py tests/test_segment_planner.py tests/test_segmented_profiles.py tests/test_segmented_pipeline.py` → 24 passed. Regression sweep on `test_video_poller_extension_chain.py`+`test_video_duration_routing.py` → 91 passed, 4 pre-existing env failures (identical on clean `main`).

## IO wiring — LANDED (2026-06-10, EYE run 2)

Both branch points are now implemented and unit-covered (5 new tests in `tests/test_segmented_io_wiring.py`; total segmented coverage 29 tests; full sweep 120 passed, same 4 pre-existing `veo_client` env failures as `main`). Live Veo/Supabase validation (R1) is still the user's smoke test.

- **Submission fan-out** — `handlers.py`: `_split_script_into_segments`, `_build_segmented_segment_prompts` (per-segment uses `build_reference_image_scene_base_prompt` for character_consistency, `build_lean_veo_base_prompt` for light, generic otherwise; ending only on last), `_submit_segmented_post` (N× `_submit_video_request` at `provider_duration_seconds=8`, shared seed, refs re-attached each segment), `_build_segmented_submission_metadata`. Wired into both `generate_video` (single) and `generate_all_videos` (batch, reserves `chain_cost_units`=segment_count). Persists `veo_segment_ops`, `video_operation_id`=segment-0 op.
- **Poller finalize** — `video_poller.py`: `process_video_operation` early-dispatches `is_segmented_route` → `_handle_segmented_video` (polls each op via `_poll_single_segment` for veo+vertex, `record_segment_result`, `any_segment_failed`→`_fail_segmented_post`+quota release, `segment_stitch_ready`→`_stitch_and_store_segments` → `stitch_segments` → existing `_store_completed_video`→caption_pending). Extend path untouched.
- **Status/schema** — `VIDEO_STATUS_STITCHING` added to `get_pollable_video_statuses()` (recovery if stuck mid-stitch) and to `VideoStatusResponse` Literal. `plan_segment_submissions` accepts pre-built mode-aware `prompts`.

### Known v1 limitations
- Mid-fan-out submission failure: already-accepted segments are logged as orphaned (paid, untracked) and the post is treated as a failed submission. Acceptable for first rollout; a future pass could persist partial `veo_segment_ops` for recovery.
- `stitching` is pollable + the stitch path is re-entrant (re-download+re-stitch is safe), so a crash mid-stitch self-heals on the next poll.

## Original remaining notes (superseded by the section above)

These two branch points are intentionally NOT landed unvalidated. Each calls the already-tested pure logic above; only the network/DB glue is new.

1. **Submission fan-out** — `app/features/videos/handlers.py` `generate_video` (1631-1970) and `generate_all_videos` (2038-2507). When `profile.route == VEO_SEGMENTED_VIDEO_ROUTE`:
   - reserve `get_profile_request_cost_units(profile)` quota units up front (existing quota path);
   - build the reference bundle ONCE (`_load_actor_identity_anchor_assets`/`_load_global_veo_reference_assets`);
   - `subs = plan_segment_submissions(profile=..., segments=<existing veo_segments>, seed=<veo_seed>, character/scene=<same as base prompt>)`;
   - for each sub: `veo_client.submit_video_generation(prompt=sub.prompt, reference_images=bundle, duration_seconds=8, seed=sub.seed, negative_prompt=None)`;
   - persist `video_metadata`: `video_pipeline_route="veo_segmented"`, `veo_segment_count=len(subs)`, `veo_seed`, `veo_segment_ops=build_initial_segment_ops([op_ids])`; set `video_status="processing"`. Release unused quota on any submit failure.

2. **Poller finalize** — `workers/video_poller.py` `_handle_veo_video` (1167) at the per-op completion points (1203, 1305). When `is_segmented_route(metadata)`:
   - on each segment op done: `metadata["veo_segment_ops"] = record_segment_result(metadata, operation_id=op, status="completed", video_uri=uri)`; persist; if `any_segment_failed` → fail post + release quota; `return` (do NOT run the extend `_needs_extension_hop` path);
   - when `segment_stitch_ready(metadata)`: set `video_status=VIDEO_STATUS_STITCHING`; download each `ordered_completed_segment_uris(metadata)` via `_decode_vertex_video_uri`/`storage_client.download_video`; `final_bytes, meta = stitch_segments(segment_videos=[...], post_id, correlation_id)`; pass `final_bytes` into the existing `_store_completed_video(...)` (→ sets `caption_pending`, captioning unchanged).
   - Add `VIDEO_STATUS_STITCHING` to `get_pollable_video_statuses()` is NOT needed (poller acts on `submitted`/`processing` per-op); confirm `schemas.py` allows the new status for API responses.

3. **R1 live gate (do FIRST):** confirm one real 9:16 8s `referenceImages` generation succeeds (user reports it works; docs say 16:9-only). If rejected → switch each segment to first-frame anchoring (`image=` canonical actor still) through the same stitch path.

Correction memory updated in `AGENTS.md §10` (worktree-test env + settings-stub pitfalls). No commit made (awaiting your go-ahead).
