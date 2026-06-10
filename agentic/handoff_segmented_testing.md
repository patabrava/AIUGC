# Handoff — Segmented (drift-free) Video Route: Further Testing

**Branch:** `segment-stitch-pipeline` (worktree `AIUGC-segment-stitch`)
**Flag:** `VEO_ENABLE_SEGMENTED_ROUTE=true` (default off)
**Status:** Implemented + unit-tested + **partially validated live**. One live bug found & fixed. Not committed.

---

## 1. What this route does

Replaces Veo "extend"-chaining (the cause of compounding character drift across hops) with **N independent
8-second reference-anchored generations stitched by ffmpeg**. Each segment re-attaches the same actor
reference bundle + shares one seed, so identity is re-anchored every segment instead of decaying. Tier → segments:
8→1 (stays SHORT), 16→2, 32→4, 48→6, 64→8. Cost == segment count (≈ parity with the extend route).

Segment joins are hard cuts (native UGC grammar, masked by burned-in captions). Captioning is unchanged —
the stitched video enters the existing `caption_pending` flow.

---

## 2. Live validation status (2026-06-10)

A real run was executed (batch `e57d6ea7-…`, tier 16, post `02bffcfe-…`). **Proven end-to-end:**

- ✅ Route selected (`video_pipeline_route: veo_segmented`), 2 segments planned.
- ✅ Fan-out submitted 2 independent 8s generations (vertex_ai, veo-3.1, 9:16, **shared seed 2518208161**, no extend chain).
- ✅ Poller tracked both ops (`segmented_video_progress 0/2 → 1/2 → 2/2`).
- ✅ `segment_stitch_ready` fired → stitch invoked.
- ❌ **Crashed at the final DB write** setting `video_status="stitching"` → `posts_video_status_check` constraint violation. **NOW FIXED** (see §3).

**Not yet validated live:** the actual stitch + caption_pending completion, and **reference-image anchoring**
(this run was `automated` mode → `reference_image_count: 0`; see §4).

---

## 3. The bug that was fixed (and the action it requires)

**Root cause:** the `posts` table has a Postgres CHECK constraint `posts_video_status_check` that whitelists
allowed `video_status` values. `"stitching"` is not in it. The poller tried to persist that intermediate
status and the row update was rejected.

**Fix applied** (`workers/video_poller.py` → `_stitch_and_store_segments`): we no longer write a `"stitching"`
status. The post stays `processing` (still pollable) through the stitch; `_store_completed_video` flips it to
`caption_pending` at the end. A crash mid-stitch self-heals on the next poll (re-download + re-stitch is idempotent).

**⚠️ ACTION REQUIRED before re-testing:** **restart the poller.** It is a plain long-running process with
**no `--reload`**, so it is still executing the old code. The uvicorn API server *does* auto-reload.

```bash
# poller terminal: Ctrl+C, then
cd ~/Documents/AI/AIUGC/AIUGC-segment-stitch
source ../AIUGC/.venv/bin/activate
VEO_ENABLE_SEGMENTED_ROUTE=true python3 workers/video_poller.py
```

---

## 4. CRITICAL for the next test: use a CHARACTER-CONSISTENCY batch

The validated run was a **topic/`automated`** batch, so **no actor reference images were attached** — segments
anchored on the text prompt + shared seed only. The actor reference bundle (the actual Laura drift fix) attaches
**only in character-consistency mode**.

**To test the real drift fix:** create a **character-consistency batch with the actor selected**, tier 16 or 32,
then generate. Confirm in the submission log that `reference_image_count` is **3** (not 0). Provider will be
`vertex_ai` for duration-routed CC; both providers attach refs at 8s — that's fine.

---

## 5. How to run a clean test

