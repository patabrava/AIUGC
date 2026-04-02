# External Speech Lip Sync Pipeline

**Date:** 2026-04-02
**Phase:** `S5_PROMPTS_BUILT` -> `S6_QA`
**Status:** Draft for review
**Locality Budget:** `{files: 5, LOC/file: 150-300 target, deps: 0}`

## Problem

Veo 3.1 preview produces usable visuals but unstable speech audio on longer clips and extension chains. The current pipeline treats Veo output as the final video asset, which means choppy speech, word substitutions, and degraded audio quality reach captioning and publishing. For speech-bearing videos, Veo audio is no longer reliable enough to be the production source of truth.

## Goal

Make Veo a visual-only generator for speech-bearing clips. Final spoken audio should come from Deepgram TTS, and the final mouth movement should come from VEED lip sync. The output that enters captioning and publishing should be the externally voiced, lip-synced MP4.

## Non-Goals

- Do not rewrite the prompt-generation or Veo submission flow.
- Do not replace the current caption worker in the first pass.
- Do not add a UI toggle matrix for many audio modes.
- Do not support fallback to Veo speech as a normal success path for talking-head clips.

## Decision

Use one product rule:

- If a clip contains spoken dialogue, the final audio source is external.
- If a clip is silent or B-roll-only, skip TTS and lip sync.

This keeps the system deterministic. Veo remains responsible for visuals. Deepgram owns speech generation. VEED owns mouth re-timing. The final video asset becomes the single source of truth for downstream captioning and publishing.

## Recommended Flow

### Speech-bearing clips

1. Generate the base or extended video with Veo as today.
2. Ignore Veo-generated audio for final delivery.
3. Generate German TTS from the approved script using Deepgram.
4. Upload the TTS audio to storage and obtain a stable public `audio_url`.
5. Submit `video_url` + `audio_url` to the VEED lip sync API.
6. Poll VEED until the final MP4 is ready.
7. Replace the post's final `video_url` with the lip-synced asset.
8. Move the post into the existing caption stage.

### Silent clips

1. Generate the video with Veo as today.
2. Skip TTS and lip sync entirely.
3. Move directly into the existing caption stage or completion state.

## Why This Approach

### Recommended option: always externalize speech

Pros:

- Removes the unstable Veo speech layer from production outputs.
- Produces one consistent debugging surface for all speech-bearing clips.
- Lets German voice quality be tuned independently of video generation.

Cons:

- Adds one async stage after video completion.
- Requires new failure handling for TTS and lip sync.

### Rejected option: fallback to Veo audio on speech failures

This looks convenient but weakens the product contract. If the system sometimes ships Deepgram speech and sometimes ships degraded Veo speech, operators cannot trust the output class. For speech clips, permanent lip-sync failure should remain a failed job, not a silent downgrade.

### Rejected option: operator-level per-post mode selection

This increases state and UI complexity before the stable default is proven. The first pass should keep the rule simple and system-driven.

## Existing Fit In The Codebase

The current code already has the right spine:

- Veo generation and polling live in the existing video handlers and poller.
- Deepgram is already integrated for transcription in the caption pipeline.
- Captioning is already a post-video async stage with its own worker and statuses.

This feature should follow the same shape:

- Add a small Deepgram TTS adapter beside the existing transcription adapter.
- Add a VEED lip-sync adapter.
- Extend the video poller so completed speech-bearing clips enter an external-audio stage before `caption_pending`.
- Reuse storage and structured logging patterns already in the repo.

## Contracts

### Speech detection contract

The first pass should avoid heuristics based on waveform or model output. A clip is treated as speech-bearing when the stored post data includes a non-empty approved spoken script:

- `seed_data.script`
- `seed_data.dialog_script`

If neither exists, the clip is treated as silent for this pipeline.

This is intentionally conservative and matches the current scripting flow.

### Deepgram contract

Deepgram is responsible only for TTS generation. The first pass should pin one German voice and one model in config instead of exposing runtime voice selection. The stable default should be one of the supported German Aura voices documented by Deepgram.

Input:

- script text
- language `de`
- configured voice/model

Output:

- synthesized audio bytes
- content type
- optional provider metadata for logging

### VEED contract

VEED is responsible only for lip sync. The request boundary is:

- `video_url`
- `audio_url`

Output:

- async request/job id
- terminal final MP4 URL
- provider metadata needed for auditing and retries

## State Machine Changes

Add explicit intermediate statuses instead of hiding this work under generic `processing`.

New statuses:

- `voiceover_pending`
- `voiceover_processing`
- `voiceover_failed`
- `lipsync_pending`
- `lipsync_processing`
- `lipsync_failed`

Existing statuses retained:

