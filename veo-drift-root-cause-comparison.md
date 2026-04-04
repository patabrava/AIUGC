# VEO Drift Root Cause Comparison

Date: 2026-04-04

This document compares:

- Drift batch: `7761f03a-be78-48cb-a531-71b834ae9159`
- No-drift batch: `28b3f8be-92d3-4a4f-9233-cea1bece61d7`

The goal is to identify the smallest set of differences that plausibly explain the visual drift.

## Compared posts

- Drift batch approved post: `fa9cf0f9-cb52-40a8-a47e-4f4e97871e44`
- No-drift batch approved post: `155d87d8-e8e7-4d42-b8b3-a0ffd6881304`

## What was actually sent

### Drift batch `7761f03a-be78-48cb-a531-71b834ae9159`

- Metadata route: `video_pipeline_route = veo_extended`
- Route flags:
  - `veo_base_seconds = 8`
  - `veo_extension_seconds = 7`
  - `veo_required_segments = 4`
  - `veo_extension_hops_target = 3`
  - `veo_efficient_long_route_enabled = true`
  - `veo_seed = 1930144744`
- Audit rows:
  - base prompt len: `1712`
  - hop prompt lens: `828`, `848`, `1050`
  - `negative_prompt` sent separately on all recorded rows
- Prompt contract:
  - full base prompt
  - lean extension prompts
  - extension prompts only carry:
    - short `Character`
    - short `Style`
    - short `Continuity`
    - short `Language`
    - next `Dialogue`
    - short `Audio`

### No-drift batch `28b3f8be-92d3-4a4f-9233-cea1bece61d7`

- Metadata route: `video_pipeline_route = veo_extended`
- Route flags:
  - `veo_base_seconds = 4`
  - `veo_extension_seconds = 7`
  - `veo_required_segments = 5`
  - `veo_extension_hops_target = 4`
  - `veo_efficient_long_route_enabled = false`
  - `veo_seed = null`
- Audit rows:
  - base prompt len: `2112`
  - hop prompt lens: `2965`, `2991`, `2975`, `3053`
  - `negative_prompt = null` on all recorded rows
- Prompt contract:
  - full base prompt
  - full extension prompts
  - negatives embedded inline in the prompt body
  - extension prompts repeat:
    - full detailed `Character`
    - full `Style`
    - full `Action`
    - full `Scene`
    - full `Cinematography`
    - long `Audio`
    - inline negatives

## Evidence-backed findings

### 1. "Prompt verbosity" is not supported as the primary cause

The no-drift batch is the more verbose batch by a wide margin.

- Drift hop prompts: `828` to `1050` chars
- No-drift hop prompts: `2965` to `3053` chars

That means the successful batch used extension prompts roughly three times larger than the drifting batch.

So the statement "we made the prompts leaner, therefore drift should improve" is not supported by the actual provider payloads.

There is an even stronger disproof:

- Both batches already share the same lean base character block.
- Both batches already share the same simple bedroom scene on the base request.

So the later experiment of simplifying the base `Character` and `Scene` did not restore stability, because the no-drift batch was already using the same lean base anchors.

### 2. The strongest structural regression is the 32s route change itself

This is the biggest difference.

- No-drift batch used legacy `4+7+7+7+7`
- Drift batch used efficient `8+7+7+7`

That changes:

- the base segment length
- the number of continuation boundaries
- the amount of speech/action packed before the first extension boundary
- the continuation state Veo inherits when hop 1 starts

Repo guidance already records this exact failure mode:

