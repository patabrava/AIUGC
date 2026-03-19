# Meta Login UX Stabilization

## Goal

Design and implement a global reusable Meta login flow for Facebook + Instagram that redirects users into Meta auth when they try to use locked networks, then returns them to the same batch/post card they were editing before schedule save is enforced.

## Primary User / Actor

The primary actor is the single operator managing FLOW-FORGE batches who is scheduling posts in `S7_PUBLISH_PLAN` and needs one reusable Meta connection across all batches.

## Inputs

### Required Inputs

- Production Meta app credentials:
  - `META_APP_ID`
  - `META_APP_SECRET`
  - `META_REDIRECT_URI`
- Existing production callback:
  - `https://aiugc-prod.srv1498567.hstgr.cloud/publish/meta/callback`
- Existing batch detail view with `S7_PUBLISH_PLAN`
- Existing batch/post identifiers for return-to-context after login
- Existing video-ready post with `video_url`

### Optional Inputs

- Global account-management placement in batch header, nav, or shared modal launcher
- Query-string or signed-state return target format
- Copy and visual treatment for locked-network CTA

## Outputs / Deliverables

- One global reusable Meta connect entry point labeled:
  - `Connect Facebook + Instagram`
- One unified Meta auth flow reused across all batches
- One return-to-context contract that brings the operator back to the exact batch/post card after login
- One scheduler gate that only enables save when:
  - caption exists
  - scheduled time exists
  - video exists
  - at least one eligible connected Meta network exists
- One bug fix pass after login UX lands to eliminate the broken `Save Schedule` path

## Core Pipeline

1. Add a global reusable Meta connection component outside the per-post scheduler flow.
2. When a user clicks `Facebook` or `Instagram` while not connected, redirect immediately into the unified Meta login flow.
3. Persist a return target that identifies the exact batch and post card being edited.
4. After successful Meta login, redirect back to the same batch page and restore focus/visibility on the same post card.
5. Reflect connection state globally so all batches can reuse the same Meta account.
6. Gate schedule saving on complete requirements:
   - caption
   - video
   - scheduled time
   - connected eligible Meta target
7. After login UX is stable, fix the broken schedule-save behavior and ensure error states are explicit instead of generic.

## Data / Evidence Contracts

- The Meta connection must become workspace-wide, not batch-scoped-only.
- The auth flow must expose one unified Meta connection, not separate Facebook and Instagram logins.
- Clicking locked Meta networks must redirect to Meta auth immediately, not open an inline warning first.
- Return-to-context must carry enough information to land the user back on the same batch/post card without ambiguity.
- Schedule-save validation must fail explicitly when any required prerequisite is missing.
- Any scheduling error surfaced to the user must reflect the real backend reason instead of generic fallback copy.

## Constraints

- Option selected: `{files: 5, LOC/file: <=280, deps: 0}`
- Keep the implementation in the existing FastAPI + Jinja + Alpine stack
- No new frameworks
- No new UI state management libraries
- Preserve locality inside the existing publish slice and batch detail surface
- Design login UX first, then fix the schedule-save bug
- One Meta login shared across all batches
- One unified login button and flow

## Non-Goals / Backlog

- Separate Facebook-only and Instagram-only auth paths
- Batch-scoped-only Meta connections
- A large provider-management dashboard or framework migration
- Multi-user account ownership and permissions
- TikTok UX redesign as part of this pass
- Provider abstraction refactor across Meta and TikTok in the same slice

## Definition of Done

- A reusable global `Connect Facebook + Instagram` entry point exists outside the per-post scheduler block.
- Clicking `Facebook` or `Instagram` while disconnected redirects directly into Meta auth.
- After successful auth, the operator returns to the same batch and same post card they were editing.
- Meta connection state is reusable across all batches.
- `Save Schedule` is only enabled when caption, video, time, and connected eligible Meta network are present.
- The broken save path is fixed and no longer fails due to missing hidden requirements.
- The user sees clear actionable error messages for schedule validation failures.
