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


def _make_clip(
    path: str,
    *,
    seconds: int,
    color: str,
    width: int = 360,
    height: int = 640,
    frequency: int = 440,
    sample_rate: int = 44100,
) -> None:
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
        f"sine=frequency={frequency}:sample_rate={sample_rate}:duration={seconds}",
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


def test_stitch_two_segments_preserves_audio_at_cut(tmp_path):
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
    # Raw duration is 5s. The stitcher must not trim joins because speech can reach the edge.
    assert 4.9 <= duration <= 5.1, duration
    assert meta["stitch_width"] == 360 and meta["stitch_height"] == 640
    assert meta["stitch_head_trim_s"] == [0.0, 0.0]
    assert meta["stitch_tail_trim_s"] == [0.0, 0.0]


def test_stitch_trims_segments_to_spoken_windows(tmp_path):
    clip_a = str(tmp_path / "a.mp4")
    clip_b = str(tmp_path / "b.mp4")
    _make_clip(clip_a, seconds=4, color="red")
    _make_clip(clip_b, seconds=4, color="blue")

    with open(clip_a, "rb") as fh:
        bytes_a = fh.read()
    with open(clip_b, "rb") as fh:
        bytes_b = fh.read()

    final_bytes, meta = stitch_segments(
        segment_videos=[bytes_a, bytes_b],
        post_id="post_test",
        correlation_id="corr_test",
        trim_windows=[
            {"start_seconds": 0.0, "end_seconds": 1.5, "source": "test"},
            {"start_seconds": 0.0, "end_seconds": 2.25, "source": "test"},
        ],
    )

    out_path = str(tmp_path / "out.mp4")
    with open(out_path, "wb") as fh:
        fh.write(final_bytes)

    duration = _probe_duration(out_path)
    assert 3.6 <= duration <= 3.95, duration
    assert meta["stitch_head_trim_s"] == [0.0, 0.0]
    assert meta["stitch_tail_trim_s"][0] >= 2.4
    assert meta["stitch_tail_trim_s"][1] >= 1.6
    assert meta["stitch_trim_window_source"] == ["test", "test"]


def test_stitch_acoustic_plan_hard_cuts_video_and_crossfades_audio(tmp_path):
    clip_a = str(tmp_path / "a.mp4")
    clip_b = str(tmp_path / "b.mp4")
    _make_clip(clip_a, seconds=2, color="red", frequency=440, sample_rate=44100)
    _make_clip(clip_b, seconds=2, color="blue", frequency=660, sample_rate=48000)

    with open(clip_a, "rb") as fh:
        bytes_a = fh.read()
    with open(clip_b, "rb") as fh:
        bytes_b = fh.read()

    final_bytes, meta = stitch_segments(
        segment_videos=[bytes_a, bytes_b],
        post_id="post_test",
        correlation_id="corr_test",
        acoustic_plan={
            "takes": [
                {
                    "audio_start_seconds": 0.0,
                    "audio_end_seconds": 1.8,
                    "video_start_seconds": 0.0,
                    "video_end_seconds": 1.775,
                    "gain_db": 0.0,
                },
                {
                    "audio_start_seconds": 0.2,
                    "audio_end_seconds": 2.0,
                    "video_start_seconds": 0.225,
                    "video_end_seconds": 2.0,
                    "gain_db": -0.5,
                },
            ],
            "seams": [
                {
                    "overlap_seconds": 0.05,
                    "visual_cut_position_seconds": 0.025,
                }
            ],
        },
    )

    out_path = str(tmp_path / "acoustic.mp4")
    with open(out_path, "wb") as fh:
        fh.write(final_bytes)

    assert _probe_duration(out_path) == pytest.approx(3.55, abs=0.06)
    assert meta["stitch_cut_softening_applied"] is True
    assert meta["stitch_audio_overlap_s"] == [0.05]
    assert meta["stitch_visual_cut_position_s"] == [0.025]
    assert meta["stitch_gain_db"] == [0.0, -0.5]
    assert abs(meta["stitch_audio_video_duration_delta_s"]) <= 1 / 24


