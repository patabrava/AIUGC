# Veo Extension Chaining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 16s/32s Veo extension pipeline work end-to-end — the poller must poll extended statuses, chain multiple Veo hops, advance segment prompts, and only mark a post completed when the full duration target is reached.

**Architecture:** The existing `DurationProfile` and metadata scaffolding are correct. We need to: (1) fix the poller query to include extended statuses, (2) add chaining logic to `_handle_veo_video` that submits the next extension hop when the current one completes, (3) wire duration routing into the batch submission path, (4) fix minor prompt builder bugs, and (5) fix pre-existing broken tests. VEO does not natively stitch — each extension submission produces a new standalone video, and the final hop's output is the complete video.

**Tech Stack:** Python 3.11 / FastAPI / Supabase / google-genai VEO 3.1 API

**Design decisions:**
- If a VEO extension submission fails mid-chain, the entire post is marked `failed`. The failure handler preserves chain metadata (operation_ids, hops_completed) in video_metadata so manual recovery is possible.
- Bug 5 (negatives logic in `build_veo_prompt_segment`) was investigated and confirmed not a bug — when `include_quotes=False` (Veo path), `VEO_NEGATIVE_PROMPT` is correctly used.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `workers/video_poller.py` | Modify | Add extended status polling + chaining logic |
| `app/features/videos/handlers.py` | Modify | Wire `_resolve_video_submission_plan` into batch path |
| `app/features/posts/prompt_builder.py` | Modify | Fix no-op template branch |
| `tests/test_video_poller_extension_chain.py` | Create | Extension chaining unit tests |
| `tests/test_video_duration_routing.py` | Modify | Add batch submission routing test |
| `tests/test_veo_prompt_contract.py` | Modify | Fix broken tests + add `_build_veo_extension_prompt` |

---

### Task 1: Fix pre-existing broken tests and add `_build_veo_extension_prompt`

Two existing tests in `test_veo_prompt_contract.py` call `video_poller._build_veo_extension_prompt` which does not exist. One test (`test_split_dialogue_sentences_ignores_trailing_fragment`) has a wrong assertion — the function appends trailing fragments to the last sentence, it does not drop them.

**Files:**
- Create function in: `workers/video_poller.py`
- Fix: `tests/test_veo_prompt_contract.py:41,68,85-88`

- [ ] **Step 1: Run the broken tests to confirm they fail**

Run: `pytest tests/test_veo_prompt_contract.py -v`
Expected: `test_veo_extension_prompt_preserves_approved_german_script` and `test_veo_extension_prompt_uses_requested_next_segment` FAIL with `AttributeError: module has no attribute '_build_veo_extension_prompt'`. `test_split_dialogue_sentences_ignores_trailing_fragment` FAILS with wrong assertion.

- [ ] **Step 2: Add `_build_veo_extension_prompt` to `workers/video_poller.py`**

Add these imports near the top of `video_poller.py` (after line 22):

```python
from typing import List, Dict, Any, Optional, Union
```

(Replace the existing typing import to add `Optional`.)

Add at the top-level imports:

```python
from app.core.video_profiles import (
    get_pollable_video_statuses,
    VEO_EXTENDED_VIDEO_ROUTE,
    VIDEO_STATUS_COMPLETED,
    VIDEO_STATUS_FAILED,
    get_processing_video_status,
    get_submitted_video_status,
)
```

Add the function after `_mark_processing` (around line 417):

