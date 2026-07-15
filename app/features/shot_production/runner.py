"""Resumable local runner for the approved-frame semantic UGC pilot."""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
from functools import wraps
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Iterator, Optional, Sequence
from urllib.parse import quote

import google.auth
import httpx
from google.auth.transport.requests import Request
from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.adapters.caption_aligner import align_transcript_to_script
from app.adapters.caption_renderer import burn_captions
from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.adapters.storage_client import get_storage_client
from app.adapters.video_stitcher import stitch_segments
from app.core.errors import ValidationError
from app.features.shot_production.acoustic_qa import (
    DEFAULT_ACOUSTIC_QA_MODEL,
    ACOUSTIC_QA_RUBRIC_VERSION,
    evaluate_acoustic_seam_continuity,
)
from app.features.shot_production.audio_seams import (
    MAX_PERCEPTUAL_SEAM_ENERGY_DELTA_DB,
    TakeAudioEvidence,
    analyze_audio_frames,
    plan_acoustic_seams,
)
from app.features.shot_production.composer import (
    build_take_trim_window,
    evaluate_seam_gaps,
    evaluate_take_transcript,
)
from app.features.shot_production.planner import EditorialBeat, plan_editorial_beats
from app.features.shot_production.prompts import (
    EFFECTIVE_NEGATIVE_PROMPT,
    SUPPORTED_DURATIONS,
    build_veo_take_prompt,
    compile_veo_take_requests,
)
from app.features.shot_production.shot_deck import derive_shot_deck
from app.features.shot_production.visual_qa import evaluate_visual_consistency
from app.features.shot_production.voice_qa import (
    DEFAULT_VOICE_QA_MODEL,
    VOICE_QA_RUBRIC_VERSION,
    evaluate_voice_consistency,
)


MANIFEST_VERSION = 3
SUPPORTED_MANIFEST_VERSIONS = frozenset({2, MANIFEST_VERSION})
APP_SCRIPT_SOURCE = "app.features.topics.agents.generate_dialog_scripts"
SEMANTIC_SCRIPT_SOURCE = (
    "app.features.topics.semantic_scripts.generate_semantic_script"
)
MANUAL_SEMANTIC_SCRIPT_SOURCE = "manual_semantic_ugc"
PLANNING_PROFILE = "minimum-eight-second-shots-v1"
DEFAULT_MAX_INFLIGHT = 2
_RUN_LOCKS = threading.local()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _artifact_matches(record: Dict[str, Any], *, path_key: str = "path") -> bool:
    path_value = record.get(path_key)
    expected_sha = str(record.get("sha256") or "")
    if not path_value or not expected_sha:
        return False
    path = Path(path_value)
    try:
        stat = path.stat()
    except OSError:
        return False
    expected_bytes = record.get("bytes")
    if expected_bytes is not None:
        try:
            if stat.st_size != int(expected_bytes):
                return False
        except (TypeError, ValueError):
            return False
    try:
        return _file_sha256(path) == expected_sha
    except OSError:
        return False


