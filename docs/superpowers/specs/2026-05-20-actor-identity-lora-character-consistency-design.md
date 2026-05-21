# Actor Identity LoRA Character Consistency Design

Date: 2026-05-20
Scope: `character_consistency` mode only
Status: Draft

## Summary

Evolve the existing `character_consistency` mode from a three-reference-image snapshot workflow into an ActorIdentity-backed workflow. The operator uploads an explicit 8-20 image ActorTrainingSet on the actor settings page. The app trains one ActiveActorIdentity LoRA with Magnific, polls training progress until ready, then auto-enables `character_consistency` for new batches.

For each post, the approved script drives a deterministic ScriptIntentMap that chooses one approved scene from the SceneCatalog and one approved look from the WardrobeSet. Magnific generates fresh SceneReferenceImage candidates from the trained ActorIdentity. The app gates the still image, surfaces it for operator review before video generation, then feeds approved scene references into the video path. Existing CharacterSnapshot-based batches remain usable as legacy batches.

## Current Context

The current repo already supports `creation_mode = "character_consistency"` with a single active character stored as three URLs: front, three-quarter, and profile. New character-consistency batches snapshot those URLs into `batches.character_snapshot`, and video submission can attach them as reference images on supported provider routes.

The new workflow keeps the same user-facing mode name but changes the identity source for new work. The legacy three-image snapshot path remains valid for batches created before this feature lands.

## Glossary Terms

The canonical language lives in `CONTEXT.md`. This spec depends on these terms:

- `ActorIdentity`
- `ActiveActorIdentity`
- `ActorTrainingSet`
- `CharacterConsistencyMode`
- `TrainingReadinessGate`
- `TrainingProgressPolling`
- `TrainingProgressDisplay`
- `AutoEnableOnTrainingComplete`
- `ActorSettingsSurface`
- `ActorReplacementAction`
- `SceneCatalog`
- `WardrobeSet`
- `ScriptIntentMap`
- `SceneReferenceImage`
- `SceneReviewCheckpoint`
- `IdentityGate`
- `IdentityGateResult`
- `LegacyBatchCompatibility`

## Provider Constraints

### Magnific Character LoRA Training

Use `POST https://api.magnific.com/v1/ai/loras/characters` with `x-magnific-api-key`.

Required request fields:

- `name`
- `quality`
- `gender`
- `images`

Training images must be public image URLs. Magnific currently requires 8-20 images for character LoRA training.

The MVP should not try to expand three uploaded character images into a training set automatically. The operator must upload the full 8-20 image ActorTrainingSet explicitly.

### Magnific Training Progress

Use TrainingProgressPolling first. Magnific's character LoRA docs currently say training status is checked through `GET https://api.magnific.com/v1/ai/loras`, where custom LoRAs include `training.status`.

The UI should show both:

- progress percentage
- phase label, such as `queued`, `training`, `ready`, or provider-normalized equivalent

If Magnific does not provide a numeric percentage, the backend should map known status phases to conservative percentages and store the raw provider status separately.

### Magnific Scene Reference Generation

Use `POST https://api.magnific.com/v1/ai/mystic` for SceneReferenceImage generation.

The adapter must locally reject LoRA-incompatible Mystic options before submission. Magnific currently documents that LoRAs can be silently ignored when incompatible fields are present, including:

- `structure_reference`
- `style_reference`
- combined structure/style references
- model choices such as `fluid`, `flexible`, `super_real`, and `editorial_portraits`

The MVP should prefer `styling.characters` with the provider LoRA id and store the exact LoRA id, strength, prompt, model fields, and rejected/accepted option set in metadata. Prompt syntax such as `@character_name` may be supported later, but `styling.characters` is easier to assert in tests.

## Goals