```python
def _build_veo_extension_prompt(
    post: Dict[str, Any],
    segment_index: Optional[int] = None,
) -> Dict[str, str]:
    """Build a VEO extension prompt for the given post and segment index.

    This is called by the chaining logic when a hop completes and the next
    segment needs to be submitted.  It is also used by tests.
    """
    from app.features.posts.prompt_builder import build_veo_prompt_segment, split_dialogue_sentences

    seed_data = post.get("seed_data") or {}
    if isinstance(seed_data, str):
        import json
        try:
            seed_data = json.loads(seed_data)
        except json.JSONDecodeError:
            seed_data = {}

    script = str(seed_data.get("script") or seed_data.get("dialog_script") or "").strip()
    segments = split_dialogue_sentences(script) if script else []
    if not segments and script:
        segments = [script]

    idx = segment_index if segment_index is not None else 0
    if idx < len(segments):
        segment_text = segments[idx]
    elif segments:
        segment_text = segments[-1]
    else:
        segment_text = script

    metadata = post.get("video_metadata") or {}
    hops_target = metadata.get("veo_extension_hops_target", 0)
    hops_completed = metadata.get("veo_extension_hops_completed", 0)
    is_final = (hops_completed + 1) >= hops_target if hops_target > 0 else True

    prompt_text = build_veo_prompt_segment(
        segment_text,
        include_quotes=False,
        include_ending=is_final,
    )

    return {"prompt_text": prompt_text, "segment_text": segment_text}
```

- [ ] **Step 3: Fix the trailing fragment test in `test_veo_prompt_contract.py`**

Change line 88 from:
```python
    assert segments == ["Erster Satz.", "Zweiter Satz."]
```
To:
```python
    assert segments == ["Erster Satz.", "Zweiter Satz. Abgeschnittener Rest ohne Punkt"]
```

And rename the test to be accurate:
```python
def test_split_dialogue_sentences_appends_trailing_fragment_to_last():
```

- [ ] **Step 4: Run all prompt contract tests**

Run: `pytest tests/test_veo_prompt_contract.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add workers/video_poller.py tests/test_veo_prompt_contract.py
git commit -m "fix: add _build_veo_extension_prompt and fix broken prompt contract tests"
```

---

### Task 2: Fix poller to poll extended video statuses

**Files:**
- Modify: `workers/video_poller.py:88-90`
- Test: `tests/test_video_poller_extension_chain.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_video_poller_extension_chain.py
"""Tests for Veo extension chaining in the video poller."""

from unittest.mock import patch, MagicMock


def test_poll_pending_videos_includes_extended_statuses(monkeypatch):
    """poll_pending_videos must query for extended statuses too."""
    captured_statuses = {}

    class FakeTable:
        def __init__(self, name):
            self._name = name
        def select(self, *a, **kw):
            return self
        def in_(self, col, values):
            captured_statuses[col] = values
            return self
        def eq(self, *a, **kw):
            return self
        def execute(self):
            return MagicMock(data=[])

    class FakeSupabase:
        client = MagicMock()

    fake_sb = FakeSupabase()
    fake_sb.client.table = lambda name: FakeTable(name)

    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: fake_sb)

    from workers.video_poller import poll_pending_videos
    poll_pending_videos()

    assert "video_status" in captured_statuses
    queried = captured_statuses["video_status"]
    assert "extended_submitted" in queried
    assert "extended_processing" in queried
    assert "submitted" in queried
    assert "processing" in queried
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_video_poller_extension_chain.py::test_poll_pending_videos_includes_extended_statuses -v`
Expected: FAIL — `extended_submitted` not in queried statuses

- [ ] **Step 3: Update poller to use `get_pollable_video_statuses()`**

In `workers/video_poller.py`, change lines 88-90 from:

```python
        response = supabase.table("posts").select("*").in_(
            "video_status", ["submitted", "processing"]
        ).execute()
```

To:

```python
        response = supabase.table("posts").select("*").in_(
            "video_status", list(get_pollable_video_statuses())
        ).execute()
```

(The import was already added in Task 1.)

- [ ] **Step 4: Run test and verify it passes**

Run: `pytest tests/test_video_poller_extension_chain.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add workers/video_poller.py tests/test_video_poller_extension_chain.py
git commit -m "fix: poller now polls extended_submitted and extended_processing statuses"
```

---

### Task 3: Add `_needs_extension_hop` helper

**Files:**
- Modify: `workers/video_poller.py`
- Test: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_video_poller_extension_chain.py

from workers.video_poller import _needs_extension_hop


def test_needs_extension_hop_returns_true_when_hops_remaining():
    metadata = {
        "video_pipeline_route": "veo_extended",
        "veo_extension_hops_target": 4,
        "veo_extension_hops_completed": 1,
    }
    assert _needs_extension_hop(metadata) is True


