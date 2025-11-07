# Phase 5 QA Review - Manual Testing Guide

**Date:** 2025-11-07  
**Status:** Ready for Testing

---

## Prerequisites

1. ✅ Dev server running on `http://localhost:8000`
2. ✅ At least one batch with completed videos (from Phase 4)
3. ✅ Video poller worker running (or batch manually set to S6_QA state)
4. ✅ Videos uploaded to ImageKit CDN

---

## Test Procedure

### Step 1: Find a Batch with Completed Videos

1. Navigate to `http://localhost:8000/batches`
2. Click on a batch that has completed video generation
3. Verify videos are displayed in the batch detail page
4. Note the batch ID for testing

**Expected:** Batch should be in `S6_QA` state (if poller has run) or `S5_PROMPTS_BUILT` state

---

### Step 2: Manually Transition to S6_QA (if needed)

If batch is still in `S5_PROMPTS_BUILT`, use Supabase MCP to update:

```python
# Use MCP tool to execute SQL
UPDATE batches 
SET state = 'S6_QA' 
WHERE id = '<batch_id>';
```

Or wait for video poller to automatically transition when all videos complete.

**Expected:** Batch state changes to `S6_QA`

---

### Step 3: Verify QA Dashboard Appears

1. Reload the batch detail page
2. Look for green "Quality Assurance Review" section at top
3. Verify it shows "X/Y Approved" count

**Expected:** QA workflow section visible with approval progress

---

### Step 4: Run Auto QA Check on First Post

1. Scroll to first post card
2. Click "Run Auto Check" button in QA section
3. Page should reload showing check results

**Expected:** 
- Duration check: ✓ (if 7.5s-8.5s) or ✗
- Resolution check: ✓ (if ≥720p) or ✗
- File accessible: ✓

---

### Step 5: Approve First Post

1. Click "Approve" button
2. Page reloads
3. Post shows "✓ Approved" badge

**Expected:** 
- Post status changes to approved
- Batch counter updates: "1/Y Approved"

---

### Step 6: Approve All Posts

1. For each remaining post, click "Approve"
2. Monitor the batch approval counter

**Expected:** Counter increases with each approval

---

### Step 7: Advance to Publish

1. When all posts approved, "Advance to Publish" button appears
2. Click the button
3. Confirm the dialog
4. Page reloads

**Expected:** 
- Batch state changes to `S7_PUBLISH_PLAN`
- State stepper shows "Plan" as active

---

## API Testing (Alternative)

### Test Auto QA Check

```bash
curl -X POST http://localhost:8000/qa/<post_id>/auto-check
```

**Expected Response:**
```json
{
  "ok": true,
  "data": {
    "duration_valid": true,
    "duration_actual": 8.0,
    "resolution_valid": true,
    "resolution_actual": "720x1280",
    "file_accessible": true,
    "overall_pass": true,
    "checked_at": "2025-11-07T12:00:00Z"
  }
}
```

---

### Test Manual Approval

```bash
curl -X PUT http://localhost:8000/qa/<post_id>/approve \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "notes": "Video quality excellent"}'
```

**Expected Response:**
```json
{
  "ok": true,
  "data": {
    "post_id": "<post_id>",
    "qa_pass": true,
    "qa_notes": "Video quality excellent",
    "qa_auto_checks": {...}
  }
}
```

---

### Test Batch QA Status

```bash
curl http://localhost:8000/qa/batch/<batch_id>/status
```

**Expected Response:**
```json
{
  "ok": true,
  "data": {
    "batch_id": "<batch_id>",
    "total_posts": 10,
    "posts_with_videos": 10,
    "posts_qa_passed": 8,
    "posts_qa_pending": 2,
    "all_passed": false,
    "can_advance_to_publish": false
  }
}
```

---

### Test Advance to Publish (with guards)

**Attempt advance when NOT all approved:**
```bash
curl -X PUT http://localhost:8000/batches/<batch_id>/advance-to-publish
```

**Expected Response:**
```json
{
  "ok": false,
  "code": "state_transition_error",
  "message": "Cannot advance to publish. 2 post(s) not approved.",
  "details": {
    "batch_id": "<batch_id>",
    "total_posts": 10,
    "approved_posts": 8,
    "pending_posts": ["<post_id_1>", "<post_id_2>"]
  }
}
```

**Attempt advance when ALL approved:**
```bash
curl -X PUT http://localhost:8000/batches/<batch_id>/advance-to-publish
```

**Expected Response:**
```json
{
  "ok": true,
  "data": {
    "id": "<batch_id>",
    "state": "S7_PUBLISH_PLAN",
    ...
  }
}
```

---

## Automated Testscript

Run the whole-app testscript:

```bash
python tests/testscript_phase5.py
```

**Expected:** All tests pass with ✅ marks

---

## Troubleshooting

### Issue: Batch not transitioning to S6_QA automatically

**Solution:** 
- Check if video poller worker is running
- Verify all posts have `video_status = 'completed'`
- Check poller logs for transition messages

### Issue: Auto QA checks fail

**Solution:**
- Verify video URL is accessible
- Check video metadata has duration and resolution fields
- Ensure ImageKit URLs are not expired

### Issue: Cannot advance to publish

**Solution:**
- Verify all posts have `qa_pass = true`
- Check batch state is `S6_QA` not `S5_PROMPTS_BUILT`
- Review error message details for specific failing posts

---

## Success Criteria

- ✅ Auto QA checks execute and return results
- ✅ Manual approval updates `qa_pass` field
- ✅ Batch QA status accurately reflects approval progress
- ✅ Advance to publish blocked when not all approved
- ✅ Advance to publish succeeds when all approved
- ✅ Batch transitions from S6_QA → S7_PUBLISH_PLAN
- ✅ UI updates reflect state changes
- ✅ All API responses follow standard envelope pattern

---

## Phase 5 Completion Checklist

- [x] QA schemas created with Pydantic validation
- [x] Auto QA check endpoint implemented
- [x] Manual approval endpoint implemented
- [x] Batch QA status endpoint implemented
- [x] Advance to publish endpoint with guards
- [x] Video poller auto-transitions to S6_QA
- [x] QA UI added to batch detail template
- [x] Phase 5 testscript created
- [x] PROGRESS.md updated

**Phase 5 is complete and ready for testing!**
