"""Integration tests for the segmented-route video stitcher using real ffmpeg.

These tests generate tiny synthetic clips with ffmpeg's lavfi sources, so they exercise the actual
concat-filter pass end to end (no mocking) and assert the joined output is a valid single mp4 whose
duration is the sum of its parts.
"""

import shutil
import subprocess

import pytest

from app.adapters.video_stitcher import _probe_duration, stitch_segments

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _make_clip(path: str, *, seconds: int, color: str, width: int = 360, height: int = 640) -> None:
    """Render a solid-color clip with a sine tone so it has both a video and an audio stream."""
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={width}x{height}:d={seconds}:r=24",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr[-300:]


def test_stitch_two_segments_sums_duration(tmp_path):
    clip_a = str(tmp_path / "a.mp4")
    clip_b = str(tmp_path / "b.mp4")
    _make_clip(clip_a, seconds=2, color="red")
    _make_clip(clip_b, seconds=3, color="blue")

    with open(clip_a, "rb") as fh:
        bytes_a = fh.read()
    with open(clip_b, "rb") as fh:
        bytes_b = fh.read()

    final_bytes, meta = stitch_segments(
        segment_videos=[bytes_a, bytes_b],
        post_id="post_test",
        correlation_id="corr_test",
    )

    assert meta["stitch_applied"] is True
    assert meta["stitch_segment_count"] == 2

    out_path = str(tmp_path / "out.mp4")
    with open(out_path, "wb") as fh:
        fh.write(final_bytes)

    duration = _probe_duration(out_path)
    # 2s + 3s = 5s; allow small container/encoder rounding.
    assert 4.6 <= duration <= 5.6, duration
    assert meta["stitch_width"] == 360 and meta["stitch_height"] == 640


def test_stitch_normalizes_mismatched_resolution(tmp_path):
    """Segments from independent generations may differ slightly; the stitcher must normalize."""
    clip_a = str(tmp_path / "a.mp4")
    clip_b = str(tmp_path / "b.mp4")
    _make_clip(clip_a, seconds=2, color="green", width=360, height=640)
    _make_clip(clip_b, seconds=2, color="black", width=362, height=640)  # off-by-two width

    with open(clip_a, "rb") as fh:
        bytes_a = fh.read()
    with open(clip_b, "rb") as fh:
        bytes_b = fh.read()

    final_bytes, meta = stitch_segments(
        segment_videos=[bytes_a, bytes_b],
        post_id="post_test",
        correlation_id="corr_test",
    )
    out_path = str(tmp_path / "out.mp4")
    with open(out_path, "wb") as fh:
        fh.write(final_bytes)
    duration = _probe_duration(out_path)
    assert 3.6 <= duration <= 4.6, duration


def test_single_segment_passthrough():
    final_bytes, meta = stitch_segments(
        segment_videos=[b"FAKE_MP4_BYTES"],
        post_id="post_test",
        correlation_id="corr_test",
    )
    assert final_bytes == b"FAKE_MP4_BYTES"
    assert meta["stitch_applied"] is False
    assert meta["stitch_segment_count"] == 1


def test_empty_input_raises():
    with pytest.raises(ValueError):
        stitch_segments(segment_videos=[], post_id="p", correlation_id="c")