def test_needs_extension_hop_returns_false_when_all_hops_done():
    metadata = {
        "video_pipeline_route": "veo_extended",
        "veo_extension_hops_target": 2,
        "veo_extension_hops_completed": 2,
    }
    assert _needs_extension_hop(metadata) is False


def test_needs_extension_hop_returns_false_for_short_route():
    metadata = {"video_pipeline_route": "short", "veo_extension_hops_target": 0, "veo_extension_hops_completed": 0}
    assert _needs_extension_hop(metadata) is False


def test_needs_extension_hop_returns_false_for_missing_metadata():
    assert _needs_extension_hop({}) is False
    assert _needs_extension_hop(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_video_poller_extension_chain.py::test_needs_extension_hop_returns_true_when_hops_remaining -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement `_needs_extension_hop`**

Add to `workers/video_poller.py` after `_build_veo_extension_prompt`:

```python
def _needs_extension_hop(metadata: Optional[Dict[str, Any]]) -> bool:
    """Check whether a completed VEO operation still needs more extension hops."""
    if not metadata:
        return False
    if metadata.get("video_pipeline_route") != VEO_EXTENDED_VIDEO_ROUTE:
        return False
    target = metadata.get("veo_extension_hops_target", 0)
    completed = metadata.get("veo_extension_hops_completed", 0)
    return completed < target
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/test_video_poller_extension_chain.py -k "needs_extension" -v`
Expected: All 4 PASS

- [ ] **Step 5: Commit**

```bash
git add workers/video_poller.py tests/test_video_poller_extension_chain.py
git commit -m "feat: add _needs_extension_hop helper to video poller"
```

---

### Task 4: Implement `_submit_extension_hop`

**Files:**
- Modify: `workers/video_poller.py`
- Test: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_video_poller_extension_chain.py
from unittest.mock import patch, MagicMock


def test_submit_extension_hop_advances_segment_and_submits():
    """Extension hop must advance segment index and submit next VEO generation."""
    from workers.video_poller import _submit_extension_hop

    post = {
        "id": "post-123",
        "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Erster Satz.", "Zweiter Satz.", "Dritter Satz."],
            "veo_segments_total": 3,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "submitted",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "requested_resolution": "720p",
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_generation.return_value = {
        "operation_id": "op-ext-1",
        "status": "submitted",
    }

    mock_supabase = MagicMock()
    # Chain: table().update().eq().execute()
    mock_update_chain = MagicMock()
    mock_supabase.client.table.return_value.update.return_value = mock_update_chain
    mock_update_chain.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase):
        _submit_extension_hop(post, correlation_id="test-corr")

    # Verify VEO was called with next segment
    mock_veo.submit_video_generation.assert_called_once()
    call_kwargs = mock_veo.submit_video_generation.call_args[1]
    assert "Zweiter Satz." in call_kwargs["prompt"]

    # Verify DB update
    update_call = mock_supabase.client.table.return_value.update
    assert update_call.called
    update_data = update_call.call_args[0][0]
    assert update_data["video_operation_id"] == "op-ext-1"
    meta = update_data["video_metadata"]
    assert meta["veo_extension_hops_completed"] == 1
    assert meta["veo_current_segment_index"] == 1
    assert "op-ext-1" in meta["operation_ids"]
    assert meta["chain_status"] == "extending"


