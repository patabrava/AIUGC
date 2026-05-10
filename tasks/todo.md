# Manual video duration auto-derive + tier-32+ support

## Problem
Manual videos cap at ~14.5s because `_resolve_manual_target_length_tier` (handlers.py:267) gates on sentence count, not actual speech time. A 40-word, 2-sentence script picks tier 16 (=15s; 14.5s after trim) and silently truncates. We also can't go beyond 32s.

## User intent (from conversation)
- Drop the manual tier picker entirely. User just writes a script.
- Auto-derive duration from word count using `WORDS_PER_SECOND = 2.5`.
- Show a live cost preview ("Estimated video: ~Xs · Y Veo calls").
- Support arbitrary lengths, including >32s.
- Topic-based batches stay UNCHANGED — they work today.

## Solution outline
**Backend**
- Add tier 48 (5 hops, ~43s) and tier 64 (7 hops, ~57s) to `_BASE_PROFILES` and `_EFFICIENT_LONG_ROUTE_PROFILES` in `app/core/video_profiles.py`. Cap at 7 hops (8s base + 7×7s ext = 57s); Veo's chain reliability degrades beyond.
- Add tier 48/64 to `SUPPORTED_TARGET_LENGTH_TIERS`.
- Rewrite `_resolve_manual_target_length_tier` (handlers.py:267) to:
  ```
  estimated_speech_seconds = word_count / 2.5
  pick smallest tier where profile.provider_target_seconds >= estimated_speech_seconds
  return DEFAULT (8) if estimated_speech_seconds <= 6
  return MAX (64) if estimated_speech_seconds > 56
  ```
  No longer use sentence-count gates.
- DB migration: extend the CHECK constraint on `batches.target_length_tier` to allow {8, 16, 32, 48, 64}.
- Pydantic schemas (`videos/schemas.py:44-47`, `videos/schemas.py:195-198`, `batches/schemas.py:50-55`): widen the Literal to {8, 16, 32, 48, 64}.

**Frontend**
- `templates/batches/list.html` (manual creation form):
  - Drop the visible tier `<select>` for manual mode (already hidden via Alpine).
  - Add a live preview block keyed off the script textarea: word count → estimated_speech_seconds → tier → Veo call count → "Estimated video: ~XXs · Y Veo calls".
- `templates/batches/detail/_video_settings.html`: for manual posts, show the auto-resolved duration per post (read-only). Keep the picker for non-manual.

**Tests**
- New test: `_resolve_manual_target_length_tier` returns the right tier for word counts spanning the buckets (5w → 8, 30w → 16, 60w → 32, 100w → 48, 140w → 64, 200w → 64).
- Run existing pytest to confirm no regressions in topic-based paths.

**Live verification (the demo)**
- Restart `uvicorn` (current local server appears hung).
- Create a manual batch with an ~80-word script.
- Watch it pick tier 48 (or 32 if word count lands lower) and submit Veo extension chain.
- Render and confirm final video > 32 seconds.

## Out of scope
- Changing topic-based-batch tier behavior.
- Refactoring the per-tier prompt-generation guidance (`prompt1_*`, `prompt2_*`) — only used by topic flow.
- Changing the 500ms TRIM_TAIL_MS.

## Acceptance criteria
1. Manual batch with 80-word script auto-resolves to tier 48 (or higher).
2. Final video duration > 32s after trim.
3. Existing topic-based batches still pick tier as before (8/16/32 only).
4. Pytest suite passes.
5. UI form shows live "Estimated video: ~Xs" as user types script.

## Lessons
(Filled in after corrections.)

## Review
(Filled in after implementation.)