def test_stitch_acoustic_plan_rejects_out_of_contract_overlap(tmp_path):
    clip_a = str(tmp_path / "a.mp4")
    clip_b = str(tmp_path / "b.mp4")
    _make_clip(clip_a, seconds=2, color="red")
    _make_clip(clip_b, seconds=2, color="blue")

    with open(clip_a, "rb") as fh:
        bytes_a = fh.read()
    with open(clip_b, "rb") as fh:
        bytes_b = fh.read()

    with pytest.raises(ValueError, match="overlap"):
        stitch_segments(
            segment_videos=[bytes_a, bytes_b],
            post_id="post_test",
            correlation_id="corr_test",
            acoustic_plan={
                "takes": [
                    {"audio_start_seconds": 0.0, "audio_end_seconds": 1.8,
                     "video_start_seconds": 0.0, "video_end_seconds": 1.75, "gain_db": 0.0},
                    {"audio_start_seconds": 0.2, "audio_end_seconds": 2.0,
                     "video_start_seconds": 0.25, "video_end_seconds": 2.0, "gain_db": 0.0},
                ],
                "seams": [{"overlap_seconds": 0.1, "visual_cut_position_seconds": 0.05}],
            },
        )


def test_stitch_accepts_dataclass_serialized_tuple_plan(tmp_path):
    clip_a = str(tmp_path / "a.mp4")
    clip_b = str(tmp_path / "b.mp4")
    _make_clip(clip_a, seconds=2, color="red")
    _make_clip(clip_b, seconds=2, color="blue")

    with open(clip_a, "rb") as fh:
        bytes_a = fh.read()
    with open(clip_b, "rb") as fh:
        bytes_b = fh.read()

    final_bytes, meta = stitch_segments(
        segment_videos=[bytes_a, bytes_b],
        post_id="post_test",
        correlation_id="corr_test",
        acoustic_plan={
            "takes": (
                {"audio_start_seconds": 0.0, "audio_end_seconds": 1.8,
                 "video_start_seconds": 0.0, "video_end_seconds": 1.775, "gain_db": 0.0},
                {"audio_start_seconds": 0.2, "audio_end_seconds": 2.0,
                 "video_start_seconds": 0.225, "video_end_seconds": 2.0, "gain_db": 0.0},
            ),
            "seams": ({"overlap_seconds": 0.05, "visual_cut_position_seconds": 0.025},),
        },
    )

    assert final_bytes
    assert meta["stitch_audio_overlap_s"] == [0.05]


def test_stitch_acoustic_plan_caps_accumulated_frame_rounding(tmp_path):
    paths = []
    colors = ("red", "blue", "green", "black")
    durations = (4, 6, 6, 4)
    for index, (color, seconds) in enumerate(zip(colors, durations)):
        path = str(tmp_path / f"take-{index}.mp4")
        _make_clip(path, seconds=seconds, color=color, width=90, height=160)
        paths.append(path)
    segment_videos = []
    for path in paths:
        with open(path, "rb") as fh:
            segment_videos.append(fh.read())

    final_bytes, meta = stitch_segments(
        segment_videos=segment_videos,
        post_id="post_test",
        correlation_id="corr_test",
        acoustic_plan={
            "takes": [
                {"audio_start_seconds": 0.0, "audio_end_seconds": 3.62,
                 "video_start_seconds": 0.0, "video_end_seconds": 3.6, "gain_db": -2.0},
                {"audio_start_seconds": 0.34, "audio_end_seconds": 4.16,
                 "video_start_seconds": 0.36, "video_end_seconds": 4.14, "gain_db": 1.422},
                {"audio_start_seconds": 0.5, "audio_end_seconds": 4.56,
                 "video_start_seconds": 0.52, "video_end_seconds": 4.525, "gain_db": -1.422},
                {"audio_start_seconds": 0.34, "audio_end_seconds": 3.49,
                 "video_start_seconds": 0.375, "video_end_seconds": 3.49, "gain_db": 1.429},
            ],
            "seams": [
                {"overlap_seconds": 0.04, "visual_cut_position_seconds": 0.02},
                {"overlap_seconds": 0.04, "visual_cut_position_seconds": 0.02},
                {"overlap_seconds": 0.07, "visual_cut_position_seconds": 0.035},
            ],
        },
    )

    assert final_bytes
    assert abs(meta["stitch_audio_video_duration_delta_s"]) <= 1 / 24


