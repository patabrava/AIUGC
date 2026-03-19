# TikTok Sandbox Flow Testscript

## Objective

Verify the TikTok sandbox OAuth and draft upload flow end-to-end against one authorized sandbox account.

## Prerequisites

- Supabase migrations applied through `006_add_tiktok_publish_integration.sql`
- TikTok sandbox app configured with:
  - `Login Kit`
  - `Content Posting API`
  - scopes `user.info.basic` and `video.upload`
- `.env` populated with TikTok sandbox values
- FastAPI app running locally
- One batch in `S7_PUBLISH_PLAN`
- One generated post with `video_url`

## Run

1. Open the batch detail page in `S7_PUBLISH_PLAN`.
2. Use the TikTok connect button to complete OAuth with the authorized sandbox account.
3. Confirm the TikTok card shows `Connected`.
4. On a post with a generated video, enter a caption and click `Upload Draft to TikTok`.

## Expected Observations

- `/api/auth/tiktok/start` redirects to TikTok with the exact configured redirect URI.
- `/api/auth/tiktok/callback` returns to the batch detail page.
- `connected_accounts` contains the TikTok sandbox account row.
- `publish_jobs` contains a `submitted` TikTok draft-upload job.
- The post card shows a TikTok draft upload result with a publish id.

## Artifact Capture

- App logs during OAuth and upload
- Screenshot of the S7 TikTok card after connect
- Screenshot of the per-post TikTok draft result
- DB snapshots for `connected_accounts` and `publish_jobs`
