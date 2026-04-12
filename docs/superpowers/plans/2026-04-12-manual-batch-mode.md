# Manual Batch Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a batch creation mode toggle so users can either run the current automated research-agent flow or create a manual batch with blank draft posts that they fully author by hand.

**Architecture:** Keep the batch lifecycle intact and add a single batch-level `creation_mode` switch that decides whether creation triggers topic discovery or manual draft insertion. Manual batches should reuse the existing post script and prompt editing paths, but their draft posts start with an empty/freeform post type and a blank script so the user can define both manually before moving the batch forward. The implementation should stay localized to the batch and post feature slices, with one database migration, one batch draft helper, one post edit extension, and small template updates.

**Tech Stack:** FastAPI, Jinja2, HTMX, Pydantic, Supabase SQL migrations, pytest. No new dependencies.

**Scope Budget:** `{files: 7-9, LOC/file: <=250 target, <=500 hard, deps: 0}`

---

### Task 1: Add persisted contracts for manual batch mode

**Files:**
- Create: `supabase/migrations/20260412_manual_batch_mode.sql`
- Modify: `app/features/batches/schemas.py`
- Modify: `app/features/posts/schemas.py`
- Modify: `app/features/batches/queries.py`
- Create: `tests/test_batches_manual_mode.py`

- [ ] **Step 1: Write the failing test**

Add a contract test that proves manual batch requests must carry a manual post count and that legacy automated requests still validate.

```python
import pytest
from pydantic import ValidationError

from app.features.batches.schemas import CreateBatchRequest


def test_manual_batch_request_requires_manual_post_count():
    with pytest.raises(ValidationError):
        CreateBatchRequest.model_validate(
            {
                "brand": "ACME",
                "creation_mode": "manual",
                "target_length_tier": 8,
            }
        )


def test_automated_batch_request_still_accepts_type_counts():
    payload = CreateBatchRequest.model_validate(
        {
            "brand": "ACME",
            "creation_mode": "automated",
            "post_type_counts": {"value": 2, "lifestyle": 1, "product": 0},
            "target_length_tier": 16,
        }
    )
    assert payload.creation_mode == "automated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batches_manual_mode.py::test_manual_batch_request_requires_manual_post_count -v`

Expected: FAIL because `CreateBatchRequest` does not yet know about `creation_mode` or `manual_post_count`.

- [ ] **Step 3: Write minimal implementation**

Add a small schema gate and migration. Keep old rows readable by defaulting missing `creation_mode` to `automated`.

```python
from typing import Optional, Literal

class CreateBatchRequest(BaseModel):
    brand: str
    creation_mode: Literal["automated", "manual"] = "automated"
    post_type_counts: Optional[PostTypeCounts] = None
    manual_post_count: Optional[int] = Field(None, ge=1, le=100)
    target_length_tier: int = Field(default=8, ge=8, le=32)

    @validator("post_type_counts")
    def validate_post_type_counts(cls, value, values):
        if values.get("creation_mode") == "automated" and value is None:
            raise ValueError("post_type_counts are required for automated batches")
        return value

    @validator("manual_post_count")
    def validate_manual_post_count(cls, value, values):
        if values.get("creation_mode") == "manual" and value is None:
            raise ValueError("manual_post_count is required for manual batches")
        return value
```

In the migration, add the batch columns and relax the posts post-type constraint so manual drafts can start blank and later accept a freeform custom type:

```sql
ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS creation_mode TEXT NOT NULL DEFAULT 'automated',
  ADD COLUMN IF NOT EXISTS manual_post_count INTEGER;

ALTER TABLE public.posts DROP CONSTRAINT IF EXISTS posts_post_type_check;
ALTER TABLE public.posts
  ADD CONSTRAINT posts_post_type_check CHECK (post_type IS NOT NULL);
```

Update `BatchResponse` and `BatchDetailResponse` to expose `creation_mode` and `manual_post_count`, and make `PostDetail.post_type` optional so blank manual drafts serialize cleanly.

