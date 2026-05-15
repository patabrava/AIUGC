# Veo Vertex Voice Consistency Root Cause Report

Date: 2026-05-15

Scope: why the live Veo 3.1 / Vertex path is still producing overlapping voice, repeated speech, and weaker character consistency even after the 32s hop downgrade work.

## Executive Summary

The route-length fix is not the problem. The 32s chain now shortens correctly when the script cannot sustain the full hop count, and the live batch completed successfully. The quality failure is upstream of that: the prompt contract and the Vertex adapter are still mismatched with how Veo is supposed to be driven.

The dominant root cause is prompt duplication. The current prompt builder sends the same spoken line multiple times in different sections, then repeats the stop instruction and room-audio instructions again. That makes the prompt self-contradictory and over-specified. With Veo 3.1, the prompt rewriter cannot be disabled, so any redundant wording is likely being amplified rather than cleaned up.

The second issue is adapter parity. The non-Vertex Veo path forwards `negativePrompt` and `seed`, but the Vertex extension adapter does not. For a model where the docs explicitly recommend consistent character description and the same seed across scenes, that gap matters.

The third issue is continuity anchoring. For legacy 32s Vertex runs, reference images are only used on the base 8s submission and are skipped on extension hops. That leaves later hops relying almost entirely on a long text prompt and the previous video, which is weaker than the docs recommend for consistency.

## What The Docs Say

The official docs point in a different direction than the current prompt shape.

- [Extend a Veo video](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/extend-a-veo-video)
  - Extension inputs are the previous video plus a text prompt.
  - Extension output length is 7 seconds.
  - The input video must be MP4, 1 to 30 seconds, and either 9:16 or 16:9.

- [Veo prompt guide](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide)
  - Audio should be described in separate sentences.
  - Dialogue should be explicit and clearly separated from the rest of the scene description.
  - Negative prompts should describe what should be avoided, not just pile up prose.

- [Veo best practices](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice)
  - Character consistency comes from a detailed, unchanged character description.
  - The same seed should be used for consistent visual, stylistic, and voice output across scenes.

- [Turn off Veo prompt rewriter](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/turn-the-prompt-rewriter-off)
  - Veo 3 and 3.1 do not let you disable the prompt rewriter.
  - That makes prompt clarity and non-redundancy much more important, because the model will rewrite what you send.

## What The Code Does Today

The live prompt assembly still bakes the same speech into multiple prompt sections.

- [app/features/posts/prompt_builder.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/prompt_builder.py:102>)
  - `OPTIMIZED_PROMPT_TEMPLATE` includes `Action:`, `Dialogue:`, `Ending:`, and `Audio:` as separate blocks.

- [app/features/posts/prompt_builder.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/prompt_builder.py:450>)
  - `build_video_prompt_from_seed()` puts the spoken line into `action_value` and also feeds the same line into the dialogue field.
  - It then appends a final stop instruction and a full audio block.
  - That means the spoken line is effectively present in `Action`, `Dialogue`, and the audio-related text stream.

- [app/features/posts/prompt_text.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/prompt_text.py:35>)
  - The canonical prompt builder also appends `audio.dialogue` and `audio.capture` into the final text composition path.
  - If the fallback path is used, it can reintroduce the same speech again.

- [app/features/videos/handlers.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py:860>)
  - The 32s extended base prompt is built from the same prompt data, then packed into segments.
  - This is not a route failure. It is still a prompt-content problem.

- [app/features/videos/handlers.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py:1937>)
  - Vertex text-video generation uses reference images only for the 8s character-consistency base.
  - [app/features/videos/handlers.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py:2013>)
  - Legacy 32s Vertex runs explicitly skip reference images on the longer duration path.

- [app/adapters/vertex_ai_client.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/vertex_ai_client.py:344>)
  - The Vertex extension payload only sends `prompt`, `video`, `aspectRatio`, `durationSeconds`, and optional `storageUri`.
  - It does not forward `negativePrompt`.
  - It does not forward `seed`.