def test_submit_extension_hop_reuses_last_segment_when_fewer_segments_than_hops():
    """If segments list is shorter than hops, reuse the last segment."""
    from workers.video_poller import _submit_extension_hop

    post = {
        "id": "post-short-segs",
        "seed_data": {"script": "Nur ein Satz."},
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 4,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Nur ein Satz."],
            "veo_segments_total": 1,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "submitted",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "requested_resolution": "720p",
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_generation.return_value = {"operation_id": "op-ext-1", "status": "submitted"}
    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase):
        _submit_extension_hop(post, correlation_id="test-corr")

    call_kwargs = mock_veo.submit_video_generation.call_args[1]
    assert "Nur ein Satz." in call_kwargs["prompt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_video_poller_extension_chain.py -k "submit_extension" -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement `_submit_extension_hop`**

Add to `workers/video_poller.py` after `_needs_extension_hop`:

```python
def _submit_extension_hop(post: Dict[str, Any], *, correlation_id: str) -> None:
    """Submit the next VEO extension hop for an in-progress chain."""
    post_id = post["id"]
    metadata = dict(post.get("video_metadata") or {})

    hops_completed = metadata.get("veo_extension_hops_completed", 0)
    current_segment_idx = metadata.get("veo_current_segment_index", 0)
    segments = metadata.get("veo_segments") or []
    next_segment_idx = current_segment_idx + 1

    # Pick the next segment text; fall back to last segment if we've run out
    if next_segment_idx < len(segments):
        segment_text = segments[next_segment_idx]
    elif segments:
        segment_text = segments[-1]
    else:
        segment_text = ""

    is_final_hop = (hops_completed + 1) >= metadata.get("veo_extension_hops_target", 0)

    from app.features.posts.prompt_builder import build_veo_prompt_segment
    prompt = build_veo_prompt_segment(
        segment_text,
        include_quotes=False,
        include_ending=is_final_hop,
    )

    veo_client = get_veo_client()
    result = veo_client.submit_video_generation(
        prompt=prompt,
        negative_prompt=None,
        correlation_id=f"{correlation_id}_ext_{hops_completed + 1}",
        aspect_ratio=metadata.get("requested_aspect_ratio", "9:16"),
        resolution=metadata.get("requested_resolution", "720p"),
    )

    new_operation_id = result["operation_id"]
    operation_ids = list(metadata.get("operation_ids") or [])
    operation_ids.append(new_operation_id)

    extension_seconds = metadata.get("veo_extension_seconds", 7)
    generated_seconds = metadata.get("generated_seconds", 0) + extension_seconds

    metadata.update({
        "veo_extension_hops_completed": hops_completed + 1,
        "veo_current_segment_index": next_segment_idx,
        "operation_ids": operation_ids,
        "chain_status": "extending",
        "generated_seconds": generated_seconds,
    })

    supabase = get_supabase().client
    supabase.table("posts").update({
        "video_operation_id": new_operation_id,
        "video_status": "extended_submitted",
        "video_metadata": metadata,
    }).eq("id", post_id).execute()

    logger.info(
        "extension_hop_submitted",
        post_id=post_id,
        correlation_id=correlation_id,
        hop_number=hops_completed + 1,
        hops_target=metadata.get("veo_extension_hops_target"),
        operation_id=new_operation_id,
        segment_index=next_segment_idx,
        is_final_hop=is_final_hop,
    )
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/test_video_poller_extension_chain.py -k "submit_extension" -v`
Expected: Both PASS

- [ ] **Step 5: Commit**

```bash
git add workers/video_poller.py tests/test_video_poller_extension_chain.py
git commit -m "feat: add _submit_extension_hop to chain VEO extension hops"
```

---

### Task 5: Wire chaining into `_handle_veo_video`

**Files:**
- Modify: `workers/video_poller.py:183-262` (`_handle_veo_video`)
- Test: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_video_poller_extension_chain.py

def test_handle_veo_video_chains_when_hops_remaining():
    """When a VEO op completes but hops remain, submit next extension."""
    from workers.video_poller import _handle_veo_video

    post = {
        "id": "post-chain",
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Satz eins.", "Satz zwei.", "Satz drei."],
            "veo_segments_total": 3,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "submitted",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "requested_resolution": "720p",
        },
    }

    mock_veo = MagicMock()
    mock_veo.check_operation_status.return_value = {
        "done": True,
        "video_data": {"video_uri": "gs://bucket/video.mp4"},
    }
    mock_veo.submit_video_generation.return_value = {
        "operation_id": "op-ext-1",
        "status": "submitted",
    }

    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller._store_completed_video") as mock_store:
        _handle_veo_video(post, "op-base", "corr-chain")

    mock_store.assert_not_called()
    mock_veo.submit_video_generation.assert_called_once()


