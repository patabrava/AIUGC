# Veo 3.1 Lite 32s Pause Root Cause Handoff

Date: 2026-05-15
Repo: `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC`
Status: root cause found, no fix implemented in this handoff

## Executive Summary

The latest 32s Veo 3.1 Lite videos are no longer primarily failing because words are split mid-sentence. The current WIP split is sentence-safe and the final run used the requested Lite model.

The current root cause is duration mismatch:

- The 32s video route asks Veo to generate an `8 + 7 + 7 + 7` chain, which produces about `29s` of video.
- The scripts sent into that chain only contain about `16-22s` of spoken material.
- Some individual 7-second extension hops receive only `7-12` words, which is not enough spoken content to fill the hop.
- Veo then fills the remaining time with room tone, stillness, or mouth-at-rest behavior, creating awkward pauses between segments.

There is also a secondary prompt-contract issue:

- The base prompt for extended chains can inherit a saved final-stop `ending_directive` from `video_prompt_json`.
- That makes the base segment say both "continue/no trailing silence" and "speech stops completely/mouth comes to rest".
- This likely contributes to base seam pauses, but it does not explain the larger 3-4 second mid-chain pauses by itself.

## Batch And Artifacts

Investigated batch:

- Batch ID: `cc027a62-5e1f-48c8-b6be-7c5ea7858258`
- Provider: `vertex_ai`
- Requested model: `veo-3.1-lite-generate-001`
- Route: `8 + 7 + 7 + 7`
- Local captioned videos: `/tmp/aiugc_veo3_lite_cc027a62_captioned`
- Runtime prompt truth: Supabase `video_prompt_audit`
- Local repo logs checked: `logs/video_poller.log`, `logs/web.log`, `logs/caption_worker.log`
- Note: local log files were zero bytes, so Supabase audit rows plus local MP4s were the authoritative evidence.

Related previous handoff:

- `agents/veo_3_1_lite_32s_extension_handoff.md`

## Exact Evidence

The final current operation set used `veo-3.1-lite-generate-001` for all base and extension operations. Model routing was not the current root cause.

Measured with `ffprobe` and `ffmpeg silencedetect` on the local MP4s:

| Post ID | Segment word counts | Stored estimated speech | Final video duration | Important silence |
| --- | ---: | ---: | ---: | --- |
| `1e950c92-64ad-4027-8cd8-3a3ce95f55fa` | `13, 11, 7, 10` | `16s` | `28.708s` | `18.226-22.168s`, `3.942s` |
| `4944d7ee-1927-4c95-8548-193a15a3e254` | `16, 9, 19, 14` | `22s` | `28.750s` | `11.502-14.581s`, `3.079s` |
| `5728f5ad-85f8-4799-9852-2fa1a1b9c848` | `20, 12, 10, 12` | `21s` | `28.708s` | `20.354-22.104s`, `1.750s` |
| `a57947cc-bf4f-4991-939e-ba671dc0337b` | `9, 10, 20, 16` | `22s` | `28.709s` | `6.881-8.033s`, `1.152s` |
| `f618d80b-beb2-4dc1-aedf-70fffd7f94ec` | `13, 11, 15, 11` | `19s` | `28.708s` | `6.155-7.149s`, `0.994s` |

The worst example is post `1e950c92`:

- Whole script: 41 words.
- Chain budget: 29 seconds.
- Third segment sent to a 7s hop: `Ich dachte früher, das sei nur Komfort.`
- That segment has 7 words.
- The output has a 3.94s silence around the segment boundary area.

Another strong example is post `4944d7ee`:

- Second segment sent to a 7s hop: `Manchmal fühlt sich das an wie ein kleiner Lottogewinn.`
- That segment has 9 words.
- The output has a 3.08s silence from `11.502s` to `14.581s`.

## What Was Sent To Veo

Representative final prompts from Supabase `video_prompt_audit`.

