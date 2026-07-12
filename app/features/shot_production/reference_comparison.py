"""Deterministic editorial-cut comparison for semantic UGC renders."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean, pstdev
import subprocess
from typing import Any, Callable, Dict, Sequence

from app.core.errors import ValidationError


REFERENCE_COMPARISON_SCHEMA = "semantic-ugc-edit-reference/v1"


def derive_edit_metrics(duration_seconds: float, cut_timestamps: Sequence[float]) -> Dict[str, Any]:
    try:
        duration = float(duration_seconds)
        cuts = [float(value) for value in cut_timestamps]
    except (TypeError, ValueError) as exc:
        raise ValidationError("Editorial comparison requires finite numeric timings.") from exc
    if not math.isfinite(duration) or duration <= 0 or any(not math.isfinite(value) for value in cuts):
        raise ValidationError("Editorial comparison requires a positive finite duration.")
    if any(value <= 0 or value >= duration for value in cuts) or any(
        current <= previous for previous, current in zip(cuts, cuts[1:])
    ):
        raise ValidationError("Editorial cut timestamps must be strictly increasing inside the video.")
    bounds = [0.0, *cuts, duration]
    shots = [bounds[index + 1] - bounds[index] for index in range(len(bounds) - 1)]
    average = mean(shots)
    return {
        "duration_seconds": duration,
        "cut_count": len(cuts),
        "cut_timestamps_seconds": cuts,
        "cut_density_per_second": len(cuts) / duration,
        "seconds_per_cut": duration / len(cuts) if cuts else None,
        "shot_durations_seconds": shots,
        "mean_shot_duration_seconds": average,
        "shot_duration_cv": pstdev(shots) / average if len(shots) > 1 else 0.0,
    }


def _profile_distance(candidate: Dict[str, Any], reference: Dict[str, Any]) -> float:
    reference_density = float(reference["cut_density_per_second"])
    reference_shot = float(reference["mean_shot_duration_seconds"])
    if reference_density <= 0 or reference_shot <= 0:
        raise ValidationError("Reference edit profile requires at least one cut and positive shots.")
    return (
        abs(float(candidate["cut_density_per_second"]) - reference_density) / reference_density
        + abs(float(candidate["mean_shot_duration_seconds"]) - reference_shot) / reference_shot
    )


def compare_edit_profiles(
    *,
    reference: Dict[str, Any],
    control: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    candidate_distance = _profile_distance(candidate, reference)
    control_distance = _profile_distance(control, reference)
    return {
        "schema": REFERENCE_COMPARISON_SCHEMA,
        "reference": reference,
        "control": control,
        "candidate": candidate,
        "candidate_reference_distance": candidate_distance,
        "control_reference_distance": control_distance,
        "closer_to_reference_than_control": candidate_distance < control_distance,
    }


def _escape_lavfi_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def probe_edit_metrics(
    media_path: Path,
    *,
    run_fn: Callable[..., Any] = subprocess.run,
    scene_threshold: float = 5.0,
) -> Dict[str, Any]:
    path = Path(media_path)
    if not path.is_file():
        raise ValidationError("Editorial comparison media does not exist.", {"path": str(path)})
    duration_result = run_fn(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if duration_result.returncode != 0:
        raise ValidationError("Editorial comparison could not probe media duration.")
    try:
        duration = float(duration_result.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError("Editorial comparison received an invalid media duration.") from exc
    filter_graph = f"movie='{_escape_lavfi_path(path)}',scdet=threshold={scene_threshold:g}"
    scene_result = run_fn(
        [
            "ffprobe", "-v", "error", "-f", "lavfi", "-i", filter_graph,
            "-show_frames", "-show_entries",
            "frame=pts_time:frame_tags=lavfi.scd.time,lavfi.scd.score", "-of", "json",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if scene_result.returncode != 0:
        raise ValidationError("Editorial comparison scene detection failed.")
    try:
        frames = json.loads(scene_result.stdout).get("frames") or []
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ValidationError("Editorial comparison scene detection returned invalid JSON.") from exc
    cuts = []
    scores = []
    for frame in frames:
        tags = frame.get("tags") or {}
        if "lavfi.scd.time" not in tags:
            continue
        cuts.append(float(tags["lavfi.scd.time"]))
        scores.append(float(tags.get("lavfi.scd.score") or 0.0))
    metrics = derive_edit_metrics(duration, cuts)
    metrics["scene_scores"] = scores
    metrics["scene_threshold"] = scene_threshold
    metrics["path"] = str(path.resolve())
    return metrics


__all__ = [
    "REFERENCE_COMPARISON_SCHEMA",
    "compare_edit_profiles",
    "derive_edit_metrics",
    "probe_edit_metrics",
]