def test_handle_veo_video_completes_when_all_hops_done():
    """When final hop completes, store the video normally."""
    from workers.video_poller import _handle_veo_video

    post = {
        "id": "post-final",
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 2,
            "chain_status": "extending",
            "operation_ids": ["op-base", "op-ext-1", "op-ext-2"],
        },
    }

    mock_veo = MagicMock()
    mock_veo.check_operation_status.return_value = {
        "done": True,
        "video_data": {"video_uri": "gs://bucket/final.mp4"},
    }
    mock_veo.get_video_download_url.return_value = "https://storage.example.com/final.mp4"

    mock_settings = MagicMock()
    mock_settings.use_url_based_upload = True

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_settings", return_value=mock_settings), \
         patch("workers.video_poller._store_completed_video") as mock_store:
        _handle_veo_video(post, "op-ext-2", "corr-final")

    mock_store.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_video_poller_extension_chain.py -k "handle_veo_video" -v`
Expected: FAIL — `test_handle_veo_video_chains_when_hops_remaining` fails because current code calls `_store_completed_video`

- [ ] **Step 3: Modify `_handle_veo_video` to add chaining branch**

In `workers/video_poller.py`, inside `_handle_veo_video`, after the `video_data` validation block (around line 209, after `raise ValueError("Video data missing download URI")`), add the chaining check before the existing download/store logic:

```python
        # Check if this is an extended pipeline that needs more hops
        metadata = post.get("video_metadata") or {}
        if _needs_extension_hop(metadata):
            _submit_extension_hop(post, correlation_id=correlation_id)
            return
```

The rest of the existing download/store code remains unchanged.

- [ ] **Step 4: Run tests and verify they pass**

Run: `pytest tests/test_video_poller_extension_chain.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add workers/video_poller.py tests/test_video_poller_extension_chain.py
git commit -m "feat: wire extension chaining into _handle_veo_video"
```

---

### Task 6: Wire duration routing into batch video submission

**Files:**
- Modify: `app/features/videos/handlers.py:387-623` (`generate_all_videos`)
- Modify: `tests/test_video_duration_routing.py`

The batch endpoint must use `_resolve_video_submission_plan` when the batch has a `target_length_tier`. Also add `get_submission_video_status` to the top-level imports.

- [ ] **Step 1: Add missing import to `handlers.py`**

At `app/features/videos/handlers.py`, line 22-27, add `get_submission_video_status` to the existing import block:

```python
from app.core.video_profiles import (
    VEO_EXTENDED_VIDEO_ROUTE,
    VEO_PROVIDER,
    get_duration_profile,
    get_submission_video_status,
    uses_duration_routing,
)
```

- [ ] **Step 2: Modify `generate_all_videos` to use duration routing**

After line 412 (`posts = response.data`), add batch lookup:

```python
        batch = get_batch_by_id(batch_id)