def test_stitch_acoustic_plan_allows_only_one_frame_of_exact_16_rounding(tmp_path):
    paths = []
    for index, color in enumerate(("red", "blue")):
        path = str(tmp_path / f"exact-16-take-{index}.mp4")
        _make_clip(path, seconds=8, color=color, width=90, height=160)
        paths.append(path)
    segment_videos = []
    for path in paths:
        with open(path, "rb") as fh:
            segment_videos.append(fh.read())

    final_bytes, meta = stitch_segments(
        segment_videos=segment_videos,
        post_id="post_exact_16",
        correlation_id="corr_exact_16",
        target_duration_seconds=16.0,
        acoustic_plan={
            "takes": [
                {
                    "audio_start_seconds": 0.0,
                    "audio_end_seconds": 8.0,
                    "video_start_seconds": 0.0,
                    "video_end_seconds": 7.98,
                    "gain_db": 0.0,
                },
                {
                    "audio_start_seconds": 0.0,
                    "audio_end_seconds": 8.0,
                    "video_start_seconds": 0.02,
                    "video_end_seconds": 8.0,
                    "gain_db": 0.0,
                },
            ],
            "seams": [
                {
                    "overlap_seconds": 0.04,
                    "visual_cut_position_seconds": 0.02,
                }
            ],
            "target_duration_seconds": 16.0,
            "delivery_padding_seconds": 0.04,
        },
    )

    output_path = str(tmp_path / "exact-16.mp4")
    with open(output_path, "wb") as fh:
        fh.write(final_bytes)

    assert _probe_duration(output_path) == pytest.approx(16.0, abs=1 / 24)
    assert meta["stitch_delivery_target_s"] == 16.0
    assert meta["stitch_delivery_padding_s"] == pytest.approx(0.04, abs=0.01)
    assert meta["stitch_delivery_padding_s"] <= 1 / 24
    assert abs(meta["stitch_audio_video_duration_delta_s"]) <= 1 / 24


def test_stitch_uses_bounded_av_retime_for_live_exact_16_shortfall(tmp_path):
    segment_videos = []
    for index, color in enumerate(("red", "blue")):
        path = str(tmp_path / f"retime-take-{index}.mp4")
        _make_clip(path, seconds=8, color=color, width=90, height=160)
        with open(path, "rb") as fh:
            segment_videos.append(fh.read())

    content_duration = 15.22
    retime_ratio = 16.0 / content_duration
    final_bytes, meta = stitch_segments(
        segment_videos=segment_videos,
        post_id="post_retime_16",
        correlation_id="corr_retime_16",
        target_duration_seconds=16.0,
        acoustic_plan={
            "takes": [
                {
                    "audio_start_seconds": 0.0,
                    "audio_end_seconds": 7.56,
                    "video_start_seconds": 0.0,
                    "video_end_seconds": 7.54,
                    "gain_db": 0.0,
                },
                {
                    "audio_start_seconds": 0.30,
                    "audio_end_seconds": 8.0,
                    "video_start_seconds": 0.32,
                    "video_end_seconds": 8.0,
                    "gain_db": 0.0,
                },
            ],
            "seams": [
                {"overlap_seconds": 0.04, "visual_cut_position_seconds": 0.02}
            ],
            "target_duration_seconds": 16.0,
            "content_duration_seconds": content_duration,
            "delivery_padding_seconds": 0.0,
            "delivery_retime_ratio": retime_ratio,
        },
    )

    output_path = str(tmp_path / "retimed-exact-16.mp4")
    with open(output_path, "wb") as fh:
        fh.write(final_bytes)

    assert _probe_duration(output_path) == pytest.approx(16.0, abs=1 / 24)
    assert meta["stitch_delivery_mode"] == "bounded_av_retime"
    assert meta["stitch_delivery_retime_ratio"] == pytest.approx(retime_ratio)
    assert meta["stitch_delivery_audio_tempo"] == pytest.approx(1.0 / retime_ratio)
    assert meta["stitch_delivery_native_shortfall_s"] == pytest.approx(0.78)
    assert meta["stitch_delivery_padding_s"] == 0.0
    assert meta["stitch_end_pan_tail_exclusion_s"] == 0.5
    assert meta["stitch_end_pan_retime_ratio"] == pytest.approx(16.0 / 15.5)
    assert meta["stitch_end_pan_protection_applied"] is True
    assert abs(meta["stitch_audio_video_duration_delta_s"]) <= 1 / 24


