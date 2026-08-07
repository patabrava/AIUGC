"""Deterministic QA for frozen frames at delivered multi-take boundaries."""

from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, Sequence


DELIVERY_VISUAL_SEAM_QA_VERSION = "delivery-visual-seam-v1"
SOURCE_TERMINAL_RESET_QA_VERSION = "source-terminal-reset-v2"
FREEZE_NOISE_DB = -50
MINIMUM_BOUNDARY_FREEZE_FRAMES = 3
PRE_CUT_INSPECTION_SECONDS = 0.350
TERMINAL_RESET_SCENE_SCORE = 0.080
TERMINAL_RESET_CUMULATIVE_SCENE_SCORE = 0.100
TERMINAL_RESET_CUMULATIVE_FRAMES = 2
TERMINAL_RESET_MINIMUM_FRAME_SCORE = 0.030

_FREEZE_EVENT = re.compile(
    r"lavfi\.freezedetect\.freeze_(start|duration|end):\s*"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))"
)
_SCENE_FRAME = re.compile(
    r"\bframe:\s*\d+.*?\bpts_time:\s*"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))"
)
_SCENE_SCORE = re.compile(
    r"lavfi\.scene_score="
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))"
)


def _probe_duration(video_path: Path) -> float:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        duration = float(probe.stdout.strip())
    except (TypeError, ValueError):
        duration = math.nan
    if probe.returncode != 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("FFprobe source terminal-reset duration failed")
    return duration


def _parse_scene_scores(output: str) -> list[Dict[str, float]]:
    records: list[Dict[str, float]] = []
    pending_timestamp: float | None = None
    for line in str(output or "").splitlines():
        frame_match = _SCENE_FRAME.search(line)
        if frame_match:
            pending_timestamp = float(frame_match.group(1))
            continue
        score_match = _SCENE_SCORE.search(line)
        if score_match and pending_timestamp is not None:
            records.append(
                {
                    "seconds": round(pending_timestamp, 6),
                    "scene_score": round(float(score_match.group(1)), 6),
                }
            )
            pending_timestamp = None
    return records


def _first_terminal_reset(
    frame_scores: Sequence[Dict[str, float]],
) -> tuple[Dict[str, float] | None, str | None, float | None]:
    candidates: list[tuple[Dict[str, float], str, float]] = []
    for frame in frame_scores:
        if frame["scene_score"] >= TERMINAL_RESET_SCENE_SCORE:
            candidates.append(
                (frame, "single_frame", float(frame["scene_score"]))
            )
    for start in range(
        0,
        len(frame_scores) - TERMINAL_RESET_CUMULATIVE_FRAMES + 1,
    ):
        window = frame_scores[
            start : start + TERMINAL_RESET_CUMULATIVE_FRAMES
        ]
        scores = [float(frame["scene_score"]) for frame in window]
        cumulative_score = sum(scores)
        if (
            cumulative_score >= TERMINAL_RESET_CUMULATIVE_SCENE_SCORE
            and max(scores) >= TERMINAL_RESET_MINIMUM_FRAME_SCORE
        ):
            candidates.append((window[0], "multi_frame", cumulative_score))
    if not candidates:
        return None, None, None
    return min(candidates, key=lambda candidate: candidate[0]["seconds"])


