# Semantic End-Panning Prevention Design

## Problem

Semantic Manual and Semantic UGC 16-second deliveries can preserve a provider-generated camera pan at the end. The exact-duration retime does not create the pan, but it can make the retained defect more visible. The previous global tail trim is intentionally disabled because it cut speech in other pipelines.

## Approved design

- Reserve the final 500 ms of the transcript-safe 16-second Semantic composition as a non-deliverable safety margin.
- Apply the exclusion after transcript-safe seam composition and its bounded delivery retime, then pitch-preservingly normalize the retained 15.5 seconds back to exactly 16 seconds in the same FFmpeg filter graph.
- Keep transcript and seam planning unchanged so the safety margin cannot reintroduce the historical speech-cutoff regression.
- Keep the protection Semantic-specific. Do not re-enable the legacy global `TRIM_TAIL_MS` setting.
- Explicitly lock camera position after the final word and add pan, tilt, dolly, orbit, and general camera movement to the required negative prompt.
- Add a visual-QA frame at the last frame eligible for delivery, immediately before the reserved margin.

## Preserved contracts

The actress identity, chosen outfit, chosen location, visible manual wheelchair, native provider audio, transcript safety, and exact 16-second delivery remain unchanged.

## Acceptance criteria

1. The exact Semantic delivery graph removes its final 500 ms before emitting the final output.
2. Exact 16-second output still uses bounded pitch-preserving A/V retiming and passes the one-frame duration contract.
3. Prompt and required negatives explicitly prevent post-speech camera movement.
4. Contact sheets include a `delivered-tail` sample at the last eligible delivered frame.
5. Existing transcript, seam, duration, character, outfit, location, and wheelchair tests remain green.