Post `a57947cc`, base operation:

```text
Dialogue:
"Deutschland 2026. Und du suchst eine wirklich altersgerechte Wohnung."

Action:
Seated in a wheelchair, she delivers the line directly to camera in one continuous take. She speaks with brisk but natural pacing, clear articulation, and no dramatic pauses, using small natural hand gestures and subtle upper-body nods while speaking.

Ending:
After the final spoken word, speech stops completely. She does not begin a new word or syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief moment before the clip ends.

Audio:
Audio: Recorded with a modern smartphone microphone in a quiet indoor room. The voice is clear, natural, and close to the microphone. No music and no background voices. Subtle natural room acoustics typical of a small bedroom. Keep the spoken delivery continuous and steady with no dramatic pause, no trailing silence, and no settling room tone at the end of this segment.
```

This shows the secondary contradiction: continuation-style audio, but final-stop ending.

Post `1e950c92`, extension hop 2:

```text
Dialogue:
"Ich dachte früher, das sei nur Komfort."

Ending:
Continue directly into the next segment with no concluding pause or scene-ending hold.

Audio:
Natural single-speaker smartphone room audio. No music. No background voices.
```

This prompt is structurally clean, but the segment is too short for a 7s hop.

## Code Boundaries

Primary duration contract:

- `app/core/video_profiles.py`
- 32s profile currently sets:
  - `provider_target_seconds=29`
  - `veo_base_seconds=8`
  - `veo_extension_seconds=7`
  - `veo_extension_hops=3`
  - `prompt1_min_words=54`
  - `prompt2_min_words=40`

Problem:

- `prompt1_min_words=54` and especially `prompt2_min_words=40` are too low for a 29s chain.
- The profile validates total word count, not per-hop spoken coverage.

Segment construction:

- `app/features/videos/handlers.py`
- `_build_veo_extended_base_prompt(...)`
- `_pack_veo_segments_for_profile(...)`

Current behavior:

- Splits/packs complete dialogue units.
- Persists `video_metadata.veo_segments`.
- Builds time windows for `8 + 7 + 7 + 7`.
- Does not reject or repair segments that are too short for their assigned window.

Extension prompt construction:

- `workers/video_poller.py`
- `_build_veo_extension_prompt(...)`

Current behavior:

- Uses persisted `video_metadata.veo_segments` verbatim.
- Good for avoiding repeated/tail-duplicated text.
- Still sends underfilled segments if the persisted split is underfilled.

Base prompt ending issue:

- `app/features/videos/handlers.py`
- `_build_veo_extended_base_prompt(...)` reads `video_prompt.get("ending_directive")`.
- For extended chains it clears some fields, but it does not clear `prompt_ending`.
- That can put a final-stop ending into the base prompt.

## Root Cause

The system currently treats "valid 32s script" as:

- Enough total words for the old topic-generation envelope.
- Enough complete sentence-like segments for the chain.

But the real provider contract for a 32s chain is stricter:

- The base segment must contain enough spoken material for about 8 seconds.
- Each extension segment must contain enough spoken material for about 7 seconds.
- Total spoken material should roughly cover the 29s provider target, leaving only a small final hold.

The current scripts do not meet that contract. Sentence-safe splitting fixed repetition and mid-clause starts, but it also exposed that the content itself is too short per hop.

## Suggested Fix

Implement the fix at the video submission boundary first, not only in prompt generation. The video boundary is the last place where the actual submitted chain is known.

### Plan Budget

Required plan envelope:

- Files: 3-4
- LOC/file: target under 80 changed LOC per production file, under 120 LOC for tests
- Deps: 0

Suggested files:

- `app/features/videos/handlers.py`
- `app/core/video_profiles.py`
- `tests/test_video_duration_routing.py`
- Optional: `app/features/topics/topic_validation.py` only if also tightening topic-generation contracts in the same pass

### Fix 1: Add Per-Segment Minimums For Extended Chains

