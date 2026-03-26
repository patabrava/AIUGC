# Session Log: 2026-03-26 — Video Generation Pipeline Fixes

## The Core Problem

16-second video generation was failing repeatedly. Three separate root causes were identified and fixed.

---

## Bug 1: `module 'google.genai.types' has no attribute 'Video'`

**Symptom:** Video poller crashes when processing completed Veo operations. Videos get marked as `failed` even though Google successfully generated them.

**Root cause:** `app/adapters/veo_client.py` imported `from google import genai` and instantiated `genai.Client()` — but the entire video pipeline uses REST via httpx, not the SDK. When the `google-genai` package was upgraded, lazy SDK internals triggered `AttributeError` on Python 3.9.

**Fix:** Removed the unused `from google import genai` import and `self.client = genai.Client(...)` from `veo_client.py`. The SDK was dead code — only REST calls are used.

**File:** `app/adapters/veo_client.py` (lines 11, 41)

**Risk if reverted:** Video poller will crash again whenever it tries to process a completed Veo operation. Videos will be generated and paid for but never downloaded.

---

## Bug 2: `NameError: name 'FlowForgeException' is not defined`

**Symptom:** Batch page returns 500 Internal Server Error when TikTok API returns HTTP 429 (rate limit). The entire batch detail page becomes inaccessible.

**Root cause:** `app/features/publish/tiktok.py` line 264 catches `FlowForgeException` but never imported it. When `RateLimitError` (a subclass of `FlowForgeException`) is raised, the except handler itself crashes.

**Fix:** Added `FlowForgeException` to the imports in `tiktok.py`.

**File:** `app/features/publish/tiktok.py` (line 23)

---

## Bug 3: RAI Safety Filter Silently Kills Videos

**Symptom:** Veo operation completes with `done: true` but returns no video — only `raiMediaFilteredCount: 1`. The poller raises `ValueError("Video data missing download URI")` with no indication of why.

**Root cause:** `veo_client.check_operation_status()` didn't check for `raiMediaFilteredCount` or `raiMediaFilteredReasons` in the Veo response. When Google's safety filter blocks a video (common with German-language medical/disability content), the response has no `generatedSamples` and the error was opaque.

**Fix (two parts):**

1. **Detection** (`app/adapters/veo_client.py`): `check_operation_status` now checks for `raiMediaFilteredCount` in the response and returns `status: "failed"` with `error.code: "RAI_FILTERED"` and the actual Google reason message.

2. **Auto-retry** (`workers/video_poller.py`): New `_retry_rai_filtered_video()` function. When the poller gets a `RAI_FILTERED` error, it fetches the original prompt from `video_prompt_audit`, resubmits to Veo, and tracks retry count in `video_metadata`. Max 3 retries. After exhaustion, marks as permanently failed with actionable advice.

3. **UI notifications** (`templates/batches/detail/_view_macros.html`, `_post_card.html`): Status chip shows amber "Retrying (N/3)" during RAI retries. Failed state shows specific guidance about modifying prompts.

**Files:**
- `app/adapters/veo_client.py` (lines 249-268 — RAI detection)
- `workers/video_poller.py` (new `_retry_rai_filtered_video` function, ~80 lines)
- `templates/batches/detail/_view_macros.html` (status chip updates)
- `templates/batches/detail/_post_card.html` (RAI status messages)

---

## Bug 4: Removed Scripts Block Batch Progression

**Symptom:** After removing scripts in S2_SEEDED stage, the batch cannot advance past S5_PROMPTS_BUILT because removed posts still have `video_status = 'pending'`.

**Root cause:** When a script is removed via `script_review_status = "removed"`, the `video_status` was left as `pending`. The video generation correctly skips removed posts, but the batch transition logic still counted them.

**Fix:** When a script is removed, `video_status` is set to `NULL`. The DB constraint already allows `NULL`, and all transition checks skip `NULL`-status posts.

**File:** `app/features/posts/handlers.py` (lines 141-146)

---

## Bug 5: DB Constraint Missing Caption Statuses

**Symptom:** `caption_pending`, `caption_processing`, `caption_completed`, `caption_failed` statuses cannot be written to the `posts` table — blocked by `posts_video_status_check` constraint.

**Root cause:** The check constraint was created before caption statuses were added to the codebase.

**Fix:** Updated the constraint via migration:
```sql
ALTER TABLE posts DROP CONSTRAINT posts_video_status_check;
ALTER TABLE posts ADD CONSTRAINT posts_video_status_check CHECK (
  video_status IS NULL OR video_status = ANY (ARRAY[
    'pending', 'queued', 'submitted', 'processing',
    'extended_submitted', 'extended_processing',
    'completed', 'failed',
    'caption_pending', 'caption_processing', 'caption_completed', 'caption_failed'
  ])
);
```

**Applied directly to Supabase** (not in a migration file).

---

## Feature: Caption Script Alignment

**Problem:** Deepgram misspells German compound words (e.g., "Entlastungs budget" instead of "Entlastungsbudget") and the wrong text gets burned into captions.

**Solution:** New `app/adapters/caption_aligner.py` with `align_transcript_to_script()`. Uses Deepgram only for word-level timing, replaces text with the known-correct script from `seed_data.script`. Handles misspellings, split compound words, extra/missing words.

**Integration:** `workers/caption_worker.py` calls the aligner between Deepgram transcription and FFmpeg caption burning.

**Files:**
- `app/adapters/caption_aligner.py` (new, ~126 lines)
- `workers/caption_worker.py` (added ~15 lines after transcription)
- `tests/test_caption_aligner.py` (7 tests)
- `tests/test_caption_worker_alignment.py` (1 integration test)

---

## Feature: Caption Font Auto-Scaling

**Problem:** Long German compound words like "ENTLASTUNGSBUDGET" in Impact font at 72px overflow the 720px video frame, getting clipped.

**Fix:** `_render_caption_frame()` in `caption_renderer.py` now measures text width against 90% of video width and shrinks the font in 4px decrements until it fits (minimum 24px).

**File:** `app/adapters/caption_renderer.py` (lines 92-99)

---

## UI Fixes

- **Caption status chips:** `_view_macros.html` now handles `caption_pending`, `caption_processing`, `caption_completed`, `caption_failed` instead of showing "Pending" for all.
- **Captioned video display:** `_post_card.html` prefers `video_metadata.caption_video_url` over `video_url` when available.

---

## Recovered Videos

Two paid Veo operations were recovered during this session:

1. **Operation `926swuplj7sb`** (batch `8fafa0c2`): First 8s segment generated successfully but poller crashed with `types.Video` error. Video downloaded to `/tmp/recovered_video_926swuplj7sb.mp4`.

2. **Operation `xzjnt6j1xnm8`** → extension `vhpq16dkxrxg` (batch `d18286ba`): Full 16s video recovered. First segment completed, extension hop submitted manually, final video downloaded and uploaded to R2.

---

## Commit

```
2d63e54 fix: video generation pipeline reliability + caption script alignment
```

Pushed to `main` on 2026-03-26.
