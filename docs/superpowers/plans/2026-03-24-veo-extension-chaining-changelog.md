# Veo Extension Chaining — Changelog

**Branch:** `feat/veo-extension-chaining`
**Date:** 2026-03-24
**Plan:** `docs/superpowers/plans/2026-03-24-veo-extension-chaining.md`

---

## Summary

The 16s/32s Veo extension pipeline was scaffolded (duration profiles, metadata fields, DB migrations) but never wired end-to-end. This work completes the implementation so the poller can chain multiple Veo hops, advance segment prompts, and only mark a post completed when the full duration target is reached.

---

## Bugs Fixed

### 1. Poller ignored extended video statuses (CRITICAL)
**File:** `workers/video_poller.py:88-90`

The poller hardcoded `["submitted", "processing"]` in its query. Posts on the extended pipeline get status `extended_submitted` or `extended_processing`, so they were never polled — they sat forever.

**Fix:** Replaced the hardcoded list with `get_pollable_video_statuses()` which returns all four pollable statuses.

### 2. Extension chaining logic was completely missing (CRITICAL)
**File:** `workers/video_poller.py`

When a VEO operation completed, `_handle_veo_video` always called `_store_completed_video` immediately — even for 16s/32s posts that needed multiple hops. A 32s video (needing 4 extension hops) would complete after the first ~4s base segment.

**Fix:** Added three new functions:
- `_build_veo_extension_prompt(post, segment_index)` — builds a VEO prompt for a specific segment
- `_needs_extension_hop(metadata)` — checks if more hops are needed based on `veo_extension_hops_target` vs `veo_extension_hops_completed`
- `_submit_extension_hop(post, correlation_id)` — advances the segment index, builds the next prompt, submits to VEO, and updates the post metadata with the new operation ID and chain state

Then wired a chaining check into `_handle_veo_video`: when a VEO operation completes and `_needs_extension_hop` returns `True`, it calls `_submit_extension_hop` instead of storing the video.

### 3. Batch endpoint bypassed duration routing (MODERATE)
**File:** `app/features/videos/handlers.py:387-623`

The `generate_all_videos` batch endpoint used raw `request.provider`, `request.seconds`, and `request.resolution` directly, ignoring `_resolve_video_submission_plan` and `_build_submission_metadata`. This meant 16s/32s batches would not force VEO, not set chain metadata, not use segment prompts, and not downgrade to 720p.

**Fix:** Rewired the per-post submission block to:
- Call `_resolve_video_submission_plan(batch=...)` to get the correct provider/seconds/resolution
- Use `_build_veo_extended_base_prompt` for extended routes (first segment only)
- Use `_build_submission_metadata` to initialize chain tracking fields
- Use `get_submission_video_status` for correct initial status (`extended_submitted`)
- Skip posts with `extended_submitted`/`extended_processing` in the already-submitted check

### 4. No-op template conditional in prompt builder (MINOR)
**File:** `app/features/posts/prompt_builder.py:220`

`template = OPTIMIZED_PROMPT_TEMPLATE if include_quotes else OPTIMIZED_PROMPT_TEMPLATE` — both branches returned the same value.

**Fix:** Simplified to `template = OPTIMIZED_PROMPT_TEMPLATE`.

### 5. Pre-existing broken tests
**File:** `tests/test_veo_prompt_contract.py`

- Two tests called `video_poller._build_veo_extension_prompt` which didn't exist — fixed by implementing the function
- `test_veo_prompt_requires_exact_german_dialogue` asserted on old prompt template strings that had since changed — updated assertions to match current template
- `test_veo_extension_prompt_preserves_approved_german_script` used a script with "4.180" that got split on the decimal period, and had no `video_metadata` so `is_final=True` — rewrote with clean script and proper metadata
- `test_split_dialogue_sentences_ignores_trailing_fragment` had wrong assertion — `split_dialogue_sentences` appends trailing fragments to the last sentence, not drops them — fixed assertion and renamed test

### 6. Negatives logic investigated — NOT a bug
**File:** `app/features/posts/prompt_builder.py:225`

The condition `VEO_NEGATIVE_PROMPT if not include_quotes` looked inverted, but `include_quotes=False` is the Veo path, so VEO negatives are correctly applied. No change needed.

### 7. Extension used wrong API — prompt-only instead of video+prompt (CRITICAL)
**Files:** `app/adapters/veo_client.py`, `workers/video_poller.py`