- Keep `character_consistency` as the only MVP entry point.
- Require one ActiveActorIdentity.
- Require explicit 8-20 image upload to train the ActorIdentity LoRA.
- Block new `character_consistency` batch creation until ActorIdentity training is complete.
- Show training status on the settings page first.
- Auto-enable `character_consistency` when polling reports training completion.
- Preserve existing CharacterSnapshot-based batches through LegacyBatchCompatibility.
- Use approved script text to choose scene and wardrobe automatically.
- Generate fresh scene-specific still references per post from the trained LoRA.
- Show the accepted SceneReferenceImage before video generation.
- Keep scene and wardrobe choices controlled by catalogs, not freeform text.
- Surface IdentityGateResult details when still or video identity checks fail.

## Non-Goals

- No global rollout to automated or manual modes.
- No multiple active actors in the MVP.
- No freeform user scene prompts in the MVP.
- No freeform wardrobe prompts in the MVP.
- No webhook-first training progress.
- No automatic training-set expansion from only three images.
- No removal of existing CharacterSnapshot-based legacy batches.
- No claim of no-drift without still/video identity gating.

## Implementation Budget

Required AGENTS budget: `{files, LOC/file, deps}`.

Target implementation block:

| Area | Files | LOC/file target | Deps |
| --- | ---: | ---: | --- |
| Actor identity schema, persistence, and settings handlers | 3-4 | <= 350 | 0 |
| Magnific adapter and provider contracts | 1-2 | <= 350 | 0 |
| Settings UI and polling partials | 1-2 | <= 350 | 0 |
| Character-consistency readiness gate | 1-2 | <= 250 | 0 |
| Scene catalog, wardrobe set, and script intent mapping | 1-2 | <= 300 | 0 |
| Scene reference generation and review metadata | 2-3 | <= 350 | 0 |
| Identity gate boundary and tests | 1-2 | <= 300 | 0-1 |
| Focused tests and migrations | 4-6 | <= 300 | 0 |

Dependency rule:

- Default to zero new dependencies.
- If automated face similarity is implemented locally, allow exactly one dependency for that concern after a focused spike proves install size, runtime cost, and accuracy are acceptable.
- Until an automated face matcher is selected, the implementation must not market the flow as production no-drift. It can store IdentityGateResult state and fail closed when the configured gate is unavailable.

## UX Design

### Actor Settings Surface

The settings page becomes the first home for ActorIdentity.

It should show:

- current ActiveActorIdentity name
- training status phase
- progress percentage
- provider job id/status when relevant
- uploaded ActorTrainingSet count
- blocked/ready state for `character_consistency`
- confirmed ActorReplacementAction

The page should require 8-20 uploaded images before starting training. The copy should make the training contract explicit: these images train the ActorIdentity LoRA used for later scene and video generation.

### Character Consistency Availability

When no trained ActiveActorIdentity exists:

- `character_consistency` should be disabled in new batch creation.
- The UI should explain that ActorIdentity training must finish first.
- The settings page should be linked from the blocked state.

When training completes:

- polling updates local status to ready
- AutoEnableOnTrainingComplete unlocks `character_consistency`
- no additional activation click is required

### Per-Post Flow

The post flow should be:

1. Script is approved.
2. ScriptIntentMap selects one SceneCatalog entry and one WardrobeSet look.
3. Magnific generates SceneReferenceImage candidates using the ActiveActorIdentity LoRA.
4. Still IdentityGate evaluates candidates.
5. The accepted still is shown at the SceneReviewCheckpoint.
6. Operator can continue, regenerate, or choose another allowed scene/look.
7. Video generation consumes the approved scene reference images on compatible provider paths.
8. Video IdentityGate samples generated output and stores IdentityGateResult.
9. Passing videos continue through the existing QA/publish lifecycle.

## Data Model

### ActorIdentity

Suggested fields:

- `id`
- `name`
- `is_active`
- `provider`
- `provider_lora_id`
- `provider_lora_name`
- `provider_training_task_id`
- `training_status`
- `training_phase`
- `training_progress_percent`
- `training_started_at`
- `training_completed_at`
- `training_error`
- `training_images`
- `consent_source`
- `created_at`
- `updated_at`

Only one row may be active.

