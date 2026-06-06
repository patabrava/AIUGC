# TikTok Content Posting Review Testscript

## Objective

Capture the exact live demo TikTok reviewers need to see for Content Posting API approval, including public website, policy links, Login Kit, creator-info-driven UI, direct-post controls, explicit consent, and post status.

## Prerequisites

- Production app is deployed at `https://lippelift.xyz`.
- TikTok Developer app has Login Kit and Content Posting API enabled.
- Requested scopes are only `user.info.basic`, `video.upload`, and `video.publish`.
- `https://lippelift.xyz`, `https://lippelift.xyz/terms`, and `https://lippelift.xyz/privacy` are configured in TikTok Developer Portal.
- TikTok URL/domain verification is complete for the app/media URL prefix.
- Reviewer login is configured through `REVIEWER_LOGIN_EMAIL` and `REVIEWER_LOGIN_TOKEN`.
- One batch exists in `S7_PUBLISH_PLAN` with one active generated video and no removed/video-excluded posts.
- Connected TikTok account has authorized `video.publish`.

## Demo Recording Steps

1. Open `https://lippelift.xyz/`.
   - Expected: public Lippe Lift Studio product page loads without authentication.
   - Expected: Terms, Privacy, and Sign in links are visible.
2. Open `https://lippelift.xyz/terms`.
   - Expected: Terms describe creator control, original content, TikTok publishing, and platform limits.
3. Open `https://lippelift.xyz/privacy`.
   - Expected: Privacy policy describes TikTok account data, access token handling, video publishing data, and disconnect/deletion handling.
4. Return to `https://lippelift.xyz/` and click Sign in.
   - Expected: login screen appears.
5. Sign in with the reviewer account.
   - Expected: reviewer reaches the batches dashboard.
6. Open the prepared `S7_PUBLISH_PLAN` batch.
   - Expected: generated post list is visible.
7. Open a generated post.
   - Expected: video preview is visible before any TikTok upload action.
8. Enable TikTok network if it is not already selected.
   - Expected: TikTok settings panel appears.
9. Show the TikTok account row.
   - Expected: creator nickname or username from TikTok creator info is displayed.
10. Show TikTok title field and edit it.
    - Expected: title is user-editable.
11. Show privacy choices.
    - Expected: privacy options match `creator_info.privacy_level_options`; no option is silently submitted without user selection.
12. Select a privacy option.
    - Expected: selected option is visually clear.
13. Show Comment, Duet, Stitch controls.
    - Expected: disabled creator settings are greyed out; enabled controls are manually selectable and unchecked by default.
14. Toggle commercial disclosure on.
    - Expected: Your Brand and Branded Content options appear.
15. Select Your Brand.
    - Expected: UI shows the post will be labeled Promotional content.
16. Switch to Branded Content.
    - Expected: UI shows Paid partnership and private/Only Me visibility is blocked.
17. Turn commercial disclosure off for a non-commercial demo post.
    - Expected: commercial disclosure returns to off state.
18. Check the consent box.
    - Expected: user sees TikTok Music Usage Confirmation before posting.
19. Click Save TikTok settings.
    - Expected: settings save successfully and consent remains checked after reopening the panel.
20. Click Post Now.
    - Expected: button says Post to TikTok when direct posting is available, or Upload Draft only when the account/app is draft-only.
21. Confirm the post.
    - Expected: app shows processing/published/awaiting-action status from TikTok publish status polling.
22. If direct post is blocked by unaudited/private-account policy, show the validation message.
    - Expected: message explains direct posting is blocked until client audit/private-account conditions are met and recommends draft upload for this deployment.

## Artifact Capture

- Screenshot of public homepage with Terms/Privacy visible.
- Screenshot of Terms page.
- Screenshot of Privacy page.
- Screenshot of TikTok settings panel showing creator nickname.
- Screenshot of video preview before posting.
- Screenshot showing privacy options and interaction toggles.
- Screenshot showing commercial disclosure behavior.
- Screenshot showing consent checkbox.
- Screenshot or log of post status after submission.
- Redacted app logs for `tiktok_publish_submitted` and any TikTok `log_id` values.

## Pass Criteria

- The recording shows the real `lippelift.xyz` app.
- No mock UI is used.
- No secret values are shown.
- User control is visible before upload.
- The video content has no Lippe Lift watermark, logo overlay, promotional URL, or unwanted brand mark.
- The submitted TikTok Developer Portal explanation matches what is shown in this demo.