def evaluate_source_terminal_reset(video_path: Path) -> Dict[str, Any]:
    """Locate abrupt or distributed whole-frame Veo motion in the final 350 ms."""
    video_path = Path(video_path)
    if not video_path.is_file() or video_path.stat().st_size <= 0:
        raise ValueError("Source terminal-reset QA requires a non-empty video")
    duration = _probe_duration(video_path)
    inspection_start = max(0.0, duration - PRE_CUT_INSPECTION_SECONDS)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "info",
        "-i",
        str(video_path),
        "-vf",
        (
            f"select='gte(t,{inspection_start:.9f})*gte(scene,0)',"
            "metadata=print"
        ),
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise ValueError(
            f"FFmpeg source terminal-reset QA failed: {result.stderr[-300:]}"
        )
    frame_scores = _parse_scene_scores(result.stderr)
    first_reset, reset_detection_mode, reset_cumulative_score = (
        _first_terminal_reset(frame_scores)
    )
    safe_video_end = (
        float(first_reset["seconds"])
        if first_reset is not None
        else None
    )
    input_contract = {
        "version": SOURCE_TERMINAL_RESET_QA_VERSION,
        "video_sha256": sha256(video_path.read_bytes()).hexdigest(),
        "duration_seconds": round(duration, 6),
        "inspection_start_seconds": round(inspection_start, 6),
        "scene_score_threshold": TERMINAL_RESET_SCENE_SCORE,
        "cumulative_scene_score_threshold": (
            TERMINAL_RESET_CUMULATIVE_SCENE_SCORE
        ),
        "cumulative_frame_count": TERMINAL_RESET_CUMULATIVE_FRAMES,
        "minimum_frame_score": TERMINAL_RESET_MINIMUM_FRAME_SCORE,
    }
    return {
        "version": SOURCE_TERMINAL_RESET_QA_VERSION,
        "status": "reset_detected" if first_reset is not None else "not_detected",
        "reset_detected": first_reset is not None,
        "safe_video_end_seconds": (
            round(safe_video_end, 6)
            if safe_video_end is not None
            else None
        ),
        "reset_start_seconds": (
            round(safe_video_end, 6)
            if safe_video_end is not None
            else None
        ),
        "reset_scene_score": (
            float(first_reset["scene_score"])
            if first_reset is not None
            else None
        ),
        "reset_detection_mode": reset_detection_mode,
        "reset_cumulative_scene_score": (
            round(float(reset_cumulative_score), 6)
            if reset_cumulative_score is not None
            else None
        ),
        "duration_seconds": duration,
        "inspection_start_seconds": inspection_start,
        "scene_score_threshold": TERMINAL_RESET_SCENE_SCORE,
        "cumulative_scene_score_threshold": (
            TERMINAL_RESET_CUMULATIVE_SCENE_SCORE
        ),
        "cumulative_frame_count": TERMINAL_RESET_CUMULATIVE_FRAMES,
        "minimum_frame_score": TERMINAL_RESET_MINIMUM_FRAME_SCORE,
        "frame_scores": frame_scores,
        "input_sha256": sha256(
            json.dumps(
                input_contract,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "video_sha256": input_contract["video_sha256"],
    }


def _parse_freeze_intervals(
    output: str,
    *,
    final_duration_seconds: float | None = None,
) -> list[Dict[str, float]]:
    intervals: list[Dict[str, float]] = []
    current: Dict[str, float] = {}
    for name, raw_value in _FREEZE_EVENT.findall(str(output or "")):
        value = float(raw_value)
        current[name] = value
        if name != "end":
            continue
        start = current.get("start")
        duration = current.get("duration")
        if start is not None and duration is not None and value >= start:
            intervals.append(
                {
                    "start_seconds": round(start, 6),
                    "end_seconds": round(value, 6),
                    "duration_seconds": round(duration, 6),
                }
            )
        current = {}
    open_start = current.get("start")
    if (
        open_start is not None
        and final_duration_seconds is not None
        and math.isfinite(final_duration_seconds)
        and final_duration_seconds > open_start
    ):
        intervals.append(
            {
                "start_seconds": round(open_start, 6),
                "end_seconds": round(final_duration_seconds, 6),
                "duration_seconds": round(
                    final_duration_seconds - open_start,
                    6,
                ),
            }
        )
    return intervals


def evaluate_delivered_visual_seams(
    video_path: Path,
    *,
    cut_times_seconds: Sequence[float],
    reframe_profiles: Sequence[str],
    fps: float,
) -> Dict[str, Any]:
    """Reject a cloned visual hold around a planned Semantic UGC jump cut."""
    video_path = Path(video_path)
    if not video_path.is_file() or video_path.stat().st_size <= 0:
        raise ValueError("Delivered visual seam QA requires a non-empty video")
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Delivered visual seam QA requires a finite positive FPS")
    cuts = [float(value) for value in cut_times_seconds]
    if any(not math.isfinite(value) or value < 0 for value in cuts):
        raise ValueError("Delivered visual seam QA cut times must be finite and non-negative")
    profiles = [str(value) for value in reframe_profiles]
    minimum_freeze_seconds = MINIMUM_BOUNDARY_FREEZE_FRAMES / fps
    command = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "info",
        "-i",
        str(video_path),
        "-vf",
        (
            f"freezedetect=n={FREEZE_NOISE_DB}dB:"
            f"d={minimum_freeze_seconds:.9f}"
        ),
        "-an",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise ValueError(
            f"FFmpeg delivered visual seam QA failed: {result.stderr[-300:]}"
        )
    final_duration_seconds = _probe_duration(video_path)
    intervals = _parse_freeze_intervals(
        result.stderr,
        final_duration_seconds=final_duration_seconds,
    )
    cut_tolerance_seconds = 1.0 / fps
    seam_verdicts = []
    for seam_index, cut_seconds in enumerate(cuts):
        boundary_freezes = [
            interval
            for interval in intervals
            if interval["start_seconds"] <= cut_seconds + cut_tolerance_seconds
            and interval["end_seconds"]
            >= cut_seconds - PRE_CUT_INSPECTION_SECONDS
        ]
        incoming_profile = (
            profiles[seam_index + 1]
            if seam_index + 1 < len(profiles)
            else None
        )
        reasons = []
        if boundary_freezes:
            reasons.append("frozen_frames_intersect_visual_boundary")
        if incoming_profile in (None, "", "full"):
            reasons.append("intentional_jump_cut_reframe_missing")
        seam_verdicts.append(
            {
                "seam_index": seam_index,
                "cut_seconds": round(cut_seconds, 6),
                "incoming_reframe_profile": incoming_profile,
                "freeze_intervals": boundary_freezes,
                "passed": not reasons,
                "failure_reasons": reasons,
            }
        )
    input_contract = {
        "version": DELIVERY_VISUAL_SEAM_QA_VERSION,
        "video_sha256": sha256(video_path.read_bytes()).hexdigest(),
        "cut_times_seconds": [round(value, 6) for value in cuts],
        "reframe_profiles": profiles,
        "fps": round(fps, 6),
        "freeze_noise_db": FREEZE_NOISE_DB,
        "minimum_freeze_seconds": round(minimum_freeze_seconds, 6),
        "pre_cut_inspection_seconds": PRE_CUT_INSPECTION_SECONDS,
    }
    return {
        "version": DELIVERY_VISUAL_SEAM_QA_VERSION,
        "status": "passed" if all(item["passed"] for item in seam_verdicts) else "failed",
        "passed": all(item["passed"] for item in seam_verdicts),
        "input_sha256": sha256(
            json.dumps(
                input_contract,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "video_sha256": input_contract["video_sha256"],
        "fps": fps,
        "minimum_freeze_seconds": minimum_freeze_seconds,
        "pre_cut_inspection_seconds": PRE_CUT_INSPECTION_SECONDS,
        "detected_freeze_intervals": intervals,
        "seams": seam_verdicts,
        "failed_seam_indexes": [
            item["seam_index"] for item in seam_verdicts if not item["passed"]
        ],
    }


__all__ = [
    "DELIVERY_VISUAL_SEAM_QA_VERSION",
    "SOURCE_TERMINAL_RESET_QA_VERSION",
    "TERMINAL_RESET_CUMULATIVE_FRAMES",
    "TERMINAL_RESET_CUMULATIVE_SCENE_SCORE",
    "TERMINAL_RESET_MINIMUM_FRAME_SCORE",
    "TERMINAL_RESET_SCENE_SCORE",
    "_first_terminal_reset",
    "evaluate_source_terminal_reset",
    "evaluate_delivered_visual_seams",
]
