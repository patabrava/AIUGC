# Magnific Actor Identity Drift Handoff

## Purpose

The next LLM should continue from the current AIUGC `character_consistency` investigation and resolve why the actor still looks different across the three scene-reference shots.

This is not a question of whether the trained LoRA exists. That part is verified. The open problem is why the generated face still varies enough across shots that the user sees drift.

## Current Live Evidence

Live batch inspected:
- Batch ID: `9d0f17c1-572a-4d29-8d83-83c0b7422b2b`
- Post ID: `96ffba81-213a-4818-a0ea-16faf48dfcf4`
- Active actor ID: `5213e989-f342-461f-9aab-e9632d6ba139`
- Active actor name: `AYRA Actor Long Character`
- Provider: `magnific`
- Provider LoRA ID: `1786946`
- Provider LoRA name: `ayra-actor-longchar-20260521`
- Training status: `completed`
- Training phase: `ready`
- Training progress: `100`

The three latest approved scene-reference rows all point to the same actor and the same reference set:
- `front_mid` / `Front`
- `left_three_quarter` / `Left three-quarter`
- `right_profile` / `Right profile`

All three prompts contain the same actor handle:

`@ayra-actor-longchar-20260521::100`

Each row also carries `provider="magnific"` and the same `reference_set_id`:

`e7d998e9-4bc4-4e64-8f28-98c1802712f6`

That means the current live path is using the trained actor identity, not the old prompt-only snapshot flow.

## What The Magnific Docs Say

Docs reviewed:
- [Magnific character LoRA training](https://docs.magnific.com/api-reference/mystic/post-loras-characters#loras-training-for-custom-characters)
- [Magnific Mystic create image](https://docs.magnific.com/api-reference/mystic/post-mystic)

Relevant doc facts:
- Character LoRA training requires `name`, `quality`, `gender`, and `images`.
- `images` must be public URLs and must contain 8-20 items.
- Mystic supports LoRA usage through the prompt handle syntax `@character_name` and `@character_name::strength`.
- Mystic also supports `styling.characters`.
- The docs explicitly say LoRAs are silently ignored when incompatible options are used.
- The incompatible cases include specific models and reference fields such as `structure_reference` and `style_reference`.
- The Mystic docs show `fixed_generation: false` in the request example.
- The docs say higher character strength makes the character more prominent.

## Root Cause

The LoRA is not being ignored. The live data proves it is being used.

The drift is happening because the request contract is not strong enough to lock identity across separate generations:

1. Each shot is an independent Mystic generation.
2. The current request uses the LoRA, but only with normal-strength identity conditioning.
3. The current path does not appear to force deterministic generation.
4. There is no automated face-match gate in the active path to reject weaker identity matches before the image is approved.

This is an inference from the docs and live rows, not a provider-side payload dump, because the production logs do not currently print the full outbound Magnific JSON body.

### Most likely failure mode

The current setup is "LoRA present, but weakly constrained generation." That is enough to keep the same general person across shots, but not enough to keep the exact face stable when the angle, composition, and generation seed all vary.

## What Is Not The Root Cause

- Not a wrong actor row. The active actor row is valid and ready.
- Not a missing LoRA. The generated prompts and persisted metadata prove the LoRA is being requested.
- Not the incompatible-model failure mode. The code already avoids the known incompatible options.

## Code Paths To Inspect

Primary files:
- [app/adapters/magnific_client.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/magnific_client.py)
- [app/features/characters/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/characters/handlers.py)
- [app/features/characters/queries.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/characters/queries.py)
- [tests/test_magnific_actor_identity.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_magnific_actor_identity.py)
- [tests/test_character_consistency_mode.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_character_consistency_mode.py)

Current adapter behavior worth checking:
- `build_mystic_character_payload(...)` only guarantees `styling.characters`.
- The scene-reference handler sends `strength=100`.
- There is no obvious persisted proof of the exact outbound payload beyond the derived prompt and metadata.

## Suggested Next Steps

1. Confirm whether `fixed_generation` should be enabled for actor scene-reference requests.
2. Decide whether the current `strength=100` should become a stronger value for scene references, and test whether Magnific recommends a different range for identity locking.
3. Add explicit payload capture to the persisted reference metadata so future debugging can verify the exact request body, not just inferred behavior.
4. Add regression tests around the generated Mystic payload, including any new deterministic-generation flag and the chosen strength value.
5. If the product expectation is truly "same face across all three shots," add a face-match gate before approving the reference set. LoRA alone is not a guarantee.
6. If deterministic generation is not available or not reliable enough, treat the remaining variation as expected provider behavior and enforce identity through a post-generation gate instead of relying on prompt strength alone.

## Suggested Debug Order For The Next LLM

1. Inspect Magnific docs again and confirm the exact supported request fields for deterministic or fixed generation.
2. Trace the current live request builder in `app/adapters/magnific_client.py`.
3. Patch the smallest possible change that makes the request more identity-stable.
4. Prove it with a focused payload test first.
5. If the provider still drifts, add a QA gate and block approval on weak matches.

## Notes

- The live production web container did read this batch while the references were being generated.
- The logs confirm the batch route was active, but they do not expose the full outbound Magnific request body.
- This handoff is intentionally narrow: fix the identity-locking contract before broadening the workflow.
