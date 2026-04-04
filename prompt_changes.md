# Prompt Changes

Date: 2026-03-16

## Goal

Simplify the talking-head prompt so the generated video stays more stable and less visually divergent while remaining aligned with the Veo 3.1 prompt guide.

## What Changed

### 1. Character block simplified

Removed:
- dense facial geometry
- eyebrow detail
- micro facial detail
- `hyper-realistic skin texture with visible pores`

New direction:
- simpler age, hair, eye color, skin tone, and expression description
- natural human description without over-constraining the face

Current character block:

```text
Character:
38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin texture; slim build with relaxed upright posture.
```

### 2. Explicit style block added to the optimized prompt

Reason:
- Veo documentation recommends including style as a core prompt element.
- The previous prompt implied style, but did not label it explicitly in the simplified provider prompt.

Current style block:

```text
Style:
Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, soft flattering indoor light, and natural skin texture.
```

### 3. Motion system simplified

Removed:
- `Golden Face/Look Anchor`
- `micro jitter`
- autofocus-lock language
- hard camera-distance measurements
- overly specific stabilization and framing constraints

Replaced with one stable camera rule:

```text
Cinematography:
Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie distance. The camera is handheld but stable, with only minimal natural movement. The framing remains consistent throughout the shot without noticeable camera drift or reframing.
```

### 4. Action block reduced

Old direction:
- more detailed head-and-shoulders stabilizing language
- stronger motion-lock phrasing

New direction:
- speaking to camera
- natural conversational pacing
- small natural gestures
- brief still hold at the end

Current action block now includes:
- `She speaks at a natural conversational pace`

### 5. Fallback schema defaults cleaned

The stored schema defaults in `app/features/posts/schemas.py` were updated so old prompt baggage cannot leak back in through non-optimized code paths.

Updated fields:
- `character`
- `action`
- `style`
- `cinematography`
- `color_and_grade`
- `camera_positioning_and_motion`
- `focus_and_lens_effects`
- `authenticity_modifiers`

Removed from fallback defaults:
- visible pores wording
- autofocus instructions
- `20–30 cm` camera distance
- `micro jitter`
- `Golden Face/Look Anchor`

Additional applied improvements:
- added shoulder-length hair to reduce hairstyle drift
- added authentic influencer-style delivery to strengthen UGC speech prior
- added conversational pacing to stabilize dialogue timing
- added partial wheelchair visibility to reduce object cropping drift
- changed the audio intro from `quiet indoor bedroom` to `quiet indoor room` to avoid repeated room labeling

## What Stayed the Same

- The single normalized audio block remains in place.
- Veo still uses a separate `negativePrompt`.
- Sora still uses inline negative constraints.
- Dialogue remains quoted.
- The end-of-line audio decay remains natural room tone, not dead silence.

## Why This Is Better

- Fewer conflicting motion instructions
- Less facial over-specification
- Lower risk of face inflation and unstable talking-head behavior
- Better alignment with Veo’s recommended prompt structure:
  - subject
  - action
  - style
  - camera/composition
  - dialogue
  - ambience

## Verification

Regression coverage was updated in:

- `agents/testscripts/testscript_video_prompt_audio.py`

The test now verifies:
- simplified stable camera wording is present
- banned terms like `golden face`, `micro jitter`, `autofocus`, `20–30 cm`, and `visible pores` are absent
- Veo prompt and Veo `negativePrompt` split still works correctly