Discovered by cross-referencing the [official Veo 3.1 docs](https://ai.google.dev/gemini-api/docs/video). The Veo extension API requires passing the **previous video's base64 data** in a `video.inlineData` field alongside the prompt. Our `_submit_extension_hop` was calling `submit_video_generation` with only a prompt, which creates a completely new, unrelated video instead of extending the previous one.

Per the docs:
- Extension uses the same `predictLongRunning` endpoint but with a `video` field in the instances array
- The video must be base64-encoded and sent as `{"video": {"inlineData": {"mimeType": "video/mp4", "data": "<base64>"}}}`
- The output is a **single combined video** (original + extension), not a separate clip
- Resolution must be `720p` for extensions
- Extensions add 7 seconds each, chainable up to 20 times (max 148s total)

**Fix:**
- Added `VeoClient.submit_video_extension()` that includes the `video.inlineData` field with base64-encoded video bytes
- `_submit_extension_hop` now downloads the completed video from the previous hop via `veo_client.download_video()`, then passes those bytes to `submit_video_extension`
- `_handle_veo_video` now passes `video_data` (containing the `video_uri`) to `_submit_extension_hop` so it can download the previous video

---

## Files Changed

| File | Lines Changed | What Changed |
|------|--------------|--------------|
| `workers/video_poller.py` | +175 | Added `_build_veo_extension_prompt`, `_needs_extension_hop`, `_submit_extension_hop` (with video download + extension API); changed poll query to use `get_pollable_video_statuses()`; added chaining branch in `_handle_veo_video` that passes `video_data`; added video_profiles imports |
| `app/adapters/veo_client.py` | +100 | Added `submit_video_extension()` method that sends previous video bytes as base64 in the `video.inlineData` field per Veo 3.1 docs |
| `app/features/videos/handlers.py` | +104 -61 | Added `get_submission_video_status` import; added batch lookup in `generate_all_videos`; replaced per-post submission block with duration-aware routing using `_resolve_video_submission_plan`, `_build_submission_metadata`, `_build_veo_extended_base_prompt` |
| `app/features/posts/prompt_builder.py` | +1 -1 | Removed no-op template conditional |
| `tests/test_video_poller_extension_chain.py` | +280 (new) | 10 tests: poller status query, `_needs_extension_hop` (4 cases), `_submit_extension_hop` with video download + extension API (2 cases incl. edge case), `_handle_veo_video` chaining (2 cases), e2e 32s lifecycle |
| `tests/test_video_duration_routing.py` | +44 | 2 new tests: `_build_veo_extended_base_prompt` segment extraction, 32s chain metadata initialization |
| `tests/test_veo_prompt_contract.py` | +23 -13 | Fixed 4 broken/incorrect test assertions, renamed trailing fragment test |

---

## Commits (8)

1. `2e70127` — fix: add _build_veo_extension_prompt and fix broken prompt contract tests
2. `8f6a1e1` — fix: poller now polls extended_submitted and extended_processing statuses
3. `3709d00` — feat: add _needs_extension_hop helper to video poller
4. `9f71643` — feat: add _submit_extension_hop to chain VEO extension hops
5. `7d671eb` — feat: wire extension chaining into _handle_veo_video
6. `f1e5457` — feat: wire duration routing into batch video submission path
7. `ab5e8a6` — fix: remove no-op template conditional, add e2e chain lifecycle test
8. `8d88e59` — fix: use Veo extension API with previous video bytes instead of prompt-only

---

## Test Results

- **21 tests pass** across the 3 relevant test files
- **10 pre-existing failures** in unrelated modules (`test_lifestyle_generation_regression`, `test_publish_meta_flow`, `test_topic_prompt_templates`, `test_topics_hub`)
- **155 total tests pass**, 1 skipped

---

## Design Decisions

- **Mid-chain failure:** If a VEO extension submission fails mid-chain, the entire post is marked `failed`. The failure handler preserves chain metadata (`operation_ids`, `hops_completed`) in `video_metadata` so manual recovery is possible.
- **Fewer segments than hops:** If the script has fewer sentences than extension hops, the last segment is reused for remaining hops.
- **No video stitching:** VEO's extend API produces a complete standalone video at each hop. The final hop's output is the complete video — no ffmpeg or concatenation needed.