### SceneReferenceImage

Suggested fields:

- `id`
- `actor_identity_id`
- `post_id`
- `scene_key`
- `wardrobe_key`
- `provider`
- `provider_task_id`
- `image_url`
- `prompt`
- `provider_metadata`
- `identity_gate_result`
- `status`
- `created_at`
- `updated_at`

### Batch/Post Metadata

New batches in `character_consistency` should reference the active ActorIdentity id in batch metadata. Existing batches with only `character_snapshot` remain valid and keep using the old path.

Post/video metadata should record:

- selected scene key
- selected wardrobe key
- selected SceneReferenceImage id/url
- still IdentityGateResult
- video IdentityGateResult
- whether the route used legacy CharacterSnapshot or ActorIdentity

## Backend Design

### Magnific Adapter

Add a narrow adapter around Magnific using the existing HTTP and config patterns.

Responsibilities:

- create character LoRA training jobs
- list LoRAs for TrainingProgressPolling
- create Mystic image tasks for SceneReferenceImage generation
- get Mystic task status for generated stills
- normalize provider status into app training/generation phases
- reject LoRA-incompatible Mystic payloads locally

Do not add general provider abstraction beyond what this feature needs. Magnific is the only MVP target.

### Readiness Gate

Batch creation must reject new `character_consistency` batches unless:

- an ActiveActorIdentity exists
- training status is ready/completed
- provider LoRA id/name is persisted

Existing legacy batches are exempt. They are identified by already persisted CharacterSnapshot metadata and continue through the old path.

### ScriptIntentMap

The MVP should use deterministic mapping first.

Inputs:

- approved script text
- post type
- target length tier
- optional topic title/seed metadata

Outputs:

- `scene_key`
- `wardrobe_key`
- reason code

The mapper may start simple:

- bathroom/adaptation/safety language maps to bathroom or home interior
- mobility/car/travel language maps to car or exterior
- advice/explainer language maps to office or neutral home
- product/demo language maps to product-friendly home or showroom

All outputs must be members of the controlled SceneCatalog and WardrobeSet. No freeform prompt fragments from the script should be allowed into provider payloads without normalization.

### Scene Reference Generation

Generate scene references lazily per post before video generation. Do not pre-generate at batch creation.

Recommended candidate behavior:

- generate up to three candidates per post
- run still IdentityGate against each candidate
- pick the first passing candidate
- if all fail, surface IdentityGateResult and stop before video submission

This protects cost and makes failures local to the post.

### Video Submission

The approved SceneReferenceImage is the source for video generation. For current video routes that can accept three reference images, generate and pass three fresh scene-specific references based on the same ActorIdentity, scene, and wardrobe choice.

The route compatibility matrix must be explicit. If a route cannot consume approved scene references in a way that preserves the identity contract, it must not be labeled no-drift. Since the MVP is scoped to `character_consistency`, incompatible routes should block or fall back to legacy behavior with visible metadata, not silently pretend to be ActorIdentity-backed.

## Identity Gates

No-drift is an enforced workflow, not a prompt claim.

Still gate:

- compare generated SceneReferenceImage against canonical ActorIdentity references
- fail closed if no face is detected
- fail closed if confidence is below threshold
- store IdentityGateResult with reason and optional score

Video gate:

- sample frames from the generated video
- compare detected face frames against canonical ActorIdentity references
- fail closed if too few usable frames are found
- store IdentityGateResult with reason and optional score

The first implementation plan must pick the gate implementation deliberately. If it uses a dependency, that dependency is the one allowed tool for face similarity. If no automated gate is available in the first slice, the feature must expose manual/pending gate state and avoid production no-drift claims.

## Observability

Use structured logs with correlation ids at these boundaries:

- actor training upload accepted
- Magnific training submitted
- training poll status changed
- TrainingReadinessGate opened
- ScriptIntentMap selected scene/look
- Magnific scene reference submitted
- scene reference generated
- still IdentityGateResult recorded
- SceneReviewCheckpoint decision recorded
- video submission received scene reference
- video IdentityGateResult recorded

