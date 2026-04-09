# Video Posting Flow (Meta + TikTok)

This document explains how video posting works in Lippe Lift Studio, including the user flow, UI elements, stage transitions, and the files involved.

## 1) Operator Flow (What to click)

### A. Connect accounts in the Accounts Hub

Open `Accounts` from the navbar.

- Meta card (`Facebook + Instagram`)
  - `Connect Meta` / `Disconnect`
  - Optional `Publishing Target` dropdown + `Save Meta Target`
- TikTok card
  - `Connect TikTok` / `Disconnect`

The accounts modal reads provider state from:

- `GET /publish/accounts/status`

### B. Go to Batch detail and open S7 publish card

In each post card (`S7_PUBLISH_PLAN`), use:

- `Scheduled Time (Europe/Berlin)` (`datetime-local`)
- `Shared Caption`
- `Meta Publish Networks` buttons:
  - `Instagram`
  - `Facebook`
- `Save Meta Schedule`

### C. Arm Meta dispatch

When all active posts are scheduled, use:

- `Confirm & Arm Meta`

This marks posts as `scheduled` and the background scheduler dispatches due posts.

### D. TikTok actions

In the `TikTok` section of the post card:

- `Post to TikTok` (only when direct-post is ready)
- `Upload Draft to TikTok` (sandbox/draft-ready path)

For draft uploads, expected terminal state is usually:

- `Awaiting creator action`
- with a visible `Publish ID`

## 2) State and Stage Behavior

### Batch stage

- Planning/publish UI is shown in `S7_PUBLISH_PLAN`.
- Batch moves to `S8_COMPLETE` when active scheduled posts are terminal (`published` or `failed`), per reconcile logic.

### Post publish statuses

Main post status:

- `pending`
- `scheduled`
- `publishing`
- `published`
- `failed`

Per-network results are stored in `publish_results` and `platform_ids`.

## 3) API/Endpoint Flow

### Accounts and OAuth

- `GET /publish/accounts/status`
- `GET /publish/meta/connect`
- `GET /publish/meta/callback`
- `POST /publish/batches/{batch_id}/meta/select-target`
- `POST /publish/batches/{batch_id}/meta/disconnect`
- `GET /api/auth/tiktok/start` (from UI link)
- `GET /publish/tiktok/callback` (also aliased by `/api/auth/tiktok/callback`)

### Meta schedule and dispatch

- `POST /publish/posts/{post_id}/schedule` -> save one post plan
- `POST /publish/batches/{batch_id}/confirm` -> arm dispatch
- Scheduler job (`run_scheduled_publish_job`) dispatches due posts

### TikTok publish paths

- `POST /api/tiktok/publish` (direct post)
- `POST /api/tiktok/upload-draft` (draft upload)
- `GET /api/tiktok/publish-jobs/{job_id}` (job/status lookup)

## 4) UI Elements and Their Owner Files

### Batch detail shell

- `templates/batches/detail.html`
- `templates/batches/detail/_workflow_panels.html`
- `templates/batches/detail/_posts_section.html`
- `templates/batches/detail/_post_card.html`

### Accounts hub modal

- `templates/components/accounts_hub.html`

### Publish interactive logic (Alpine component)

- `static/js/batches/detail.js`
  - `publishSchedulerComponent(...)`
  - Methods used by S7 controls:
    - `toggleNetwork`
    - `saveSchedule`
    - `postToTikTok`
    - `uploadTikTokDraft`
    - `canSave`
    - `canPostTikTok`
    - `canUploadTikTokDraft`

## 5) Backend Files Involved

### Publish (Meta + orchestrator)

- `app/features/publish/handlers.py`
  - account status
  - Meta OAuth + target selection
  - schedule save/update
  - batch confirm/arm
  - due dispatch + network publish
  - batch completion reconciliation

### TikTok

- `app/features/publish/tiktok.py`
  - TikTok OAuth callbacks
  - account state/readiness
  - direct publish
  - draft upload
  - publish job/status handling

- `app/features/publish/tiktok_crypto.py`
  - token/account secret handling

### Contracts/schemas

- `app/features/publish/schemas.py`
- `app/features/batches/schemas.py`

### Batch detail assembly

- `app/features/batches/handlers.py`
  - composes batch payload + publish state for template rendering

## 6) Data Fields Used in Posting

Post-level fields used by publish flow:

- `scheduled_at`
- `publish_caption`
- `social_networks`
- `publish_status`
- `publish_results`
- `platform_ids`
- `video_url`

Batch-level fields used by Meta readiness:

- `meta_connection`
  - `available_pages`
  - `selected_page`
  - `selected_instagram`
  - `publish_ready`
  - `readiness_reason`

TikTok readiness payload is provided via `tiktok_connection` in batch detail context.

## 7) Quick Troubleshooting Checklist

- If buttons are visible but dead:
  - verify `static/js/batches/detail.js` is loading (no mixed-content block).
- If Meta schedule save is disabled:
  - ensure `video_url`, caption, datetime, and at least one selected Meta network.
- If `Confirm & Arm Meta` does not appear:
  - ensure all active posts are scheduled.
- If TikTok direct post returns validation errors:
  - check `tiktok_connection.publish_ready` and `readiness_reason`.
- If TikTok draft succeeds but nothing appears as public post:
  - expected for draft mode; continue from TikTok inbox/creator action.

