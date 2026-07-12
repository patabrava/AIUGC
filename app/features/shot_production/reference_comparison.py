"""Deterministic editorial-cut comparison for semantic UGC renders."""

from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean, pstdev
import subprocess
from typing import Any, Callable, Dict, Sequence

from app.core.errors import ValidationError


REFERENCE_COMPARISON_SCHEMA = "semantic-ugc-edit-reference/v1"
REFERENCE_COMPARISON_PROOF_SCHEMA = "semantic-ugc-edit-reference-proof/v1"


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
    closer = candidate_distance < control_distance
    failures = []
    duration = float(candidate["duration_seconds"])
    cuts = candidate["cut_timestamps_seconds"]
    shots = candidate["shot_durations_seconds"]
    if not 14.5 <= duration <= 16.5:
        failures.append("candidate_duration_out_of_range")
    if int(candidate["cut_count"]) != 1:
        failures.append("candidate_must_have_exactly_one_cut")
    if len(cuts) == 1 and not 0.44 <= float(cuts[0]) / duration <= 0.56:
        failures.append("candidate_cut_position_out_of_range")
    if any(not 6.3 <= float(shot) <= 9.3 for shot in shots):
        failures.append("candidate_shot_duration_out_of_range")
    if float(candidate["shot_duration_cv"]) > 0.12:
        failures.append("candidate_shot_duration_variation_exceeded")
    if not closer:
        failures.append("candidate_not_closer_to_reference_than_control")
    return {
        "schema": REFERENCE_COMPARISON_SCHEMA,
        "reference": reference,
        "control": control,
        "candidate": candidate,
        "candidate_reference_distance": candidate_distance,
        "control_reference_distance": control_distance,
        "closer_to_reference_than_control": closer,
        "candidate_two_shot_gate": {
            "passed": not failures,
            "failure_reasons": failures,
        },
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


def _artifact_record(path: Path, *, label: str) -> Dict[str, Any]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValidationError(
            "Reference comparison artifact does not exist.",
            {"artifact": label, "path": str(resolved)},
        )
    digest = sha256()
    size = 0
    with resolved.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": str(resolved), "sha256": digest.hexdigest(), "bytes": size}


def _read_manifest(path: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    artifact = _artifact_record(path, label="candidate_manifest")
    try:
        payload = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Candidate manifest is not valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise ValidationError("Candidate manifest must contain a JSON object.")
    return payload, artifact


def _resolved_manifest_media_path(raw: Any, manifest_path: Path) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    value = Path(text).expanduser()
    if not value.is_absolute():
        value = manifest_path.resolve().parent / value
    return str(value.resolve())


def _add_failure(failures: list[str], reason: str, condition: bool) -> None:
    if condition and reason not in failures:
        failures.append(reason)


def build_artifact_bound_report(
    *,
    reference_path: Path,
    control_path: Path,
    candidate_path: Path,
    candidate_manifest_path: Path,
    probe_fn: Callable[[Path], Dict[str, Any]] = probe_edit_metrics,
) -> Dict[str, Any]:
    """Bind editorial metrics to the exact candidate and persisted delivery evidence."""
    paths = {
        "reference": Path(reference_path),
        "control": Path(control_path),
        "candidate": Path(candidate_path),
    }
    artifacts = {
        name: _artifact_record(path, label=name) for name, path in paths.items()
    }
    manifest_path = Path(candidate_manifest_path)
    manifest, manifest_artifact = _read_manifest(manifest_path)
    editorial = compare_edit_profiles(
        reference=probe_fn(paths["reference"]),
        control=probe_fn(paths["control"]),
        candidate=probe_fn(paths["candidate"]),
    )

    failures: list[str] = []
    candidate = artifacts["candidate"]
    _add_failure(failures, "candidate_manifest_not_uploaded", manifest.get("status") != "uploaded")

    caption = manifest.get("caption") if isinstance(manifest.get("caption"), dict) else {}
    caption_path = _resolved_manifest_media_path(caption.get("captioned_path"), manifest_path)
    caption_sha256 = str(caption.get("sha256") or "").strip().lower()
    try:
        caption_bytes = int(caption.get("bytes"))
    except (TypeError, ValueError):
        caption_bytes = None
    _add_failure(failures, "candidate_caption_missing", not caption)
    _add_failure(failures, "candidate_caption_path_mismatch", caption_path != candidate["path"])
    _add_failure(
        failures,
        "candidate_caption_sha256_mismatch",
        caption_sha256 != candidate["sha256"],
    )
    _add_failure(
        failures,
        "candidate_caption_size_mismatch",
        caption_bytes != candidate["bytes"],
    )

    script_text = str((manifest.get("script") or {}).get("text") or "")
    transcript = (
        manifest.get("final_transcript_qa")
        if isinstance(manifest.get("final_transcript_qa"), dict)
        else {}
    )
    expected_text = str(transcript.get("expected_text") or "")
    actual_text = str(transcript.get("actual_text") or "")
    try:
        word_error_rate = float(transcript.get("word_error_rate"))
    except (TypeError, ValueError):
        word_error_rate = None
    transcript_summary = {
        "script_text": script_text,
        "expected_text": expected_text,
        "actual_text": actual_text,
        "word_error_rate": word_error_rate,
        "passed": transcript.get("passed") is True,
        "failure_reasons": list(transcript.get("failure_reasons") or []),
    }
    _add_failure(failures, "final_transcript_qa_missing", not transcript)
    _add_failure(failures, "final_transcript_qa_failed", transcript.get("passed") is not True)
    _add_failure(failures, "final_transcript_wer_nonzero", word_error_rate != 0.0)
    _add_failure(
        failures,
        "final_transcript_script_mismatch",
        not script_text or expected_text != script_text,
    )
    _add_failure(failures, "final_transcript_text_missing", not actual_text)

    seam = manifest.get("seam_qa") if isinstance(manifest.get("seam_qa"), dict) else {}
    raw_gaps = seam.get("gaps_seconds") if seam else None
    seam_gaps: list[float] = []
    valid_gaps = isinstance(raw_gaps, list)
    if valid_gaps:
        try:
            seam_gaps = [float(value) for value in raw_gaps]
            valid_gaps = all(math.isfinite(value) and value >= 0 for value in seam_gaps)
        except (TypeError, ValueError):
            valid_gaps = False
    _add_failure(failures, "seam_qa_missing", not seam)
    _add_failure(failures, "seam_qa_failed", seam.get("passed") is not True)
    _add_failure(failures, "seam_gaps_missing_or_invalid", not valid_gaps)
    _add_failure(
        failures,
        "seam_gap_count_mismatch",
        not valid_gaps or len(seam_gaps) != int(editorial["candidate"]["cut_count"]),
    )

    verdicts: Dict[str, Dict[str, Any]] = {}
    verdict_sources = (
        ("acoustic", "acoustic_seam_qa"),
        ("visual", "visual_qa"),
        ("voice", "voice_qa"),
        ("media", "media_qa"),
    )
    for label, key in verdict_sources:
        source = manifest.get(key) if isinstance(manifest.get(key), dict) else {}
        summary = {"passed": source.get("passed") is True}
        if label == "acoustic":
            summary["deterministic_passed"] = source.get("deterministic_passed") is True
        verdicts[label] = summary
        _add_failure(failures, f"{label}_qa_missing", not source)
        _add_failure(failures, f"{label}_qa_failed", source.get("passed") is not True)
        if label == "acoustic":
            _add_failure(
                failures,
                "acoustic_deterministic_qa_failed",
                source.get("deterministic_passed") is not True,
            )

    upload = manifest.get("upload") if isinstance(manifest.get("upload"), dict) else {}
    upload_sha256 = str(upload.get("sha256") or "").strip().lower()
    try:
        upload_size = int(upload.get("size"))
    except (TypeError, ValueError):
        upload_size = None
    upload_passed = bool(
        upload and upload_sha256 == candidate["sha256"] and upload_size == candidate["bytes"]
    )
    upload_summary = {
        "storage_provider": upload.get("storage_provider"),
        "storage_key": upload.get("storage_key"),
        "url": upload.get("url"),
        "sha256": upload_sha256 or None,
        "size": upload_size,
        "passed": upload_passed,
    }
    _add_failure(failures, "upload_receipt_missing", not upload)
    _add_failure(failures, "upload_sha256_mismatch", upload_sha256 != candidate["sha256"])
    _add_failure(failures, "upload_size_mismatch", upload_size != candidate["bytes"])

    remote = (
        manifest.get("upload_verification")
        if isinstance(manifest.get("upload_verification"), dict)
        else {}
    )
    remote_summary = {
        "expected_sha256": remote.get("expected_sha256"),
        "actual_sha256": remote.get("actual_sha256"),
        "expected_size": remote.get("expected_size"),
        "actual_size": remote.get("actual_size"),
        "passed": remote.get("passed") is True,
        "failure_reasons": list(remote.get("failure_reasons") or []),
    }
    _add_failure(failures, "remote_verification_missing", not remote)
    _add_failure(failures, "remote_verification_failed", remote.get("passed") is not True)
    for field in ("expected_sha256", "actual_sha256"):
        _add_failure(
            failures,
            f"remote_{field}_mismatch",
            str(remote.get(field) or "").strip().lower() != candidate["sha256"],
        )
    for field in ("expected_size", "actual_size"):
        try:
            remote_size = int(remote.get(field))
        except (TypeError, ValueError):
            remote_size = None
        _add_failure(
            failures,
            f"remote_{field}_mismatch",
            remote_size != candidate["bytes"],
        )

    evidence_gate = {"passed": not failures, "failure_reasons": failures}
    editorial_gate = editorial["candidate_two_shot_gate"]
    proof_failures = [
        *[f"editorial:{reason}" for reason in editorial_gate["failure_reasons"]],
        *[f"evidence:{reason}" for reason in failures],
    ]
    return {
        "schema": REFERENCE_COMPARISON_PROOF_SCHEMA,
        "artifacts": artifacts,
        "candidate_manifest": {
            "artifact": manifest_artifact,
            "run_id": manifest.get("run_id"),
            "status": manifest.get("status"),
        },
        "editorial_comparison": editorial,
        "candidate_evidence": {
            "final_transcript_qa": transcript_summary,
            "seam_gaps_seconds": seam_gaps,
            "seam_qa": {
                "passed": seam.get("passed") is True,
                "failure_reasons": list(seam.get("failure_reasons") or []),
            },
            "verdicts": verdicts,
            "delivery": {
                "local_candidate": candidate,
                "manifest_caption": {
                    "path": caption_path,
                    "sha256": caption_sha256 or None,
                    "bytes": caption_bytes,
                },
                "upload": upload_summary,
                "remote_verification": remote_summary,
            },
        },
        "evidence_gate": evidence_gate,
        "proof_gate": {"passed": not proof_failures, "failure_reasons": proof_failures},
    }


__all__ = [
    "REFERENCE_COMPARISON_SCHEMA",
    "REFERENCE_COMPARISON_PROOF_SCHEMA",
    "build_artifact_bound_report",
    "compare_edit_profiles",
    "derive_edit_metrics",
    "probe_edit_metrics",
]