Provider metadata should be preserved, but API keys, signed URLs, and private object metadata must not be logged.

## Error Handling

Training errors:

- persist raw provider status and safe operator message
- keep `character_consistency` blocked
- allow confirmed retrain/replace from settings

Scene reference errors:

- stop before video generation
- show failed generation or gate reason
- allow regenerate with same controlled scene/look or alternate allowed choice

Video identity errors:

- mark video generation as failed or gated-failed
- preserve generated artifact metadata for debugging if available
- do not move the post forward as approved

Provider incompatibility:

- reject invalid Magnific LoRA payloads before API submission
- store compatibility failure as a validation error, not as generic provider failure

## Testscripts

### Unit And Contract Tests

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py tests/test_character_consistency_mode.py
```

Expected:

- ActorTrainingSet validation accepts 8-20 images and rejects fewer/more.
- ActiveActorIdentity readiness blocks new `character_consistency` batches until training is complete.
- Existing CharacterSnapshot batch behavior remains covered.

### Magnific Adapter Tests

Run:

```bash
python3 -m pytest -q tests/test_magnific_actor_identity.py
```

Expected:

- training payload includes required fields and public image URLs.
- list-LoRA polling normalizes provider training statuses.
- Mystic scene generation payload uses `styling.characters`.
- incompatible Mystic options are rejected locally.

### Intent Mapping Tests

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_intent.py
```

Expected:

- approved scripts map only to known SceneCatalog keys.
- approved scripts map only to known WardrobeSet keys.
- unknown/ambiguous scripts fall back to a conservative default.
- no freeform script fragments become raw provider options.

### Video Regression Tests

Run:

```bash
python3 -m pytest -q tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py
```

Expected:

- 8s/16s/32s routing does not regress.
- legacy CharacterSnapshot metadata remains valid.
- ActorIdentity metadata is explicit when used.
- incompatible no-drift routes fail visibly.

### Optional Live Smoke

Run only when explicit paid-provider env flags are set:

```bash
AIUGC_LIVE_MAGNIFIC_SMOKE=1 python3 -m pytest -q tests/live/test_magnific_actor_identity_smoke.py
```

Expected:

- a training request can be submitted from public R2 image URLs.
- polling can read the training state.
- a Mystic scene-reference task can be submitted with a trained LoRA id.
- no video is generated unless a separate live video flag is enabled.

## Pass/Fail Criteria

Pass:

- Settings page requires 8-20 uploaded training images before training.
- Training progress shows phase and percentage.
- `character_consistency` is blocked until training completes.
- Training completion auto-enables the mode.
- Existing CharacterSnapshot batches remain usable.
- New `character_consistency` batches bind to the ready ActiveActorIdentity.
- ScriptIntentMap chooses controlled scene and wardrobe values from approved script text.
- SceneReferenceImage generation persists provider task metadata and generated image URL.
- Still failures block video generation with visible IdentityGateResult.
- Approved scene still is shown before video generation.
- Video identity failures block approval/publication with visible IdentityGateResult.
- Magnific LoRA-incompatible payload options are rejected locally.

Fail:

- The app allows new character-consistency batches without a ready ActorIdentity.
- The operator can train from only three images.
- Freeform scene or wardrobe prompts bypass the catalogs.
- A scene still proceeds to video without a gate result or review checkpoint.
- A video proceeds as no-drift when the route did not consume approved scene references.
- Magnific silently ignores a LoRA because the adapter allowed incompatible options.

## Sources

- Magnific Character LoRA docs: `https://docs.magnific.com/api-reference/mystic/post-loras-characters`
- Magnific Mystic generation docs: `https://docs.magnific.com/api-reference/mystic/post-mystic`
- Magnific LoRA listing docs: `https://docs.magnific.com/api-reference/mystic/get-loras`
- Magnific Mystic task status docs: `https://docs.magnific.com/api-reference/mystic/get-mystic-task`
