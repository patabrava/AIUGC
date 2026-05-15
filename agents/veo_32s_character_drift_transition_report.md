# Veo 32s Character Drift Transition Report

Date: 2026-05-15

## Finding

The stable no-drift behavior came from forcing 32s extended Veo prompts onto a short canonical character contract, regardless of the stored `video_prompt_json.character`.

The regression happened when `_build_veo_extended_base_prompt(...)` stopped forcing that short contract and instead kept the stored prompt character whenever it existed.

## Commit Trail

### Before stability: forensic character prompt

- Commit before `a523f1b`: `DEFAULT_CHARACTER` was the long forensic prompt:
  - long light-brown hair
  - subtle crow's feet
  - detailed brows, nose, lips, jawline, skin texture, build

This matched the kind of detailed prompt that can over-constrain Veo and produce identity changes between segments.

### Stable transition: `a523f1b`

- Commit: `a523f1b` - `veo: stabilize legacy 32s submission and extension chain`
- Change:
  - `DEFAULT_CHARACTER` became the short prompt:
    - `38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, and a warm light-medium skin tone. Friendly oval face and natural expression.`
  - `LEGACY_32_CHARACTER` kept the old long forensic description only as a legacy fallback.
  - Lean extension prompts were introduced:
    - `Same person as the previous segment...`

### Partial regression: `d5e29b9`

- Commit: `d5e29b9` - `Fix prompt character persistence and rebuilds`
- Change:
  - `DEFAULT_CHARACTER` became detailed again.
  - The short prompt moved into `LEGACY_SHORT_CHARACTER`.
  - Prompt sync began preserving/resolving stored character values.

This made the prompt builder more complex again, but by itself it was not the final 32s drift boundary because later routing still forced the 32s extended base prompt to the short contract.

### Stable 32s route restored: `0cc7607` / local `c515895`

- Commit: `0cc7607` - `Add Veo model selection and prompt fixes`
- Current local base: `c515895`
- Important behavior:
  - For extended routes, `_build_veo_extended_base_prompt(...)` forced:
    - `prompt_character = LEGACY_SHORT_CHARACTER if target_length_tier == 32 else DEFAULT_CHARACTER`
    - `prompt_scene = None`
    - `prompt_action = None`
    - `prompt_audio_block = None`

This prevented saved detailed prompt fields from reaching the 32s base segment.

### Character consistency mode: `09d3427`

- Commit: `09d3427` - `feat: character consistency mode with reference image video generation`
- Change:
  - Added batch `character_snapshot` and reference image routing.
  - Added scene plan support for `character_consistency` batches.
  - Still kept the 32s extended prompt override to `LEGACY_SHORT_CHARACTER`.

This commit added the new mode, but it did not appear to be the exact prompt-text drift transition.

### Actual current transition: `315725f` and later WIP

- Commit: `315725f` - `fix: harden script duration contracts`
- Current WIP repeated the same behavior while fixing sound/duration.
- Change:
  - Replaced the hard 32s override with:
    - `prompt_character = prompt_character or (LEGACY_SHORT_CHARACTER if target_length_tier == 32 else DEFAULT_CHARACTER)`
  - Stopped clearing `prompt_scene`, `prompt_action`, and `prompt_audio_block` in the same stable way.

Because 32s prompt rows usually already contain a stored `character`, this made the generated base prompt inherit the long `LEGACY_32_CHARACTER`/detailed prompt again.

## Fix Applied

Restored the 32s extended-route contract while preserving the sound/duration fixes:

- 32s extended base prompts now force `LEGACY_SHORT_CHARACTER`.
- 32s extended base prompts clear saved scene/action fields and use canonical legacy 32s visual defaults.
- Final-stop ending and audio block are still stripped from non-final base prompts.
- Segment budget validation remains active.

## Verification

Prompt simulation with a stored long `LEGACY_32_CHARACTER` now reports:

- `contains_short_character=True`
- `contains_long_legacy_character=False`
- `contains_default_detailed_character=False`
- `contains_edited_style=False`
- `contains_she_says=False`

Regression suite:

- `.venv/bin/pytest -q tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py tests/test_video_poller_extension_chain.py tests/test_vertex_ai_client.py tests/test_video_quota_guard.py tests/test_batches_manual_mode.py tests/test_topics_gemini_flow.py tests/test_topic_prompt_templates.py`
- Result: `209 passed, 22 warnings`
