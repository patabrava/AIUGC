# Fix: actor ignored on 32s character-consistency videos

## Root cause (confirmed by code read)
For character-consistency / ActorIdentity videos, the selected actor is overwritten by
hardcoded "generic German woman" character text — worse the longer the video:

- **Base prompt, tier 32 only** (`handlers.py:1543`): `prompt_character = LEGACY_SHORT_CHARACTER`
  replaces the post's real character. Tier 16 (else branch) keeps the real character.
- **Every extension hop** (`prompt_builder.py:221`, `LEAN_EXTENSION_CHARACTER`): re-asserts the
  same hardcoded stranger. 32s has 3 hops; 16s has 1; 8s has 0.

Net: 8s ok / 16s ok (1 nudge) / 32s broken (base override + 3 nudges) -> actor "completely ignored".
The light-mode continuation template already does the right thing (defer to previous segment,
no hardcoded face), proving the pattern.

## Plan
- [x] 1. Scope the tier-32 legacy character/style override to NON character-consistency modes
      so CC tier-32 keeps the real per-post character (matches tier-16 behavior).
- [x] 2. Replace `LEAN_EXTENSION_CHARACTER` hardcoded person with a continuity-deferring
      identity-preservation directive (mirrors the working light-mode continuation).
- [x] 3. Add regression tests: CC tier-32 base uses post character (not LEGACY_SHORT_CHARACTER);
      extension prompt defers to previous segment instead of hardcoding the specific face.
      Existing automated/topic 32s tests stay green (they legitimately use the generic persona).
- [x] 4. Run tests locally — 192 passed across duration-routing, veo-prompt-contract,
      character-consistency, actor-identity, and poller-transition suites.
- [ ] 5. Commit + push + watch deploy green.

## Review
Two-line root cause: tier-32 base prompt hardcoded `LEGACY_SHORT_CHARACTER` (overriding the
selected actor — tier 16 didn't), and every extension hop hardcoded `LEAN_EXTENSION_CHARACTER`
(a generic stranger). 32s = base override + 3 hops -> actor erased; 16s = 1 hop -> survived;
8s = base only -> fine. Fix scopes the tier-32 override to non-CC modes and makes extension
hops preserve identity by deferring to the previous segment. Updated one test that pinned the
old extension text; added two regression tests.

---

# Segmented (drift-free) video route — IO wiring (2026-06-10)

## Goal
Make `VEO_ENABLE_SEGMENTED_ROUTE=true` run end-to-end: N independent 8s reference-anchored Veo
generations stitched with ffmpeg, captioned as usual. Eliminates extend-chain character drift by
re-anchoring the actor reference bundle on every segment instead of chaining off a drifted frame.

## Done
- [x] `video_profiles.py`: `stitching` added to pollable statuses.
- [x] `schemas.py`: `stitching` added to `VideoStatusResponse` Literal.
- [x] `segmented_pipeline.py`: `plan_segment_submissions` accepts pre-built mode-aware prompts.
- [x] `handlers.py`: `_split_script_into_segments`, `_build_segmented_segment_prompts`
      (CC → `build_reference_image_scene_base_prompt`; light → `build_lean_veo_base_prompt`;
      ending only on last), `_submit_segmented_post`, `_build_segmented_submission_metadata`.
- [x] `handlers.py`: segmented branch in `generate_video` (single) + `generate_all_videos` (batch).
- [x] `video_poller.py`: `_handle_segmented_video` (+ `_poll_single_segment`,
      `_download_segment_bytes`, `_stitch_and_store_segments`, `_fail_segmented_post`);
      early dispatch in `process_video_operation`.
- [x] Tests: 29 segmented tests pass (5 new IO-wiring). Full sweep 120 passed, 4 pre-existing
      `veo_client` env failures (identical on `main`). Zero regression to `veo_extended`.

## Not verifiable here (user smoke test)
- [ ] R1: Veo accepts 9:16 8s `referenceImages` live.
- [ ] Stitched 9:16 output correct; captions burn across full duration.
- [ ] Commit + push (awaiting go-ahead).
