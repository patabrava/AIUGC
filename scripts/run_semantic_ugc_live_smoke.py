"""Run exactly one budget-capped, approved-frame Veo 3.1 live proof.

The harness is intentionally narrower than the production worker. It can submit one
eight-second operation, poll only that persisted operation, and never retry.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.errors import ValidationError  # noqa: E402


MODEL = "veo-3.1-generate-001"
DURATION_SECONDS = 8
ASPECT_RATIO = "9:16"
RESOLUTION = "720p"
PRICE_PER_SECOND_USD = Decimal("0.40")
ABSOLUTE_BUDGET_CAP_USD = Decimal("17.70")
DEFAULT_SEED = 240712
MANIFEST_NAME = "manifest.json"
PROMPT_TEMPLATE = (
    "Treat the supplied first frame as the sole visual truth. Keep the same adult woman's "
    "identity and hair, cream knit sweater, room, posture, camera position, and framing exactly "
    "as shown. Continue as restrained, natural phone-camera AI UGC with a subtle conversational "
    "expression, subtle blinking, minimal head movement, and no polished commercial performance. "
    "Use a warm adult German female voice, speaking native German with natural conversational "
    "pacing and close smartphone microphone sound. She says exactly this German beat once: "
    "“{beat}” Do not speak any other words or any English. After the final word, naturally stop "
    "speaking, close her mouth, and keep quiet eye contact. Do not freeze or perform an artificial "
    "end pose. Keep every frame completely free of on-screen text: no captions, subtitles, logos, "
    "watermarks, letters, symbols, or gibberish glyphs."
)
NEGATIVE_PROMPT = (
    "face change, age change, hair change, wardrobe change, room change, extra person, camera zoom, "
    "push-in, reframe, posture reset, generated text, subtitles, music, background voices, extra "
    "speech, hands entering frame, repeated dialogue, English speech, logos, watermarks, gibberish text"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_sha256(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError("Live proof JSON is unreadable.", {"path": str(path)}) from exc
    if not isinstance(payload, dict):
        raise ValidationError("Live proof JSON must contain one object.", {"path": str(path)})
    return payload


def _extract_approved_beat(script_payload: Dict[str, Any]) -> str:
    explicit = str(script_payload.get("approved_beat") or "").strip()
    script = explicit or str(script_payload.get("script") or "").strip()
    if not script:
        raise ValidationError("Live proof script input has no approved script text.")
    if explicit:
        return " ".join(explicit.split())
    match = re.match(r"^(.+?[.!?])(?:\s|$)", " ".join(script.split()))
    return match.group(1) if match else " ".join(script.split())


def _word_count(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", text, flags=re.UNICODE))


def build_live_plan(
    *,
    approved_frame_path: Path,
    expected_sha256: str,
    script_input_path: Path,
    output_dir: Path,
    max_budget_usd: str,
    max_submissions: int,
    output_count: int,
    retry_requested: bool,
    image_generation_collaborators: list[str],
    seed: int = DEFAULT_SEED,
) -> Dict[str, Any]:
    frame_path = Path(approved_frame_path).resolve()
    script_path = Path(script_input_path).resolve()
    destination = Path(output_dir).resolve()
    if not frame_path.is_file():
        raise ValidationError("Live proof approved frame is missing.", {"path": str(frame_path)})
    if not script_path.is_file():
        raise ValidationError("Live proof approved script input is missing.", {"path": str(script_path)})
    actual_frame_hash = _file_sha256(frame_path)
    expected_hash = str(expected_sha256 or "").strip().lower()
    if not expected_hash:
        raise ValidationError("Live proof requires an approved master hash.")
    if actual_frame_hash != expected_hash:
        raise ValidationError(
            "Live proof approved master hash does not match the file.",
            {"expected": expected_hash, "actual": actual_frame_hash},
        )
    script_bytes = script_path.read_bytes()
    script_payload = _load_json(script_path)
    beat = _extract_approved_beat(script_payload)
    prompt = PROMPT_TEMPLATE.format(beat=beat)
    request_contract = {
        "model": MODEL,
        "prompt": prompt,
        "negative_prompt": NEGATIVE_PROMPT,
        "approved_master_sha256": expected_hash,
        "approved_master_mime_type": "image/png",
        "aspect_ratio": ASPECT_RATIO,
        "duration_seconds": DURATION_SECONDS,
        "resolution": RESOLUTION,
        "generate_audio": True,
        "sample_count": output_count,
        "seed": seed,
    }
    estimated_cost = (PRICE_PER_SECOND_USD * DURATION_SECONDS * output_count).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    plan = {
        "version": 1,
        "approved_frame_path": str(frame_path),
        "approved_master_sha256": expected_hash,
        "approved_script_input_path": str(script_path),
        "approved_script_input_sha256": sha256(script_bytes).hexdigest(),
        "approved_beat": beat,
        "approved_beat_word_count": _word_count(beat),
        "output_dir": str(destination),
        "model": MODEL,
        "duration_seconds": DURATION_SECONDS,
        "aspect_ratio": ASPECT_RATIO,
        "resolution": RESOLUTION,
        "generate_audio": True,
        "output_count": output_count,
        "planned_take_count": 1,
        "price_per_second_usd": format(PRICE_PER_SECOND_USD, ".2f"),
        "estimated_cost_usd": format(estimated_cost, ".2f"),
        "max_budget_usd": str(max_budget_usd),
        "max_submissions": max_submissions,
        "retry_requested": bool(retry_requested),
        "image_generation_collaborators": list(image_generation_collaborators),
        "request_contract": request_contract,
        "request_sha256": _canonical_sha256(request_contract),
    }
    validate_live_plan(plan)
    return plan


def _money(value: Any, *, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"Live proof {field} is invalid.") from exc
    if not amount.is_finite() or amount < 0:
        raise ValidationError(f"Live proof {field} is invalid.")
    return amount


def validate_live_plan(plan: Dict[str, Any]) -> None:
    if int(plan.get("planned_take_count") or 0) != 1:
        raise ValidationError("Live proof permits exactly one planned take.")
    if int(plan.get("output_count") or 0) != 1:
        raise ValidationError("Live proof permits exactly one output.")
    if int(plan.get("max_submissions") or 0) != 1:
        raise ValidationError("Live proof permits exactly one paid submission.")
    if not str(plan.get("approved_master_sha256") or "").strip():
        raise ValidationError("Live proof requires an approved master hash.")
    if bool(plan.get("retry_requested")):
        raise ValidationError("Live proof retry requests are forbidden.")
    if plan.get("image_generation_collaborators"):
        raise ValidationError("Live proof cannot load an image-generation collaborator.")
    if str(plan.get("model") or "") != MODEL:
        raise ValidationError("Live proof must use the full Veo 3.1 model.")
    if int(plan.get("duration_seconds") or 0) != DURATION_SECONDS:
        raise ValidationError("Live proof must be one eight-second request.")
    if str(plan.get("aspect_ratio") or "") != ASPECT_RATIO:
        raise ValidationError("Live proof must use 9:16 output.")
    if plan.get("generate_audio") is not True:
        raise ValidationError("Live proof must request synchronized audio.")
    word_count = int(plan.get("approved_beat_word_count") or 0)
    if not 14 <= word_count <= 18:
        raise ValidationError("Live proof approved beat must contain 14 to 18 words.")
    estimated = _money(plan.get("estimated_cost_usd"), field="estimated cost")
    maximum = _money(plan.get("max_budget_usd"), field="budget")
    if estimated > ABSOLUTE_BUDGET_CAP_USD:
        raise ValidationError("Live proof estimated cost exceeds the absolute budget cap.")
    if maximum > ABSOLUTE_BUDGET_CAP_USD:
        raise ValidationError("Live proof configured budget exceeds the absolute budget cap.")
    if estimated > maximum:
        raise ValidationError("Live proof estimated cost exceeds its configured budget.")
    expected_cost = PRICE_PER_SECOND_USD * DURATION_SECONDS
    if estimated != expected_cost:
        raise ValidationError("Live proof cost does not match the one-output pricing contract.")
    request_contract = plan.get("request_contract")
    if not isinstance(request_contract, dict):
        raise ValidationError("Live proof request contract is missing.")
    expected_request_fields = {
        "model": MODEL,
        "prompt": PROMPT_TEMPLATE.format(beat=str(plan.get("approved_beat") or "")),
        "negative_prompt": NEGATIVE_PROMPT,
        "approved_master_sha256": str(plan.get("approved_master_sha256") or ""),
        "approved_master_mime_type": "image/png",
        "aspect_ratio": ASPECT_RATIO,
        "duration_seconds": DURATION_SECONDS,
        "resolution": RESOLUTION,
        "generate_audio": True,
        "sample_count": 1,
    }
    if any(request_contract.get(key) != value for key, value in expected_request_fields.items()):
        raise ValidationError("Live proof request contract differs from the approved one-output plan.")
    if not isinstance(request_contract.get("seed"), int):
        raise ValidationError("Live proof request contract requires an integer seed.")
    if _canonical_sha256(request_contract) != str(plan.get("request_sha256") or ""):
        raise ValidationError("Live proof request hash is stale.")


def initialize_manifest(plan: Dict[str, Any]) -> Path:
    validate_live_plan(plan)
    output_dir = Path(str(plan["output_dir"]))
    manifest_path = output_dir / MANIFEST_NAME
    if manifest_path.exists():
        existing = _load_json(manifest_path)
        if existing.get("request_sha256") != plan.get("request_sha256"):
            raise ValidationError("Existing live proof manifest belongs to another request.")
        return manifest_path
    payload = {
        **plan,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "status": "pending_paid_confirmation",
        "ledger": {
            "maximum_submissions": 1,
            "submission_attempts": 0,
            "accepted_operations": 0,
            "requested_outputs": 1,
            "estimated_spend_usd": plan["estimated_cost_usd"],
        },
        "submission": {"state": "not_started"},
        "artifacts": {},
    }
    _atomic_write_json(manifest_path, payload)
    return manifest_path


def submit_once(manifest_path: Path, vertex_client: Any) -> Dict[str, Any]:
    manifest_path = Path(manifest_path)
    payload = _load_json(manifest_path)
    validate_live_plan(payload)
    attempts = int((payload.get("ledger") or {}).get("submission_attempts") or 0)
    if attempts >= 1 or (payload.get("submission") or {}).get("operation_id"):
        raise ValidationError("Live proof blocked a second paid submission.")
    frame_path = Path(str(payload["approved_frame_path"]))
    if not frame_path.is_file() or _file_sha256(frame_path) != payload["approved_master_sha256"]:
        raise ValidationError("Approved live proof frame changed before submission.")
    if _canonical_sha256(payload["request_contract"]) != payload["request_sha256"]:
        raise ValidationError("Live proof request changed before submission.")
    correlation_id = f"semantic_live_{payload['request_sha256'][:16]}"
    payload["ledger"]["submission_attempts"] = 1
    payload["submission"] = {
        "state": "intent_persisted",
        "correlation_id": correlation_id,
        "request_sha256": payload["request_sha256"],
        "intent_persisted_at": _utc_now(),
    }
    payload["status"] = "submission_intent_persisted"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    request = payload["request_contract"]
    try:
        result = vertex_client.submit_image_video(
            prompt=request["prompt"],
            image_bytes=frame_path.read_bytes(),
            mime_type=request["approved_master_mime_type"],
            correlation_id=correlation_id,
            aspect_ratio=request["aspect_ratio"],
            duration_seconds=request["duration_seconds"],
            model=request["model"],
            negative_prompt=request["negative_prompt"],
            seed=request["seed"],
            resolution=request["resolution"],
            generate_audio=request["generate_audio"],
            sample_count=request["sample_count"],
        )
        operation_id = str(result.get("operation_id") or "").strip()
        if not operation_id:
            raise ValidationError("Vertex accepted response is missing an operation id.")
    except Exception as exc:
        payload = _load_json(manifest_path)
        payload["submission"].update(
            {"state": "unknown", "failed_at": _utc_now(), "error": str(exc)[:1000]}
        )
        payload["status"] = "submission_unknown_no_retry"
        payload["updated_at"] = _utc_now()
        _atomic_write_json(manifest_path, payload)
        raise
    payload = _load_json(manifest_path)
    payload["submission"].update(
        {
            "state": "accepted",
            "operation_id": operation_id,
            "provider_model": str(result.get("provider_model") or MODEL),
            "accepted_at": _utc_now(),
        }
    )
    payload["ledger"]["accepted_operations"] = 1
    payload["status"] = "submitted"
    payload["updated_at"] = _utc_now()
    _atomic_write_json(manifest_path, payload)
    return payload


def _probe_media(path: Path) -> Dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=index,codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValidationError("Live proof FFprobe failed.", {"stderr": result.stderr[-500:]})
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or 0)
    if not video or not audio:
        raise ValidationError("Live proof output must contain video and audio streams.")
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0 or abs((width / height) - (9 / 16)) > 0.02:
        raise ValidationError("Live proof output is not 9:16.", {"width": width, "height": height})
    if not 7.0 <= duration <= 9.5:
        raise ValidationError("Live proof output duration is outside the eight-second envelope.")
    return {"duration_seconds": duration, "width": width, "height": height, "streams": streams}


def _deterministic_transcript(beat: str, *, duration_seconds: float):
    from app.adapters.deepgram_client import Word, WordLevelTranscript

    words = beat.split()
    start = 0.35
    usable = max(min(duration_seconds - 0.8, 7.0), 1.0)
    step = usable / len(words)
    timed = [
        Word(word=word.strip(), start=start + index * step, end=start + (index + 1) * step)
        for index, word in enumerate(words)
    ]
    return WordLevelTranscript(words=timed, full_text=beat)


def _create_contact_sheet(video_path: Path, output_path: Path) -> Dict[str, Any]:
    from PIL import Image, ImageOps

    frames_dir = output_path.parent / "identity-frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, second in enumerate((0.5, 2.5, 4.5, 6.5)):
        frame_path = frames_dir / f"frame-{index}.jpg"
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(second),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(frame_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise ValidationError("Live proof identity-frame extraction failed.")
        paths.append(frame_path)
    images = []
    for path in paths:
        with Image.open(path) as source:
            images.append(source.convert("RGB"))
    thumb_width = 270
    thumbs = [ImageOps.contain(image, (thumb_width, 480)) for image in images]
    sheet = Image.new("RGB", (thumb_width * len(thumbs), 480), "white")
    for index, image in enumerate(thumbs):
        sheet.paste(image, (index * thumb_width, (480 - image.height) // 2))
    sheet.save(output_path, quality=92)
    sheet.close()
    for image in thumbs:
        image.close()
    for image in images:
        image.close()
    return {
        "path": str(output_path),
        "sha256": _file_sha256(output_path),
        "frame_paths": [str(path) for path in paths],
    }


def poll_and_finalize(
    manifest_path: Path,
    vertex_client: Any,
    *,
    uri_loader: Optional[Callable[[str], bytes]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_interval_seconds: float = 10.0,
    timeout_seconds: float = 1800.0,
) -> Dict[str, Any]:
    payload = _load_json(manifest_path)
    operation_id = str((payload.get("submission") or {}).get("operation_id") or "")
    if not operation_id or payload.get("submission", {}).get("state") != "accepted":
        raise ValidationError("Live proof has no accepted operation to poll.")
    if int(payload.get("ledger", {}).get("submission_attempts") or 0) != 1:
        raise ValidationError("Live proof submission ledger is invalid before polling.")
    started = time.monotonic()
    correlation_id = str(payload["submission"]["correlation_id"])
    while True:
        result = vertex_client.check_operation_status(
            operation_id=operation_id,
            correlation_id=correlation_id,
        )
        payload = _load_json(manifest_path)
        payload["submission"]["last_polled_at"] = _utc_now()
        payload["submission"]["provider_status"] = result.get("status")
        if result.get("error") or result.get("status") == "failed":
            payload["status"] = "provider_failed_no_retry"
            payload["submission"]["provider_error"] = result.get("error")
            _atomic_write_json(manifest_path, payload)
            raise ValidationError("Live proof Vertex operation failed; retry is forbidden.")
        if result.get("done"):
            video_uri = str(result.get("video_uri") or "")
            if not video_uri:
                payload["status"] = "provider_completed_without_video_no_retry"
                _atomic_write_json(manifest_path, payload)
                raise ValidationError("Live proof completed without a video URI; retry is forbidden.")
            if uri_loader is None:
                from app.features.shot_production.runner import load_video_uri

                uri_loader = load_video_uri
            video_bytes = uri_loader(video_uri)
            if not isinstance(video_bytes, bytes) or not video_bytes:
                raise ValidationError("Live proof provider video download is empty.")
            output_dir = Path(payload["output_dir"])
            raw_path = output_dir / "veo-raw.mp4"
            raw_path.write_bytes(video_bytes)
            raw_probe = _probe_media(raw_path)
            from app.adapters.caption_renderer import burn_captions

            transcript = _deterministic_transcript(
                payload["approved_beat"], duration_seconds=raw_probe["duration_seconds"]
            )
            temporary_captioned = Path(
                burn_captions(
                    video_path=str(raw_path),
                    transcript=transcript,
                    correlation_id=correlation_id,
                    video_width=raw_probe["width"],
                    video_height=raw_probe["height"],
                )
            )
            final_path = output_dir / "final-captioned.mp4"
            shutil.copy2(temporary_captioned, final_path)
            try:
                temporary_captioned.unlink()
            except OSError:
                pass
            final_probe = _probe_media(final_path)
            contact_sheet = _create_contact_sheet(
                final_path, output_dir / "identity-contact-sheet.jpg"
            )
            payload = _load_json(manifest_path)
            payload["status"] = "completed"
            payload["submission"]["video_uri"] = video_uri
            payload["submission"]["completed_at"] = _utc_now()
            payload["artifacts"] = {
                "raw_video": {
                    "path": str(raw_path),
                    "sha256": _file_sha256(raw_path),
                    "bytes": raw_path.stat().st_size,
                    "probe": raw_probe,
                },
                "captioned_video": {
                    "path": str(final_path),
                    "sha256": _file_sha256(final_path),
                    "bytes": final_path.stat().st_size,
                    "probe": final_probe,
                    "caption_source": "deterministic_approved_script_timing",
                },
                "identity_contact_sheet": contact_sheet,
            }
            payload["updated_at"] = _utc_now()
            _atomic_write_json(manifest_path, payload)
            return payload
        payload["status"] = "processing"
        payload["updated_at"] = _utc_now()
        _atomic_write_json(manifest_path, payload)
        if time.monotonic() - started >= timeout_seconds:
            payload["status"] = "poll_timeout_no_retry"
            _atomic_write_json(manifest_path, payload)
            raise TimeoutError("Live proof polling timed out; retry is forbidden.")
        sleep_fn(max(0.0, poll_interval_seconds))


def execute_live_proof(
    plan: Dict[str, Any],
    *,
    confirm_paid_plan: bool,
    vertex_factory: Callable[[], Any],
    uri_loader: Optional[Callable[[str], bytes]] = None,
    poll_interval_seconds: float = 10.0,
    timeout_seconds: float = 1800.0,
) -> Dict[str, Any]:
    validate_live_plan(plan)
    manifest_path = initialize_manifest(plan)
    payload = _load_json(manifest_path)
    if not confirm_paid_plan:
        return payload
    if payload.get("status") == "completed":
        return payload
    vertex = vertex_factory()
    state = str((payload.get("submission") or {}).get("state") or "not_started")
    if state == "not_started":
        payload = submit_once(manifest_path, vertex)
    elif state != "accepted":
        raise ValidationError("Live proof has an unresolved submission and cannot retry.")
    return poll_and_finalize(
        manifest_path,
        vertex,
        uri_loader=uri_loader,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-frame", type=Path, required=True)
    parser.add_argument("--approved-sha", required=True)
    parser.add_argument("--script-input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-budget-usd", required=True)
    parser.add_argument("--max-submissions", type=int, required=True)
    parser.add_argument("--output-count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--image-generation-collaborator", action="append", default=[])
    parser.add_argument("--confirm-paid-plan", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=1800.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    plan = build_live_plan(
        approved_frame_path=args.approved_frame,
        expected_sha256=args.approved_sha,
        script_input_path=args.script_input,
        output_dir=args.output_dir,
        max_budget_usd=args.max_budget_usd,
        max_submissions=args.max_submissions,
        output_count=args.output_count,
        retry_requested=args.retry,
        image_generation_collaborators=args.image_generation_collaborator,
        seed=args.seed,
    )
    if args.confirm_paid_plan:
        from app.adapters.vertex_ai_client import get_vertex_ai_client

        vertex_factory = get_vertex_ai_client
    else:
        def vertex_factory():
            raise AssertionError("dry run instantiated Vertex")
    result = execute_live_proof(
        plan,
        confirm_paid_plan=args.confirm_paid_plan,
        vertex_factory=vertex_factory,
        poll_interval_seconds=args.poll_interval,
        timeout_seconds=args.timeout,
    )
    print(
        json.dumps(
            {
                "manifest": str(Path(plan["output_dir"]) / MANIFEST_NAME),
                "status": result["status"],
                "request_sha256": result["request_sha256"],
                "estimated_cost_usd": result["estimated_cost_usd"],
                "submission_attempts": result["ledger"]["submission_attempts"],
                "accepted_operations": result["ledger"]["accepted_operations"],
                "captioned_video": (result.get("artifacts") or {}).get("captioned_video", {}).get("path"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
