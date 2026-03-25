# VO 3.1 Extension MD

## Purpose

This document records concrete proof that Veo 3.1 extended chaining works with `9:16` for duration tiers `16` and `32`.

## Claim Being Verified

- Extended Veo runs (`target_length_tier=16` and `target_length_tier=32`) can complete successfully in `9:16`.
- Extended route uses `720p` resolution for chaining.

## Verification Method

Historical records were checked from Supabase `posts` rows where:

- `video_metadata.video_pipeline_route = "veo_extended"`
- `video_metadata.requested_aspect_ratio = "9:16"`

Then result fields were inspected:

- `video_status`
- `video_url`
- `video_metadata.target_length_tier`
- `video_metadata.requested_seconds`
- `video_metadata.actual_seconds`
- `video_metadata.generated_seconds`
- `video_metadata.requested_resolution`

## Evidence (Successful 9:16 Extended Runs)

### Evidence A: 32s completed in 9:16

- `batch_id`: `114ff2c8-7d57-403d-a310-8130d8976fce`
- `post_id`: `a29cc858-4bcd-42e8-8627-f76482aaa3bb`
- `video_status`: `completed`
- `video_pipeline_route`: `veo_extended`
- `requested_aspect_ratio`: `9:16`
- `requested_resolution`: `720p`
- `target_length_tier`: `32`
- `requested_seconds`: `32`
- `actual_seconds`: `32.084`
- `generated_seconds`: `32.084`
- `video_url`: [final output](https://pub-7036b4dec03b49e5bacaab577befbbbf.r2.dev/flow-forge/videos/20260319T175136Z_post_a29cc858-4bcd-42e8-8627-f76482aaa3bb.mp4)

### Evidence B: 16-tier completed in 9:16

- `batch_id`: `2c9d2dee-d776-4421-9268-89243a5fc53a`
- `post_id`: `52bcbf2e-b6ba-4f7a-990e-598da548cba1`
- `video_status`: `completed`
- `video_pipeline_route`: `veo_extended`
- `requested_aspect_ratio`: `9:16`
- `requested_resolution`: `720p`
- `target_length_tier`: `16`
- `requested_seconds`: `16`
- `actual_seconds`: `18`
- `generated_seconds`: `18`

### Evidence C: Another 32-tier success in 9:16

- `batch_id`: `5caf9053-6fe8-44e0-9126-08fe7eb8655e`
- `post_id`: `37cab8e2-4655-414f-a8d1-cdcaef0bfc06`
- `video_status`: `completed`
- `video_pipeline_route`: `veo_extended`
- `requested_aspect_ratio`: `9:16`
- `requested_resolution`: `720p`
- `target_length_tier`: `32`
- `requested_seconds`: `32`
- `actual_seconds`: `32`
- `generated_seconds`: `32`

## Additional Context

- A historical 16-tier failure also exists:
  - `post_id`: `e3c1c32f-a16c-4f46-ae7e-4df7ab1dc218`
  - `status`: `failed`
  - `target_length_tier`: `16`
  - `aspect`: `9:16`
  - `resolution`: `720p`
  - This indicates intermittent failure modes are possible, but does not invalidate that 9:16 extended runs can and do complete.

## Conclusion

The statement "16 and 32 cannot be done in 9:16" is false based on recorded production data.

What is true:

- Extended Veo (`16/32`) is working in `9:16`.
- The extended route is running at `720p` resolution for chaining.