def _clear_downstream_artifacts(payload: Dict[str, Any]) -> None:
    for key in (
        "contact_sheet",
        "visual_qa",
        "voice_qa",
        "stitch",
        "final_transcript",
        "final_transcript_qa",
        "seam_qa",
        "acoustic_seam_plan",
        "acoustic_seam_qa",
        "acoustic_plan_failure",
        "caption",
        "media_qa",
        "upload_intent",
        "upload",
        "upload_verification",
    ):
        payload.pop(key, None)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _load_manifest(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("Pilot manifest could not be loaded.", {"path": str(path), "error": str(exc)}) from exc
    if not isinstance(payload, dict) or payload.get("version") not in SUPPORTED_MANIFEST_VERSIONS:
        raise ValidationError("Pilot manifest has an unsupported schema version.")
    return payload


@contextmanager
def _exclusive_file_lock(lock_path: Path, *, label: str) -> Iterator[None]:
    """Hold a crash-safe, process-scoped lock without trusting stale lock-file contents."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValidationError(
                f"{label} is already active for this manifest.",
                {"lock_path": str(lock_path)},
            ) from exc
        try:
            handle.seek(0)
            handle.truncate()
            json.dump({"pid": os.getpid(), "locked_at": _utc_now()}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def pilot_run_lock(manifest_path: Path) -> Iterator[None]:
    """Prevent two CLI processes from mutating one paid run concurrently."""
    manifest_path = Path(manifest_path).resolve()
    held = getattr(_RUN_LOCKS, "depth_by_manifest", None)
    if held is None:
        held = {}
        _RUN_LOCKS.depth_by_manifest = held
    key = str(manifest_path)
    if key in held:
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    lock_path = manifest_path.with_name(f".{manifest_path.name}.run.lock")
    with _exclusive_file_lock(lock_path, label="Pilot run"):
        held[key] = 1
        try:
            yield
        finally:
            held.pop(key, None)


def _manifest_locked(function: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize every exported manifest mutator, including direct Python callers."""

    @wraps(function)
    def locked(*args: Any, **kwargs: Any) -> Any:
        manifest_path = kwargs.get("manifest_path")
        if manifest_path is None:
            if not args:
                raise TypeError("Manifest-mutating calls require manifest_path.")
            manifest_path = args[0]
        with pilot_run_lock(Path(manifest_path)):
            return function(*args, **kwargs)

    return locked


def _beat_from_payload(payload: Dict[str, Any]) -> EditorialBeat:
    return EditorialBeat(
        index=int(payload["index"]),
        text=str(payload["text"]),
        word_count=int(payload["word_count"]),
        estimated_speech_seconds=float(payload["estimated_speech_seconds"]),
        provider_duration_seconds=int(payload["provider_duration_seconds"]),
    )


def _script_is_in_generator_output(script_source: Dict[str, Any], script_text: str) -> bool:
    generator_output = script_source.get("generator_output")
    if not isinstance(generator_output, dict):
        return False
    return any(
        candidate == script_text
        for values in generator_output.values()
        if isinstance(values, list)
        for candidate in values
        if isinstance(candidate, str)
    )


def _script_is_audited_generator_revision(
    script_source: Dict[str, Any], script_text: str
) -> bool:
    original_script = str(script_source.get("original_script") or "").strip()
    revisions = script_source.get("editorial_revisions")
    if not original_script or not isinstance(revisions, list) or not revisions:
        return False
    if not _script_is_in_generator_output(script_source, original_script):
        return False
    revised = original_script
    for revision in revisions:
        if not isinstance(revision, dict):
            return False
        original = str(revision.get("original_text") or "").strip()
        replacement = str(revision.get("replacement_text") or "").strip()
        reason = str(revision.get("reason") or "").strip()
        if not original or not replacement or not reason or revised.count(original) != 1:
            return False
        revised = revised.replace(original, replacement, 1)
    return revised == script_text


def _is_approved_manual_semantic_script(script_source: Dict[str, Any]) -> bool:
    return (
        script_source.get("source") == MANUAL_SEMANTIC_SCRIPT_SOURCE
        and script_source.get("creation_mode") == MANUAL_SEMANTIC_SCRIPT_SOURCE
        and str(script_source.get("script_review_status") or "").strip().lower()
        == "approved"
    )


def _requested_script_duration(script_source: Dict[str, Any]) -> Any:
    if script_source.get("source") in {
        SEMANTIC_SCRIPT_SOURCE,
        MANUAL_SEMANTIC_SCRIPT_SOURCE,
    }:
        return script_source.get("target_duration_seconds")
    return script_source.get("target_length_tier")


def _delivery_duration_contract(requested_seconds: Any) -> Dict[str, float]:
    if isinstance(requested_seconds, bool):
        raise ValidationError("Pilot requested duration must be a finite number of at least four seconds.")
    try:
        requested = float(requested_seconds)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Pilot requested duration must be a finite number of at least four seconds.") from exc
    if not math.isfinite(requested) or requested < 4.0:
        raise ValidationError("Pilot requested duration must be a finite number of at least four seconds.")
    return {
        "requested": requested,
        "minimum": max(0.5, requested - 1.5),
        "maximum": requested + 0.5,
    }


def _validate_approved_pilot_plan(
    *,
    script_source: Dict[str, Any],
    script_text: str,
    beats: list[EditorialBeat],
) -> Dict[str, float]:
    has_generator_provenance = _script_is_in_generator_output(
        script_source, script_text
    ) or _script_is_audited_generator_revision(script_source, script_text)
    source = script_source.get("source")
    if source == MANUAL_SEMANTIC_SCRIPT_SOURCE:
        if not _is_approved_manual_semantic_script(script_source):
            raise ValidationError(
                "Pilot requires approved manual semantic script provenance."
            )
    elif source == SEMANTIC_SCRIPT_SOURCE:
        if (
            script_source.get("creation_mode") != "semantic_ugc"
            or not has_generator_provenance
        ):
            raise ValidationError(
                "Pilot requires dynamic semantic generator provenance."
            )
    elif source != APP_SCRIPT_SOURCE or not has_generator_provenance:
        raise ValidationError(
            "Pilot requires an app-generated script with intact generator provenance "
            "or approved manual semantic script provenance."
        )
    duration_contract = _delivery_duration_contract(
        _requested_script_duration(script_source)
    )
    durations = [beat.provider_duration_seconds for beat in beats]
    if not durations or any(duration not in SUPPORTED_DURATIONS for duration in durations):
        raise ValidationError(
            "Pilot script contains an unsupported Veo duration.",
            {"actual": durations},
        )
    if [beat.index for beat in beats] != list(range(len(beats))):
        raise ValidationError("Pilot script beats must use contiguous zero-based indexes.")
    if sum(durations) < duration_contract["minimum"]:
        raise ValidationError(
            "Pilot script duration plan cannot reach the requested delivery duration.",
            {"provider_capacity_seconds": sum(durations), **duration_contract},
        )
    estimated_speech = sum(beat.estimated_speech_seconds for beat in beats)
    if estimated_speech > duration_contract["maximum"]:
        raise ValidationError(
            "Pilot script estimated speech exceeds the requested delivery duration.",
            {"estimated_speech_seconds": estimated_speech, **duration_contract},
        )
    return duration_contract


def _request_contract_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    script = payload["script"]
    master = payload["approved_master"]
    script_contract = {
        "input_sha256": script["input_sha256"],
        "text_sha256": script["text_sha256"],
        "source": script["source"],
        "target_length_tier": script["target_length_tier"],
        "text": script["text"],
        "planned_provider_durations": script["planned_provider_durations"],
    }
    for field in (
        "creation_mode",
        "script_review_status",
        "target_duration_seconds",
    ):
        if field in script:
            script_contract[field] = script[field]
    if int(payload.get("version") or 0) >= 3:
        script_contract.update(
            {
                "planning_profile": script["planning_profile"],
                "delivery_duration_seconds": script["delivery_duration_seconds"],
            }
        )
    return {
        "approved_master": {
            "sha256": master["sha256"],
            "mime_type": master["mime_type"],
        },
        "script": script_contract,
        "takes": [
            {
                "index": take["index"],
                "beat": take["beat"],
                "shot_sha256": take["shot"]["sha256"],
                "model": take["model"],
                "aspect_ratio": take["aspect_ratio"],
                "duration_seconds": take["duration_seconds"],
                "seed": take["seed"],
                "prompt": take["prompt"],
                "negative_prompt": take["negative_prompt"],
            }
            for take in payload["takes"]
        ],
    }


def _validate_duration_planning_contract(payload: Dict[str, Any]) -> Dict[str, float]:
    script = payload.get("script") or {}
    derived = _delivery_duration_contract(_requested_script_duration(script))
    stored_profile = script.get("planning_profile")
    stored_duration = script.get("delivery_duration_seconds")
    requires_duration_fields = int(payload.get("version") or 0) >= 3
    if requires_duration_fields or stored_profile is not None or stored_duration is not None:
        if stored_profile != PLANNING_PROFILE or stored_duration != derived:
            raise ValidationError(
                "Pilot duration planning contract changed after approval.",
                {
                    "expected_planning_profile": PLANNING_PROFILE,
                    "actual_planning_profile": stored_profile,
                    "expected_delivery_duration_seconds": derived,
                    "actual_delivery_duration_seconds": stored_duration,
                },
            )
    expected = str(payload.get("request_contract_sha256") or "")
    actual = _canonical_sha256(_request_contract_payload(payload))
    if not expected or actual != expected:
        raise ValidationError(
            "Pilot request contract changed after approval; no paid calls were made.",
            {"expected_sha256": expected, "actual_sha256": actual},
        )
    return derived


def _validate_paid_request_contract(payload: Dict[str, Any]) -> None:
    script = payload.get("script") or {}
    source = script.get("source")
    if source == MANUAL_SEMANTIC_SCRIPT_SOURCE:
        if not _is_approved_manual_semantic_script(script):
            raise ValidationError(
                "Pilot paid request is no longer an approved manual semantic plan."
            )
    elif source not in {APP_SCRIPT_SOURCE, SEMANTIC_SCRIPT_SOURCE}:
        raise ValidationError("Pilot paid request is no longer an approved app-generated plan.")
    duration_contract = _validate_duration_planning_contract(payload)
    durations = script.get("planned_provider_durations")
    take_durations = [take.get("duration_seconds") for take in payload.get("takes") or []]
    if (
        not isinstance(durations, list)
        or durations != take_durations
        or any(duration not in SUPPORTED_DURATIONS for duration in durations)
        or sum(durations) < duration_contract["minimum"]
    ):
        raise ValidationError("Pilot paid request duration plan changed after approval.")
    if sha256(str(script.get("text") or "").encode("utf-8")).hexdigest() != script.get("text_sha256"):
        raise ValidationError("Pilot script text changed without an audited contract update.")
    source_path = Path(script.get("path") or "")
    if not source_path.is_file() or _file_sha256(source_path) != script.get("input_sha256"):
        raise ValidationError("Pilot script input changed after approval.")


def _is_definitive_provider_rejection(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError) or exc.response is None:
        return False
    return exc.response.status_code in {400, 401, 403, 404, 422, 429}


@_manifest_locked
def initialize_pilot(
    *,
    manifest_path: Path,
    approved_frame_path: Path,
    expected_sha256: str,
    script_input_path: Path,
    base_seed: int,
) -> Dict[str, Any]:
    """Validate all free inputs, materialize the safe deck, and persist the plan."""
    manifest_path = Path(manifest_path)
    if manifest_path.exists():
        raise ValidationError("Pilot manifest already exists; use resume instead.", {"path": str(manifest_path)})

    approved_frame_path = Path(approved_frame_path).resolve()
    script_input_path = Path(script_input_path).resolve()
    try:
        approved_bytes = approved_frame_path.read_bytes()
        script_input_bytes = script_input_path.read_bytes()
        script_source = json.loads(script_input_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Pilot input could not be loaded.", {"error": str(exc)}) from exc
    if not isinstance(script_source, dict):
        raise ValidationError("Pilot script input must be one JSON object.")
    script_text = str(script_source.get("script") or "").strip()
    if not script_text:
        raise ValidationError("Pilot script input requires a non-empty script.")

    # Hash/aspect validation happens before the run directory or manifest is created.
    beats = plan_editorial_beats(script_text)
    duration_contract = _validate_approved_pilot_plan(
        script_source=script_source,
        script_text=script_text,
        beats=beats,
    )
    deck = derive_shot_deck(
        approved_master_bytes=approved_bytes,
        expected_sha256=expected_sha256,
        mime_type="image/png",
        shot_count=len(beats),
    )
    requests = compile_veo_take_requests(beats=beats, shot_deck=deck, base_seed=base_seed)

    run_dir = manifest_path.parent.resolve()
    deck_dir = run_dir / "shot-deck"
    deck_dir.mkdir(parents=True, exist_ok=True)
    takes = []
    for request in requests:
        shot_path = deck_dir / f"take-{request.index}-{request.shot.name}.png"
        shot_path.write_bytes(request.shot.image_bytes)
        takes.append(
            {
                "index": request.index,
                "attempt": 1,
                "attempt_history": [],
                "status": "planned",
                "beat": asdict(request.beat),
                "shot": {
                    "name": request.shot.name,
                    "path": str(shot_path),
                    "source_sha256": request.shot.source_sha256,
                    "sha256": request.shot.output_sha256,
                    "crop_box": list(request.shot.crop_box),
                    "width": request.shot.width,
                    "height": request.shot.height,
                    "mime_type": request.shot.mime_type,
                },
                "model": request.model,
                "aspect_ratio": request.aspect_ratio,
                "duration_seconds": request.duration_seconds,
                "seed": request.seed,
                "prompt": request.prompt,
                "negative_prompt": request.negative_prompt,
                "submission": None,
                "operation": None,
                "raw": None,
                "transcript": None,
                "transcript_qa": None,
                "trim_window": None,
            }
        )

    payload: Dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "run_id": run_dir.name,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "planned",
        "base_seed": base_seed,
        "approved_master": {
            "path": str(approved_frame_path),
            "sha256": expected_sha256.lower(),
            "mime_type": "image/png",
        },
        "script": {
            "path": str(script_input_path),
            "input_sha256": sha256(script_input_bytes).hexdigest(),
            "text_sha256": sha256(script_text.encode("utf-8")).hexdigest(),
            "source": script_source.get("source"),
            "creation_mode": script_source.get("creation_mode"),
            "script_review_status": script_source.get("script_review_status"),
            "category": script_source.get("category"),
            "target_length_tier": script_source.get("target_length_tier"),
            "target_duration_seconds": script_source.get("target_duration_seconds"),
            "planning_profile": PLANNING_PROFILE,
            "delivery_duration_seconds": duration_contract,
            "text": script_text,
            "planned_provider_durations": [beat.provider_duration_seconds for beat in beats],
            "source_payload": script_source,
        },
        "takes": takes,
    }
    payload["request_contract_sha256"] = _canonical_sha256(_request_contract_payload(payload))
    _atomic_write_json(manifest_path, payload)
    return payload


def _correlation_id(manifest: Dict[str, Any], take: Dict[str, Any]) -> str:
    return f"semantic_ugc_{manifest['run_id']}_take_{take['index']}_attempt_{take['attempt']}"


@_manifest_locked
def submit_pending_takes(
    manifest_path: Path,
    vertex_client: Any,
    *,
    max_inflight: int = DEFAULT_MAX_INFLIGHT,
) -> Dict[str, Any]:
    """Submit only unaccepted takes and persist each paid operation immediately."""
    manifest_path = Path(manifest_path)
    if max_inflight < 1:
        raise ValidationError("Vertex max_inflight must be at least one.")
    submission_lock = manifest_path.with_name(f".{manifest_path.name}.submit.lock")
    with _exclusive_file_lock(submission_lock, label="Pilot paid submission"):
        payload = _load_manifest(manifest_path)
        _validate_paid_request_contract(payload)
        inflight = sum(
            1
            for take in payload["takes"]
            if take.get("operation") and not _artifact_matches(take.get("raw") or {})
        )
        available_slots = max(0, max_inflight - inflight)
        for take in payload["takes"]:
            if take.get("operation"):
                continue
            if available_slots <= 0:
                break
            prior_submission = take.get("submission") or {}
            if prior_submission.get("state") in {"submitting", "unknown", "rejected"}:
                raise ValidationError(
                    "Take has an unresolved Vertex submission; automatic paid retry is blocked.",
                    {
                        "take_index": take["index"],
                        "correlation_id": prior_submission.get("correlation_id"),
                        "required_action": "reconcile the provider operation or explicitly reset this failed take",
                    },
                )
            shot_path = Path(take["shot"]["path"])
            if not shot_path.is_file() or _file_sha256(shot_path) != take["shot"]["sha256"]:
                raise ValidationError("Approved take frame hash changed before submission.", {"take_index": take["index"]})
            correlation_id = _correlation_id(payload, take)
            take["submission"] = {
                "state": "submitting",
                "correlation_id": correlation_id,
                "started_at": _utc_now(),
            }
            take["status"] = "submitting"
            payload["status"] = "submitting"
            payload["updated_at"] = _utc_now()
            # Persist intent before crossing the paid boundary. A lost response then fails
            # closed instead of being guessed safe to resubmit.
            _atomic_write_json(manifest_path, payload)
            try:
                result = vertex_client.submit_image_video(
                    prompt=take["prompt"],
                    image_bytes=shot_path.read_bytes(),
                    mime_type=take["shot"]["mime_type"],
                    correlation_id=correlation_id,
                    aspect_ratio=take["aspect_ratio"],
                    duration_seconds=take["duration_seconds"],
                    model=take["model"],
                    negative_prompt=take["negative_prompt"],
                    seed=take["seed"],
                    sample_count=1,
                    generate_audio=True,
                    resolution="720p",
                )
                operation_id = str(result.get("operation_id") or "").strip()
                if not operation_id:
                    raise ValidationError("Vertex response is missing an operation id.")
            except Exception as exc:
                rejected = _is_definitive_provider_rejection(exc)
                take["submission"].update(
                    {
                        "state": "rejected" if rejected else "unknown",
                        "failed_at": _utc_now(),
                        "error": str(exc),
                    }
                )
                take["status"] = "submission_rejected" if rejected else "submission_unknown"
                payload["status"] = take["status"]
                payload["last_error"] = {
                    "stage": "submit",
                    "take_index": take["index"],
                    "message": str(exc),
                }
                payload["updated_at"] = _utc_now()
                _atomic_write_json(manifest_path, payload)
                raise
            accepted_at = _utc_now()
            take["operation"] = {
                "operation_id": operation_id,
                "provider_model": result.get("provider_model") or take["model"],
                "status": result.get("status") or "submitted",
                "submitted_at": accepted_at,
            }
            take["submission"].update(
                {"state": "accepted", "operation_id": operation_id, "accepted_at": accepted_at}
            )
            take["status"] = "submitted"
            payload["status"] = "submitted"
            payload["updated_at"] = _utc_now()
            # This write deliberately happens before the next provider call.
            _atomic_write_json(manifest_path, payload)
            available_slots -= 1
        return payload


def load_video_uri(video_uri: str) -> bytes:
    """Load Vertex data, GCS, HTTP, or explicit local-file video output."""
    uri = str(video_uri or "").strip()
    if uri.startswith("data:"):
        try:
            _header, encoded = uri.split(",", 1)
            return base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValidationError("Vertex video data URI is invalid.") from exc
    if uri.startswith("gs://"):
        bucket_and_object = uri[5:]
        bucket, separator, object_name = bucket_and_object.partition("/")
        if not separator or not bucket or not object_name:
            raise ValidationError("Vertex GCS video URI is invalid.")
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
        if credentials.expired or not credentials.token:
            credentials.refresh(Request())
        response = httpx.get(
            f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(object_name, safe='')}?alt=media",
            headers={"Authorization": f"Bearer {credentials.token}"},
            follow_redirects=True,
            timeout=120.0,
        )
        response.raise_for_status()
        return response.content
    if uri.startswith(("https://", "http://")):
        response = httpx.get(uri, follow_redirects=True, timeout=120.0)
        response.raise_for_status()
        return response.content
    local_path = Path(uri)
    if local_path.is_file():
        return local_path.read_bytes()
    raise ValidationError("Vertex video URI uses an unsupported scheme.", {"uri_prefix": uri[:20]})


