# TikTok Content Posting API — Audit Reapply Notes

## What changed

- `posts.tiktok_settings JSONB` and `batches.tiktok_defaults JSONB` columns store every TikTok-required field per post and per batch.
- New Pydantic models `TikTokPostSettings`, `TikTokBatchDefaults`, and an updated `TikTokPublishRequest` enforce the disclosure rules server-side (privacy from `creator_info.privacy_level_options`, branded content cannot use `SELF_ONLY`, etc.).
- `DEFAULT_PRIVACY_LEVEL` has been removed from `app/features/publish/tiktok.py`. The backend refuses to build a TikTok post-info payload without an explicit title and privacy level.
- A new Jinja partial `templates/batches/detail/_tiktok_post_settings.html` plus Alpine component `static/js/batches/tiktok_post_settings.js` render every required UX block:
  - §1 Creator strip (`creator_nickname`, `creator_username`, readiness) + duration vs `max_video_post_duration_sec`.
  - §2 Title, privacy radio cards (no default), interaction toggles (unchecked by default; greyed-out per creator settings).
  - §3 Commercial disclosure toggle with Your Brand / Branded Content sub-checkboxes and live "Promotional content" / "Paid partnership" preview chip.
  - §4 Music Usage Confirmation always rendered; Branded Content Policy added when Branded Content is selected.
  - §5 Editable caption + hashtag visibility, processing notice, explicit Save / Post button, status polling already wired in the adapter.
- A batch-level "TikTok defaults" panel (`_tiktok_batch_defaults.html`) lets the editor configure once and have those values pre-fill every TikTok-targeted post.
- `/publish/posts/{id}/now` and scheduled dispatch direct-post only when `tiktok_state.readiness_status` is `publish_ready` and complete settings are saved. Every generated video sends server-owned `post_info.is_aigc=true`. Because TikTok's draft-upload API cannot attach that native AI label, automated publishing fails closed instead of falling back to a draft.
- Batch Arm refuses to schedule any post that lists `tiktok` in its networks without complete TikTok settings.

## Reviewer walkthrough script

1. Connect a TikTok account → batch detail shows the creator strip.
2. Toggle TikTok on the batch → the "TikTok defaults" panel appears.
3. Try to publish before selecting privacy → button stays disabled.
4. Choose Public → 3 interaction toggles default to off.
5. Toggle "Disclose video content" → choose Your Brand → "Promotional content" chip appears.
6. Switch to Branded Content → chip becomes "Paid partnership"; Private radio is disabled with a tooltip explaining why.
7. Save settings, then Post Now → modal shows preview, panel and the consent line "Content may take a few minutes to appear on your profile."

## Files of interest

- `templates/batches/detail/_tiktok_post_settings.html`
- `templates/batches/detail/_tiktok_batch_defaults.html`
- `static/js/batches/tiktok_post_settings.js`
- `app/features/publish/schemas.py` (TikTokPostSettings, TikTokPublishRequest)
- `app/features/publish/tiktok.py` (no defaults, brand toggles forwarded)
- `app/features/publish/handlers.py` (Post Now routing, settings endpoints)
- `app/features/publish/arm.py` (Arm-time validation)
