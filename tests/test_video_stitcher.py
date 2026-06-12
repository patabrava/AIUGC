"""Integration tests for the segmented-route video stitcher using real ffmpeg.

These tests generate tiny synthetic clips with ffmpeg's lavfi sources, so they exercise the actual
concat-filter pass end to end (no mocking) and assert the joined output is a valid single mp4 whose
duration is the sum of its parts.
"""

import shutil
import subprocess

import pytest

from app.adapters.video_stitcher import _probe_duration, extract_anchor_frame, stitch_segments

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


def test_stitch_two_segments_softens_cut_duration(tmp_path):
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
    # Raw duration is 5s. Cut softening removes the first segment tail and second segment head.
    assert 4.3 <= duration <= 4.7, duration
    assert meta["stitch_width"] == 360 and meta["stitch_height"] == 640
    assert meta["stitch_head_trim_s"] == [0.0, 0.18]
    assert meta["stitch_tail_trim_s"] == [0.35, 0.0]


def test_stitch_softens_i2v_resets_with_trims_and_reframes(tmp_path):
    clip_a = str(tmp_path / "a.mp4")
    clip_b = str(tmp_path / "b.mp4")
    clip_c = str(tmp_path / "c.mp4")
    _make_clip(clip_a, seconds=3, color="red")
    _make_clip(clip_b, seconds=3, color="blue")
    _make_clip(clip_c, seconds=3, color="green")

    with open(clip_a, "rb") as fh:
        bytes_a = fh.read()
    with open(clip_b, "rb") as fh:
        bytes_b = fh.read()
    with open(clip_c, "rb") as fh:
        bytes_c = fh.read()

    final_bytes, meta = stitch_segments(
        segment_videos=[bytes_a, bytes_b, bytes_c],
        post_id="post_test",
        correlation_id="corr_test",
    )

    assert meta["stitch_cut_softening_applied"] is True
    assert meta["stitch_head_trim_s"] == [0.0, 0.18, 0.18]
    assert meta["stitch_tail_trim_s"] == [0.35, 0.35, 0.0]
    assert meta["stitch_reframe_profile"] == ["full", "punch_in_center", "punch_in_left"]

    out_path = str(tmp_path / "out.mp4")
    with open(out_path, "wb") as fh:
        fh.write(final_bytes)

    duration = _probe_duration(out_path)
    # Raw duration is 9s. Head/tail softening removes 1.06s before concat.
    assert 7.5 <= duration <= 8.4, duration


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
    assert 3.3 <= duration <= 3.7, duration


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


def test_extract_anchor_frame_returns_jpeg(tmp_path):
    clip = str(tmp_path / "anchor.mp4")
    _make_clip(clip, seconds=2, color="red")
    with open(clip, "rb") as fh:
        video_bytes = fh.read()

    frame_bytes, mime = extract_anchor_frame(
        video_bytes=video_bytes, post_id="post_test", correlation_id="corr_test"
    )
    assert mime == "image/jpeg"
    assert len(frame_bytes) > 0
    assert frame_bytes[:2] == b"\xff\xd8"  # JPEG SOI marker


@pytest.mark.parametrize("fraction", [0.1, 0.9])
def test_extract_anchor_frame_honors_fraction(tmp_path, fraction):
    clip = str(tmp_path / "anchor.mp4")
    _make_clip(clip, seconds=2, color="red")
    with open(clip, "rb") as fh:
        video_bytes = fh.read()

    frame_bytes, mime = extract_anchor_frame(
        video_bytes=video_bytes, post_id="post_test", correlation_id="corr_test", at_fraction=fraction
    )
    # Solid-color synthetic clips are byte-identical across fractions, so assert only that the seek
    # runs and returns a valid JPEG (distinctness across fractions is covered in test_segmented_i2v).
    assert mime == "image/jpeg"
    assert len(frame_bytes) > 0
    assert frame_bytes[:2] == b"\xff\xd8"  # JPEG SOI marker


def test_extract_anchor_frame_rejects_empty_input():
    with pytest.raises(ValueError):
        extract_anchor_frame(video_bytes=b"", post_id="p", correlation_id="c")


def test_extract_anchor_frame_rejects_garbage_bytes():
    with pytest.raises(ValueError):
        extract_anchor_frame(video_bytes=b"not a video", post_id="p", correlation_id="c")
