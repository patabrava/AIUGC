# Character Consistency Light Handoff

Date: 2026-05-22
Repo: `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC`
Scope: add `character_consistency_light` as a fourth batch mode that reuses ActorIdentity and approved scene references, but switches Veo prompt construction to the lean reference-image contract.

## Outcome

`character_consistency_light` now exists as a real batch mode beside `automated`, `manual`, and `character_consistency`.

It keeps the same identity-locking surface as the existing character-consistency flow:

- requires a ready active `ActorIdentity` at batch creation
- requires approved scene reference images before video submission
- forces full Veo 3.1
- stays on the Vertex route for duration-routed submissions

What changed is the prompt contract:

- base prompt uses a lean reference-image-driven motion/audio/dialogue prompt
- extension prompts use a lean continuity prompt instead of the older detailed character/scene/cinematography template
- the worker reads `creation_mode` from stored `video_metadata` so extension hops stay on the light prompt path

## Routing Behavior

8s:
- short single submission
- light base prompt

16s:
- forced to the efficient long-route profile for light mode only
- `8s` base plus `1` extension hop of `7s`
- this is intentional so the base request can still attach reference images

32s:
- uses extended routing with the light base prompt plus lean extension prompts
- existing duration validation still applies; the mode does not bypass script-length contracts

## Files Changed

- `app/features/characters/actor_identity.py`
- `app/features/batches/schemas.py`
- `app/features/batches/queries.py`
- `app/adapters/veo_client.py`
- `app/features/posts/prompt_builder.py`
- `app/core/video_profiles.py`
- `app/features/videos/handlers.py`
- `workers/video_poller.py`
- `templates/batches/list.html`
- `templates/batches/detail/_post_card.html`
- `templates/batches/detail/_video_settings.html`
- `tests/test_character_consistency_mode.py`

## Exact Submitted Prompts

These are the exact prompts used in the live lean test generation for the latest character-consistency batch (`f84894a7-e6c4-4942-8327-53b1c1b27ea2`, post `39696109-10ce-4067-9458-6197e8f2938b`).

### Base Prompt

```text
Action:
The referenced woman sits in the referenced wheelchair setup and speaks directly to camera in one continuous natural smartphone take. Keep her identity, wardrobe, room, lighting, camera distance, and framing matched to the reference images. Use small natural hand gestures and subtle upper-body nods while speaking.

Language:
Speak only in German, with natural conversational pacing.

Dialogue:
Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht. Mit einer klaren Routine bleibst

Ending:
Continue directly into the next segment with no concluding pause, no scene-ending hold, and no visual reset.

Audio:
Natural single-speaker smartphone room audio. Clear close voice. No music. No background voices.
```

### Extension Prompt

```text
Action:
Continue from the previous generated segment with the same referenced woman, same wheelchair setup, same room, same wardrobe, same lighting, same camera position, and same speaking rhythm. Do not redesign the person or the environment. Continue as one seamless smartphone take.

Language:
Speak only in German. Maintain the same voice, accent, and speaking pace from the previous segment.

Dialogue:
du im Alltag trotzdem deutlich entspannter. So bleibt dein Tag klarer und planbarer.

Ending:
After the final spoken word, speech stops completely. Her mouth closes and comes fully to rest. She holds a gentle natural expression for a brief moment before the clip ends.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices. Let the room tone settle briefly after the final word.
```

### Negative Prompt

```text
subtitles, burned-in subtitles, auto-generated subtitles, closed captions, lower-third captions, karaoke text, speech transcription overlays, captions, watermark, text overlays, words on screen, readable typography, UI text, logos, branding, poor lighting, blurry footage, low resolution, unwanted objects, character inconsistency, lip-sync drift, cartoon styling, unrealistic proportions, distorted hands, artificial lighting, oversaturation, excessive camera shake, background voices, music bed, audio hiss, static, clipping, abrupt cuts, angle changes, mirror appearing or disappearing, layout changes, background drift, new furniture, wall color change, bedding color change, different room, lighting shift
```

## Live Verification Artifact

The lean prompt was verified with a real Vertex submission and uploaded to R2:

- base MP4: `https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260522T082550Z_lean-character-consistency-f84894a7-e6c4-4942-8327-53b1c1b27ea2-base.mp4`
- extended MP4: `https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260522T082552Z_lean-character-consistency-f84894a7-e6c4-4942-8327-53b1c1b27ea2-extended.mp4`

Live run details:

- seed: `1403591871`
- base operation: `projects/project-b386e56f-c889-4762-ab3/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/17705388-2f59-49ab-9409-7a9f48b51ac4`
- extension operation: `projects/project-b386e56f-c889-4762-ab3/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/b8455ce3-2daf-4f36-aa71-df9608001bba`

## Tests Run

Focused regression coverage that passed after the implementation:

```bash
python3 -m py_compile app/features/characters/actor_identity.py app/features/batches/schemas.py app/features/batches/queries.py app/features/posts/prompt_builder.py app/core/video_profiles.py app/features/videos/handlers.py workers/video_poller.py app/adapters/veo_client.py

python3 -m pytest tests/test_character_consistency_mode.py tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py tests/test_batches_manual_mode.py tests/test_batches_status_progress.py -q
```

Result:

- `154 passed`

## Important Design Boundaries

- `character_consistency` remains the existing richer prompt mode; this change did not rewrite that mode into the lean prompt.
- `character_consistency_light` is a separate opt-in mode so both contracts can be compared live.
- `ensure_scene_plan(...)` still only runs for the existing `character_consistency` mode. Light mode intentionally does not depend on generated scene-plan text.
- 16s light mode uses the efficient long route only for that mode. The general profile table was not globally changed.

## Suggested Next Session

- live-create one `character_consistency_light` batch in the UI
- submit 8s, 16s, and 32s runs
- compare drift and scene adherence against the existing `character_consistency` mode
- if the light mode is clearly better, consider collapsing the old mode onto the light prompt contract or hiding the older mode