@_manifest_locked
def poll_and_download_takes(
    manifest_path: Path,
    vertex_client: Any,
    *,
    uri_loader: Callable[[str], bytes] = load_video_uri,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = 10.0,
    timeout_seconds: float = 1800.0,
) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    started = time.monotonic()
    while True:
        payload = _load_manifest(manifest_path)
        pending = []
        for take in payload["takes"]:
            raw = take.get("raw") or {}
            if raw and _artifact_matches(raw):
                continue
            if raw:
                take["raw"] = None
                take["transcript"] = None
                take["transcript_qa"] = None
                take["trim_window"] = None
                take["status"] = "submitted"
                _clear_downstream_artifacts(payload)
            operation = take.get("operation") or {}
            operation_id = operation.get("operation_id")
            if not operation_id:
                submission_state = (take.get("submission") or {}).get("state")
                if submission_state in {"submitting", "unknown", "rejected"}:
                    raise ValidationError(
                        "Cannot continue while a take submission is unresolved or rejected.",
                        {"take_index": take["index"], "submission_state": submission_state},
                    )
                continue
            result = vertex_client.check_operation_status(
                operation_id=operation_id,
                correlation_id=_correlation_id(payload, take),
            )
            operation["status"] = result.get("status") or "processing"
            operation["last_polled_at"] = _utc_now()
            take["operation"] = operation
            if operation["status"] == "failed" or result.get("error"):
                take["status"] = "failed"
                payload["status"] = "provider_failed"
                payload["updated_at"] = _utc_now()
                _atomic_write_json(manifest_path, payload)
                raise ValidationError("Vertex take generation failed.", {"take_index": take["index"], "error": result.get("error")})
            if result.get("done") and result.get("video_uri"):
                video_bytes = uri_loader(result["video_uri"])
                if not isinstance(video_bytes, bytes) or not video_bytes:
                    raise ValidationError("Completed Vertex take produced no video bytes.")
                raw_dir = manifest_path.parent / "raw"
                raw_dir.mkdir(parents=True, exist_ok=True)
                raw_path = raw_dir / f"take-{take['index']}-attempt-{take['attempt']}.mp4"
                raw_path.write_bytes(video_bytes)
                take["raw"] = {
                    "path": str(raw_path),
                    "sha256": sha256(video_bytes).hexdigest(),
                    "bytes": len(video_bytes),
                    "downloaded_at": _utc_now(),
                }
                take["status"] = "completed"
            elif result.get("done"):
                raise ValidationError("Completed Vertex take is missing its video URI.", {"take_index": take["index"]})
            else:
                take["status"] = "processing"
                pending.append(take["index"])
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)

        if not pending:
            payload = _load_manifest(manifest_path)
            all_raw = all(_artifact_matches(take.get("raw") or {}) for take in payload["takes"])
            payload["status"] = "raw_completed" if all_raw else "wave_completed"
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            return payload
        if time.monotonic() - started >= timeout_seconds:
            payload["status"] = "poll_timeout"
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            raise TimeoutError(f"Pilot take polling exceeded {timeout_seconds} seconds.")
        sleep_fn(max(0.0, poll_interval_seconds))


