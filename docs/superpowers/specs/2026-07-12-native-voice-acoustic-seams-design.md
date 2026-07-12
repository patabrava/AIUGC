# Native-Voice Acoustic Seams for Semantic Veo UGC

Date: 2026-07-12
Status: Design approved; written specification awaiting review
Supersedes: The fixed 0.25-second head/tail padding and hard-audio-concat contract in `2026-07-11-veo31-semantic-ugc-16s-design.md`

## Goal

Make semantic Veo 3.1 jump cuts sound like one continuous UGC performance while preserving the approved actor, native Veo voice, exact script, captions, and independent reference-anchored takes. The first proof recomposes the existing four approved takes without another Veo submission. The result must be shown for human review before this behavior becomes the default composition path.

## Measured Problem

The current final video passes transcript and word-gap QA but sounds choppy because each independently generated take retains its own respiratory pre-roll. The current composer keeps 0.25 seconds after the previous final word and 0.25 seconds before the next first word, then the stitcher hard-concatenates both audio streams.

The three current seams contain isolated low-level broadband islands between two quiet regions:

| Seam | Island duration | RMS | Spectral centroid | Observed pattern |
|---|---:|---:|---:|---|
| 1 | 245 ms | -40.4 dBFS | 3.33 kHz | pause, inhale/noise, pause |
| 2 | 140 ms | -45.1 dBFS | 3.76 kHz | pause, inhale/noise, pause |
| 3 | 194 ms | -43.7 dBFS | 3.20 kHz | pause, inhale/noise, pause |

These islands fall outside Deepgram's spoken-word windows and have breath/noise-like energy and zero-crossing behavior. Word-gap QA accepts them because every measured gap remains below 0.6 seconds.

The 53.824-second reference video has five strong visual cuts. Its first three cuts fall inside one contiguous 102-312 ms low-energy pause. Its last two visual cuts occur during continuous dialogue, with the nearest acoustic valley approximately 35-55 ms later. Active-speech RMS across its six visual segments varies by only 1.17 dB. The reference therefore treats picture and audio edit points as related but independently selectable.

Evidence inputs:

- reference post: `https://x.com/0xROAS/status/2076331407748575675`;
- downloaded reference-media SHA-256: `b2356c50c78206359a7df1e01ebc553894a2d37e65129d6b3d7a914b733e5efc`;
- current pilot run: `2026-07-11-ayra-semantic-16s-v2`;
- current final-captioned SHA-256: `7c067f13ceeea0e2e005a914ac288e6bdd94f70756be900d85a9be906f30cccd`;
- measurements: FFmpeg scene detection, silence detection, short-window time/frequency statistics, per-segment active-speech RMS, and hashed qualitative seam excerpts.

## Decision

Add a deterministic, native-voice acoustic seam planner between take transcription and FFmpeg composition. It will use word timestamps as inviolable speech boundaries, analyze only the available non-speech margins, remove retained breath/noise islands, match dialogue gain conservatively, and create a short audio overlap whose visual cut can sit at a separately selected point inside that overlap.

The implementation will not replace the voice, synthesize dialogue, alter Veo prompts, regenerate the actor, or submit another paid Veo request. It will not apply blanket silence removal or fixed blind trims because this repository previously clipped speech with that approach.

## Scope

### Included

- acoustic analysis of approved raw take audio using the installed FFmpeg filters;
- transcript-guarded head and tail boundary selection;
- breath/noise-island rejection;
- speech-only loudness measurement and bounded clip gain;
- 40-70 ms equal-power audio overlaps when both sides are verified non-speech;
- independently placed hard visual cuts inside those audio overlaps;
- deterministic and qualitative acoustic seam QA;
- manifest evidence, recomposition history, and a revised captioned preview;
- focused regression tests for speech safety, duration, lip-sync, and idempotent recomposition.

### Excluded

- TTS, voice cloning, external lip-sync, LoRA, Magnific, new image generation, or new Veo generation;
- music, a generic denoiser, aggressive compression, synthetic ambience, or a permanent room-tone bed;
- changing the approved script, character references, shot deck, captions style, or upload contract;
- automatic paid retries when no safe acoustic seam exists;
- production-default activation before the revised preview is approved.

## Architecture

### Acoustic analysis boundary

A focused `shot_production` acoustic-seam module will receive, for every approved take:

- raw media path and hash;
- provider duration;
- Deepgram first-word and final-word timestamps;
- current transcript QA and trim window;
- take index and script boundary context.

It will extract or reuse mono PCM audio and obtain deterministic short-window telemetry through FFmpeg `astats`, `aspectralstats`, `silencedetect`, and `ebur128`. This adds no Python dependency. Frame metrics include timestamp, RMS, peak, zero-crossing rate, spectral centroid, spectral flatness, and low-energy classification.

The module returns an immutable plan containing revised source windows, audio-overlap duration, visual-cut position inside the overlap, per-take gain, selected-candidate evidence, rejected-candidate reasons, and all safety margins.

### Word-safety contract

Deepgram timestamps remain the speech source of truth. The planner must retain at least 60 ms before the first word and 60 ms after the final word. It may search outward from those hard guards for a quieter boundary, but it may never move inward across a guard or use a candidate whose analysis window intersects a detected word.

Every final transcript must retain exact normalized ordered words with zero word error. A clipped first or final word, changed word count, or cross-beat leakage fails the composition regardless of acoustic score.

### Breath/noise-island contract

For every inter-beat gap, the planner searches the previous take's post-word margin and the next take's pre-word margin together. A valid seam produces either:

- one contiguous low-energy pause between words; or
- a short verified non-speech overlap whose picture cut is offset from the acoustic transition.

A candidate is rejected when it would retain an isolated non-speech island longer than 80 ms between two low-energy blocks. The island classifier combines RMS, spectral centroid, spectral flatness, zero-crossing rate, word exclusion, and neighboring low-energy context. No single amplitude threshold decides the result.

The desired final inter-word seam gap is 100-280 ms. The planner prefers the shortest natural candidate near 160 ms, but speech guards and valid low-energy structure take precedence over the preferred duration. A seam outside 100-320 ms fails unless an explicit operator-reviewed exception is persisted.

### Audio and visual timeline contract

Audio and video are composed as separate filter chains.

For seam `i`, the audio overlap duration `d_i` is 40-70 ms. The hard picture cut occurs `v_i` seconds after the overlap begins, where `0 <= v_i <= d_i`. The planner may move `v_i` within the overlap so the picture cut does not have to equal the lowest-energy audio point. The maximum picture/audio offset is 60 ms.

For an internal take:

- video starts at `audio_start + v_previous`;
- video ends at `audio_end - (d_next - v_next)`;
- audio retains the full planned start/end window and participates in the adjacent equal-power overlaps.

This makes the total hard-cut video duration equal the crossfaded audio duration while keeping each take's source video and source audio aligned at the picture cut. The overlap must contain verified non-speech on both sides. If that condition is unavailable, the seam fails rather than crossfading spoken phonemes from different takes.

FFmpeg performs the audio transitions with an equal-power curve. Video remains a hard concat with full approved framing; no dissolve, synthetic reframe, or generated bridge frame is introduced.

### Dialogue loudness contract

The planner measures active speech only, excluding pre-roll, post-roll, and inter-word silence. It targets the median active-speech level across approved takes and applies one constant gain per take, clamped to plus or minus 2.0 dB. After adjustment, active-speech RMS range across takes must be at most 1.5 dB.

If the range cannot be reached inside the gain clamp, acoustic QA fails. The pipeline does not hide a materially different recording behind heavy normalization.

### Duration contract

Removing respiratory islands must not reinsert silence at semantic seams merely to preserve duration. After all natural seams are selected, the compositor may extend only the final take's post-dialogue outro to keep the 16-second tier inside 14.5-16.5 seconds. It may use only real unused frames and native room tone already present after the last word.

If the final raw take lacks enough safe capacity to reach 14.5 seconds, duration QA fails. The pipeline will not time-stretch dialogue, duplicate frames, slow motion, or add synthetic room tone.

## Composition Flow

1. Reuse the four approved raw Veo takes and their persisted Deepgram transcripts.
2. Extract deterministic acoustic telemetry from each take.
3. Compute speech-only gain targets.
4. Enumerate transcript-safe seam candidates.
5. Reject candidates containing retained breath/noise islands or spoken overlap.
6. Select one natural pause, overlap duration, and visual-cut position per seam.
7. Persist the acoustic seam plan before FFmpeg composition.
8. Build separate hard-cut video and equal-power audio filter chains.
9. Extend only the final native outro when required by the duration envelope.
10. Transcribe the recomposed video and require exact ordered dialogue.
11. Burn captions from the final transcript timings.
12. Run deterministic acoustic QA, qualitative seam-audio QA, media probing, and upload verification.
13. Show the revised captioned video for approval before enabling the behavior by default.