Add a validation helper near `_validate_veo_extension_segment_budget(...)`:

```python
def _validate_veo_segment_spoken_budget(*, segments, profile, target_length_tier):
    ...
```

Suggested initial thresholds:

- 8s base: minimum 16 words
- 7s extension: minimum 14 words
- final 7s extension: minimum 12-14 words, depending on desired end hold

The exact thresholds can be tuned, but the current failing examples prove these are invalid:

- 7 words for a 7s hop
- 9 words for a 7s hop
- 10-11 words for a 7s hop when the full clip already underfills the chain

Fail fast with a structured `ValidationError` that includes:

- `target_length_tier`
- `segment_index`
- `budget_seconds`
- `word_count`
- `minimum_words`
- `segment_preview`

This prevents paid Veo calls for obviously underfilled chains.

### Fix 2: Raise 32s Topic Script Minimums

The current 32s profile allows scripts that are too short for the chain:

- value: `54-74` words
- lifestyle: `40-66` words

For a 29s spoken chain, 40-54 words is not enough unless the actor speaks unnaturally slowly.

Suggested safer first pass:

- `prompt1_min_words`: raise from `54` to about `68`
- `prompt1_max_words`: keep around `84-90`
- `prompt2_min_words`: raise from `40` to about `64`
- `prompt2_max_words`: raise to about `84`

This should be validated against actual generated voice pacing. If the app wants intentionally short speech with visual pause, keep the lower tier, but then it should not be called "32s continuous conversation flow".

### Fix 3: Clear Final Ending From Extended Base Prompts

In `_build_veo_extended_base_prompt(...)`, when `profile.route == VEO_EXTENDED_VIDEO_ROUTE`, clear `prompt_ending` too.

Current extended branch clears:

- `prompt_scene`
- `prompt_action`
- `prompt_audio_block`

It should also clear:

- `prompt_ending`

This lets `build_veo_prompt_segment(... include_ending=False ...)` use the continuation/base contract instead of inheriting a saved final-stop ending.

### Fix 4: Add Tests That Reproduce This Exact Failure

Add focused tests in `tests/test_video_duration_routing.py`:

1. A 32s script with 4 sentence-safe segments but one 7-word extension segment must fail before provider submission.
2. The current `1e950c92` script should be rejected for 32s because segments are `[13, 11, 7, 10]`.
3. The current `4944d7ee` script should be rejected or repaired because one hop is 9 words.
4. Extended base prompt must not include `After the final spoken word, speech stops completely` unless it is the final segment.

Use local unit tests first. Do not spend provider credits until those pass.

## Suggested Verification

Run focused tests:

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py tests/test_video_poller_extension_chain.py
```

Before another paid live run, simulate segment packing for the target batch and print:

- segment text
- segment word count
- assigned budget seconds
- pass/fail against per-hop minimums

Only submit to Veo when all posts pass the per-hop contract.

After live generation, verify:

```bash
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 <video.mp4>
ffmpeg -hide_banner -nostats -i <video.mp4> -af silencedetect=noise=-35dB:d=0.35 -f null -
```

Acceptance target:

- No internal silence longer than about `1.0s`.
- No base or hop seam silence longer than about `0.8-1.0s`, unless intentionally final.
- Caption/transcript word count should match the submitted script word count.
- Supabase `video_prompt_audit` should show all operations on `veo-3.1-lite-generate-001`.

## Notes For The Next LLM

- Do not revert the current WIP sentence-boundary split. It fixed the previous repetition problem.
- Do not reintroduce word-level slicing across sentence boundaries as the main solution.
- The right fix is to reject or regenerate underfilled segments before paid provider submission.
- Keep the fix local and test-first. The issue can be reproduced without another live Veo call.
- Treat Supabase `video_prompt_audit` as runtime truth for what was sent to Veo.
- Treat local repo logs as non-authoritative for this run because they were empty.
