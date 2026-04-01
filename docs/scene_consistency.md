# VEO 3.1 Scene Consistency Research

**Date:** 2026-04-01  
**Status:** Research complete, implementation pending

---

## Current State Analysis

### What We Do Today (`app/features/posts/prompt_builder.py`)

- **Single static scene block** - `DEFAULT_SCENE` exists, but it is still fairly generic and leaves room for prop drift
- **Scene is embedded in the final prompt** - `OPTIMIZED_PROMPT_TEMPLATE` includes a dedicated `Scene:` section, which is the right place to lock environment details
- **Negative prompt coverage is broad, not scene-specific** - `VEO_NEGATIVE_PROMPT` covers artifacts and motion issues, but it does not yet explicitly forbid background mutations
- **The prompt shape already separates subject, action, scene, cinematography, dialogue, and ending** - this is good, because scene consistency fails faster when those concerns are mixed together
- **The current runtime is text-first** - the active prompt path does not yet use image-conditioning as the main scene-locking mechanism

### Observed Drift Pattern

The generated clips are not drifting in the character only. The environment is also mutating:

- A plant appears in some clips and disappears in others
- A mirror appears in some clips and is missing in others
- Wall color and roof-line details change between generations
- Bed color and furniture placement vary

That means the model is treating the scene as a loose suggestion instead of a fixed set.

### Current Runtime Truth

For scene consistency, the live code path still behaves like a prompt-driven generator:

- The scene is described in text and not anchored by a canonical scene asset
- The model is free to invent or swap background elements unless the prompt strongly constrains them
- Extension chaining helps carry context forward, but it does not fully lock the set if the base clip drifts

---

## What The VEO 3.1 Docs Actually Support

### 1. Reference Images Can Lock Scene, Object, Or Character

Google's Vertex AI docs describe `referenceImages` with `referenceType: "asset"` as valid for:

- a scene
- an object
- a character

That is the clearest official lever for scene consistency when image guidance is available.

Important caveat: `referenceImages.style` is not supported on VEO 3.1, only on VEO 2.0.

### 2. Image-to-Video Uses The Source Image As The Basis For Everything

The docs state that when using image-to-video, the source image already supplies the subject, scene, and style. In practice, that means the prompt should focus on motion, not on re-describing the set.

This is the strongest official guidance for scene consistency:

- make the source frame match the desired room
- keep the prompt focused on movement
- avoid reintroducing new background objects in text

### 3. Short Clips Should Stay In One Scene

Google's best-practice guidance is explicit: short videos should stay focused on a single scene. Chaining multiple distinct events into one prompt makes the result muddier and increases set drift.

That maps directly to the issue we are seeing. If the clip is trying to do too much, the model compensates by mutating the room.

### 4. Use A Strong Source Image If You Want Continuity

The docs say a high-quality source image is the basis for the rest of the clip. For scene locking, that means a clean canonical room frame is more valuable than a long prose description of the room.

---

## Validated Findings

### Official Capability Summary

From the current Vertex AI docs:

- `veo-3.1-generate-preview` and `veo-3.1-generate-001` support text-to-video, image-to-video, prompt rewriting, reference asset images, extend videos, and first/last frame generation
- Supported aspect ratios include `9:16` and `16:9`
- Reference image-to-video only supports `8` seconds
- `referenceImages` can describe a scene, object, or character

### Practical Limitation On Our Current Surface

The repo previously validated that the Gemini Developer preview surface rejected `image.inlineData` for our use case. So while the docs support image-guided consistency in principle, the current runtime path should not assume first-frame anchoring or reference-image locking is available without confirming the exact provider surface.

### Meaning For Scene Consistency

There are two different levels here:

1. **Prompt-only scene stabilization** - available now, should be improved immediately
2. **Asset-anchored scene locking** - likely the strongest long-term option, but it depends on the provider surface actually accepting the image payloads we need

---

## Prompt-Only Scene Techniques

These are the best immediate controls when the model is still text-first.

### 1. Make The Scene A Locked Inventory, Not A Vibe

Use an explicit room inventory and keep it identical across clips.

Good scene elements to freeze:

- wall color
- bed color and bedding
- window placement
- lamp type and position
- mirror presence or absence
- plant presence or absence
- desk, chair, shelf, artwork, and other furniture

The scene should read like a fixed set design, not a loose interior style.

### 2. Treat The Scene As A Canonical Bible

Write a single canonical scene description and reuse it verbatim.

The scene bible should cover:

- room type
- exact wall color
- exact visible furniture
- exact decor count
- daylight direction
- light source type
- camera position relative to the room

This is the scene equivalent of a character bible.