- [app/adapters/veo_client.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/veo_client.py:490>)
  - The non-Vertex Veo extension path does forward `negativePrompt` and `seed`.
  - That makes the Vertex path less controlled than the path that already existed for Veo.

- [workers/video_poller.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py:1674>)
  - The worker already consumes the shortened segment chain correctly.
  - [workers/video_poller.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py:1801>)
  - The extension hop itself is the same content path problem, not a worker scheduling problem.

## Live Evidence

I also checked this against real submissions from batch `cc027a62-5e1f-48c8-b6be-7c5ea7858258`.

- Three live generations were submitted and all reached `caption_completed`.
- The chain shortened to 2 effective extension hops where needed.
- That means the duration routing is working and the failure is not the hop logic.
- The remaining complaint, voice overlap and weak consistency, survived even when the jobs completed normally.

## Findings

### 1. The spoken line is repeated too many times

This is the strongest root cause.

The builder repeats the speech in:

- `Action:` as a narrated statement.
- `Dialogue:` as the actual quote.
- `Ending:` as a stop instruction.
- `Audio:` as a room-tone / no-background-voices instruction.

That is too much instruction density for a model that is already going to rewrite the prompt internally. The likely result is:

- duplicated delivery of the same line,
- trailing overlap into the next segment,
- uneven stop timing,
- and a higher chance of the model treating the speech as both action and audio rather than one clean utterance.

### 2. The prompt is over-specified for a model with an always-on rewriter

The docs say Veo 3 and 3.1 cannot disable the prompt rewriter.

That means long, repetitive prompts are not just verbose. They are risky. The model will reinterpret the input, and the more redundant the input, the more room it has to invent transitions between duplicate directives.

This is especially visible in the current prompt structure:

- the same character description is repeated,
- the same voice instruction is repeated,
- the same stop cue is repeated,
- the same silence / room-tone instruction is repeated.

Some repetition is fine when it is the same stable character description across scenes. Repeating the speech and end-of-line cues is not the same thing.

### 3. Vertex extension parity is incomplete

The Vertex extension adapter is missing two controls that matter for consistency:

- `seed`
- `negativePrompt`

The docs explicitly recommend the same seed for consistent visual, stylistic, and voice output across scenes. The non-Vertex Veo extension path already preserves that control. Vertex does not.

That is a structural drift between the two provider paths. Even if the prompt were perfect, the Vertex path is still losing consistency controls on extension hops.

### 4. Legacy 32s Vertex runs lose their strongest visual anchors after the base clip

For character-consistency 32s jobs, the code uses reference images on the base submission and skips them on the longer duration path.

That may be acceptable as an API limitation, but it has a real effect:

- the first clip has the strongest anchor,
- later clips rely on the prompt and previous video alone,
- and any prompt ambiguity becomes much more visible over 2 or 3 extension hops.

So the drift is not surprising. It is the predictable outcome of removing one of the main anchors while also keeping a verbose prompt.

### 5. The route-shortening work is not the failure boundary

The 32s downgrade work did what it was supposed to do.

The chain now stops at the effective target when the script cannot support the full 32s route. That prevents wasted hops and improves completion reliability.

But quality problems remain because the prompt contract itself is still noisy. So the failure is not in segment counting. It is in how the model is instructed to speak and continue.

## Root Cause

The root cause is a prompt-contract mismatch, not a chain-routing bug.

In plain terms:

1. The code sends Veo the same spoken content multiple times.
2. The code repeats the stop and audio instructions in several sections.
3. The Vertex extension path drops consistency controls that the docs recommend.
4. Veo 3.1 rewrites the prompt anyway, so the redundant input gets amplified.
5. The 32s path also weakens visual anchoring by skipping reference images on extension hops and by staying unseeded on the legacy route.

That combination explains both complaints:

- Voice overlap: the model is being told to say the same thing in overlapping ways.
- Consistency drift: the model is losing anchors while also being asked to interpret a noisy prompt.

## Suggestions

### Immediate fixes

