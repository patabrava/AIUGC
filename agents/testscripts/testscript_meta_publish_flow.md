# TS-S7-Meta-Publish

Objective: verify the batch-scoped standard Facebook Login connection, per-post caption planning, and scheduled Facebook/Instagram dispatch flow in the real app.

Prerequisites:
- `META_APP_ID`, `META_APP_SECRET`, `META_REDIRECT_URI`
- `CRON_SECRET`
- one Meta user with at least one manageable Facebook Page linked to an Instagram business account
- one batch already advanced to `S7_PUBLISH_PLAN` with at least one approved post and a generated `video_url`

Setup:
1. Start the app: `PYTHONPYCACHEPREFIX=/tmp ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
2. Open the target batch detail page in the browser.

Run:
1. In `S7_PUBLISH_PLAN`, click `Connect Meta` and complete the standard Facebook Login flow.
2. Back on the batch page, select the Page that shows the linked Instagram business account and save the target.
3. For one post, enter a shared caption, choose `Facebook` and `Instagram`, and save a schedule at least one hour in the future.
4. Click `Confirm & Arm Dispatch`.
5. Either wait for the in-process scheduler or trigger dispatch manually:
   `curl -X POST http://127.0.0.1:8000/publish/cron/dispatch -H "Authorization: Bearer $CRON_SECRET"`
6. Reload the batch detail page after the scheduled time passes.

Expected observations:
- The batch shows the connected Meta user and the bound Page + Instagram target.
- The per-post scheduler displays the saved caption and the UTC preview matches the Berlin wall-clock time chosen in the input.
- `Confirm & Arm Dispatch` does not immediately move the batch to `S8_COMPLETE`.
- After dispatch, the post shows separate Facebook and Instagram result statuses.
- The batch advances to `S8_COMPLETE` only after every active scheduled post reaches `published` or `failed`.

Artifacts to capture on failure:
- browser screenshot of the `S7_PUBLISH_PLAN` section
- server log lines containing `meta_connection_created`, `batch_publish_dispatch_armed`, or `meta_due_post_dispatched`
- the `posts.publish_results` and `posts.publish_status` values for the failing post
