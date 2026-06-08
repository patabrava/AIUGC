# VEO 3.1 Reference Prompt Conflict Handoff

Date: 2026-06-09

## Issue

The current AIUGC 8-second `character_consistency` / canonical-scene workflow appears to overconstrain Veo 3.1:

- 2 actor anchor images define the person.
- 1 canonical scene image defines the environment.
- The text prompt still includes a full written `Character:` description of the woman.

The open question is whether that extra written identity description is hurting facial motion quality by competing with the identity already present in the reference images.

## Current Local Evidence

Relevant test artifact:
- [`output/video-generation-tests/canonical-scene-matrix-20260608/state.json`](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/output/video-generation-tests/canonical-scene-matrix-20260608/state.json)

Observed request contract in the saved runs:
- `reference_image_roles = ["actor_identity_anchor", "actor_identity_anchor", "canonical_scene_anchor"]`
- `source = "actor_identity_plus_canonical_scene_anchor"`
- prompt still begins with a generic written block:
  - `38-year-old German woman with shoulder-length light brown hair... hazel eyes...`

Observed visuals:
- Identity is mostly stable across the six test videos.
- The artifact looks more like mouth / blink / expression instability than full subject replacement.
- That makes the most likely failure mode "identity overconstraint during talking-head animation", not "wrong person entirely".

## Current Code Path

Prompt builder:
- [`app/features/posts/prompt_builder.py`](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/prompt_builder.py)
  - `build_reference_image_scene_base_prompt(...)`
  - fallback character text still resolves to `LEGACY_SHORT_CHARACTER`

Reference image assembly:
- [`app/features/videos/handlers.py`](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py)
  - `_load_actor_identity_anchor_assets(...)`
  - `submit_video_generation_request(...)`

Current runtime contract:
1. Resolve canonical scene asset from scene key.
2. Attach first 2 actor identity anchors.
3. Attach 1 canonical scene anchor.
4. Send those 3 images to Veo 3.1.
5. Also send a text prompt that still restates character phenotype, scene identity, action, dialogue, ending, and audio.

## What The Latest Docs Say

### Google Cloud / Vertex / Agent Platform

Reference-image support:
- Google documents subject reference-image support for Veo and says you can provide up to 3 images of a single subject.
- Source:
  - [Guide video generation using asset and style images](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/use-reference-images-to-guide-video-generation)

API contract:
- Google documents `referenceImages` as up to 3 asset images or 1 style image.
- Asset images may represent a scene, object, or character.
- Source:
  - [Veo on Vertex AI video generation API](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation?hl=en)

Most important prompt guidance:
- Google explicitly says that when an image is already providing subject / scene / style, the prompt should focus on motion.
- Google explicitly warns that re-describing the character, background, or lighting already depicted in the image can confuse the model and degrade results.
- Google also recommends using general terms like `the subject`, `the woman`, `she`, or `they`.
- Source:
  - [Best practices for Veo on Gemini Enterprise Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/best-practice)

### Google AI Developers Forum

There are forum threads showing that reference-image support has had capability / aspect-ratio inconsistencies depending on model surface and configuration.
- One forum reply states `referenceImages` support had limitations around `9:16` in at least some API contexts.
- Sources:
  - [Veo 3.1 Reference Images - Docs Say Available, API Says "Not Supported"](https://discuss.ai.google.dev/t/veo-3-1-reference-images-docs-say-available-api-says-not-supported/111853)
  - [Veo 3.1 API aspect ratio parameter](https://discuss.ai.google.dev/t/veo-3-1-api-aspect-ratio-parameter/107902)

These threads should not override the official docs, but they are useful evidence that Veo 3.1 reference-image behavior is still somewhat surface-sensitive and not perfectly stable across all routes.

### Magnific

Magnific's Veo 3.1 Reference-to-Video docs describe the intended reference workflow as:
- 1-3 reference images
- prompt describes the target video scene
- fixed 8-second duration
- character / object consistency is the main goal

Source:
- [Magnific Veo 3.1 Reference-to-Video Overview](https://docs.magnific.com/api-reference/reference-to-video/veo-3-1/overview)

## Working Hypothesis

The current prompt is likely too verbose for a reference-image-driven talking-head workflow.

Why:
1. The actor images already define face, age band, hair, and skin texture.
2. The canonical scene image already defines room / curb / bathroom layout and lighting.
3. Google now explicitly advises against re-describing those same details in the prompt.
4. Our current prompt still re-describes both identity and environment in text.

Likely result:
- Veo tries to satisfy the exact actor face from the images.
- Veo also tries to satisfy a generic textual woman description.
- Veo also tries to satisfy the canonical scene plate.
- During speech animation, that extra identity pressure shows up as mouth / eye / expression weirdness.

This is an inference from the docs plus our saved prompts and generated outputs. It is not yet proven by an A/B rerun with a leaner prompt.

## Recommended Next Experiment

Run a strict A/B/C comparison on the same actor, same canonical scene image, same dialogue, same duration, same provider route:

### Variant A: Current contract

- 2 actor anchors
- 1 canonical scene anchor
- full written `Character:` block
- full written `Scene:` block
- full action block

Purpose:
- establish the baseline already observed

### Variant B: Reference-first lean contract

- 2 actor anchors
- 1 canonical scene anchor
- no detailed `Character:` phenotype block
- keep only general identity references like `the woman` / `the subject`
- keep `Scene:` only as a light semantic label or scene guardrail, not a full environment re-description
- prompt mainly for:
  - speaking behavior
  - framing
  - hand gestures
  - end hold
  - audio

Purpose:
- align the request with Google's "prompt for motion only" guidance as closely as possible while still preserving enough control for scene behavior

### Variant C: Hybrid guardrail contract

- 2 actor anchors
- 1 canonical scene anchor
- no phenotype description
- keep minimal scene guardrails only for forbidden additions and framing boundaries
- keep action block but refer only to `the woman` / `the subject`

Purpose:
- test whether sparse scene guardrails help preserve environment consistency without re-describing the entire image

## Suggested Acceptance Criteria

Judge each variant on:

1. Facial motion quality
   - mouth naturalness
   - blink quality
   - expression stability during speech

2. Identity lock
   - same face across frames
   - no sudden age / cheekbone / eye-shape drift

3. Scene lock
   - same living room / bathroom / curb identity
   - no unlisted props

4. Wardrobe stability
   - not yet solved by the current 3-image contract, but should still be tracked

## Expected Best Direction

Most likely winner:
- Variant B or C, not Variant A

Reason:
- That is the closest match to Google's published guidance for image-driven Veo workflows.

## If The Lean Prompt Works

Then the follow-up implementation should:
1. remove the detailed hardcoded `Character:` block from reference-image-driven VEO 3.1 prompts
2. switch to general subject wording
3. keep only motion / framing / action / ending / audio instructions
4. preserve scene consistency through the canonical scene anchor first, not text duplication

## If The Lean Prompt Fails

Then the next likely causes are:
1. speech animation is the dominant artifact source, not identity conflict
2. 9:16 + reference-image route still has provider instability
3. canonical scene image itself may be over-specifying lighting / framing in a way that fights facial animation
4. the current actor anchors may be too portrait-still and not expressive enough for speech motion

## Practical Next Step For The Next LLM

Do not start by changing the whole workflow.

Start with one narrowly-scoped live experiment:
1. same actor
2. same canonical living-room anchor
3. same single dialogue line
4. current route
5. compare current prompt vs lean prompt

Only after that A/B result should the production prompt contract be changed.