- Remove the spoken line from `Action:` and keep it in one place only.
- Use a lean continuation prompt for extension hops.
- Forward `seed` through the Vertex extension payload.
- Forward `negativePrompt` through the Vertex extension payload.
- Keep the audio direction in one clean block, not repeated in action, dialogue, ending, and audio sections.
- Keep the instruction language and the spoken language clearly separated.
- Make the main prompt shorter and more declarative.

### Suggested prompt shape

For Veo, the prompt should look closer to this:

- one stable character block,
- one stable scene or continuity block,
- one action block,
- one spoken line,
- one ending instruction,
- one audio block.

The important constraint is that the line spoken by the character should appear once, not twice or three times.

### Suggested code changes

- Replace the current base prompt assembly with a non-duplicative template.
- Make the extension prompt the default shape for Vertex 32s continuation hops.
- Add a Vertex extension payload option for `seed`.
- Add a Vertex extension payload option for `negativePrompt`.
- Decide whether 32s Vertex should remain unseeded by policy or whether that old assumption should be retired.
- If Vertex extension cannot keep strong enough continuity, stop pretending text-only prompts alone will solve it and route those cases to the best supported generation path.

### Suggested tests

- Add a unit test that fails if the same dialogue text appears in both `Action` and `Dialogue`.
- Add a test that asserts the Vertex extension payload contains `seed` when one is available.
- Add a test that asserts the Vertex extension payload contains `negativePrompt` when one is available.
- Add a prompt snapshot test for the lean continuation template.
- Add a live regression test that inspects the exact prompt submitted to Vertex for a 32s continuation run.

### Suggested acceptance criteria

- The spoken line appears exactly once in the submitted prompt.
- The extension payload preserves the same seed when consistency mode is enabled.
- The extension payload preserves a negative prompt when one is supplied.
- The 32s route still shortens when needed, but the prompt stays lean enough to avoid repeated speech.
- New live videos no longer show repeated voice or a trailing spoken overlap at segment boundaries.

## Community Signal

The forum and Reddit results line up with the docs and the code symptoms.

- Users repeatedly report that voice and accent consistency still drift unless the prompt, starting image, and voice guidance are extremely stable.
- Users extending clips report that voice consistency becomes harder, not easier, once the model has to continue from an existing clip.
- Community guidance commonly falls back to keeping the same prompt, the same starting image, or external voice replacement tools.

That does not prove a bug in our code by itself, but it supports the same conclusion: prompt stability and anchor parity matter more than long, repeated instruction blocks.

## Sources

Official docs:

- [Use Veo to extend videos](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/extend-a-veo-video)
- [Veo on Vertex AI video generation prompt guide](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/video-gen-prompt-guide)
- [Best practices for Veo on Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/best-practice)
- [Turn off Veo on Vertex AI's prompt rewriter](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/turn-the-prompt-rewriter-off)

Community threads:

- [Voice and accent consistency](https://www.reddit.com/r/VEO3/comments/1qvm8nt/voice_and_accent_consistency/)
- [How do you guys get consistent voices](https://www.reddit.com/r/VEO3/comments/1r8sz6q/how_do_you_guys_get_consistent_voices/)
- [How do you keep your characters voices from changing when you extend?](https://www.reddit.com/r/VEO3/comments/1rxi8vg/how_do_you_keep_your_characters_voices_from/)
- [How to use Gemini Image for Veo character consistency: Doogler Edition](https://discuss.google.dev/t/how-to-use-gemini-image-for-veo-character-consistency-doogler-edition/259879)

Code references:

- [app/features/posts/prompt_builder.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/prompt_builder.py:102>)
- [app/features/posts/prompt_text.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/posts/prompt_text.py:35>)
- [app/features/videos/handlers.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/videos/handlers.py:860>)
- [app/adapters/vertex_ai_client.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/vertex_ai_client.py:344>)
- [app/adapters/veo_client.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/adapters/veo_client.py:490>)
- [workers/video_poller.py](</Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/workers/video_poller.py:1674>)