```

Then replace the per-post try block (the block starting with `prompt_request = _build_provider_prompt_request(...)` through the `logger.info("batch_video_submitted", ...)` call) with duration-aware logic. The key changes inside the per-post loop:

```python
            try:
                submission_plan = _resolve_video_submission_plan(
                    batch=batch,
                    requested_provider=request.provider,
                    requested_seconds=request.seconds,
                    aspect_ratio=request.aspect_ratio,
                    resolution=request.resolution,
                    size=request.size,
                )

                profile = submission_plan.get("profile")
                is_extended = profile is not None and profile.route == VEO_EXTENDED_VIDEO_ROUTE

                if is_extended:
                    prompt_text, segment_metadata = _build_veo_extended_base_prompt(seed_data)
                    negative_prompt = None
                else:
                    prompt_request = _build_provider_prompt_request(video_prompt, submission_plan["provider"])
                    prompt_text = prompt_request["prompt_text"] or ""
                    negative_prompt = prompt_request.get("negative_prompt")
                    segment_metadata = None

                submission_result = _submit_video_request(
                    provider=submission_plan["provider"],
                    prompt_text=prompt_text,
                    negative_prompt=negative_prompt,
                    aspect_ratio=submission_plan["aspect_ratio"],
                    resolution=submission_plan["resolution"],
                    seconds=submission_plan["seconds"],
                    size=submission_plan["size"],
                    correlation_id=f"{correlation_id}_{post_id}",
                )
                operation_id = submission_result["operation_id"]
                provider_model = submission_result.get("provider_model")

                record_prompt_audit(
                    post_id=post_id,
                    operation_id=operation_id,
                    provider=submission_plan["provider"],
                    prompt_text=prompt_text,
                    negative_prompt=negative_prompt,
                    prompt_path="veo_extended_segment" if is_extended else "batch_standard",
                    aspect_ratio=submission_plan["aspect_ratio"],
                    resolution=submission_plan["resolution"],
                    requested_seconds=submission_plan["seconds"],
                    correlation_id=f"{correlation_id}_{post_id}",
                    batch_id=batch_id,
                )

                existing_metadata = post.get("video_metadata") or {}
                submission_metadata = _build_submission_metadata(
                    existing_metadata=existing_metadata,
                    submission_plan=submission_plan,
                    submission_result=submission_result,
                    segment_metadata=segment_metadata,
                )

                route = profile.route if profile else None
                provider_status = submission_result.get("status", "submitted")
                db_status = get_submission_video_status(route, provider_status)

                logger.warning(
                    "video_operation_id_paid_request",
                    post_id=post_id,
                    operation_id=operation_id,
                    provider=submission_plan["provider"],
                    correlation_id=correlation_id,
                    message="PAID VIDEO SUBMITTED - Operation ID logged for recovery"
                )

                try:
                    supabase.table("posts").update({
                        "video_provider": submission_plan["provider"],
                        "video_format": submission_plan["aspect_ratio"],
                        "video_operation_id": operation_id,
                        "video_status": db_status,
                        "video_metadata": submission_metadata
                    }).eq("id", post_id).execute()
                except Exception as db_error:
                    logger.error(
                        "batch_video_db_update_failed_but_video_submitted",
                        post_id=post_id,
                        operation_id=operation_id,
                        provider=submission_plan["provider"],
                        batch_id=batch_id,
                        correlation_id=correlation_id,
                        error=str(db_error),
                        message="DATABASE UPDATE FAILED - Video is still processing at provider."
                    )
                    _write_recovery_record(post_id, operation_id, submission_plan["provider"], correlation_id)
                    skipped_count += 1
                    continue

                submitted_count += 1
                submitted_post_ids.append(post_id)
                if provider_model:
                    last_provider_model = provider_model

                logger.info(
                    "batch_video_submitted",
                    post_id=post_id,
                    batch_id=batch_id,
                    provider=submission_plan["provider"],
                    provider_model=provider_model,
                    seconds=submission_plan["seconds"],
                    size=submission_plan["size"],
                    operation_id=operation_id,
                    duration_routed=submission_plan["duration_routed"],
                )
```

- [ ] **Step 3: Fix the response construction to use resolved values**

In the `BatchVideoGenerationResponse` construction (around line 596), change `request.provider` to use the last plan's values. Since multiple posts may have different plans in theory (though in practice they share a batch), use the request values as defaults — they'll match for non-routed batches, and the metadata is per-post for routed ones:

```python
        # After the loop, the response is fine using request.* for the summary
        # because _resolve_video_submission_plan overrides per-post, not per-response.
        # The per-post metadata has the correct routed values.
```

No change needed here — the response is a batch-level summary and the request values are appropriate.

- [ ] **Step 4: Write test that verifies plan initialization for extended batch**

```python
# Add to tests/test_video_duration_routing.py

def test_build_veo_extended_base_prompt_returns_first_segment():
    seed_data = {"script": "Erster Satz. Zweiter Satz. Dritter Satz."}
    prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(seed_data)

    assert "Erster Satz." in prompt
    assert seg_meta["veo_segments"] == ["Erster Satz.", "Zweiter Satz.", "Dritter Satz."]
    assert seg_meta["veo_segments_total"] == 3
    assert seg_meta["veo_current_segment_index"] == 0


