# AIUGC Magnific Actor Identity Handoff

## Purpose

The next LLM should write an implementation plan, not code, for evolving AIUGC's current `character_consistency` mode into a no-face-drift workflow using the Magnific API as the chosen provider.

User intent:
- Current character consistency works, but the same 3 uploaded reference images make every generated video feel visually identical.
- The user wants scene freedom: prompts like "the actors in the bathroom" or "the actors in the car".
- The user explicitly does not accept face drift.
- The user has now decided to move forward with the Magnific API for LoRA-based identity training and scene-reference generation.

## Required Skills / Instructions For Next Session

Use:
- `superpowers:writing-plans` if available, because the next task is plan creation.
- `grill-with-docs` only if terminology or domain boundaries are still unclear; ask one question at a time.
- The repo's AGENTS instructions. Any plan must explicitly include `{files, LOC/file, deps}`.

Important repo instruction:
- Generated artifacts belong in `agents/`.
- Planning/debugging must follow `LLM_FRIENDLY_PLAN_TEST_DEBUG`.
- Plans should describe a single end-to-end implementation block with capability map, dependency map, boundary map, pass/fail criteria, and testscripts.

## Current Repo Context

Working directory:
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC`

Files already inspected in this session:
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/agents/canon.md`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/docs/character_consistency.md`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/docs/scene_consistency.md`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/characters/schemas.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/characters/queries.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/characters/handlers.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches/schemas.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/batches/queries.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/prompt_builder.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/veo_client.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/vertex_ai_client.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/llm_client.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/core/config.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_character_consistency_mode.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_video_duration_routing.py`
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_veo_prompt_contract.py`

Current implementation shape:
- `CharacterRecord` stores one active character with exactly three image URLs: front, three-quarter, profile.
- `character_consistency` batches snapshot that active character into `batch_data["character_snapshot"]`.
- The prompt builder can create a text `scene_plan`, but scene identity is not image-anchored.
- Video generation can load those three snapshot images and pass them as reference images in some provider paths.
- Vertex reference-image support is duration-sensitive in the current implementation: character refs are attached for 8s base requests, but skipped for 4s base routes with metadata reason `vertex_reference_images_support_only_8s_base`.
- Current Gemini/Nano image adapter is text-to-image only. It is not a training path and should not be presented as a no-drift solution.

Existing prior artifact worth referencing:
- `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/agents/reference_image_video_generation_handoff.md`

## Research Findings To Carry Forward

Provider pattern from HeyGen, Arcads, Tavus, Synthesia:
- They do not solve identity by repeatedly prompting a generic image model.
- They create a persistent trained avatar/actor/replica identity first, then render it into looks, scenes, or driven speech/video.
- This is the product architecture lesson: identity must be a first-class asset, not just prompt text.

Provider pattern from LoRA/image providers:
- A trained identity adapter/LoRA can generate still images of the same person in different places.
- This can produce scene-specific reference stills like "actor in bathroom" or "actor in car".
- It does not automatically guarantee video no-drift after image-to-video; the video stage can still mutate the face.
- Therefore the production pipeline needs validation gates and retries: face-similarity check on generated stills, then video-frame face-similarity check after VEO/other I2V output.

Recommended product concept name:
- `Actor Identity LoRA + Scene Reference Pack`

Avoid naming it:
- "Nano training"
- "prompt-only character consistency"

## Provider Findings, Verified 2026-05-19

Magnific is the selected provider for the first implementation.

Magnific Custom Character LoRA:
- Endpoint: `POST https://api.magnific.com/v1/ai/loras/characters`
- Requires API key header `x-magnific-api-key`.
- Requires `name`, `quality`, `gender`, and `images`.
- `images` must be public image URLs; required array length is 8-20.
- Supports optional `description` and `webhook_url`.
- Returns an async task id/status.
- Training status is checked via the LoRA listing endpoint.
- Source: https://docs.magnific.com/api-reference/mystic/post-loras-characters

Magnific Mystic generation:
- Endpoint: `POST https://api.magnific.com/v1/ai/mystic`
- Supports character LoRAs via prompt syntax like `@character_name` / `@character_name::strength` or via `styling.characters`.
- Important gotcha: LoRAs can be silently ignored if incompatible request options are used.
- Incompatible options include `structure_reference`, `style_reference`, combined references, and specific model selections like `fluid`, `flexible`, `super_real`, `editorial_portraits`.
- The API does not necessarily return an error when LoRAs are ignored, so the adapter must prevent incompatible combinations.
- Source: https://docs.magnific.com/api-reference/mystic/post-mystic

Other providers were reviewed only as context:
- fal.ai, Replicate, Astria, Leonardo, and VicSee are not the target for this handoff.
- The next plan should not expand scope into those providers unless it is needed for a narrowly defined future fallback story.

## Non-Negotiable Product Constraint

"No face drift" cannot mean "the prompt usually resembles the same person."

For the plan, define no-drift as an enforced workflow:
- Identity model is trained from curated references.
- Scene still generation must pass a face match gate against the canonical actor.
- Video generation must pass a frame-level face match gate against the canonical actor.
- Failed gates block publication and trigger retry/fallback.
- If no face can be detected or confidence is low, fail closed rather than accepting drift.

The plan should be honest that a LoRA improves identity in generated stills, but the video provider can still drift. The no-drift guarantee comes from identity asset + validation gates + retry/fallback, not from LoRA alone.

## Suggested Architecture Direction