- `completed`
- `caption_pending`
- `caption_processing`
- `caption_completed`
- `caption_failed`

Transition rules:

1. Veo completes:
   - speech clip -> `voiceover_pending`
   - silent clip -> `caption_pending`
2. TTS succeeds -> `lipsync_pending`
3. Lip sync succeeds -> `caption_pending`
4. Caption worker continues unchanged

The batch-level reconciliation gate must treat `voiceover_*` and `lipsync_*` as in-progress video pipeline states so speech posts do not appear complete before the final asset exists.

## Data Model

Store first-pass state in `posts.video_metadata` and avoid schema expansion unless later needed.

Required metadata fields:

- `audio_strategy`: `external_voiceover` or `silent_passthrough`
- `tts_provider`: `deepgram`
- `tts_model`
- `tts_voice`
- `tts_language`
- `tts_audio_url`
- `tts_audio_storage_key`
- `lipsync_provider`: `veed`
- `lipsync_job_id`
- `lipsync_source_video_url`
- `lipsync_final_video_url`
- `external_audio_failed_at`
- `external_audio_error`

The canonical post `video_url` should be overwritten only after VEED returns the final MP4. Until then, the original Veo asset remains referenced in metadata for audit and retry.

## Worker Design

### Recommended structure

Keep one orchestration spine in `workers/video_poller.py` instead of creating a separate standalone lip-sync worker immediately.

Responsibilities:

- When Veo completes, route the post into either `voiceover_pending` or `caption_pending`.
- Poll for posts in `voiceover_pending` and synthesize audio.
- Poll for posts in `lipsync_pending` and submit or continue polling VEED jobs.
- On final lip-sync success, update `video_url`, metadata, and move to `caption_pending`.

Why:

- The current video poller already owns post-video transitions.
- This keeps the async orchestration in one place for the first pass.
- It avoids creating another worker process and more deployment coordination before the path is proven.

### Adapter split

Add two focused adapters:

- `app/adapters/deepgram_tts_client.py`
- `app/adapters/veed_lipsync_client.py`

Both adapters should follow the existing house style:

- small singleton/factory wrapper
- `httpx` client
- structured logs
- typed exception with transient flag and details

## Error Handling

### Deepgram failures

- Retry transient network or 5xx errors with bounded retry count.
- Permanent request validation failures should mark the post `voiceover_failed`.
- Do not fall back to Veo speech for spoken clips.

### VEED failures

- Retry transient queue, timeout, or provider availability failures.
- Permanent contract errors should mark the post `lipsync_failed`.
- Preserve `lipsync_job_id`, source URLs, and provider response snippets in metadata.

### Recovery

The video poller should continue reconciling these states every poll cycle, just as it currently must reconcile stuck video states. No speech post should remain stranded because one edge-triggered transition was missed.

## Config

Add minimal new settings only:

- `deepgram_tts_api_key` if separate from the existing Deepgram key path
- `deepgram_tts_model`
- `deepgram_tts_voice`
- `veed_api_key`
- `external_audio_enable_speech_pipeline`

The feature flag should default to off until the implementation is verified in staging.

## Testing

### Unit tests

- Deepgram TTS payload builder uses the configured German model/voice.
- VEED lip-sync payload uses the stored `video_url` and `audio_url`.
- Speech routing sends scripted posts into external audio and silent posts around it.
- Status constants and pollable-state helpers include the new states.

### Integration tests

- Completed Veo speech post -> TTS -> lip sync -> `caption_pending`
- Completed Veo silent post -> `caption_pending`
- TTS transient failure retries and then recovers
- VEED transient failure retries and then recovers
- Permanent lip-sync failure leaves the post failed with provider diagnostics

### Regression tests

- Batch reconciliation still advances to QA once all active posts reach caption completion or approved terminal states.
- Caption worker burns captions on the final lip-synced video, not on the raw Veo asset.

## Implementation Slice

Planned first-pass files:

1. `app/adapters/deepgram_tts_client.py`
2. `app/adapters/veed_lipsync_client.py`
3. `app/core/video_profiles.py`
4. `workers/video_poller.py`
5. `tests/` additions for payloads, routing, and failure recovery

This fits the locality budget:

- files: 5
- LOC/file: 150-300 target
- deps: 0

## Open Decision Locked For First Pass

Use one default German TTS voice for all speech clips in the first pass. Voice experimentation can come later, but the initial implementation should optimize for determinism, not a provider-selection surface.

## Success Criteria

- Speech-bearing clips no longer publish Veo-generated speech as final audio.
- Final `video_url` for speech-bearing clips points to the VEED lip-synced MP4.
- Silent clips continue through the existing pipeline without extra latency.
- Failures in TTS or lip sync are visible, structured, and retryable.
- No new worker process is required for the first pass.