### 3. Lock The Camera Before You Lock The Motion

Scene drift often gets worse when camera motion is too open-ended.

Prefer:

- fixed selfie framing
- minimal handheld movement
- no reframing
- no orbit shot
- no angle change

If the camera moves too much, the model tends to reveal or invent additional set elements.

### 4. Use Negative Prompts For Background Mutation

Negative prompts should explicitly forbid common scene mutations.

Useful negatives:

- no new furniture
- no extra plants
- no mirror appearing or disappearing
- no wall color change
- no bedding color change
- no new artwork
- no layout changes
- no different room
- no lighting shift

Negatives should support the prompt, not replace it.

### 5. Keep Each Short Video To One Visual Situation

If a clip tries to move from one emotional beat to another, the model may rewrite the room to match the new beat.

Better:

- one room
- one camera setup
- one lighting setup
- one emotional beat
- one movement goal

### 6. Use Seed Stability As A Secondary Control

Seed helps reduce variance, but it is not a scene lock on its own.

Use the same seed only as a supporting measure after the scene description, camera, and negatives are already stabilized.

---

## Asset-Anchored Scene Techniques

These are the strongest options when the provider surface supports them.

### 1. Use A Canonical Scene Frame

Create one reference image that contains the exact room layout you want.

The canonical frame should include:

- the exact bed
- the exact wall color
- the exact window
- the exact lamp
- the exact mirror or no mirror
- the exact plant or no plant
- the exact decor balance

If the scene image is stable, the video is much less likely to invent new set elements.

### 2. Use Reference Images For The Scene, Not Just The Character

Google's docs allow `referenceImages` to describe a scene. That matters because a character portrait alone cannot stabilize the room.

For scene consistency, the hierarchy should be:

1. scene reference
2. character reference
3. motion prompt

### 3. Keep The Motion Prompt Strictly About Movement

When the source image already defines the scene, the prompt should say what moves, not what the room looks like.

Good motion directions:

- slight head turn
- small hand gesture
- gentle shoulder movement
- subtle blink
- slow camera push-in

Bad motion directions:

- re-describe the bed
- re-describe the wall
- introduce a plant
- add a mirror
- change the room mood

### 4. Prefer One Source Image Over A Loose Text Set

If the provider accepts it, a clean source image is stronger than a long prose prompt because it removes ambiguity about the room layout.

---

## Recommended Scene Prompt Structure

Use a fixed order so the prompt becomes reusable.

```text
Scene:
[room type], [wall color], [window placement], [bed color], [lamp position], [mirror yes/no], [plant yes/no], [decor count], [lighting direction], [time of day], [camera relation to room]
```

Then keep the rest of the prompt separate:

```text
Action:
[what the subject does]

Cinematography:
[fixed camera framing]

Ending:
[how the clip resolves]
```

This makes the room a contract instead of a suggestion.

---

## Scene Bible Example

```text
A tidy modern bedroom with soft blush-pink walls, a white bed with neutral bedding, a single small potted plant on the windowsill, a round wall mirror mounted on the right side of the frame, and one warm bedside lamp visible in the background. Natural daylight enters from camera-right. The room is uncluttered, with no additional furniture changes, no extra decor, and no layout variation across clips. The camera stays at the same front-facing selfie angle throughout.
```

That is stronger than a generic "modern bedroom" prompt because it defines the fixed inventory.

---

## Practical Recommendation For This Repo

### Immediate

- Expand `DEFAULT_SCENE` into a true scene bible
- Add scene-specific negative constraints
- Keep the prompt in a single fixed room setup for the whole batch
- Avoid giving the model room to invent new background props

### Next Step If We Want Stronger Locking

- Add a canonical scene reference image
- Pass that image into the provider surface only after confirming the exact Veo surface supports the payload we need
- Use the image as the scene anchor and keep the prompt motion-only

### Best Order Of Operations

1. Lock the scene in text first
2. Lock the camera second
3. Lock the seed third
4. Add image anchoring when the provider surface is confirmed

---

## Decision Rule

Use prompt-only control when we want the smallest change.

Use a reference scene image when the room must stay identical across clips and we can verify the provider accepts the image payload on the active surface.

If the room is still drifting after that, the prompt is still too open-ended or the shot is trying to do too much in one generation.

---

## Sources

- [Best practices for Veo on Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice)
- [Veo 3.1 model documentation](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate#3.1-generate-preview)
- [Guide video generation using asset and style images](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/use-reference-images-to-guide-video-generation)
- [Veo on Vertex AI video generation API reference](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation)
- [Ultimate prompting guide for Veo 3.1](https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1)