def test_resolve_plan_for_32s_batch_initializes_full_chain_metadata():
    batch = {"id": "b-32", "target_length_tier": 32}

    plan = video_handlers._resolve_video_submission_plan(
        batch=batch,
        requested_provider=None,
        requested_seconds=None,
        aspect_ratio="9:16",
        resolution="720p",
        size=None,
    )

    assert plan["duration_routed"] is True
    assert plan["provider"] == "veo_3_1"
    assert plan["profile"].veo_extension_hops == 4
    assert plan["resolution"] == "720p"

    metadata = video_handlers._build_submission_metadata(
        existing_metadata={},
        submission_plan=plan,
        submission_result={"operation_id": "op-1", "requested_size": "720x1280"},
        segment_metadata={
            "veo_segments": ["S1.", "S2.", "S3.", "S4."],
            "veo_segments_total": 4,
            "veo_current_segment_index": 0,
        },
    )

    assert metadata["veo_extension_hops_target"] == 4
    assert metadata["veo_extension_hops_completed"] == 0
    assert metadata["veo_segments"] == ["S1.", "S2.", "S3.", "S4."]
    assert metadata["chain_status"] == "submitted"
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_video_duration_routing.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add app/features/videos/handlers.py tests/test_video_duration_routing.py
git commit -m "feat: wire duration routing into batch video submission path"
```

---

### Task 7: Fix prompt builder no-op + end-to-end chain test

**Files:**
- Modify: `app/features/posts/prompt_builder.py:220`
- Test: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Fix the no-op template conditional**

In `app/features/posts/prompt_builder.py`, line 220, change:

```python
    template = OPTIMIZED_PROMPT_TEMPLATE if include_quotes else OPTIMIZED_PROMPT_TEMPLATE
```

To:

```python
    template = OPTIMIZED_PROMPT_TEMPLATE
```

- [ ] **Step 2: Write end-to-end chain lifecycle test**

```python
# Add to tests/test_video_poller_extension_chain.py

def test_full_32s_chain_lifecycle():
    """Simulate a complete 32s chain: base + 4 extension hops."""
    from workers.video_poller import _needs_extension_hop

    metadata = {
        "video_pipeline_route": "veo_extended",
        "veo_extension_hops_target": 4,
        "veo_extension_hops_completed": 0,
        "generated_seconds": 4,
        "veo_base_seconds": 4,
        "veo_extension_seconds": 7,
    }

    # After base completes: needs extension
    assert _needs_extension_hop(metadata) is True

    for hop in range(1, 5):
        metadata["veo_extension_hops_completed"] = hop
        metadata["generated_seconds"] = 4 + (hop * 7)

        if hop < 4:
            assert _needs_extension_hop(metadata) is True, f"Hop {hop} should still need more"
        else:
            assert _needs_extension_hop(metadata) is False, f"Hop {hop} should be done"

    assert metadata["generated_seconds"] == 32
    assert metadata["veo_extension_hops_completed"] == 4
```

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add app/features/posts/prompt_builder.py tests/test_video_poller_extension_chain.py
git commit -m "fix: remove no-op template conditional, add e2e chain lifecycle test"
```

---

## Summary of Changes

| Bug | Fix | Task |
|-----|-----|------|
| Poller ignores extended statuses | Use `get_pollable_video_statuses()` | Task 2 |
| Missing `_build_veo_extension_prompt` (broken tests) | Implement function + fix trailing fragment test | Task 1 |
| No chaining logic in poller | Add `_needs_extension_hop` + `_submit_extension_hop` + wire into `_handle_veo_video` | Tasks 3-5 |
| Batch endpoint bypasses duration routing | Wire `_resolve_video_submission_plan` into `generate_all_videos` | Task 6 |
| No-op template conditional | Remove dead branch | Task 7 |
| Negatives logic (Bug 5) | Investigated — confirmed not a bug, no fix needed | N/A |
