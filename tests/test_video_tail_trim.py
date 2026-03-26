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
