"""Regression coverage for delivered frozen-frame seam detection."""

import shutil
import subprocess

import pytest

from app.features.shot_production.visual_seams import (
    _parse_freeze_intervals,
    evaluate_delivered_visual_seams,
    evaluate_source_terminal_reset,
)


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is required",
)


def _make_moving_video(path, *, freeze_tail_seconds=0.0):
    if freeze_tail_seconds:
        moving_seconds = 1.0 - freeze_tail_seconds
        video_filter = (
            f"trim=duration={moving_seconds:.6f},setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop_duration={freeze_tail_seconds:.6f}"
        )
    else:
        video_filter = "trim=duration=1,setpts=PTS-STARTPTS"
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=180x320:rate=24:duration=1",
            "-vf",
            video_filter,
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-300:]


def _make_terminal_reframe_video(path):
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=180x320:rate=24:duration=0.916667",
            "-f",
            "lavfi",
            "-i",
            "color=white:size=180x320:rate=24:duration=0.083333",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0,fps=24,format=yuv420p[v]",
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-300:]


def test_parse_freeze_intervals_matches_ffmpeg_event_format():
    output = """
[Parsed_freezedetect_0] lavfi.freezedetect.freeze_start: 8.333333
[Parsed_freezedetect_0] lavfi.freezedetect.freeze_duration: 0.208333
[Parsed_freezedetect_0] lavfi.freezedetect.freeze_end: 8.541667
"""
    assert _parse_freeze_intervals(output) == [
        {
            "start_seconds": 8.333333,
            "end_seconds": 8.541667,
            "duration_seconds": 0.208333,
        }
    ]


def test_visual_seam_qa_rejects_cloned_tail_at_cut(tmp_path):
    video_path = tmp_path / "cloned-tail.mp4"
    _make_moving_video(video_path, freeze_tail_seconds=0.22)

    report = evaluate_delivered_visual_seams(
        video_path,
        cut_times_seconds=[1.0],
        reframe_profiles=["full", "punch_in_center"],
        fps=24.0,
    )

    assert report["passed"] is False
    assert report["failed_seam_indexes"] == [0]
    assert report["seams"][0]["failure_reasons"] == [
        "frozen_frames_intersect_visual_boundary"
    ]
    assert report["seams"][0]["freeze_intervals"][0]["duration_seconds"] >= 0.125


def test_visual_seam_qa_accepts_moving_tail_with_intentional_jump_cut(tmp_path):
    video_path = tmp_path / "moving-tail.mp4"
    _make_moving_video(video_path)

    report = evaluate_delivered_visual_seams(
        video_path,
        cut_times_seconds=[0.95],
        reframe_profiles=["full", "punch_in_center"],
        fps=24.0,
    )

    assert report["passed"] is True
    assert report["failed_seam_indexes"] == []
    assert report["seams"][0]["incoming_reframe_profile"] == "punch_in_center"


def test_visual_seam_qa_requires_non_full_incoming_edit_profile(tmp_path):
    video_path = tmp_path / "moving-tail.mp4"
    _make_moving_video(video_path)

    report = evaluate_delivered_visual_seams(
        video_path,
        cut_times_seconds=[0.95],
        reframe_profiles=["full", "full"],
        fps=24.0,
    )

    assert report["passed"] is False
    assert report["seams"][0]["failure_reasons"] == [
        "intentional_jump_cut_reframe_missing"
    ]


def test_source_terminal_reset_qa_locates_first_bad_tail_frame(tmp_path):
    video_path = tmp_path / "terminal-reframe.mp4"
    _make_terminal_reframe_video(video_path)

    report = evaluate_source_terminal_reset(video_path)

    assert report["reset_detected"] is True
    assert 0.90 <= report["safe_video_end_seconds"] <= 0.96
    assert report["reset_scene_score"] >= report["scene_score_threshold"]
    assert report["video_sha256"]


def test_source_terminal_reset_qa_keeps_conservative_fallback_when_not_detected(
    tmp_path,
):
    video_path = tmp_path / "moving.mp4"
    _make_moving_video(video_path)

    report = evaluate_source_terminal_reset(video_path)

    assert report["reset_detected"] is False
    assert report["safe_video_end_seconds"] is None
    assert report["status"] == "not_detected"
