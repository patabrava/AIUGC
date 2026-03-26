# Video Tail Trim (200ms) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trim the last 200ms from every generated video before it gets uploaded to R2 storage.

**Architecture:** Add a `_trim_tail()` function in `video_poller.py` that uses ffmpeg with stream copy (no re-encode) to cut the tail. Call it inside `_store_completed_video()` for all videos — both byte-based and URL-based sources — before upload. Add a `TRIM_TAIL_MS` constant in `video_profiles.py` for configurability.

**Tech Stack:** Python 3.11, ffmpeg (already available on workers), pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `app/core/video_profiles.py` | Add `TRIM_TAIL_MS` constant |
| Modify | `workers/video_poller.py` | Add `_probe_video_duration()`, `_trim_tail()`, call trim in `_store_completed_video()` |
| Create | `tests/test_video_tail_trim.py` | Unit tests for trim logic |

---

### Task 1: Add TRIM_TAIL_MS constant

**Files:**
- Modify: `app/core/video_profiles.py:16` (after existing status constants)

- [ ] **Step 1: Add the constant**

In `app/core/video_profiles.py`, after line 15 (`VIDEO_STATUS_FAILED = "failed"`), add:

```python
TRIM_TAIL_MS = 200
```

- [ ] **Step 2: Commit**

```bash
git add app/core/video_profiles.py
git commit -m "feat: add TRIM_TAIL_MS constant (200ms)"
```

---

### Task 2: Write failing tests for tail trim

**Files:**
- Create: `tests/test_video_tail_trim.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_video_tail_trim.py`:

```python
"""Tests for video tail trim functionality."""

import subprocess
import tempfile
import os
import pytest


def _generate_test_video(duration_seconds: float, path: str) -> None:
    """Generate a silent test video with exact duration using ffmpeg."""
    command = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=320x240:d={duration_seconds}",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
        "-t", str(duration_seconds),
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest",
        path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Test video generation failed: {result.stderr}"


def _get_duration_ms(path: str) -> float:
    """Get video duration in milliseconds via ffprobe."""
    command = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    return float(result.stdout.strip()) * 1000


class TestTrimTail:
    """Tests for _trim_tail function in video_poller."""

    def test_trim_removes_200ms_from_8s_video(self):
        """An 8-second video should become ~7.8 seconds after trim."""
        from workers.video_poller import _trim_tail

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "input.mp4")
            _generate_test_video(8.0, src)

            with open(src, "rb") as f:
                original_bytes = f.read()

            trimmed_bytes, metadata = _trim_tail(
                video_bytes=original_bytes,
                trim_ms=200,
                post_id="test-001",
                correlation_id="corr-001",
            )

            out = os.path.join(td, "output.mp4")
            with open(out, "wb") as f:
                f.write(trimmed_bytes)

            duration_ms = _get_duration_ms(out)
            # Should be ~7800ms, allow 50ms tolerance for codec framing
            assert 7700 < duration_ms < 7850, f"Expected ~7800ms, got {duration_ms}ms"

    def test_trim_returns_metadata(self):
        """Trim should return metadata dict with trim details."""
        from workers.video_poller import _trim_tail

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "input.mp4")
            _generate_test_video(4.0, src)

            with open(src, "rb") as f:
                original_bytes = f.read()

            _, metadata = _trim_tail(
                video_bytes=original_bytes,
                trim_ms=200,
                post_id="test-002",
                correlation_id="corr-002",
            )

            assert metadata["trim_tail_ms"] == 200
            assert "trim_original_duration_ms" in metadata
            assert "trim_final_duration_ms" in metadata

    def test_trim_skipped_when_video_too_short(self):
        """If the video is shorter than trim amount, return original bytes unchanged."""
        from workers.video_poller import _trim_tail

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "input.mp4")
            _generate_test_video(0.15, src)  # 150ms — shorter than 200ms trim

            with open(src, "rb") as f:
                original_bytes = f.read()

            result_bytes, metadata = _trim_tail(
                video_bytes=original_bytes,
                trim_ms=200,
                post_id="test-003",
                correlation_id="corr-003",
            )

            assert result_bytes == original_bytes
            assert metadata.get("trim_tail_skipped") is True

    def test_trim_zero_ms_returns_original(self):
        """Trim of 0ms should be a no-op."""
        from workers.video_poller import _trim_tail

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "input.mp4")
            _generate_test_video(4.0, src)

            with open(src, "rb") as f:
                original_bytes = f.read()

            result_bytes, metadata = _trim_tail(
                video_bytes=original_bytes,
                trim_ms=0,
                post_id="test-004",
                correlation_id="corr-004",
            )

            assert result_bytes == original_bytes
            assert metadata == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_video_tail_trim.py -v`
Expected: FAIL — `_trim_tail` does not exist yet.

- [ ] **Step 3: Commit**

```bash
git add tests/test_video_tail_trim.py
git commit -m "test: add failing tests for video tail trim"
```

---

### Task 3: Implement _trim_tail and _probe_video_duration

**Files:**
- Modify: `workers/video_poller.py`

- [ ] **Step 1: Add _probe_video_duration helper**

In `workers/video_poller.py`, after the `_probe_video_dimensions` function (after line 135), add:

```python
def _probe_video_duration(video_path: str) -> float:
    """Return video duration in seconds as a float."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError(f"ffprobe duration failed: {result.stderr[-200:]}")
    return float(result.stdout.strip())
```