- [AGENTS.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/AGENTS.md#L66): `VEO 32s must stay on the legacy 4+7+7+7+7 chain; the efficient 8+7+7+7 route posterizes contrast and facial detail after ~2 hops across unrelated subjects even when segment routing and voice continuity are correct.`
- [app/core/video_profiles.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/core/video_profiles.py#L88) defines the legacy `32s` profile.
- [app/core/video_profiles.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/core/video_profiles.py#L127) defines the efficient `32s` profile.

The DB evidence matches that rule exactly:

- No-drift batch: `veo_efficient_long_route_enabled = false`
- Drift batch: `veo_efficient_long_route_enabled = true`

### 3. The extension visual anchor got weaker exactly where drift begins

The reported drift starts after the first cut, not during the base clip.

That points directly at the continuation boundary.

Current lean continuation contract:

- [app/features/posts/prompt_builder.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/prompt_builder.py#L97)
- [workers/video_poller.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py#L1193)

The no-drift batch repeated the entire visual contract on every hop.

The drift batch removed:

- full room description
- full camera/framing description
- full action reinforcement
- full physical-detail reinforcement

That makes the continuation prompt more elegant, but also weaker as a text-only anchor.

### 4. Negatives transport changed too

This is another meaningful contract change.

- No-drift batch: negatives inline in the prompt body, `negative_prompt = null`
- Drift batch: negatives moved into REST `negativePrompt`

This matters because the only confirmed no-drift `32s` batch is still on the inline-negative contract.

Repo guidance also captures that:

- [AGENTS.md](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/AGENTS.md#L74): `Legacy 32s VEO chains must restore the whole historical request contract, not just hop timing: keep visual negatives inline on base and extension prompts and do not move them to REST negativePrompt, or late-hop continuations posterize and drift even on the old 4+7+7+7+7 route.`

### 5. The successful and drifting batches are both text-only, which is the systemic limitation

Official Google guidance now puts the strongest consistency improvements on reference-image flows, not on text-only continuation alone.

- Google’s Veo docs say Veo 3.1 can use up to three reference images and that it preserves the subject’s appearance in the output video. [Google AI docs](https://ai.google.dev/gemini-api/docs/video)
- Google’s January 13, 2026 Veo 3.1 update says "Ingredients to Video" improves identity consistency, background consistency, and native portrait outputs. [Google blog](https://blog.google/innovation-and-ai/technology/ai/veo-3-1-ingredients-to-video/)

That means the current text-only chain is working without the strongest consistency mechanism Google is actively promoting.

## Web corroboration

### Official docs

- Veo extension takes the previous Veo video plus an optional text prompt and continues from the final second / 24 frames. [Google AI docs](https://ai.google.dev/gemini-api/docs/video)
- Google explicitly warns that voice is not effectively extended if it is not present in the last 1 second of video. [Google AI docs](https://ai.google.dev/gemini-api/docs/video)
- Google shows that more specific prompt details are used to refine outcomes, not that shorter prompts are inherently better. [Google AI docs](https://ai.google.dev/gemini-api/docs/video)
- The same docs say text input is capped at `1,024` tokens for Veo 3.1 preview. The stable batch hop prompts are not obviously near that ceiling, so raw prompt length is not a convincing overload explanation. [Google AI docs](https://ai.google.dev/gemini-api/docs/video)

### Community guidance

- A recent `r/PromptEngineering` thread says the best text-only consistency gains came from keeping the core physical description, style/lighting language, and camera framing fixed while only changing scene/action, and several replies say reference images help much more. [Reddit](https://www.reddit.com/r/PromptEngineering/comments/1rmsmv9/prompt_engineering_problem_keeping_ai_characters/)
- A `r/VEO3` guide recommends breaking scripts into `6-8` second scenes, repeating the exact character descriptor every time, and using reference images as the stronger method when available. [Reddit](https://www.reddit.com/r/VEO3/comments/1p0slkg/a_guide_on_how_to_craft_veo_prompts_keep/)
- The same `r/VEO3` guide also includes a reply pointing out that when people rely on elements / continuation, "the quality degrades tho," which matches the late-hop degradation pattern you are seeing. [Reddit](https://www.reddit.com/r/VEO3/comments/1p0slkg/a_guide_on_how_to_craft_veo_prompts_keep/)
- An X post surfaced by search describes a Veo workflow using consistent selfie/reference photos plus Veo 3.1 for motion/dialogue, again pointing toward image anchoring rather than prompt slimming as the stronger lever. [X](https://x.com/Letthemwhisper/status/2030350154998702540)

## 80/20 root cause ranking

1. Primary: the `32s` route changed from legacy `4+7+7+7+7` to efficient `8+7+7+7`.
2. Secondary: continuation prompts got much weaker exactly at the boundary where drift begins.
3. Tertiary: the request contract changed again by moving negatives out of the inline prompt and into REST `negativePrompt`.
4. Systemic ceiling: this is still a text-only continuity workflow, while Google’s strongest identity-consistency improvements now live in image-anchored "Ingredients to Video" flows.

## Practical conclusion

The evidence does not support "the prompt is too verbose" as the main cause.

The evidence does support:

1. The stable batch used a different `32s` chain topology.
2. The stable batch used stronger continuation anchors, not weaker ones.
3. The stable batch kept the older inline-negative contract.

If the goal is to maximize the probability of removing drift with the fewest moving parts, the next test should not be "make the prompt even leaner."

It should be:

1. Restore `32s` to legacy `4+7+7+7+7`.
2. Restore the historical continuation contract for `32s`.
3. Test one variable at a time after that:
   - separate `negativePrompt`
   - leaner continuation hops
   - stronger or weaker character detail

If the goal is long-term robustness rather than just reproducing the stable legacy behavior, the real platform-aligned fix is image anchoring, not text slimming. The blocker is that the current Gemini Developer preview surface in this repo is still running text-only for Veo continuity.