Also update `duplicate_batch(...)` so it carries the source batch's `creation_mode` and `manual_post_count` forward instead of silently resetting duplicated manual batches back to automated mode.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_batches_manual_mode.py::test_manual_batch_request_requires_manual_post_count -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260412_manual_batch_mode.sql app/features/batches/schemas.py app/features/posts/schemas.py app/features/batches/queries.py tests/test_batches_manual_mode.py
git commit -m "feat: add manual batch mode contracts"
```

### Task 2: Branch batch creation between automated research and manual draft insertion

**Files:**
- Modify: `app/features/batches/handlers.py`
- Modify: `app/features/batches/queries.py`
- Modify: `templates/batches/list.html`
- Modify: `tests/test_batches_manual_mode.py`

- [ ] **Step 1: Write the failing test**

Add a handler test that proves manual batch creation does not schedule topic discovery and does create blank draft posts instead.

```python
@pytest.mark.anyio
async def test_create_batch_manual_mode_skips_discovery_and_creates_drafts(monkeypatch):
    scheduled = {"called": False}
    created = {"count": 0}

    def fake_schedule(*args, **kwargs):
        scheduled["called"] = True

    def fake_create_manual_draft_posts(batch_id, manual_post_count, target_length_tier):
        created["count"] = manual_post_count
        return [{"id": f"post-{i}"} for i in range(manual_post_count)]

    monkeypatch.setattr(topic_handlers, "schedule_batch_discovery", fake_schedule)
    monkeypatch.setattr(batch_handlers, "create_manual_draft_posts", fake_create_manual_draft_posts)
    # await create_batch_endpoint(request) with creation_mode=manual and manual_post_count=3

    assert scheduled["called"] is False
    assert created["count"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batches_manual_mode.py::test_create_batch_manual_mode_skips_discovery_and_creates_drafts -v`

Expected: FAIL because the handler still always schedules discovery.

- [ ] **Step 3: Write minimal implementation**

Split the batch create handler into two branches and add a batch-local manual draft helper. Keep automated behavior unchanged.

```python
batch = create_batch(
    brand=payload.brand,
    post_type_counts=payload.post_type_counts.model_dump() if payload.post_type_counts else {},
    target_length_tier=normalize_target_length_tier(payload.target_length_tier),
    creation_mode=payload.creation_mode,
    manual_post_count=payload.manual_post_count,
)

if payload.creation_mode == "manual":
    create_manual_draft_posts(
        batch_id=batch["id"],
        manual_post_count=payload.manual_post_count or 0,
        target_length_tier=payload.target_length_tier,
    )
    batch = update_batch_state(batch["id"], BatchState.S2_SEEDED)
else:
    start_seeding_interaction(
        batch_id=batch["id"],
        brand=batch["brand"],
        expected_posts=payload.post_type_counts.total,
    )
    schedule_batch_discovery(batch["id"], reason="batch_create")
```

Implement `create_manual_draft_posts(...)` in `app/features/batches/queries.py` by reusing the existing post insert helper with a blank post type and a placeholder title:

```python
from app.features.topics.queries import create_post_for_batch

def create_manual_draft_posts(batch_id: str, manual_post_count: int, target_length_tier: int) -> list[dict[str, Any]]:
    created = []
    for index in range(manual_post_count):
        created.append(
            create_post_for_batch(
                batch_id=batch_id,
                post_type="",
                topic_title=f"Manual Draft {index + 1}",
                topic_rotation="",
                topic_cta="",
                spoken_duration=0,
                seed_data={"script": "", "script_review_status": "pending", "manual_draft": True},
                target_length_tier=target_length_tier,
            )
        )
    return created
```

Update the create-batch modal in `templates/batches/list.html` so the form switches between the current type-count inputs and a single `manual_post_count` input. The HTMX temp-progress script should compute `expected` from `manual_post_count` when the manual mode is selected.

```html
<select name="creation_mode" x-model="creationMode">
  <option value="automated">Automated</option>
  <option value="manual">Manual</option>
</select>

<template x-if="creationMode === 'manual'">
  <input type="number" name="manual_post_count" min="1" value="3">
</template>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_batches_manual_mode.py::test_create_batch_manual_mode_skips_discovery_and_creates_drafts -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/features/batches/handlers.py app/features/batches/queries.py templates/batches/list.html tests/test_batches_manual_mode.py
git commit -m "feat: branch batch creation for manual drafts"
```

### Task 3: Let manual drafts capture a freeform post type and keep the prompt path reusable

**Files:**
- Modify: `app/features/posts/handlers.py`
- Modify: `templates/batches/detail/_post_card.html`
- Create: `tests/test_posts_manual_draft_mode.py`

- [ ] **Step 1: Write the failing test**

Add a post-handler test that proves a manual draft save can set a custom freeform post type alongside the script, and that saving still invalidates stale prompt JSON.

```python
@pytest.mark.anyio
async def test_manual_draft_save_updates_post_type_and_script(monkeypatch):
    stored = {
        "id": "post-1",
        "batch_id": "batch-1",
        "post_type": "",
        "seed_data": {"script": "", "script_review_status": "pending"},
        "video_prompt_json": {"old": True},
    }

    monkeypatch.setattr(posts_handlers, "_load_post_seed_data", lambda post_id, supabase: (stored, stored["seed_data"]))
    monkeypatch.setattr(posts_handlers, "get_supabase", fake_supabase_that_records_updates)

    # await update_post_script("post-1", request) with script_text="Neuer Text" and post_type="custom_story"
    # assert update payload contains top-level post_type="custom_story"
    # assert seed_data["script"] == "Neuer Text"
    # assert video_prompt_json is cleared
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_posts_manual_draft_mode.py::test_manual_draft_save_updates_post_type_and_script -v`

Expected: FAIL because `/posts/{post_id}/script` only knows about script text today.

- [ ] **Step 3: Write minimal implementation**

Extend the existing script update request so it can carry a freeform post type. For manual batches, reject saves that still omit the type, because the user must define it before the draft can advance.

Before updating, widen `_load_post_seed_data(...)` so it selects the top-level `post_type` column too; the handler needs that value to write the freeform type back to the row.

```python
class UpdateScriptRequest(BaseModel):
    script_text: str = Field(..., min_length=1, max_length=900)
    post_type: Optional[str] = Field(default=None, max_length=120)
```

Inside `update_post_script(...)`, load the batch row, branch on `creation_mode`, and write both the script and the custom post type when the batch is manual:

```python
post, seed_data = _load_post_seed_data(post_id, supabase)
batch = get_batch_by_id(post["batch_id"])

post_type = str(payload.post_type or "").strip()
if batch.get("creation_mode") == "manual" and not post_type:
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="post_type is required for manual drafts")