- [ ] **Step 2: Add _trim_tail function**

After `_probe_video_duration`, add:

```python
def _trim_tail(
    *,
    video_bytes: bytes,
    trim_ms: int,
    post_id: str,
    correlation_id: str,
) -> tuple[bytes, Dict[str, Any]]:
    """Trim the last `trim_ms` milliseconds from a video using ffmpeg stream copy."""
    if trim_ms <= 0:
        return video_bytes, {}

    with tempfile.TemporaryDirectory(prefix="video_trim_") as temp_dir:
        input_path = os.path.join(temp_dir, "input.mp4")
        output_path = os.path.join(temp_dir, "output.mp4")
        with open(input_path, "wb") as file_obj:
            file_obj.write(video_bytes)

        original_duration = _probe_video_duration(input_path)
        trim_seconds = trim_ms / 1000.0

        if original_duration <= trim_seconds:
            logger.warning(
                "trim_tail_skipped_too_short",
                post_id=post_id,
                correlation_id=correlation_id,
                original_duration=original_duration,
                trim_ms=trim_ms,
            )
            return video_bytes, {"trim_tail_skipped": True}

        target_duration = original_duration - trim_seconds

        command = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-t",
            f"{target_duration:.3f}",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise ValueError(f"ffmpeg trim failed: {result.stderr[-300:]}")

        with open(output_path, "rb") as file_obj:
            trimmed_bytes = file_obj.read()

    final_duration = None
    with tempfile.TemporaryDirectory(prefix="video_trim_verify_") as temp_dir:
        verify_path = os.path.join(temp_dir, "verify.mp4")
        with open(verify_path, "wb") as file_obj:
            file_obj.write(trimmed_bytes)
        final_duration = _probe_video_duration(verify_path)

    logger.info(
        "trim_tail_applied",
        post_id=post_id,
        correlation_id=correlation_id,
        original_duration_ms=round(original_duration * 1000),
        final_duration_ms=round(final_duration * 1000),
        trim_ms=trim_ms,
    )

    return trimmed_bytes, {
        "trim_tail_ms": trim_ms,
        "trim_original_duration_ms": round(original_duration * 1000),
        "trim_final_duration_ms": round(final_duration * 1000),
    }
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_video_tail_trim.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add workers/video_poller.py
git commit -m "feat: implement _trim_tail with ffmpeg stream copy"
```

---

### Task 4: Integrate trim into _store_completed_video

**Files:**
- Modify: `workers/video_poller.py:578-597` (`_store_completed_video` function)

- [ ] **Step 1: Add TRIM_TAIL_MS import**

In the imports section of `workers/video_poller.py` (around line 26), add `TRIM_TAIL_MS` to the existing `video_profiles` import:

```python
from app.core.video_profiles import (
    get_pollable_video_statuses,
    VEO_EXTENDED_VIDEO_ROUTE,
    VIDEO_STATUS_CAPTION_COMPLETED,
    VIDEO_STATUS_CAPTION_PENDING,
    TRIM_TAIL_MS,
```

(Add `TRIM_TAIL_MS,` to the existing import block — keep whatever other imports are already there.)

- [ ] **Step 2: Add trim call in _store_completed_video**

In `_store_completed_video`, the current code (lines 587-597) is:

```python
    storage_client = get_storage_client()
    processed_source = video_source
    postprocess_metadata: Dict[str, Any] = {}

    if isinstance(video_source, bytes):
        processed_source, postprocess_metadata = _maybe_postprocess_video_bytes(
            post_id=post_id,
            video_bytes=video_source,
            existing_metadata=existing_metadata,
            correlation_id=correlation_id,
        )
```

Replace with:

```python
    storage_client = get_storage_client()
    processed_source = video_source
    postprocess_metadata: Dict[str, Any] = {}

    if isinstance(video_source, bytes):
        processed_source, postprocess_metadata = _maybe_postprocess_video_bytes(
            post_id=post_id,
            video_bytes=video_source,
            existing_metadata=existing_metadata,
            correlation_id=correlation_id,
        )

    if isinstance(processed_source, bytes) and TRIM_TAIL_MS > 0:
        processed_source, trim_metadata = _trim_tail(
            video_bytes=processed_source,
            trim_ms=TRIM_TAIL_MS,
            post_id=post_id,
            correlation_id=correlation_id,
        )
        postprocess_metadata.update(trim_metadata)
```

This ensures trim runs **after** crop/scale (so it operates on the final video) and applies to all byte-based videos regardless of whether crop was needed.

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass, including the new trim tests.

- [ ] **Step 4: Commit**

```bash
git add workers/video_poller.py app/core/video_profiles.py
git commit -m "feat: integrate 200ms tail trim into video upload pipeline"
```

---

## Design Notes

- **Stream copy (`-c copy`)** avoids re-encoding, making trim near-instant (~100ms) regardless of video length. The cut point may not be frame-exact (depends on keyframe placement), but for 200ms this is acceptable.
- **Trim runs after crop/scale** so if a video needs both, the crop re-encodes first (producing clean keyframes), then trim does a fast stream copy.
- **URL-based sources skip trim** — currently only Sora returns URLs that get passed through `upload_video_from_url`. If Sora videos also need trimming, a separate task would add a download-then-trim path. For now this covers all Veo videos (which arrive as bytes).
- **Safety**: Videos shorter than 200ms are returned unchanged. `TRIM_TAIL_MS = 0` disables the feature entirely.
