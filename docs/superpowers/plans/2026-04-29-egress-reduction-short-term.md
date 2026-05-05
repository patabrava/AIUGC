# Supabase Egress Reduction Short-Term Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Supabase egress in the current monolith by lowering idle polling frequency and slowing low-value UI refresh loops, without changing the batch/video/topic architecture.

**Architecture:** Keep the existing vertical-slice app and worker topology. Make the smallest possible changes in the highest-volume read paths first: worker loops and batch/topic UI refreshes. Do not add new queues, caches, or realtime dependencies in this pass.

**Tech Stack:** FastAPI, HTMX, Jinja templates, Python workers, Supabase PostgREST, existing `pytest` suite

**Plan Budget:** `{files: 7, LOC/file: <=100 target, deps: 0}`

---

## File Structure

- Modify: `workers/caption_worker.py`
  - Add a longer empty-queue backoff so the caption worker stops hitting Supabase every 10 seconds when there is nothing to process.
- Modify: `workers/video_poller.py`
  - Add an idle backoff path so the poller sleeps longer when no rows are eligible, while leaving active polling unchanged.
- Modify: `templates/batches/detail.html`
  - Increase the batch-detail refresh interval so the page stops re-requesting itself as aggressively.
- Modify: `templates/topics/partials/run_card.html`
  - Increase the topic run refresh interval so operator tabs generate fewer repeat reads.
- Modify: `tests/test_caption_worker_alignment.py`
  - Add the empty-queue backoff regression for the caption worker.
- Modify: `tests/test_video_poller_extension_chain.py`
  - Add the idle-reconcile regression for the video poller.
- Modify: `tests/test_batches_status_progress.py`
  - Add the template cadence regression for the batch detail and topic run refresh loops.

## Assumptions

- The app stays on the current Supabase-backed monolith; no new queue, cache, or realtime dependency is introduced in this pass.
- The current production fix for batch-detail idle polling is kept as the baseline and not reworked.
- The main egress win will come from reducing repeated no-op reads, not from changing the provider APIs.
- The batch detail handler logic stays unchanged in this pass; only the template refresh cadence is reduced.

### Task 1: Make Worker Loops Back Off When There Is No Work

**Files:**
- Modify: `workers/caption_worker.py`
- Modify: `workers/video_poller.py`
- Test: `tests/test_caption_worker_alignment.py`
- Test: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Write the failing tests for empty-queue and idle-loop behavior**

```python
def test_caption_worker_uses_longer_backoff_when_queue_is_empty():
    from workers import caption_worker

    assert caption_worker._caption_worker_sleep_seconds(processed_count=0) == 45
```

```python
def test_video_poller_uses_longer_backoff_when_no_rows_are_ready():
    from workers import video_poller

    assert video_poller._video_poller_sleep_seconds(active_post_count=0) == 30
```

- [ ] **Step 2: Run the tests to verify they fail against the current code**

Run:
```bash
pytest tests/test_caption_worker_alignment.py -v
pytest tests/test_video_poller_extension_chain.py -v
```

Expected: fail because the new idle-backoff helpers are not implemented yet.

- [ ] **Step 3: Implement the smallest backoff change that matches the tests**

Add one explicit idle backoff constant per worker and a tiny helper that maps the observed work count to the next sleep duration:

```python
CAPTION_IDLE_BACKOFF_SECONDS = int(os.getenv("CAPTION_IDLE_BACKOFF_SECONDS", "45"))
VIDEO_IDLE_BACKOFF_SECONDS = int(os.getenv("VIDEO_IDLE_BACKOFF_SECONDS", "30"))
```

```python
def _caption_worker_sleep_seconds(processed_count: int) -> int:
    return CAPTION_IDLE_BACKOFF_SECONDS if processed_count == 0 else POLL_INTERVAL_SECONDS
```

```python
def _video_poller_sleep_seconds(active_post_count: int) -> int:
    return VIDEO_IDLE_BACKOFF_SECONDS if active_post_count == 0 else POLL_INTERVAL_SECONDS
```

Use those values only in no-work cases and keep the existing active-cycle cadence unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
pytest tests/test_caption_worker_alignment.py -v
pytest tests/test_video_poller_extension_chain.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workers/video_poller.py workers/caption_worker.py tests/test_caption_worker_alignment.py tests/test_video_poller_extension_chain.py
git commit -m "fix: back off idle polling workers"
```

### Task 2: Slow Operator Refresh Loops Without Touching the Handler Gate

**Files:**
- Modify: `templates/batches/detail.html`
- Modify: `templates/topics/partials/run_card.html`
- Test: `tests/test_batches_status_progress.py`

- [ ] **Step 1: Write the failing tests for refresh cadence**

```python
def test_batch_detail_template_uses_slower_refresh_interval():
    from pathlib import Path

    template = Path("templates/batches/detail.html").read_text(encoding="utf-8")
    assert 'hx-trigger="every 5s"' not in template
    assert 'hx-trigger="every 15s"' in template
```

```python
def test_topic_run_card_uses_slower_refresh_interval():
    from pathlib import Path

    template = Path("templates/topics/partials/run_card.html").read_text(encoding="utf-8")
    assert 'hx-trigger="load, every 6s"' not in template
    assert 'hx-trigger="load, every 15s"' in template
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
pytest tests/test_batches_status_progress.py -v
```

Expected: fail because the template cadence still uses the shorter refresh interval.

- [ ] **Step 3: Implement the cadence change**

Change only the refresh intervals:

```html
hx-trigger="every 15s"
```

```html
hx-trigger="load, every 15s"
```

Do not change the active-state gate in the handler in this pass.

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
pytest tests/test_batches_status_progress.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/batches/detail.html templates/topics/partials/run_card.html tests/test_batches_status_progress.py
git commit -m "fix: slow operator refresh polling"
```

## Validation

- Confirm the batch detail page no longer polls as aggressively in idle or near-idle states.
- Confirm the worker loops sleep longer when there is no work instead of hammering Supabase at the default cadence.
- Confirm the next 10-15 minute Supabase egress sample trends lower than the prior baseline, with the remaining calls coming from active worker work rather than idle refresh loops.

## Gaps To Watch

- If egress is still high after this pass, the next place to look is provider-backed worker polling, not the browser UI.
- If the idle backoff proves too aggressive, lower the backoff constant instead of reintroducing always-on reads.