High-level flow to plan:
1. Actor enrollment
   - User uploads/chooses 8-20 curated images for one actor.
   - Store public training image URLs in existing R2-backed storage.
   - Start Magnific LoRA training job.
   - Persist provider, training job id, LoRA id/name, status, and consent/source metadata.

2. Scene reference generation
   - For each post/video scene, build a scene-still prompt using the trained actor handle.
   - Generate one or more portrait 9:16 still candidates through Magnific.
   - Store generated candidate images and request metadata.

3. Still QA
   - Compare candidate face against canonical actor references.
   - Accept only if identity score is above threshold and image passes basic checks.
   - If all candidates fail, retry with adjusted prompt/strength or fall back to a conservative scene.

4. Video generation
   - Feed the approved scene still into the best available video provider path.
   - Current VEO/Vertex constraints must be preserved; do not regress 8s/16s/32s routing.
   - For providers/routes that cannot accept first-frame/reference input, plan should mark them incompatible with no-drift mode.

5. Video QA
   - Sample frames from generated video.
   - Run the same face match gate against canonical actor references.
   - If the score fails, retry or mark the video failed with a structured reason.

6. UX/state model
   - Character settings should show actor identity training status and scene-pack generation status.
   - Batch/post surfaces should make it clear whether a video used prompt-only, reference-image, LoRA still, or no-drift gated mode.

## Likely Code Areas For The Plan To Inspect

The plan should inspect current schema/migration conventions before naming exact migration files, but likely areas are:
- `app/features/characters/` for Actor Identity records, training status, and UI handlers.
- `templates/settings/character.html` for enrollment/status UI.
- `app/adapters/` for a new Magnific client adapter.
- `app/features/posts/prompt_builder.py` for scene-still prompt construction.
- `app/features/videos/handlers.py` for choosing approved scene stills and recording metadata.
- `app/core/config.py` for provider keys, feature flags, thresholds, and timeouts.
- `tests/test_character_consistency_mode.py` plus new focused tests for provider payloads and gating decisions.

Possible new terms the plan may formalize:
- `ActorIdentity`: trained, durable person asset.
- `SceneReferenceImage`: generated still for a particular actor + scene + post.
- `IdentityProviderJob`: async LoRA training/generation task.
- `IdentityGate`: face similarity / validation result.

## Plan Output Requirements

The next LLM's plan should include:
- Context-Zero: OS, Python version, dependency pins, provider env vars needed, existing feature flags, current video provider matrix.
- Capability map: actor enrollment, LoRA training, scene still generation, still QA, video submission, video QA, UX/status, observability.
- Boundary map: R2 storage, Supabase tables, Magnific APIs, VEO/Vertex video APIs, background/webhook handling.
- Dependency map: default to zero new deps; justify any face-similarity dependency as the only tool for that concern.
- Files table with `{files, LOC/file, deps}` as required by AGENTS.
- Testscripts: unit/contract tests, provider payload tests, mocked async job tests, optional live paid-provider smoke script gated behind explicit env flags.
- Pass/fail criteria:
  - LoRA training job can be created from 8-20 public images.
  - Invalid/incompatible Magnific LoRA generation options are rejected locally.
  - Generated scene reference images are persisted with metadata.
  - Video generation refuses no-drift mode if no approved scene reference exists.
  - The current 8s/16s/32s character consistency behavior does not regress.
  - Drift failures are structured and visible, not silent.

## Risk Notes For The Plan

Privacy/rights:
- Training a recognizable person requires explicit rights/consent handling. The plan should include minimal metadata to record consent/source, even if the first implementation is internal.

Provider drift:
- Magnific docs may change. Refresh docs before finalizing exact request fields.

Silent provider failure:
- Magnific can silently ignore LoRAs with incompatible options. The plan must include local compatibility guards and metadata proving which LoRA was requested.

Video-stage drift:
- A LoRA scene still is not enough for no drift. The plan must include post-video QA or a hard statement that no-drift is not guaranteed.

Cost:
- Training, still generation, and video retries are paid paths. Plan should include feature flags and explicit live-test gates.

Current Nano/Gemini path:
- Treat Nano/Gemini image generation as optional helper for prompt refinement or image cleanup only. Do not plan on it as the identity-locking mechanism unless official docs expose trainable identity adapters through API.

## Open Questions For The Plan Writer To Resolve

1. Does the MVP train exactly one active actor first, or support multiple Actor Identities immediately?
   - Recommended: one active actor first, matching current `get_active_character()` mental model.

2. Should the implementation preserve a thin provider interface for later reuse, even though Magnific is the only immediate target?
   - Recommended: yes, but keep the first implementation Magnific-only.

3. What is the first acceptable identity gate?
   - Recommended: start with a feature-flagged identity gate. If a local face embedding dependency is too heavy, define the adapter boundary and start with a conservative manual/LLM visual-review gate only for prototype, but do not call that "no drift" in production.

4. Should scene references be generated at batch creation time or right before video submit?
   - Recommended: generate per post before video submit so failures are isolated and can be retried per post.

5. How should 16s/32s VEO routes handle no-drift mode if the stable path uses 4s base and reference images are currently skipped there?
   - Recommended: plan must explicitly preserve legacy 32s routing and define no-drift compatibility per route, rather than forcing all durations into one provider path.

## Suggested Handoff Sentence For The Next LLM

Write a production-grade implementation plan for Magnific-backed `Actor Identity LoRA + Scene Reference Pack` in AIUGC. Preserve current character-consistency/VEO duration constraints, and design the workflow so "no face drift" is enforced by trained identity plus gated still/video QA rather than prompt-only generation.