## Failure and Recovery

The planner fails closed with structured reasons when no safe candidate exists, word guards are insufficient, analysis output is incomplete, loudness matching exceeds its clamp, crossfade inputs contain speech, final duration cannot be satisfied, or final transcript QA changes.

Recomposition remains non-generative and resumable. It archives the prior stitch, captions, manifest snapshot, and upload evidence before replacing working artifacts. A rerun with the same input hashes and planner version reuses the persisted analysis and does not submit a provider operation.

An acoustic failure may recommend an operator-reviewed retry of only the affected take, but it never triggers that paid retry automatically.

## QA Contract

### Deterministic hard gates

Every seam must satisfy all of the following:

- at least 60 ms speech guard on each side;
- no speech inside the crossfade;
- final inter-word gap of 100-320 ms unless an operator exception is persisted;
- at most one contiguous low-energy pause;
- no isolated non-speech island over 80 ms between low-energy blocks;
- audio overlap of 40-70 ms;
- picture/audio offset no greater than 60 ms;
- short-window seam energy change no greater than 6 dB after gain matching;
- active-speech RMS range no greater than 1.5 dB across takes;
- audio and video final durations within one output video frame;
- exact final ordered words with zero word error;
- final duration inside 14.5-16.5 seconds;
- valid 9:16 H.264/AAC MP4.

### Qualitative seam gate

The existing Gemini audio path receives short hashed seam excerpts and returns structured results for audible breath restart, duplicated breath, click, room-tone reset, choppy cadence, speaker discontinuity, and evidence sufficiency. Every blocking dimension must pass with confidence of at least 0.85. The result is cached by source hashes, planner version, model, and rubric version.

This model check supplements deterministic evidence. It cannot override a failed word guard, transcript, duration, or timeline invariant, and it never authorizes automatic paid work.

### Human approval gate

The first implementation recomposes the existing run into a new versioned artifact while leaving the accepted delivery in history. The user receives the revised captioned MP4 for direct review. Production-default activation remains blocked until that preview is approved.

## Manifest and Observability

`manifest.json` adds:

- acoustic-analysis version and FFmpeg version;
- input audio hashes and analysis cache keys;
- per-take active-speech measurements and applied gain;
- every accepted and rejected seam candidate with reason codes;
- selected audio start/end, video start/end, overlap duration, and picture-cut position;
- breath/noise-island metrics and low-energy intervals;
- deterministic acoustic QA report;
- qualitative seam QA report and excerpt hashes;
- source and final audio/video duration reconciliation;
- composition-history linkage and final artifact hashes.

Structured logs record analysis start/completion, candidate rejection summaries, selected seam plans, FFmpeg composition completion, and QA failures without logging raw audio, prompts, credentials, or personal data.

## Testing

### Unit tests

- word guards cannot be crossed;
- a `pause -> breath island -> pause` fixture is rejected;
- one contiguous pause is accepted;
- candidate selection prefers the natural 160 ms neighborhood;
- speech-bearing overlap is rejected;
- loudness gain is median-targeted and clamped;
- picture-cut placement and overlap math preserve A/V duration equality;
- insufficient final-outro capacity fails explicitly;
- analysis cache keys change with audio, planner, model, or rubric version.

### FFmpeg integration tests

- synthetic clips produce a hard picture cut with a short equal-power audio overlap;
- output duration equals the planned duration within one frame;
- tones before and after a seam remain present without a click or long amplitude hole;
- mismatched input sample rates and channel layouts normalize deterministically;
- captions remain monotonic after the shorter acoustic seams.

### Existing-run regression

The current four-take run must be recomposed without new Veo requests. The revised artifact must remove the measured 245/140/194 ms breath/noise islands, retain exact German dialogue, preserve actor framing and captions, remain within the duration envelope, and pass upload byte/hash readback.

## Acceptance

The feature is ready for preview when focused and regression tests pass, the existing run recomposes without a paid generation, deterministic and qualitative acoustic QA pass, final transcript WER remains 0.0, A/V duration differs by no more than one frame, and a playable captioned MP4 is produced.

The feature is ready for default integration only after the user approves that revised MP4. Until then, the current production/default composition behavior remains unchanged.