seed_data["script"] = script_text
seed_data["script_review_status"] = "pending"
seed_data.pop("video_excluded", None)
if post_type:
    seed_data["manual_post_type"] = post_type

supabase.table("posts").update(
    {
        "seed_data": seed_data,
        "post_type": post_type or post.get("post_type"),
        "video_prompt_json": None,
    }
).eq("id", post_id).execute()
```

Render the manual draft card in `templates/batches/detail/_post_card.html` so manual batches show a freeform post type input next to the script textarea. Keep the current automated script editor unchanged.

```html
{% if batch.creation_mode == 'manual' %}
  <label class="block text-sm font-medium text-gray-700">Post Type</label>
  <input
    type="text"
    name="post_type"
    value="{{ post.post_type or '' }}"
    placeholder="e.g. testimonial_story"
    class="mt-1 w-full border border-gray-300 rounded-md px-3 py-2"
  >
{% endif %}
```

The existing prompt builder path stays reusable: once the script is saved, the current `/posts/{post_id}/build-prompt` flow should still generate the prompt basis, and the existing prompt edit modal should remain the place where the whole setting is adjusted.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_posts_manual_draft_mode.py::test_manual_draft_save_updates_post_type_and_script -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/features/posts/handlers.py templates/batches/detail/_post_card.html tests/test_posts_manual_draft_mode.py
git commit -m "feat: support manual draft post editing"
```

### Task 4: Add lifecycle regressions and verify the existing automated path stays unchanged

**Files:**
- Modify: `app/features/batches/handlers.py`
- Modify: `app/features/batches/schemas.py`
- Modify: `tests/test_batches_manual_mode.py`
- Modify: `tests/test_posts_manual_draft_mode.py`

- [ ] **Step 1: Write the failing test**

Add a regression test that proves manual batches do not trigger the topic-discovery recovery path if they ever appear in `S1_SETUP`, and that the detail/status payload exposes `creation_mode` for template branching.

```python
@pytest.mark.anyio
async def test_manual_batch_status_does_not_restart_research(monkeypatch):
    batch = {"id": "batch-1", "brand": "ACME", "state": "S1_SETUP", "creation_mode": "manual"}
    monkeypatch.setattr(batch_handlers, "get_batch_by_id", lambda batch_id: batch)
    monkeypatch.setattr(batch_handlers, "get_batch_posts_summary", lambda batch_id: {"posts_count": 0, "posts_by_state": {}})
    monkeypatch.setattr(batch_handlers, "schedule_batch_discovery", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not schedule")))

    payload = await batch_handlers.get_batch_status("batch-1")
    assert payload.data["state"] == "S1_SETUP"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batches_manual_mode.py::test_manual_batch_status_does_not_restart_research -v`

Expected: FAIL because the status endpoint still assumes every empty `S1_SETUP` batch is an automated discovery batch.

- [ ] **Step 3: Write minimal implementation**

Short-circuit the discovery recovery logic for manual batches and make sure the response schemas include the new fields so HTMX and Jinja can branch on them.

```python
if batch.get("creation_mode") == "manual":
    payload = {
        "id": batch["id"],
        "state": batch["state"],
        "creation_mode": batch.get("creation_mode", "automated"),
        "manual_post_count": batch.get("manual_post_count"),
        "posts_count": posts_summary["posts_count"],
        "posts_by_state": posts_summary["posts_by_state"],
        "updated_at": batch["updated_at"],
        "progress": None,
    }
    return SuccessResponse(data=payload)
```

In the detail templates, branch on `batch.creation_mode` so the manual editor appears only for manual batches. The automated path should look exactly like it does now.

- [ ] **Step 4: Run test suite slices to verify nothing regressed**

Run:

`pytest tests/test_batches_manual_mode.py -v`

`pytest tests/test_posts_manual_draft_mode.py -v`

`pytest tests/test_posts_script_review.py -v`

Expected: all pass, and the existing automated script-review flow behaves the same as before.

- [ ] **Step 5: Commit**

```bash
git add app/features/batches/handlers.py app/features/batches/schemas.py tests/test_batches_manual_mode.py tests/test_posts_manual_draft_mode.py
git commit -m "feat: harden manual batch lifecycle"
```

## Self-Review Checklist

- The database schema now supports a manual batch mode without breaking old automated batches.
- Manual batches create blank drafts and do not schedule research.
- Manual post type is freeform and must be supplied by the user before the draft advances.
- The existing prompt builder remains the same reusable path after the manual script is entered.
- Existing automated batches, script review, prompt generation, and publish behavior stay unchanged.