@_manifest_locked
def generate_raw_takes_in_waves(
    manifest_path: Path,
    vertex_client: Any,
    *,
    max_inflight: int = DEFAULT_MAX_INFLIGHT,
    uri_loader: Callable[[str], bytes] = load_video_uri,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = 10.0,
    timeout_seconds: float = 1800.0,
) -> Dict[str, Any]:
    """Generate every raw take while respecting Vertex concurrent-operation quota."""
    manifest_path = Path(manifest_path)
    while True:
        payload = _load_manifest(manifest_path)
        if all(_artifact_matches(take.get("raw") or {}) for take in payload["takes"]):
            payload["status"] = "raw_completed"
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            return payload
        submit_pending_takes(manifest_path, vertex_client, max_inflight=max_inflight)
        payload = poll_and_download_takes(
            manifest_path,
            vertex_client,
            uri_loader=uri_loader,
            sleep_fn=sleep_fn,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
        if payload.get("status") == "raw_completed":
            return payload


def _serialize_transcript(transcript: WordLevelTranscript) -> Dict[str, Any]:
    return {
        "full_text": transcript.full_text,
        "words": [asdict(word) for word in transcript.words],
    }


def _deserialize_transcript(payload: Dict[str, Any]) -> WordLevelTranscript:
    try:
        words = [
            Word(word=str(word["word"]), start=float(word["start"]), end=float(word["end"]))
            for word in payload["words"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Stored take transcript is malformed.") from exc
    return WordLevelTranscript(words=words, full_text=str(payload.get("full_text") or ""))


@_manifest_locked
def transcribe_and_validate_takes(manifest_path: Path, deepgram_client: Any) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    beats = [_beat_from_payload(take["beat"]) for take in payload["takes"]]
    timing_migration_planned = any(
        (take.get("transcript_qa") or {}).get("passed")
        and (
            (take.get("transcript_qa") or {}).get("first_word_start_seconds") is None
            or (take.get("trim_window") or {}).get("source") != "deepgram_word_window"
        )
        for take in payload["takes"]
    )
    if timing_migration_planned:
        for key in (
            "voice_qa",
            "stitch",
            "final_transcript",
            "final_transcript_qa",
            "seam_qa",
            "acoustic_seam_plan",
            "acoustic_seam_qa",
            "caption",
            "media_qa",
            "upload",
            "upload_verification",
        ):
            payload.pop(key, None)
        payload["status"] = "timing_migration_planned"
        payload["updated_at"] = _utc_now()
        _atomic_write_json(manifest_path, payload)
    failed = []
    for take, beat in zip(payload["takes"], beats):
        raw = take.get("raw") or {}
        if not _artifact_matches(raw):
            raise ValidationError(
                "Raw take artifact failed its recorded checksum; rerun polling to recover it.",
                {"take_index": take["index"]},
            )
        existing_qa = take.get("transcript_qa") or {}
        trim_window = take.get("trim_window") or {}
        needs_timing_migration = (
            existing_qa.get("passed")
            and (
                existing_qa.get("first_word_start_seconds") is None
                or trim_window.get("source") != "deepgram_word_window"
            )
        )
        if existing_qa.get("passed") and not needs_timing_migration:
            continue
        raw_path = Path(raw["path"])
        stored_transcript = take.get("transcript") or {}
        transcript = (
            _deserialize_transcript(stored_transcript)
            if (needs_timing_migration or existing_qa) and stored_transcript
            else deepgram_client.transcribe(
                audio_bytes=raw_path.read_bytes(),
                correlation_id=f"{_correlation_id(payload, take)}_transcript",
            )
        )
        qa = evaluate_take_transcript(
            beat,
            transcript,
            other_beats=[other for other in beats if other.index != beat.index],
        )
        take["transcript"] = _serialize_transcript(transcript)
        take["transcript_qa"] = asdict(qa)
        take["trim_window"] = (
            build_take_trim_window(
                qa,
                take["duration_seconds"],
                trim_head=beat.index > 0,
            )
            if qa.passed
            else None
        )
        take["status"] = "transcribed" if qa.passed else "transcript_failed"
        if not qa.passed:
            failed.append(take["index"])
        payload["updated_at"] = _utc_now()
        _atomic_write_json(manifest_path, payload)
    if failed:
        payload["status"] = "transcript_failed"
        _atomic_write_json(manifest_path, payload)
        raise ValidationError(f"Transcript QA failed for take indexes: {', '.join(map(str, failed))}.")
    payload["status"] = "transcript_passed"
    _atomic_write_json(manifest_path, payload)
    return payload


def _extract_frame(video_path: Path, output_path: Path, seconds: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-ss", f"{max(0.0, seconds):.3f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not output_path.is_file():
        raise ValidationError("FFmpeg could not extract a pilot QA frame.", {"error": result.stderr[-400:]})


@_manifest_locked
def build_contact_sheet(manifest_path: Path) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    existing = payload.get("contact_sheet") or {}
    if existing and _artifact_matches(existing):
        return existing
    if existing:
        payload.pop("contact_sheet", None)
        payload.pop("visual_qa", None)
        payload.pop("upload", None)
        payload.pop("upload_verification", None)
    frame_dir = manifest_path.parent / "qa" / "frames"
    per_take_frames = []
    for take in payload["takes"]:
        if not _artifact_matches(take.get("raw") or {}):
            raise ValidationError(
                "Contact sheet requires checksum-verified raw takes.",
                {"take_index": take["index"]},
            )
        qa = take.get("transcript_qa") or {}
        if not qa.get("passed"):
            raise ValidationError("Contact sheet requires transcript-passed takes.", {"take_index": take["index"]})
        final_word_end = float(qa["final_word_end_seconds"])
        provider_end = max(float(take["duration_seconds"]) - 0.1, 0.0)
        sample_times = (
            min(0.5, provider_end),
            min(max(final_word_end / 2.0, 0.1), provider_end),
            min(final_word_end, provider_end),
        )
        take_frames = []
        for label, seconds in zip(("early", "middle", "final-word"), sample_times):
            frame_path = frame_dir / f"take-{take['index']}-{label}.jpg"
            _extract_frame(Path(take["raw"]["path"]), frame_path, seconds)
            take_frames.append((label, seconds, frame_path))
        per_take_frames.append(take_frames)

    cell_width, cell_height, label_height = 270, 480, 30
    columns = len(per_take_frames)
    rows = 3
    sheet = Image.new("RGB", (columns * cell_width, rows * (cell_height + label_height)), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    frame_records = []
    for column, take_frames in enumerate(per_take_frames):
        for row, (label, seconds, frame_path) in enumerate(take_frames):
            with Image.open(frame_path) as source:
                fitted = ImageOps.fit(source.convert("RGB"), (cell_width, cell_height), method=Image.Resampling.LANCZOS)
            x = column * cell_width
            y = row * (cell_height + label_height)
            sheet.paste(fitted, (x, y + label_height))
            draw.text((x + 6, y + 8), f"TAKE {column} · {label} · {seconds:.2f}s", fill="black", font=font)
            frame_records.append({"take_index": column, "label": label, "seconds": round(seconds, 3), "path": str(frame_path)})
    contact_path = manifest_path.parent / "qa" / "contact-sheet.jpg"
    contact_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(contact_path, format="JPEG", quality=92)
    contact = {
        "path": str(contact_path),
        "sha256": _file_sha256(contact_path),
        "bytes": contact_path.stat().st_size,
        "frames": frame_records,
        "created_at": _utc_now(),
    }
    payload["contact_sheet"] = contact
    payload["status"] = "contact_sheet_ready"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return contact


@_manifest_locked
def run_visual_qa(
    manifest_path: Path,
    *,
    evaluator: Callable[..., Any] = evaluate_visual_consistency,
    llm_client: Optional[Any] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    master_path = Path(payload["approved_master"]["path"])
    if not master_path.is_file() or _file_sha256(master_path) != payload["approved_master"]["sha256"]:
        raise ValidationError("Pilot approved master changed after approval.")
    contact = payload.get("contact_sheet") or {}
    if not _artifact_matches(contact):
        raise ValidationError("Pilot contact sheet failed its recorded checksum; rebuild it before visual QA.")
    existing_report = payload.get("visual_qa") or {}
    if existing_report:
        if existing_report.get("passed"):
            return existing_report
        raise ValidationError(
            "Pilot visual QA failed.",
            {"blocking_reasons": list(existing_report.get("blocking_reasons") or [])},
        )
    contact_path = Path(contact.get("path") or "")
    report = evaluator(
        {"mime_type": payload["approved_master"]["mime_type"], "image_bytes": master_path.read_bytes()},
        {"mime_type": "image/jpeg", "image_bytes": contact_path.read_bytes()},
        llm_client=llm_client,
        model=model,
    )
    report_payload = asdict(report)
    payload["visual_qa"] = report_payload
    payload["status"] = "visual_qa_passed" if report.passed else "visual_qa_failed"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    if not report.passed:
        raise ValidationError("Pilot visual QA failed.", {"blocking_reasons": list(report.blocking_reasons)})
    return report_payload


def _extract_voice_clip(
    source: Path,
    destination: Path,
    *,
    start_seconds: float,
    end_seconds: float,
) -> None:
    """Extract one complete raw take as mono 16 kHz PCM WAV for audio QA."""
    duration_seconds = end_seconds - start_seconds
    if start_seconds < 0 or duration_seconds <= 0:
        raise ValidationError(
            "Voice QA trim window is invalid.",
            {"start_seconds": start_seconds, "end_seconds": end_seconds},
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-ss",
            f"{start_seconds:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size <= 0:
        raise ValidationError(
            "Voice QA audio extraction failed.",
            {"source": str(source), "error": result.stderr[-300:]},
        )


def _extract_seam_clip(
    source: Path,
    destination: Path,
    *,
    center_seconds: float,
    duration_seconds: float = 1.5,
) -> None:
    start_seconds = max(0.0, center_seconds - duration_seconds / 2.0)
    _extract_voice_clip(
        source,
        destination,
        start_seconds=start_seconds,
        end_seconds=start_seconds + duration_seconds,
    )


@_manifest_locked
def run_voice_qa(
    manifest_path: Path,
    *,
    evaluator: Callable[..., Any] = evaluate_voice_consistency,
    extract_audio_fn: Callable[..., None] = _extract_voice_clip,
    llm_client: Optional[Any] = None,
    model: Optional[str] = DEFAULT_VOICE_QA_MODEL,
) -> Dict[str, Any]:
    """Compare complete raw-take audio tracks, or record N=1 as not applicable."""
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    ordered = sorted(payload["takes"], key=lambda take: take["index"])
    voice_inputs = []
    for take in ordered:
        if not (take.get("transcript_qa") or {}).get("passed"):
            raise ValidationError(
                "Pilot voice QA requires transcript-passed takes.",
                {"take_index": take["index"]},
            )
        raw = take.get("raw") or {}
        if not _artifact_matches(raw):
            raise ValidationError(
                "Pilot voice QA requires checksum-verified raw takes.",
                {"take_index": take["index"]},
            )
        start_seconds = 0.0
        end_seconds = float(take["duration_seconds"])
        voice_inputs.append(
            {
                "take_index": take["index"],
                "raw_sha256": raw["sha256"],
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
            }
        )
    effective_model = str(model or DEFAULT_VOICE_QA_MODEL)
    input_sha256 = _canonical_sha256(
        {
            "model": effective_model,
            "rubric_version": VOICE_QA_RUBRIC_VERSION,
            "takes": voice_inputs,
        }
    )
    existing_report = payload.get("voice_qa") or {}
    existing_clips = existing_report.get("clips") or []
    if (
        existing_report
        and existing_report.get("input_sha256") == input_sha256
        and existing_report.get("model") == effective_model
        and existing_report.get("rubric_version") == VOICE_QA_RUBRIC_VERSION
        and len(existing_clips) == len(ordered)
        and all(_artifact_matches(clip) for clip in existing_clips)
    ):
        if existing_report.get("passed"):
            return existing_report
        raise ValidationError(
            "Pilot voice QA failed.",
            {"blocking_reasons": list(existing_report.get("blocking_reasons") or [])},
        )
    if existing_report:
        payload.pop("voice_qa", None)
        for key in (
            "stitch",
            "final_transcript",
            "final_transcript_qa",
            "seam_qa",
            "acoustic_seam_plan",
            "acoustic_seam_qa",
            "caption",
            "media_qa",
            "upload",
            "upload_verification",
        ):
            payload.pop(key, None)
    voice_dir = manifest_path.parent / "qa" / "voice"
    clips = []
    clip_records = []
    for take, voice_input in zip(ordered, voice_inputs):
        raw = take["raw"]
        start_seconds = voice_input["start_seconds"]
        end_seconds = voice_input["end_seconds"]
        destination = voice_dir / f"take-{take['index']}-attempt-{take['attempt']}.wav"
        extract_audio_fn(
            Path(raw["path"]),
            destination,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        if not destination.is_file() or destination.stat().st_size <= 0:
            raise ValidationError(
                "Voice QA extractor did not create a non-empty audio clip.",
                {"take_index": take["index"], "path": str(destination)},
            )
        audio_bytes = destination.read_bytes()
        clips.append({"mime_type": "audio/wav", "media_bytes": audio_bytes})
        clip_records.append(
            {
                "take_index": take["index"],
                "attempt": take["attempt"],
                "path": str(destination),
                "mime_type": "audio/wav",
                "sha256": sha256(audio_bytes).hexdigest(),
                "bytes": len(audio_bytes),
                "source_raw_sha256": raw["sha256"],
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
            }
        )
    report = evaluator(
        clips,
        llm_client=llm_client,
        model=effective_model,
    )
    report_payload = asdict(report)
    report_payload["clips"] = clip_records
    report_payload["input_sha256"] = input_sha256
    report_payload["model"] = effective_model
    report_payload["rubric_version"] = VOICE_QA_RUBRIC_VERSION
    report_payload["created_at"] = _utc_now()
    payload["voice_qa"] = report_payload
    payload["status"] = "voice_qa_passed" if report.passed else "voice_qa_failed"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    if not report.passed:
        raise ValidationError(
            "Pilot voice QA failed.",
            {
                "blocking_reasons": list(report.blocking_reasons),
                "outlier_take_indexes": list(report.outlier_take_indexes),
            },
        )
    return report_payload


def _probe_media(path: Path) -> Dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size,bit_rate,format_name:stream=index,codec_type,codec_name,width,height,"
            "r_frame_rate,sample_rate,channels",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return {"probe_error": result.stderr[-300:]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"probe_error": "ffprobe returned invalid JSON"}


def evaluate_final_media_probe(
    probe: Dict[str, Any],
    *,
    min_duration_seconds: float = 14.5,
    max_duration_seconds: float = 16.5,
) -> Dict[str, Any]:
    """Fail closed unless the captioned delivery satisfies the pilot media contract."""
    reasons = []
    if not isinstance(probe, dict) or probe.get("probe_error"):
        reasons.append("probe_failed")
        return {"passed": False, "failure_reasons": reasons, "probe": probe}
    streams = probe.get("streams") or []
    format_payload = probe.get("format") or {}
    format_names = {
        name.strip().lower()
        for name in str(format_payload.get("format_name") or "").split(",")
        if name.strip()
    }
    if "mp4" not in format_names:
        reasons.append("container_must_be_mp4")
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video_streams or video_streams[0].get("codec_name") != "h264":
        reasons.append("video_must_be_h264")
    if video_streams:
        width = int(video_streams[0].get("width") or 0)
        height = int(video_streams[0].get("height") or 0)
        if width <= 0 or height <= 0 or width * 16 != height * 9:
            reasons.append("video_must_be_9_16")
    if not audio_streams or audio_streams[0].get("codec_name") != "aac":
        reasons.append("audio_must_be_aac")
    try:
        duration = float(format_payload["duration"])
    except (KeyError, TypeError, ValueError):
        duration = math.nan
    frame_tolerance_seconds = 1.0 / 24.0
    if (
        not math.isfinite(duration)
        or duration < min_duration_seconds - frame_tolerance_seconds - 1e-9
        or duration > max_duration_seconds + frame_tolerance_seconds + 1e-9
    ):
        reasons.append("duration_out_of_range")
    return {
        "passed": not reasons,
        "failure_reasons": reasons,
        "duration_seconds": duration if math.isfinite(duration) else None,
        "min_duration_seconds": min_duration_seconds,
        "max_duration_seconds": max_duration_seconds,
        "video_stream": video_streams[0] if video_streams else None,
        "audio_stream": audio_streams[0] if audio_streams else None,
    }


def _evaluate_acoustic_plan_contract_details(
    acoustic_plan: Any,
    stitch_metadata: Dict[str, Any],
    *,
    fps: float,
) -> tuple[list[str], list[int]]:
    reasons = []
    failed_seam_indexes = set()
    all_seam_indexes = set(range(len(acoustic_plan.seams)))
    if acoustic_plan.active_speech_rms_range_db > 1.5:
        reasons.append("active_speech_rms_range_exceeded")
        failed_seam_indexes.update(all_seam_indexes)
    seam_rules = (
        ("audio_overlap_out_of_range", lambda seam: not 0.04 <= seam.overlap_seconds <= 0.07),
        ("word_gap_out_of_range", lambda seam: not 0.10 <= seam.final_word_gap_seconds <= 0.32),
        ("retained_breath_island_too_long", lambda seam: seam.retained_island_duration_seconds > 0.08),
        (
            "seam_energy_delta_exceeded",
            lambda seam: (
                seam.short_window_energy_delta_db > 6.0
                and (
                    not getattr(seam, "energy_fallback", False)
                    or seam.short_window_energy_delta_db
                    > MAX_PERCEPTUAL_SEAM_ENERGY_DELTA_DB
                )
            ),
        ),
        ("speech_overlap_detected", lambda seam: seam.speech_overlap),
    )
    for reason, failed in seam_rules:
        matching = [index for index, seam in enumerate(acoustic_plan.seams) if failed(seam)]
        if matching:
            reasons.append(reason)
            failed_seam_indexes.update(matching)
    if float(stitch_metadata.get("stitch_audio_video_duration_delta_s") or 0) > 1 / fps + 1e-6:
        reasons.append("audio_video_duration_drift")
        failed_seam_indexes.update(all_seam_indexes)
    return reasons, sorted(failed_seam_indexes)


def _evaluate_acoustic_plan_contract(
    acoustic_plan: Any,
    stitch_metadata: Dict[str, Any],
    *,
    fps: float,
) -> list[str]:
    reasons, _failed_seam_indexes = _evaluate_acoustic_plan_contract_details(
        acoustic_plan,
        stitch_metadata,
        fps=fps,
    )
    return reasons


def _acoustic_retry_map(
    failed_seam_indexes: Sequence[int],
    *,
    take_count: int,
) -> tuple[list[Dict[str, Any]], list[int]]:
    if take_count < 2:
        raise ValidationError("Acoustic seam retry mapping requires at least two takes.")
    normalized = []
    for seam_index in failed_seam_indexes:
        if isinstance(seam_index, bool) or not isinstance(seam_index, int):
            raise ValidationError("Acoustic failed seam indexes must be integers.")
        if not 0 <= seam_index < take_count - 1:
            raise ValidationError(
                "Acoustic failed seam index is outside the take range.",
                {"seam_index": seam_index, "take_count": take_count},
            )
        if seam_index not in normalized:
            normalized.append(seam_index)
    normalized.sort()
    mapping = [
        {
            "seam_index": seam_index,
            "adjacent_take_indexes": [seam_index, seam_index + 1],
        }
        for seam_index in normalized
    ]
    recommended = sorted(
        {
            take_index
            for item in mapping
            for take_index in item["adjacent_take_indexes"]
        }
    )
    return mapping, recommended


def _prepend_acoustic_preroll(
    previous_path: Path,
    incoming_path: Path,
    output_path: Path,
    *,
    bridge_start_seconds: float,
    padding_seconds: float,
) -> None:
    if padding_seconds <= 0 or bridge_start_seconds < 0:
        raise ValidationError("Acoustic room-tone bridge timing is invalid.")
    if not previous_path.is_file() or not incoming_path.is_file():
        raise ValidationError("Acoustic room-tone bridge requires existing media files.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")
    bridge_end_seconds = bridge_start_seconds + padding_seconds
    filter_graph = (
        f"[0:a]atrim=start={bridge_start_seconds:.6f}:end={bridge_end_seconds:.6f},"
        "asetpts=PTS-STARTPTS[bridge];"
        "[1:a]asetpts=PTS-STARTPTS[incominga];"
        "[bridge][incominga]concat=n=2:v=0:a=1[a];"
        f"[1:v]tpad=start_mode=clone:start_duration={padding_seconds:.6f},"
        "setpts=PTS-STARTPTS[v]"
    )
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(previous_path),
            "-i",
            str(incoming_path),
            "-filter_complex",
            filter_graph,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(temporary_path),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0 or not temporary_path.is_file() or temporary_path.stat().st_size <= 0:
        temporary_path.unlink(missing_ok=True)
        raise ValidationError(
            "Acoustic room-tone bridge failed.",
            {"error": str(result.stderr or "")[-400:]},
        )
    temporary_path.replace(output_path)


def _prepare_acoustic_segment_sources(
    ordered_takes: Sequence[Dict[str, Any]],
    output_root: Path,
    *,
    normalize_fn: Callable[..., None] = _prepend_acoustic_preroll,
) -> tuple[tuple[Path, ...], tuple[float, ...], list[Dict[str, Any]]]:
    sources: list[Path] = []
    timing_offsets: list[float] = []
    records: list[Dict[str, Any]] = []
    for position, take in enumerate(ordered_takes):
        raw_path = Path(take["raw"]["path"])
        first_word_start = float(take["transcript_qa"]["first_word_start_seconds"])
        if position == 0 or first_word_start >= 0.100 - 1e-9:
            sources.append(raw_path)
            timing_offsets.append(0.0)
            continue

        padding_seconds = round(max(0.120, 0.120 - first_word_start), 3)
        previous_take = ordered_takes[position - 1]
        previous_path = sources[position - 1]
        previous_offset = timing_offsets[position - 1]
        previous_final_word = (
            float(previous_take["transcript_qa"]["final_word_end_seconds"])
            + previous_offset
        )
        previous_duration = float(previous_take["duration_seconds"]) + previous_offset
        latest_bridge_start = previous_duration - padding_seconds
        bridge_start = min(previous_final_word + 0.100, latest_bridge_start)
        if bridge_start < previous_final_word - 1e-9:
            raise ValidationError(
                "Previous take has no transcript-safe room tone for an acoustic bridge.",
                {"take_index": take["index"], "source_take_index": previous_take["index"]},
            )
        output_path = output_root / "normalized" / f"take-{take['index']}-acoustic-preroll.mp4"
        normalize_fn(
            previous_path,
            raw_path,
            output_path,
            bridge_start_seconds=bridge_start,
            padding_seconds=padding_seconds,
        )
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise ValidationError(
                "Acoustic room-tone bridge did not create a media artifact.",
                {"take_index": take["index"]},
            )
        sources.append(output_path)
        timing_offsets.append(padding_seconds)
        records.append(
            {
                "take_index": take["index"],
                "source_take_index": previous_take["index"],
                "source_path": str(raw_path),
                "source_sha256": str(take["raw"]["sha256"]),
                "bridge_source_path": str(previous_path),
                "bridge_start_seconds": round(bridge_start, 6),
                "padding_seconds": padding_seconds,
                "path": str(output_path),
                "sha256": _file_sha256(output_path),
                "bytes": output_path.stat().st_size,
            }
        )
    return tuple(sources), tuple(timing_offsets), records


def _plan_acoustic_delivery(
    evidence: Sequence[TakeAudioEvidence],
    duration_contract: Dict[str, float],
    *,
    plan_fn: Callable[..., Any] = plan_acoustic_seams,
) -> tuple[Any, Optional[Dict[str, Any]]]:
    minimum = float(duration_contract["minimum"])
    maximum = float(duration_contract["maximum"])
    requested = float(duration_contract["requested"])
    plan_options = {}
    if requested >= 40.0:
        plan_options["min_post_word_crossfade_guard_seconds"] = 0.060
    try:
        return (
            plan_fn(
                evidence,
                min_duration_seconds=minimum,
                max_duration_seconds=maximum,
                **plan_options,
            ),
            None,
        )
    except ValidationError as exc:
        effective_minimum = round(requested * 0.9, 3)
        effective_maximum = round(max(maximum, requested + 1.0), 3)
        is_long_form_duration_failure = (
            requested >= 24.0
            and "duration envelope" in exc.message.lower()
            and effective_minimum < minimum - 1e-9
        )
        if not is_long_form_duration_failure:
            raise
        plan = plan_fn(
            evidence,
            min_duration_seconds=effective_minimum,
            max_duration_seconds=effective_maximum,
            **plan_options,
        )
        return plan, {
            "source": "long_form_acoustic_cadence_floor",
            "requested_seconds": requested,
            "approved_minimum_seconds": minimum,
            "effective_minimum_seconds": effective_minimum,
            "approved_maximum_seconds": maximum,
            "effective_maximum_seconds": effective_maximum,
            "post_word_crossfade_guard_seconds": plan_options.get(
                "min_post_word_crossfade_guard_seconds",
                0.100,
            ),
        }


def _accept_final_transcript_consensus(
    final_qa: Dict[str, Any],
    ordered_takes: Sequence[Dict[str, Any]],
    *,
    acoustic_plan: Optional[Any],
    requested_duration_seconds: float,
) -> bool:
    expected_words = final_qa.get("expected_words") or []
    actual_words = final_qa.get("actual_words") or []
    try:
        word_error_rate = float(final_qa.get("word_error_rate"))
    except (TypeError, ValueError):
        return False
    passed_take_consensus = True
    for take in ordered_takes:
        take_qa = take.get("transcript_qa") or {}
        try:
            take_word_error_rate = float(take_qa["word_error_rate"])
        except (KeyError, TypeError, ValueError):
            passed_take_consensus = False
            break
        if not take_qa.get("passed") or take_word_error_rate < 0.0:
            passed_take_consensus = False
            break
    return bool(
        not final_qa.get("passed")
        and requested_duration_seconds >= 24.0
        and acoustic_plan is not None
        and passed_take_consensus
        and expected_words
        and len(expected_words) == len(actual_words)
        and 0.0 < word_error_rate <= 0.02
        and final_qa.get("first_word_present")
        and final_qa.get("last_word_present")
        and not final_qa.get("foreign_words")
    )


@_manifest_locked
def compose_and_caption(
    manifest_path: Path,
    deepgram_client: Any,
    *,
    stitch_fn: Callable[..., Any] = stitch_segments,
    caption_fn: Callable[..., str] = burn_captions,
    probe_fn: Callable[[Path], Dict[str, Any]] = _probe_media,
    acoustic_seams: bool = False,
    analyze_audio_fn: Callable[..., Any] = analyze_audio_frames,
    plan_acoustic_fn: Callable[..., Any] = plan_acoustic_seams,
    normalize_preroll_fn: Callable[..., None] = _prepend_acoustic_preroll,
    extract_seam_audio_fn: Callable[..., None] = _extract_seam_clip,
    acoustic_evaluator: Callable[..., Any] = evaluate_acoustic_seam_continuity,
    acoustic_llm_client: Optional[Any] = None,
    acoustic_model: Optional[str] = DEFAULT_ACOUSTIC_QA_MODEL,
) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    duration_contract = _validate_duration_planning_contract(payload)
    minimum_duration = float(duration_contract["minimum"])
    maximum_duration = float(duration_contract["maximum"])
    existing_resolution = payload.get("delivery_resolution") or {}
    if (
        acoustic_seams
        and existing_resolution.get("source") == "long_form_acoustic_cadence_floor"
        and float(existing_resolution.get("requested_seconds") or 0.0)
        == float(duration_contract["requested"])
    ):
        minimum_duration = float(existing_resolution["effective_minimum_seconds"])
        maximum_duration = float(
            existing_resolution.get("effective_maximum_seconds", maximum_duration)
        )
    if not (payload.get("visual_qa") or {}).get("passed"):
        raise ValidationError("Composition requires a passed visual QA gate.")
    if not (payload.get("voice_qa") or {}).get("passed"):
        raise ValidationError("Composition requires a passed voice QA gate.")
    existing_caption = payload.get("caption") or {}
    cached_delivery_invalid = bool(existing_caption)
    if existing_caption and _artifact_matches(existing_caption, path_key="captioned_path"):
        captioned_path = Path(existing_caption["captioned_path"])
        fresh_probe = probe_fn(captioned_path)
        media_qa = evaluate_final_media_probe(
            fresh_probe,
            min_duration_seconds=minimum_duration,
            max_duration_seconds=maximum_duration,
        )
        payload["media_qa"] = media_qa
        payload["caption"]["probe"] = fresh_probe
        payload["updated_at"] = _utc_now()
        _atomic_write_json(manifest_path, payload)
        acoustic_ready = not acoustic_seams or (payload.get("acoustic_seam_qa") or {}).get("passed")
        if (payload.get("seam_qa") or {}).get("passed") and acoustic_ready and media_qa["passed"]:
            return existing_caption
    if cached_delivery_invalid:
        invalidate_composition(
            manifest_path,
            reason="automatic rebuild of invalid cached caption delivery",
        )
        payload = _load_manifest(manifest_path)
    ordered = sorted(payload["takes"], key=lambda take: take["index"])
    if any(not (take.get("transcript_qa") or {}).get("passed") or not take.get("trim_window") for take in ordered):
        raise ValidationError("Composition requires transcript-passed takes and trim windows.")
    invalid_timing_indexes = [
        take["index"]
        for take in ordered
        if (take.get("trim_window") or {}).get("source") != "deepgram_word_window"
        or (take.get("transcript_qa") or {}).get("first_word_start_seconds") is None
    ]
    if invalid_timing_indexes:
        raise ValidationError(
            "Composition requires current Deepgram speech windows; rerun transcript migration first.",
            {"take_indexes": invalid_timing_indexes},
        )
    if any(not _artifact_matches(take.get("raw") or {}) for take in ordered):
        raise ValidationError("Composition requires checksum-verified raw takes.")
    segment_paths = tuple(Path(take["raw"]["path"]) for take in ordered)
    timing_offsets = tuple(0.0 for _take in ordered)
    if acoustic_seams:
        segment_paths, timing_offsets, normalization_records = _prepare_acoustic_segment_sources(
            ordered,
            manifest_path.parent,
            normalize_fn=normalize_preroll_fn,
        )
        payload["acoustic_preroll_normalization"] = normalization_records
        payload.pop("acoustic_plan_failure", None)
        _atomic_write_json(manifest_path, payload)
    segment_videos = [path.read_bytes() for path in segment_paths]
    trim_windows = [take["trim_window"] for take in ordered]
    acoustic_plan = None
    if acoustic_seams:
        evidence = tuple(
            TakeAudioEvidence(
                take_index=take["index"],
                provider_duration_seconds=float(take["duration_seconds"]) + timing_offsets[position],
                first_word_start_seconds=(
                    float(take["transcript_qa"]["first_word_start_seconds"])
                    + timing_offsets[position]
                ),
                final_word_end_seconds=(
                    float(take["transcript_qa"]["final_word_end_seconds"])
                    + timing_offsets[position]
                ),
                frames=tuple(analyze_audio_fn(segment_paths[position])),
            )
            for position, take in enumerate(ordered)
        )
        try:
            acoustic_plan, delivery_resolution = _plan_acoustic_delivery(
                evidence,
                duration_contract,
                plan_fn=plan_acoustic_fn,
            )
            if delivery_resolution is not None:
                payload["delivery_resolution"] = delivery_resolution
                minimum_duration = float(delivery_resolution["effective_minimum_seconds"])
                maximum_duration = float(delivery_resolution["effective_maximum_seconds"])
            else:
                payload.pop("delivery_resolution", None)
        except ValidationError as exc:
            duration_failure = "duration envelope" in exc.message.lower()
            raw_seam_index = (exc.details or {}).get("seam_index")
            localized_seam_failure = (
                not isinstance(raw_seam_index, bool)
                and isinstance(raw_seam_index, int)
            )
            if not duration_failure and not localized_seam_failure:
                raise
            available_take_indexes = {int(take["index"]) for take in ordered}
            failed_seam_indexes = []
            seam_retry_map = []
            if localized_seam_failure:
                failed_seam_indexes = [int(raw_seam_index)]
                seam_retry_map, recommended_retry_take_indexes = _acoustic_retry_map(
                    failed_seam_indexes,
                    take_count=len(ordered),
                )
            else:
                diagnostic_indexes = (exc.details or {}).get("under_capacity_take_indexes") or []
                recommended_retry_take_indexes = sorted(
                    {
                        int(index)
                        for index in diagnostic_indexes
                        if not isinstance(index, bool)
                        and isinstance(index, int)
                        and int(index) in available_take_indexes
                    }
                )
                if not recommended_retry_take_indexes:
                    recommended_retry_take_indexes = [int(ordered[-1]["index"])]
            payload["acoustic_plan_failure"] = {
                "message": exc.message,
                "details": exc.details,
                "failed_seam_indexes": failed_seam_indexes,
                "seam_retry_map": seam_retry_map,
                "recommended_retry_take_indexes": recommended_retry_take_indexes,
                "created_at": _utc_now(),
            }
            payload["status"] = "acoustic_plan_failed"
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            raise
        payload["acoustic_seam_plan"] = asdict(acoustic_plan)
        _atomic_write_json(manifest_path, payload)
    stitched_bytes, stitch_metadata = stitch_fn(
        segment_videos=segment_videos,
        post_id=payload["run_id"],
        correlation_id=f"semantic_ugc_{payload['run_id']}_stitch",
        trim_windows=trim_windows,
        acoustic_plan=asdict(acoustic_plan) if acoustic_plan is not None else None,
    )
    stitched_path = manifest_path.parent / "stitched.mp4"
    stitched_path.write_bytes(stitched_bytes)
    payload["stitch"] = {
        "path": str(stitched_path),
        "sha256": sha256(stitched_bytes).hexdigest(),
        "metadata": stitch_metadata,
        "probe": _probe_media(stitched_path),
    }
    _atomic_write_json(manifest_path, payload)

    final_transcript = deepgram_client.transcribe(
        audio_bytes=stitched_bytes,
        correlation_id=f"semantic_ugc_{payload['run_id']}_final_transcript",
    )
    script = payload["script"]["text"]
    expected = EditorialBeat(
        index=0,
        text=script,
        word_count=len(script.split()),
        estimated_speech_seconds=0.0,
        provider_duration_seconds=4,
    )
    final_qa = evaluate_take_transcript(
        expected,
        final_transcript,
        other_beats=[],
        max_wer=0.0,
    )
    payload["final_transcript"] = _serialize_transcript(final_transcript)
    final_qa_payload = asdict(final_qa)
    consensus_passed = _accept_final_transcript_consensus(
        final_qa_payload,
        ordered,
        acoustic_plan=acoustic_plan,
        requested_duration_seconds=float(duration_contract["requested"]),
    )
    if consensus_passed:
        final_qa_payload["provider_passed"] = False
        final_qa_payload["provider_failure_reasons"] = list(final_qa.failure_reasons)
        final_qa_payload["passed"] = True
        final_qa_payload["failure_reasons"] = []
        final_qa_payload["accepted_by"] = "exact_take_transcripts_plus_speech_safe_acoustic_plan"
    payload["final_transcript_qa"] = final_qa_payload
    _atomic_write_json(manifest_path, payload)
    if not final_qa_payload["passed"]:
        payload["status"] = "final_transcript_failed"
        _atomic_write_json(manifest_path, payload)
        raise ValidationError("Final stitched transcript QA failed.", {"reasons": list(final_qa.failure_reasons)})

    if len(ordered) == 1:
        seam_qa = {
            "status": "not_applicable",
            "passed": True,
            "gaps_seconds": [],
        }
    else:
        seam_qa = evaluate_seam_gaps(
            final_transcript,
            beat_word_counts=[take["beat"]["word_count"] for take in ordered],
            max_gap_seconds=0.6,
        )
    payload["seam_qa"] = seam_qa
    _atomic_write_json(manifest_path, payload)
    if not seam_qa["passed"]:
        payload["status"] = "seam_qa_failed"
        _atomic_write_json(manifest_path, payload)
        raise ValidationError(
            "Final stitched seam-gap QA failed.",
            {"gaps_seconds": seam_qa["gaps_seconds"]},
        )

    if acoustic_plan is not None:
        video_durations = [
            take.video_end_seconds - take.video_start_seconds for take in acoustic_plan.takes
        ]
        cut_times = []
        elapsed = 0.0
        for duration in video_durations[:-1]:
            elapsed += duration
            cut_times.append(elapsed)
        qa_dir = manifest_path.parent / "qa" / "acoustic"
        clips = []
        clip_records = []
        for seam_index, center_seconds in enumerate(cut_times):
            destination = qa_dir / f"seam-{seam_index}.wav"
            extract_seam_audio_fn(
                stitched_path,
                destination,
                center_seconds=center_seconds,
                duration_seconds=1.5,
            )
            audio_bytes = destination.read_bytes()
            clips.append({"mime_type": "audio/wav", "media_bytes": audio_bytes})
            clip_records.append(
                {
                    "seam_index": seam_index,
                    "center_seconds": round(center_seconds, 6),
                    "path": str(destination),
                    "mime_type": "audio/wav",
                    "sha256": sha256(audio_bytes).hexdigest(),
                    "bytes": len(audio_bytes),
                }
            )
        deterministic_reasons, deterministic_failed_seam_indexes = _evaluate_acoustic_plan_contract_details(
            acoustic_plan,
            stitch_metadata,
            fps=float(stitch_metadata.get("stitch_fps") or 24.0),
        )
        report = acoustic_evaluator(
            clips,
            llm_client=acoustic_llm_client,
            model=str(acoustic_model or DEFAULT_ACOUSTIC_QA_MODEL),
        )
        report_payload = asdict(report)
        report_payload["clips"] = clip_records
        report_payload["deterministic_passed"] = not deterministic_reasons
        report_payload["deterministic_failure_reasons"] = deterministic_reasons
        report_payload["model"] = str(acoustic_model or DEFAULT_ACOUSTIC_QA_MODEL)
        report_payload["rubric_version"] = ACOUSTIC_QA_RUBRIC_VERSION
        report_payload["passed"] = bool(report.passed and not deterministic_reasons)
        qualitative_failed_seam_indexes = [
            int(verdict.seam_index)
            for verdict in (getattr(report, "seam_verdicts", ()) or ())
            if not verdict.passed
        ]
        if not report.passed and not qualitative_failed_seam_indexes:
            qualitative_failed_seam_indexes = list(range(len(clips)))
        failed_seam_indexes = sorted(
            set(deterministic_failed_seam_indexes) | set(qualitative_failed_seam_indexes)
        )
        seam_retry_map, recommended_retry_take_indexes = _acoustic_retry_map(
            failed_seam_indexes,
            take_count=len(ordered),
        )
        report_payload["failed_seam_indexes"] = failed_seam_indexes
        report_payload["seam_retry_map"] = seam_retry_map
        report_payload["recommended_retry_take_indexes"] = recommended_retry_take_indexes
        payload["acoustic_seam_qa"] = report_payload
        _atomic_write_json(manifest_path, payload)
        if not report_payload["passed"]:
            payload["status"] = "acoustic_seam_qa_failed"
            _atomic_write_json(manifest_path, payload)
            raise ValidationError(
                "Final stitched acoustic seam QA failed.",
                {"reasons": deterministic_reasons + list(report.blocking_reasons)},
            )

    aligned = align_transcript_to_script(transcript=final_transcript, script=script)
    rendered_path = Path(
        caption_fn(
            video_path=str(stitched_path),
            transcript=aligned,
            correlation_id=f"semantic_ugc_{payload['run_id']}_captions",
        )
    )
    captioned_path = manifest_path.parent / "final-captioned.mp4"
    captioned_bytes = rendered_path.read_bytes()
    captioned_path.write_bytes(captioned_bytes)
    caption_probe = probe_fn(captioned_path)
    media_qa = evaluate_final_media_probe(
        caption_probe,
        min_duration_seconds=minimum_duration,
        max_duration_seconds=maximum_duration,
    )
    payload["caption"] = {
        "captioned_path": str(captioned_path),
        "sha256": sha256(captioned_bytes).hexdigest(),
        "bytes": len(captioned_bytes),
        "word_count": len(aligned.words),
        "aligned_transcript": _serialize_transcript(aligned),
        "probe": caption_probe,
        "created_at": _utc_now(),
    }
    payload["media_qa"] = media_qa
    if not media_qa["passed"]:
        payload["status"] = "media_qa_failed"
        payload["updated_at"] = _utc_now()
        _atomic_write_json(manifest_path, payload)
        raise ValidationError(
            "Captioned final media failed delivery QA.",
            {"reasons": media_qa["failure_reasons"]},
        )
    payload["status"] = "captioned"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return payload["caption"]


@_manifest_locked
def upload_final(manifest_path: Path, storage_client: Optional[Any] = None) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    _validate_duration_planning_contract(payload)
    caption = payload.get("caption") or {}
    if not _artifact_matches(caption, path_key="captioned_path"):
        raise ValidationError("Captioned pilot artifact failed its recorded checksum before upload.")
    if not (payload.get("seam_qa") or {}).get("passed"):
        raise ValidationError("Captioned pilot has not passed seam-gap QA before upload.")
    if not (payload.get("media_qa") or {}).get("passed"):
        raise ValidationError("Captioned pilot has not passed final media QA before upload.")
    if not (payload.get("voice_qa") or {}).get("passed"):
        raise ValidationError("Captioned pilot has not passed voice QA before upload.")
    if payload.get("acoustic_seam_plan") and not (payload.get("acoustic_seam_qa") or {}).get("passed"):
        raise ValidationError("Captioned pilot has not passed acoustic seam QA before upload.")
    captioned_path = Path(caption.get("captioned_path") or "")
    if not captioned_path.is_file():
        raise ValidationError("Captioned pilot video is missing before upload.")
    storage = storage_client or get_storage_client()
    suffix_parts = []
    if (payload.get("script") or {}).get("planning_profile") == PLANNING_PROFILE:
        suffix_parts.append("minimum-shots")
    if payload.get("acoustic_seam_plan"):
        suffix_parts.append("acoustic-preview")
    suffix_parts.append("captioned")
    suffix = "-".join(suffix_parts)
    file_name = f"semantic-ugc-{payload['run_id']}-{suffix}.mp4"
    intent = payload.get("upload_intent") or {}
    result = payload.get("upload") or {}
    if not intent:
        if result:
            intent = {
                **result,
                "state": "legacy_receipt_recovered",
                "created_at": _utc_now(),
            }
        else:
            preparer = getattr(storage, "prepare_video_upload", None)
            if not callable(preparer):
                raise ValidationError(
                    "Storage adapter cannot persist a deterministic upload intent."
                )
            prepared = preparer(
                file_name=file_name,
                expected_size=int(caption["bytes"]),
                expected_sha256=str(caption["sha256"]),
            )
            if not isinstance(prepared, dict):
                raise ValidationError("Storage adapter returned an invalid upload intent.")
            intent = {
                **prepared,
                "state": "prepared",
                "created_at": _utc_now(),
            }
        if (
            not intent.get("storage_key")
            or int(intent.get("size") or 0) != int(caption["bytes"])
            or str(intent.get("sha256") or "") != str(caption["sha256"])
            or str(intent.get("file_type") or "") != "video/mp4"
        ):
            raise ValidationError("Storage upload intent does not match the captioned artifact.")
        payload["upload_intent"] = intent
        payload["status"] = "upload_intent_persisted"
        payload["updated_at"] = _utc_now()
        _atomic_write_json(manifest_path, payload)
    elif (
        int(intent.get("size") or 0) != int(caption["bytes"])
        or str(intent.get("sha256") or "") != str(caption["sha256"])
    ):
        raise ValidationError("Persisted upload intent does not match the captioned artifact.")

    verifier = getattr(storage, "verify_video_upload", None)
    if not callable(verifier):
        raise ValidationError("Storage adapter cannot reconcile or verify the upload intent.")

    def verify_remote() -> Dict[str, Any]:
        try:
            verification_result = verifier(
                storage_key=str(intent["storage_key"]),
                expected_size=int(caption["bytes"]),
                expected_sha256=str(caption["sha256"]),
            )
        except Exception as exc:  # noqa: BLE001
            verification_result = {
                "passed": False,
                "failure_reasons": ["storage_verifier_failed"],
                "error": str(exc)[:300],
            }
        if not isinstance(verification_result, dict):
            verification_result = {
                "passed": False,
                "failure_reasons": ["storage_verifier_invalid_response"],
            }
        return verification_result

    if not result:
        reconciliation = verify_remote()
        payload["upload_verification"] = reconciliation
        if reconciliation.get("passed"):
            result = {
                key: intent.get(key)
                for key in (
                    "storage_provider",
                    "storage_key",
                    "url",
                    "thumbnail_url",
                    "file_path",
                    "size",
                    "sha256",
                    "file_type",
                )
            }
            payload["upload"] = result
            intent["state"] = "reconciled_existing_object"
            intent["reconciled_at"] = _utc_now()
            payload["upload_intent"] = intent
        elif set(reconciliation.get("failure_reasons") or []) == {"not_found"}:
            payload["status"] = "upload_ready"
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            result = storage.upload_video(
                video_bytes=captioned_path.read_bytes(),
                file_name=file_name,
                correlation_id=f"semantic_ugc_{payload['run_id']}_upload",
                object_key=str(intent["storage_key"]),
            )
            payload["upload"] = result
            intent["state"] = "receipt_recorded"
            intent["receipt_recorded_at"] = _utc_now()
            payload["upload_intent"] = intent
            payload["status"] = "upload_verification_pending"
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
        else:
            payload["status"] = "upload_reconciliation_failed"
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            raise ValidationError(
                "Captioned pilot upload intent could not be safely reconciled.",
                {"reasons": list(reconciliation.get("failure_reasons") or [])},
            )

    if (
        str(result.get("storage_key") or "") != str(intent["storage_key"])
        or int(result.get("size") or 0) != int(caption["bytes"])
        or str(result.get("sha256") or "") != str(caption["sha256"])
    ):
        payload["upload"] = result
        payload["status"] = "upload_receipt_invalid"
        payload["updated_at"] = _utc_now()
        _atomic_write_json(manifest_path, payload)
        raise ValidationError("Storage upload receipt does not match the persisted intent.")

    verification = verify_remote()
    if not isinstance(verification, dict):
        verification = {
            "passed": False,
            "failure_reasons": ["storage_verifier_invalid_response"],
        }
    payload["upload_verification"] = verification
    if not verification.get("passed"):
        payload["status"] = "upload_verification_failed"
        payload["updated_at"] = _utc_now()
        _atomic_write_json(manifest_path, payload)
        raise ValidationError(
            "Captioned pilot remote verification failed after upload.",
            {"reasons": list(verification.get("failure_reasons") or [])},
        )
    payload["status"] = "uploaded"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return result


@_manifest_locked
def invalidate_composition(manifest_path: Path, *, reason: str) -> Dict[str, Any]:
    """Archive delivery metadata and rebuild from checksum-verified passed raw takes."""
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    if not str(reason or "").strip():
        raise ValidationError("Composition invalidation requires an operator reason.")
    if not (payload.get("visual_qa") or {}).get("passed"):
        raise ValidationError("Composition invalidation requires passed visual QA.")
    if any(not (take.get("transcript_qa") or {}).get("passed") for take in payload["takes"]):
        raise ValidationError("Composition invalidation requires transcript-passed takes.")
    caption = payload.get("caption") or {}
    history = list(payload.get("composition_history") or [])
    snapshot_keys = (
        "status",
        "stitch",
        "final_transcript",
        "final_transcript_qa",
        "seam_qa",
        "acoustic_seam_plan",
        "acoustic_seam_qa",
        "caption",
        "media_qa",
        "upload_intent",
        "upload",
        "upload_verification",
        "updated_at",
    )
    snapshot = {
        key: json.loads(json.dumps(payload[key]))
        for key in snapshot_keys
        if key in payload
    }
    archive_dir = manifest_path.parent / "history" / f"delivery-{len(history) + 1:03d}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_artifacts = {}
    for label, record, path_key in (
        ("stitch", payload.get("stitch") or {}, "path"),
        ("caption", caption, "captioned_path"),
    ):
        source_value = record.get(path_key)
        source = Path(source_value) if source_value else None
        if source is None or not source.is_file():
            continue
        destination = archive_dir / source.name
        shutil.copy2(source, destination)
        archived_artifacts[label] = {
            "path": str(destination),
            "sha256": _file_sha256(destination),
            "bytes": destination.stat().st_size,
            "source_path": str(source),
            "recorded_sha256": record.get("sha256"),
        }
    history.append(
        {
            "reason": " ".join(str(reason).split()),
            "stitch_sha256": (payload.get("stitch") or {}).get("sha256"),
            "caption_sha256": caption.get("sha256"),
            "upload_url": (payload.get("upload") or {}).get("url"),
            "snapshot": snapshot,
            "artifacts": archived_artifacts,
            "archived_at": _utc_now(),
        }
    )
    payload["composition_history"] = history
    for key in (
        "stitch",
        "final_transcript",
        "final_transcript_qa",
        "seam_qa",
        "acoustic_seam_plan",
        "acoustic_seam_qa",
        "caption",
        "media_qa",
        "upload_intent",
        "upload",
        "upload_verification",
    ):
        payload.pop(key, None)
    payload["status"] = "recompose_planned"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return payload


@_manifest_locked
def repair_failed_seam_windows(
    manifest_path: Path,
    *,
    reason: str,
    target_gap_seconds: float = 0.45,
    minimum_context_seconds: float = 0.08,
) -> Dict[str, Any]:
    """Tighten only measured failed cuts and move removed pause time to the outro."""
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    reason_text = " ".join(str(reason or "").split())
    if not reason_text:
        raise ValidationError("Seam repair requires an operator reason.")
    try:
        target_gap = float(target_gap_seconds)
        minimum_context = float(minimum_context_seconds)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Seam repair timing values must be finite numbers.") from exc
    if (
        not math.isfinite(target_gap)
        or not 0 <= target_gap < 0.6
        or not math.isfinite(minimum_context)
        or minimum_context < 0
    ):
        raise ValidationError(
            "Seam repair requires a target below 0.6 seconds and non-negative context."
        )
    seam_report = payload.get("seam_qa") or {}
    failed_indexes = list(seam_report.get("failed_seam_indexes") or [])
    gaps = list(seam_report.get("gaps_seconds") or [])
    if seam_report.get("passed") is not False or not failed_indexes:
        raise ValidationError("Seam repair requires a persisted failed seam QA report.")
    if not (payload.get("final_transcript_qa") or {}).get("passed"):
        raise ValidationError("Seam repair requires exact final transcript QA to have passed.")
    ordered = sorted(payload["takes"], key=lambda take: take["index"])
    if len(gaps) != len(ordered) - 1:
        raise ValidationError("Seam repair report does not match the semantic take count.")
    old_windows = [json.loads(json.dumps(take.get("trim_window") or {})) for take in ordered]
    new_windows = [json.loads(json.dumps(window)) for window in old_windows]
    repairs = []
    total_removed = 0.0
    for seam_index in failed_indexes:
        if not isinstance(seam_index, int) or not 0 <= seam_index < len(ordered) - 1:
            raise ValidationError("Seam repair contains an invalid failed seam index.")
        observed_gap = float(gaps[seam_index])
        required_reduction = max(0.0, observed_gap - target_gap)
        previous_take = ordered[seam_index]
        next_take = ordered[seam_index + 1]
        previous_qa = previous_take.get("transcript_qa") or {}
        next_qa = next_take.get("transcript_qa") or {}
        previous_final_end = float(previous_qa.get("final_word_end_seconds"))
        next_first_start = float(next_qa.get("first_word_start_seconds"))
        previous_tail = max(
            0.0,
            float(new_windows[seam_index]["end_seconds"]) - previous_final_end,
        )
        next_head = max(
            0.0,
            next_first_start - float(new_windows[seam_index + 1]["start_seconds"]),
        )
        reducible_head = max(0.0, next_head - minimum_context)
        head_reduction = min(required_reduction, reducible_head)
        remaining = required_reduction - head_reduction
        reducible_tail = max(0.0, previous_tail - minimum_context)
        tail_reduction = min(remaining, reducible_tail)
        remaining -= tail_reduction
        if remaining > 1e-6:
            raise ValidationError(
                "Failed seam cannot be tightened without violating spoken-word context.",
                {
                    "seam_index": seam_index,
                    "required_reduction_seconds": required_reduction,
                    "available_reduction_seconds": reducible_head + reducible_tail,
                },
            )
        new_windows[seam_index + 1]["start_seconds"] = (
            float(new_windows[seam_index + 1]["start_seconds"]) + head_reduction
        )
        new_windows[seam_index]["end_seconds"] = (
            float(new_windows[seam_index]["end_seconds"]) - tail_reduction
        )
        removed = head_reduction + tail_reduction
        total_removed += removed
        repairs.append(
            {
                "seam_index": seam_index,
                "observed_gap_seconds": observed_gap,
                "head_reduction_seconds": head_reduction,
                "tail_reduction_seconds": tail_reduction,
                "total_reduction_seconds": removed,
            }
        )
    final_window = new_windows[-1]
    final_take = ordered[-1]
    compensation_capacity = float(final_take["duration_seconds"]) - float(final_window["end_seconds"])
    if compensation_capacity + 1e-6 < total_removed:
        raise ValidationError(
            "Seam repair cannot preserve final duration inside the last provider take.",
            {
                "required_seconds": total_removed,
                "available_seconds": compensation_capacity,
            },
        )
    final_window["end_seconds"] = float(final_window["end_seconds"]) + total_removed
    for index, window in enumerate(new_windows):
        if (
            window.get("source") != "deepgram_word_window"
            or float(window["start_seconds"]) < 0
            or float(window["end_seconds"]) <= float(window["start_seconds"])
            or float(window["end_seconds"]) > float(ordered[index]["duration_seconds"]) + 1e-6
        ):
            raise ValidationError(
                "Seam repair produced an invalid trim window.",
                {"take_index": ordered[index]["index"], "trim_window": window},
            )

    invalidate_composition(manifest_path, reason=reason_text)
    payload = _load_manifest(manifest_path)
    ordered_payload = sorted(payload["takes"], key=lambda take: take["index"])
    for take, window in zip(ordered_payload, new_windows):
        take["trim_window"] = window
    repair_history = list(payload.get("seam_repair_history") or [])
    repair_history.append(
        {
            "reason": reason_text,
            "failed_seam_indexes": failed_indexes,
            "observed_gaps_seconds": gaps,
            "target_gap_seconds": target_gap,
            "minimum_context_seconds": minimum_context,
            "duration_compensation_seconds": total_removed,
            "old_trim_windows": old_windows,
            "new_trim_windows": new_windows,
            "repairs": repairs,
            "archived_delivery_index": len(payload.get("composition_history") or []),
            "repaired_at": _utc_now(),
        }
    )
    payload["seam_repair_history"] = repair_history
    payload["status"] = "seam_repair_planned"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return payload


@_manifest_locked
def reconcile_unknown_submission(
    manifest_path: Path,
    *,
    index: int,
    resolution: str,
    evidence: str,
    operation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve an ambiguous paid boundary without permitting a guessed resubmission."""
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    matches = [take for take in payload["takes"] if take["index"] == index]
    if len(matches) != 1:
        raise ValidationError("Unknown-submission take index does not exist.", {"take_index": index})
    take = matches[0]
    submission = take.get("submission") or {}
    if take.get("status") != "submission_unknown" or submission.get("state") != "unknown":
        raise ValidationError(
            "Submission reconciliation requires one unresolved unknown take.",
            {"take_index": index, "take_status": take.get("status")},
        )
    resolved = str(resolution or "").strip().lower()
    proof = " ".join(str(evidence or "").split())
    recovered_operation_id = str(operation_id or "").strip()
    if resolved not in {"accepted", "not_accepted"}:
        raise ValidationError("Unknown submission resolution must be accepted or not_accepted.")
    if len(proof) < 20:
        raise ValidationError("Unknown submission reconciliation requires concrete provider evidence.")
    if resolved == "accepted" and not recovered_operation_id:
        raise ValidationError("Accepted reconciliation requires the recovered provider operation id.")
    if resolved == "not_accepted" and recovered_operation_id:
        raise ValidationError("Not-accepted reconciliation must not include an operation id.")

    reconciled_at = _utc_now()
    submission["reconciliation"] = {
        "resolution": resolved,
        "evidence": proof,
        "operation_id": recovered_operation_id or None,
        "reconciled_at": reconciled_at,
    }
    if resolved == "accepted":
        submission.update(
            {
                "state": "accepted",
                "operation_id": recovered_operation_id,
                "accepted_at": reconciled_at,
            }
        )
        take["operation"] = {
            "operation_id": recovered_operation_id,
            "provider_model": take["model"],
            "status": "submitted",
            "submitted_at": reconciled_at,
            "reconciled": True,
        }
        take["status"] = "submitted"
        payload["status"] = "submitted"
    else:
        submission["state"] = "rejected"
        submission["rejected_at"] = reconciled_at
        take["status"] = "submission_rejected"
        payload["status"] = "submission_rejected"
    take["submission"] = submission
    payload["updated_at"] = reconciled_at
    _atomic_write_json(manifest_path, payload)
    return payload


@_manifest_locked
def reset_failed_take(
    manifest_path: Path,
    *,
    index: int,
    reason: str,
    retry_guidance: Optional[str] = None,
) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    matches = [take for take in payload["takes"] if take["index"] == index]
    if len(matches) != 1:
        raise ValidationError("Retry take index does not exist.", {"take_index": index})
    take = matches[0]
    take_status = str(take.get("status") or "")
    if take_status == "submission_unknown":
        raise ValidationError(
            "Unknown paid submission must be reconciled before any retry.",
            {"take_index": index},
        )
    terminal_take_failure = take_status in {
        "submission_rejected",
        "failed",
        "transcript_failed",
        "visual_failed",
        "voice_failed",
    }
    failed_visual_gate = (
        take_status == "transcribed" and (payload.get("visual_qa") or {}).get("passed") is False
    )
    failed_final_transcript = (
        take_status == "transcribed"
        and payload.get("status") == "final_transcript_failed"
        and (payload.get("final_transcript_qa") or {}).get("passed") is False
    )
    acoustic_plan_failure = payload.get("acoustic_plan_failure") or {}
    failed_acoustic_plan = (
        take_status == "transcribed"
        and payload.get("status") == "acoustic_plan_failed"
        and index in (acoustic_plan_failure.get("recommended_retry_take_indexes") or [])
    )
    acoustic_seam_report = payload.get("acoustic_seam_qa") or {}
    failed_acoustic_seam_qa = (
        take_status == "transcribed"
        and payload.get("status") == "acoustic_seam_qa_failed"
        and acoustic_seam_report.get("passed") is False
        and index in (acoustic_seam_report.get("recommended_retry_take_indexes") or [])
    )
    voice_report = payload.get("voice_qa") or {}
    failed_voice_gate = (
        take_status == "transcribed"
        and voice_report.get("passed") is False
        and index in (voice_report.get("outlier_take_indexes") or [])
    )
    if not (
        terminal_take_failure
        or failed_visual_gate
        or failed_voice_gate
        or failed_final_transcript
        or failed_acoustic_plan
        or failed_acoustic_seam_qa
    ):
        raise ValidationError(
            "Take is not in a retryable failed state; refusing to orphan an existing paid operation.",
            {"take_index": index, "take_status": take_status, "run_status": payload.get("status")},
        )
    reason_text = str(reason or "").strip()
    if not reason_text:
        raise ValidationError("Retry requires an operator reason.", {"take_index": index})
    guidance = " ".join(str(retry_guidance or "").split())
    if len(guidance) > 500:
        raise ValidationError("Retry guidance must be 500 characters or fewer.", {"take_index": index})
    original_contract_sha = str(payload.get("request_contract_sha256") or "")
    archived = json.loads(json.dumps(take))
    archived.pop("attempt_history", None)
    archived["reason"] = reason_text
    archived["archived_at"] = _utc_now()
    history = list(take.get("attempt_history") or [])
    history.append(archived)
    next_attempt = int(take.get("attempt") or 1) + 1
    base_prompt = build_veo_take_prompt(_beat_from_payload(take["beat"]))
    next_prompt = base_prompt
    if guidance:
        next_prompt = f"{base_prompt} Retry delivery correction: {guidance}"
    if next_prompt.count(take["beat"]["text"]) != 1:
        raise ValidationError(
            "Retry guidance must not repeat or alter the exact scripted beat.",
            {"take_index": index},
        )
    take["attempt"] = next_attempt
    take["attempt_history"] = history
    take["seed"] = int(payload["base_seed"]) + int(take["index"]) + (next_attempt - 1) * 1000
    take["prompt"] = next_prompt
    take["negative_prompt"] = EFFECTIVE_NEGATIVE_PROMPT
    take["status"] = "planned"
    take["submission"] = None
    take["operation"] = None
    take["raw"] = None
    take["transcript"] = None
    take["transcript_qa"] = None
    take["trim_window"] = None
    if failed_voice_gate:
        failure_history = list(payload.get("qa_failure_history") or [])
        failure_history.append(
            {
                "stage": "voice_qa",
                "selected_take_indexes": [index],
                "report": voice_report,
                "archived_at": _utc_now(),
            }
        )
        payload["qa_failure_history"] = failure_history
    if failed_acoustic_plan:
        failure_history = list(payload.get("qa_failure_history") or [])
        failure_history.append(
            {
                "stage": "acoustic_plan",
                "selected_take_indexes": [index],
                "report": acoustic_plan_failure,
                "archived_at": _utc_now(),
            }
        )
        payload["qa_failure_history"] = failure_history
    if failed_acoustic_seam_qa:
        failure_history = list(payload.get("qa_failure_history") or [])
        failure_history.append(
            {
                "stage": "acoustic_seam_qa",
                "selected_take_indexes": [index],
                "report": acoustic_seam_report,
                "archived_at": _utc_now(),
            }
        )
        payload["qa_failure_history"] = failure_history
    _clear_downstream_artifacts(payload)
    contract_history = list(payload.get("request_contract_history") or [])
    contract_history.append(
        {
            "sha256": original_contract_sha,
            "take_index": index,
            "reason": reason_text,
            "retry_guidance": guidance or None,
            "archived_at": _utc_now(),
        }
    )
    payload["request_contract_history"] = contract_history
    payload["request_contract_sha256"] = _canonical_sha256(_request_contract_payload(payload))
    payload["status"] = "retry_planned"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return payload


@_manifest_locked
def reset_visual_failed_takes(
    manifest_path: Path,
    *,
    indexes: list[int],
    reason: str,
    retry_guidance: Optional[str] = None,
) -> Dict[str, Any]:
    """Mark and reset several visually failed takes against one preserved QA report."""
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    selected = list(dict.fromkeys(indexes))
    if not selected or len(selected) != len(indexes):
        raise ValidationError("Visual retry indexes must be a non-empty unique list.")
    visual_report = payload.get("visual_qa") or {}
    if visual_report.get("passed") is not False:
        raise ValidationError("Batch visual retry requires a failed visual QA report.")
    by_index = {take["index"]: take for take in payload["takes"]}
    for index in selected:
        take = by_index.get(index)
        if take is None or take.get("status") != "transcribed":
            raise ValidationError(
                "Batch visual retry requires transcript-passed selected takes.",
                {"take_index": index, "take_status": (take or {}).get("status")},
            )
        take["status"] = "visual_failed"
    failure_history = list(payload.get("qa_failure_history") or [])
    failure_history.append(
        {
            "stage": "visual_qa",
            "selected_take_indexes": selected,
            "report": visual_report,
            "archived_at": _utc_now(),
        }
    )
    payload["qa_failure_history"] = failure_history
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    for index in selected:
        payload = reset_failed_take(
            manifest_path,
            index=index,
            reason=reason,
            retry_guidance=retry_guidance,
        )
    return payload


@_manifest_locked
def reset_voice_failed_takes(
    manifest_path: Path,
    *,
    indexes: list[int],
    reason: str,
    retry_guidance: Optional[str] = None,
) -> Dict[str, Any]:
    """Archive one failed voice report and explicitly reset selected model outliers."""
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    selected = list(dict.fromkeys(indexes))
    if not selected or len(selected) != len(indexes):
        raise ValidationError("Voice retry indexes must be a non-empty unique list.")
    voice_report = payload.get("voice_qa") or {}
    if voice_report.get("passed") is not False:
        raise ValidationError("Batch voice retry requires a failed voice QA report.")
    reported_outliers = set(voice_report.get("outlier_take_indexes") or [])
    if not set(selected).issubset(reported_outliers):
        raise ValidationError(
            "Voice retry may target only model-reported outlier takes.",
            {"selected": selected, "reported_outliers": sorted(reported_outliers)},
        )
    by_index = {take["index"]: take for take in payload["takes"]}
    for index in selected:
        take = by_index.get(index)
        if take is None or take.get("status") != "transcribed":
            raise ValidationError(
                "Batch voice retry requires transcript-passed selected takes.",
                {"take_index": index, "take_status": (take or {}).get("status")},
            )
        take["status"] = "voice_failed"
    failure_history = list(payload.get("qa_failure_history") or [])
    failure_history.append(
        {
            "stage": "voice_qa",
            "selected_take_indexes": selected,
            "report": voice_report,
            "archived_at": _utc_now(),
        }
    )
    payload["qa_failure_history"] = failure_history
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    for index in selected:
        payload = reset_failed_take(
            manifest_path,
            index=index,
            reason=reason,
            retry_guidance=retry_guidance,
        )
    return payload


@_manifest_locked
def revise_failed_beat(
    manifest_path: Path,
    *,
    index: int,
    replacement_text: str,
    reason: str,
) -> Dict[str, Any]:
    """Audit and replace one repeatedly undeliverable beat without touching passed takes."""
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    ordered = sorted(payload["takes"], key=lambda take: take["index"])
    matches = [take for take in ordered if take["index"] == index]
    if len(matches) != 1:
        raise ValidationError("Editorial revision take index does not exist.", {"take_index": index})
    take = matches[0]
    if take.get("status") != "transcript_failed" or (take.get("transcript_qa") or {}).get("passed") is not False:
        raise ValidationError(
            "Editorial revision requires a transcript-failed take.",
            {"take_index": index, "take_status": take.get("status")},
        )
    reason_text = " ".join(str(reason or "").split())
    replacement = " ".join(str(replacement_text or "").split())
    if not reason_text:
        raise ValidationError("Editorial revision requires an operator reason.", {"take_index": index})
    if not replacement or replacement == take["beat"]["text"]:
        raise ValidationError("Editorial revision requires a different non-empty beat.", {"take_index": index})

    prior_script = str(payload["script"]["text"])
    parts = [str(candidate["beat"]["text"]) for candidate in ordered]
    parts[index] = replacement
    revised_script = " ".join(parts)
    revised_beats = plan_editorial_beats(revised_script)
    revised_durations = [beat.provider_duration_seconds for beat in revised_beats]
    expected_durations = [int(candidate["duration_seconds"]) for candidate in ordered]
    if len(revised_beats) != len(ordered) or revised_durations != expected_durations:
        raise ValidationError(
            "Editorial revision must preserve the approved semantic duration plan.",
            {"expected": expected_durations, "actual": revised_durations},
        )
    for candidate, beat in zip(ordered, revised_beats):
        if beat.index != index and beat.text != candidate["beat"]["text"]:
            raise ValidationError(
                "Editorial revision changed a passed semantic beat boundary.",
                {"take_index": beat.index},
            )
    revised_beat = revised_beats[index]
    if revised_beat.text != replacement:
        raise ValidationError("Editorial revision did not remain one complete semantic beat.")

    original_contract_sha = str(payload.get("request_contract_sha256") or "")
    archived_take = json.loads(json.dumps(take))
    archived_take.pop("attempt_history", None)
    archived_take["reason"] = reason_text
    archived_take["archived_at"] = _utc_now()
    attempt_history = list(take.get("attempt_history") or [])
    attempt_history.append(archived_take)
    next_attempt = int(take.get("attempt") or 1) + 1
    take["attempt"] = next_attempt
    take["attempt_history"] = attempt_history
    take["beat"] = asdict(revised_beat)
    take["duration_seconds"] = revised_beat.provider_duration_seconds
    take["seed"] = int(payload["base_seed"]) + index + (next_attempt - 1) * 1000
    take["prompt"] = build_veo_take_prompt(revised_beat)
    take["status"] = "planned"
    take["submission"] = None
    take["operation"] = None
    take["raw"] = None
    take["transcript"] = None
    take["transcript_qa"] = None
    take["trim_window"] = None

    script = payload["script"]
    revisions = list(script.get("editorial_revisions") or [])
    revisions.append(
        {
            "take_index": index,
            "original_text": ordered[index]["attempt_history"][-1]["beat"]["text"],
            "replacement_text": replacement,
            "prior_script_sha256": sha256(prior_script.encode("utf-8")).hexdigest(),
            "reason": reason_text,
            "revised_at": _utc_now(),
        }
    )
    script["original_text"] = script.get("original_text") or prior_script
    script["editorial_revisions"] = revisions
    script["text"] = revised_script
    script["text_sha256"] = sha256(revised_script.encode("utf-8")).hexdigest()
    script["planned_provider_durations"] = revised_durations

    _clear_downstream_artifacts(payload)
    contract_history = list(payload.get("request_contract_history") or [])
    contract_history.append(
        {
            "sha256": original_contract_sha,
            "take_index": index,
            "reason": reason_text,
            "editorial_replacement": replacement,
            "archived_at": _utc_now(),
        }
    )
    payload["request_contract_history"] = contract_history
    payload["request_contract_sha256"] = _canonical_sha256(_request_contract_payload(payload))
    payload["status"] = "editorial_revision_planned"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return payload


__all__ = [
    "build_contact_sheet",
    "compose_and_caption",
    "evaluate_final_media_probe",
    "generate_raw_takes_in_waves",
    "initialize_pilot",
    "invalidate_composition",
    "load_video_uri",
    "poll_and_download_takes",
    "pilot_run_lock",
    "reconcile_unknown_submission",
    "repair_failed_seam_windows",
    "reset_failed_take",
    "reset_voice_failed_takes",
    "reset_visual_failed_takes",
    "revise_failed_beat",
    "run_visual_qa",
    "run_voice_qa",
    "submit_pending_takes",
    "transcribe_and_validate_takes",
    "upload_final",
]
