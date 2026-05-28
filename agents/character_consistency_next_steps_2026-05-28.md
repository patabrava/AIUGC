# Character Consistency Next Steps

Date: 2026-05-28
Scope: follow-up work after removing text scene prompts from reference-image-driven Character Consistency video generation.

## Current State

- `character_consistency`, `manual_character_consistency`, and `character_consistency_mid` now submit Veo prompts without a text `Scene:` block.
- Live 8s verification succeeded against approved three-image scene reference set `7a977f57-b8a4-402f-9e7d-928f6aa16071`.
- The current prompt still includes a text `Character:` block and a text `Style:` block even though Veo 3.1 reference asset images already provide subject and scene information.

## Doc-Based Reading

- Veo 3.1 reference asset images support up to three subject images and are meant to preserve the subject appearance in the output video.
- Veo best practices for image-conditioned generation say the source image already provides the subject, scene, and style.
- The same guidance says not to re-describe the character, background, or lighting, and to refer to the person with general terms such as `the woman` or `the subject`.

## Immediate Next Step

Tighten the Character Consistency prompt contract one step further:

- Remove the detailed `Character:` text block for reference-image-driven modes.
- Remove the explicit `Style:` text block unless live tests show it is needed.
- Keep only:
  - generic subject language such as `the woman`
  - motion direction
  - camera/framing direction
  - dialogue
  - ending/audio constraints

## Recommended Validation Pass

Run a focused live A/B test on the same approved three-image set:

1. Current deployed prompt:
   includes `Character:` and `Style:` blocks.
2. Motion-only prompt:
   generic subject wording, no character description, no text scene description, minimal style wording.

Compare:

- face/identity lock
- wardrobe retention
- room/layout retention
- framing stability
- gesture naturalness
- lip sync / speech timing

Accept the slimmer prompt only if it matches or beats the current output on identity and scene retention.

## Secondary Follow-Ups

- Check whether `character_consistency_light` should also lose any remaining text that re-states subject/style details.
- Audit whether preview-only model IDs should be moved to current GA Veo 3.1 endpoints after prompt behavior is stable.
- Keep prompt language operationally simple: reference images define identity and environment; text should define motion and delivery.

## Non-Goals For The Next Pass

- No provider client rewrite.
- No database migration.
- No new Character Consistency mode.
- No scene-plan changes outside reference-image-driven modes.