1. API server (auto-reloads):
   ```bash
   cd ~/Documents/AI/AIUGC/AIUGC-segment-stitch
   source ../AIUGC/.venv/bin/activate
   ln -s ../AIUGC/.env .env      # one-time, if not already linked
   VEO_ENABLE_SEGMENTED_ROUTE=true uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
2. Poller (separate terminal, **must be restarted after the §3 fix**):
   ```bash
   VEO_ENABLE_SEGMENTED_ROUTE=true python3 workers/video_poller.py
   ```
3. Open http://127.0.0.1:8000/batches → create/seed a **character-consistency** batch (tier 16) → approve →
   build prompts → Generate.
4. Watch the poller terminal for: `veo_segmented_fanout_start` (check `seed` shared, `segment_count`) →
   `vertex_ai_video_submission` (check **`reference_image_count: 3`**) → `segmented_video_progress N/N` →
   `segmented_video_stitch_start` → `segmented_video_stitch_complete` → post reaches `caption_pending` →
   captions burn → `completed`.

### Pass / fail
- **PASS:** final 9:16 mp4 whose duration ≈ N×8s; actor face/texture holds across the **whole** clip
  (the drift test — compare last frames vs. the same script on the extend route); captions span full duration;
  every segment submission carried 3 reference images + the same seed + `durationSeconds: 8`.
- **FAIL:** any segment without reference images (in CC mode); stitch on a partial set; caption pipeline broken;
  drift still visible.

---

## 6. Known limitations / open items

- **Orphaned paid segments:** the failed validation post (`02bffcfe-…`) has 2 completed segments (paid) whose
  URIs are in its `video_metadata.veo_segment_ops`, but it's now `failed` so it won't re-poll. *Optional recovery
  without re-paying:* set that row's `video_status` back to `processing` — the (restarted) poller will re-run
  `_handle_segmented_video`, see both complete, and stitch. Otherwise just regenerate (costs 2 fresh segments).
- **Mid-fan-out submission failure:** if segment k fails to submit after k−1 succeeded, those are logged as
  orphaned (`veo_segmented_fanout_partial_failure_orphaned_ops`) and the post is treated as a failed submission.
  A future pass could persist partial ops for recovery.
- **`stitching` status is app-only:** still present in `get_pollable_video_statuses()` and the `VideoStatusResponse`
  Literal but **never written** (DB constraint forbids it). *Optional follow-up:* add a migration to extend
  `posts_video_status_check` to include `stitching`, then restore the intermediate write for better observability.
- **Non-fatal noise:** `prompt_audit_failed … 'reference_image_metadata' column` (PGRST204). Pre-existing schema
  gap in `video_prompt_audit`, unrelated to this route; caught + logged, blocks nothing.

---

## 7. Tests & files

**Tests (run from worktree):**
```bash
APP_ENV_FILE=.env GOOGLE_APPLICATION_CREDENTIALS=<any-existing-file> \
../AIUGC/.venv/bin/python -m pytest \
  tests/test_segmented_profiles.py tests/test_segment_planner.py \
  tests/test_segmented_pipeline.py tests/test_video_stitcher.py \
  tests/test_segmented_io_wiring.py
```
→ 29 passed. Full sweep incl. `test_video_poller_extension_chain.py` + `test_video_duration_routing.py`:
120 passed, 4 **pre-existing** `veo_client` env failures (identical on `main`). Zero regression to `veo_extended`.

**Touched files:**
- `app/core/video_profiles.py` — segmented profiles/selection, `VEO_SEGMENTED_VIDEO_ROUTE`, `SEGMENTED_SEGMENT_SECONDS`, `segment_count_for_tier`, `VIDEO_STATUS_STITCHING`, pollable set.
- `app/core/config.py` — `veo_enable_segmented_route` flag.
- `app/features/posts/prompt_builder.py` — `build_segment_prompts`.
- `app/features/videos/segmented_pipeline.py` — pure orchestration (planner, op tracking, stitch-readiness).
- `app/features/videos/handlers.py` — `_split_script_into_segments`, `_build_segmented_segment_prompts`, `_submit_segmented_post`, `_build_segmented_submission_metadata`; segmented branches in `generate_video` + `generate_all_videos`.
- `app/adapters/video_stitcher.py` — ffmpeg concat/normalize → final bytes.
- `workers/video_poller.py` — `_handle_segmented_video` + helpers; early dispatch in `process_video_operation`.
- `app/features/videos/schemas.py` — `stitching` in status Literal.
- `tests/test_segmented_*.py`, `tests/test_video_stitcher.py`.

**Not committed.** Awaiting go-ahead once a character-consistency run is confirmed clean end-to-end.