def test_stitch_rejects_exact_16_delivery_that_needs_a_frozen_multi_second_outro(tmp_path):
    segment_videos = []
    for index, color in enumerate(("red", "blue")):
        path = str(tmp_path / f"short-take-{index}.mp4")
        _make_clip(path, seconds=8, color=color, width=90, height=160)
        with open(path, "rb") as fh:
            segment_videos.append(fh.read())

    with pytest.raises(ValueError, match="more than one frame of synthetic padding"):
        stitch_segments(
            segment_videos=segment_videos,
            post_id="post_short_16",
            correlation_id="corr_short_16",
            target_duration_seconds=16.0,
            acoustic_plan={
                "takes": [
                    {
                        "audio_start_seconds": 0.0,
                        "audio_end_seconds": 6.5,
                        "video_start_seconds": 0.0,
                        "video_end_seconds": 6.48,
                        "gain_db": 0.0,
                    },
                    {
                        "audio_start_seconds": 0.2,
                        "audio_end_seconds": 6.54,
                        "video_start_seconds": 0.22,
                        "video_end_seconds": 6.54,
                        "gain_db": 0.0,
                    },
                ],
                "seams": [
                    {"overlap_seconds": 0.04, "visual_cut_position_seconds": 0.02}
                ],
                "target_duration_seconds": 16.0,
                "delivery_padding_seconds": 3.2,
            },
        )


def test_stitch_trims_subframe_overshoot_to_exact_16_seconds(tmp_path):
    segment_videos = []
    for index, color in enumerate(("red", "blue")):
        path = str(tmp_path / f"long-take-{index}.mp4")
        _make_clip(path, seconds=9, color=color, width=90, height=160)
        with open(path, "rb") as fh:
            segment_videos.append(fh.read())

    final_bytes, meta = stitch_segments(
        segment_videos=segment_videos,
        post_id="post_trim_16",
        correlation_id="corr_trim_16",
        target_duration_seconds=16.0,
        acoustic_plan={
            "takes": [
                {
                    "audio_start_seconds": 0.0,
                    "audio_end_seconds": 8.02,
                    "video_start_seconds": 0.0,
                    "video_end_seconds": 8.0,
                    "gain_db": 0.0,
                },
                {
                    "audio_start_seconds": 0.0,
                    "audio_end_seconds": 8.04,
                    "video_start_seconds": 0.02,
                    "video_end_seconds": 8.04,
                    "gain_db": 0.0,
                },
            ],
            "seams": [
                {"overlap_seconds": 0.04, "visual_cut_position_seconds": 0.02}
            ],
            "target_duration_seconds": 16.0,
            "delivery_padding_seconds": 0.0,
        },
    )

    output_path = str(tmp_path / "trimmed-exact-16.mp4")
    with open(output_path, "wb") as fh:
        fh.write(final_bytes)

    assert _probe_duration(output_path) == pytest.approx(16.0, abs=1 / 24)
    assert meta["stitch_content_duration_s"] == pytest.approx(16.02)
    assert meta["stitch_delivery_padding_s"] == 0.0
    assert abs(meta["stitch_audio_video_duration_delta_s"]) <= 1 / 24


def test_stitch_preserves_full_framing_for_character_consistency_segments(tmp_path):
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

    assert meta["stitch_cut_softening_applied"] is False
    assert meta["stitch_head_trim_s"] == [0.0, 0.0, 0.0]
    assert meta["stitch_tail_trim_s"] == [0.0, 0.0, 0.0]
    assert meta["stitch_reframe_profile"] == ["full", "full", "full"]

    out_path = str(tmp_path / "out.mp4")
    with open(out_path, "wb") as fh:
        fh.write(final_bytes)

    duration = _probe_duration(out_path)
    # Raw duration is 9s. Framing must stay stable and audio remains untrimmed.
    assert 8.8 <= duration <= 9.2, duration


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
    assert 3.9 <= duration <= 4.1, duration


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
