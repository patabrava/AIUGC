"""Resumable local runner for the approved-frame semantic UGC pilot."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote

import google.auth
import httpx
from google.auth.transport.requests import Request
from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.adapters.caption_aligner import align_transcript_to_script
from app.adapters.caption_renderer import burn_captions
from app.adapters.deepgram_client import WordLevelTranscript
from app.adapters.storage_client import get_storage_client
from app.adapters.video_stitcher import stitch_segments
from app.core.errors import ValidationError
from app.features.shot_production.composer import (
    build_take_trim_window,
    evaluate_take_transcript,
)
from app.features.shot_production.planner import EditorialBeat, plan_editorial_beats
from app.features.shot_production.prompts import compile_veo_take_requests
from app.features.shot_production.shot_deck import derive_shot_deck
from app.features.shot_production.visual_qa import evaluate_visual_consistency


MANIFEST_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


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
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _load_manifest(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("Pilot manifest could not be loaded.", {"path": str(path), "error": str(exc)}) from exc
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        raise ValidationError("Pilot manifest has an unsupported schema version.")
    return payload


def _beat_from_payload(payload: Dict[str, Any]) -> EditorialBeat:
    return EditorialBeat(
        index=int(payload["index"]),
        text=str(payload["text"]),
        word_count=int(payload["word_count"]),
        estimated_speech_seconds=float(payload["estimated_speech_seconds"]),
        provider_duration_seconds=int(payload["provider_duration_seconds"]),
    )


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
        script_source = json.loads(script_input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("Pilot input could not be loaded.", {"error": str(exc)}) from exc
    if not isinstance(script_source, dict):
        raise ValidationError("Pilot script input must be one JSON object.")
    script_text = str(script_source.get("script") or "").strip()
    if not script_text:
        raise ValidationError("Pilot script input requires a non-empty script.")

    # Hash/aspect validation happens before the run directory or manifest is created.
    deck = derive_shot_deck(
        approved_master_bytes=approved_bytes,
        expected_sha256=expected_sha256,
        mime_type="image/png",
    )
    beats = plan_editorial_beats(script_text)
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
            "source": script_source.get("source"),
            "category": script_source.get("category"),
            "target_length_tier": script_source.get("target_length_tier"),
            "text": script_text,
            "source_payload": script_source,
        },
        "takes": takes,
    }
    _atomic_write_json(manifest_path, payload)
    return payload


def _correlation_id(manifest: Dict[str, Any], take: Dict[str, Any]) -> str:
    return f"semantic_ugc_{manifest['run_id']}_take_{take['index']}_attempt_{take['attempt']}"


def submit_pending_takes(manifest_path: Path, vertex_client: Any) -> Dict[str, Any]:
    """Submit only unaccepted takes and persist each paid operation immediately."""
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    for take in payload["takes"]:
        if take.get("operation"):
            continue
        shot_path = Path(take["shot"]["path"])
        if _file_sha256(shot_path) != take["shot"]["sha256"]:
            raise ValidationError("Approved take frame hash changed before submission.", {"take_index": take["index"]})
        try:
            result = vertex_client.submit_image_video(
                prompt=take["prompt"],
                image_bytes=shot_path.read_bytes(),
                mime_type=take["shot"]["mime_type"],
                correlation_id=_correlation_id(payload, take),
                aspect_ratio=take["aspect_ratio"],
                duration_seconds=take["duration_seconds"],
                model=take["model"],
                negative_prompt=take["negative_prompt"],
                seed=take["seed"],
            )
        except Exception as exc:
            payload["status"] = "submit_failed"
            payload["last_error"] = {"stage": "submit", "take_index": take["index"], "message": str(exc)}
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            raise
        operation_id = str(result.get("operation_id") or "").strip()
        if not operation_id:
            raise ValidationError("Vertex accepted response is missing an operation id.")
        take["operation"] = {
            "operation_id": operation_id,
            "provider_model": result.get("provider_model") or take["model"],
            "status": result.get("status") or "submitted",
            "submitted_at": _utc_now(),
        }
        take["status"] = "submitted"
        payload["status"] = "submitted"
        payload["updated_at"] = _utc_now()
        # This write deliberately happens before the next provider call.
        _atomic_write_json(manifest_path, payload)
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
            if raw.get("path") and Path(raw["path"]).is_file():
                continue
            operation = take.get("operation") or {}
            operation_id = operation.get("operation_id")
            if not operation_id:
                raise ValidationError("Cannot poll a take without an accepted operation.", {"take_index": take["index"]})
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
            payload["status"] = "raw_completed"
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            return payload
        if time.monotonic() - started >= timeout_seconds:
            payload["status"] = "poll_timeout"
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            raise TimeoutError(f"Pilot take polling exceeded {timeout_seconds} seconds.")
        sleep_fn(max(0.0, poll_interval_seconds))


def _serialize_transcript(transcript: WordLevelTranscript) -> Dict[str, Any]:
    return {
        "full_text": transcript.full_text,
        "words": [asdict(word) for word in transcript.words],
    }


def transcribe_and_validate_takes(manifest_path: Path, deepgram_client: Any) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    beats = [_beat_from_payload(take["beat"]) for take in payload["takes"]]
    failed = []
    for take, beat in zip(payload["takes"], beats):
        existing_qa = take.get("transcript_qa") or {}
        if existing_qa:
            if not existing_qa.get("passed"):
                failed.append(take["index"])
            continue
        raw_path = Path((take.get("raw") or {}).get("path") or "")
        if not raw_path.is_file():
            raise ValidationError("Raw take is missing before transcription.", {"take_index": take["index"]})
        transcript = deepgram_client.transcribe(
            audio_bytes=raw_path.read_bytes(),
            correlation_id=f"{_correlation_id(payload, take)}_transcript",
        )
        qa = evaluate_take_transcript(
            beat,
            transcript,
            other_beats=[other for other in beats if other.index != beat.index],
        )
        take["transcript"] = _serialize_transcript(transcript)
        take["transcript_qa"] = asdict(qa)
        take["trim_window"] = (
            build_take_trim_window(qa, take["duration_seconds"]) if qa.passed else None
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


def build_contact_sheet(manifest_path: Path) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    existing = payload.get("contact_sheet") or {}
    if existing.get("path") and Path(existing["path"]).is_file():
        return existing
    frame_dir = manifest_path.parent / "qa" / "frames"
    per_take_frames = []
    for take in payload["takes"]:
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
        "frames": frame_records,
        "created_at": _utc_now(),
    }
    payload["contact_sheet"] = contact
    payload["status"] = "contact_sheet_ready"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return contact


def run_visual_qa(
    manifest_path: Path,
    *,
    evaluator: Callable[..., Any] = evaluate_visual_consistency,
    llm_client: Optional[Any] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    existing_report = payload.get("visual_qa") or {}
    if existing_report:
        if existing_report.get("passed"):
            return existing_report
        raise ValidationError(
            "Pilot visual QA failed.",
            {"blocking_reasons": list(existing_report.get("blocking_reasons") or [])},
        )
    contact = payload.get("contact_sheet") or {}
    contact_path = Path(contact.get("path") or "")
    master_path = Path(payload["approved_master"]["path"])
    if not contact_path.is_file() or not master_path.is_file():
        raise ValidationError("Visual QA inputs are missing.")
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


def _probe_media(path: Path) -> Dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate",
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


def compose_and_caption(
    manifest_path: Path,
    deepgram_client: Any,
    *,
    stitch_fn: Callable[..., Any] = stitch_segments,
    caption_fn: Callable[..., str] = burn_captions,
) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    existing_caption = payload.get("caption") or {}
    if existing_caption.get("captioned_path") and Path(existing_caption["captioned_path"]).is_file():
        return existing_caption
    if not (payload.get("visual_qa") or {}).get("passed"):
        raise ValidationError("Composition requires a passed visual QA gate.")
    ordered = sorted(payload["takes"], key=lambda take: take["index"])
    if any(not (take.get("transcript_qa") or {}).get("passed") or not take.get("trim_window") for take in ordered):
        raise ValidationError("Composition requires transcript-passed takes and trim windows.")
    segment_videos = [Path(take["raw"]["path"]).read_bytes() for take in ordered]
    trim_windows = [take["trim_window"] for take in ordered]
    stitched_bytes, stitch_metadata = stitch_fn(
        segment_videos=segment_videos,
        post_id=payload["run_id"],
        correlation_id=f"semantic_ugc_{payload['run_id']}_stitch",
        trim_windows=trim_windows,
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
    final_qa = evaluate_take_transcript(expected, final_transcript, other_beats=[])
    payload["final_transcript"] = _serialize_transcript(final_transcript)
    payload["final_transcript_qa"] = asdict(final_qa)
    _atomic_write_json(manifest_path, payload)
    if not final_qa.passed:
        payload["status"] = "final_transcript_failed"
        _atomic_write_json(manifest_path, payload)
        raise ValidationError("Final stitched transcript QA failed.", {"reasons": list(final_qa.failure_reasons)})

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
    payload["caption"] = {
        "captioned_path": str(captioned_path),
        "sha256": sha256(captioned_bytes).hexdigest(),
        "bytes": len(captioned_bytes),
        "word_count": len(aligned.words),
        "aligned_transcript": _serialize_transcript(aligned),
        "probe": _probe_media(captioned_path),
        "created_at": _utc_now(),
    }
    payload["status"] = "captioned"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return payload["caption"]


def upload_final(manifest_path: Path, storage_client: Optional[Any] = None) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    if payload.get("upload"):
        return payload["upload"]
    caption = payload.get("caption") or {}
    captioned_path = Path(caption.get("captioned_path") or "")
    if not captioned_path.is_file():
        raise ValidationError("Captioned pilot video is missing before upload.")
    storage = storage_client or get_storage_client()
    result = storage.upload_video(
        video_bytes=captioned_path.read_bytes(),
        file_name=f"semantic-ugc-{payload['run_id']}-captioned.mp4",
        correlation_id=f"semantic_ugc_{payload['run_id']}_upload",
    )
    payload["upload"] = result
    payload["status"] = "uploaded"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return result


def reset_failed_take(manifest_path: Path, *, index: int, reason: str) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_manifest(manifest_path)
    matches = [take for take in payload["takes"] if take["index"] == index]
    if len(matches) != 1:
        raise ValidationError("Retry take index does not exist.", {"take_index": index})
    take = matches[0]
    archived = json.loads(json.dumps(take))
    archived.pop("attempt_history", None)
    archived["reason"] = str(reason or "manual retry")
    archived["archived_at"] = _utc_now()
    history = list(take.get("attempt_history") or [])
    history.append(archived)
    take["attempt"] = int(take.get("attempt") or 1) + 1
    take["attempt_history"] = history
    take["status"] = "planned"
    take["operation"] = None
    take["raw"] = None
    take["transcript"] = None
    take["transcript_qa"] = None
    take["trim_window"] = None
    for downstream_key in (
        "contact_sheet", "visual_qa", "stitch", "final_transcript", "final_transcript_qa", "caption", "upload"
    ):
        payload.pop(downstream_key, None)
    payload["status"] = "retry_planned"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return payload


__all__ = [
    "build_contact_sheet",
    "compose_and_caption",
    "initialize_pilot",
    "load_video_uri",
    "poll_and_download_takes",
    "reset_failed_take",
    "run_visual_qa",
    "submit_pending_takes",
    "transcribe_and_validate_takes",
    "upload_final",
]
