"""Prepare and run one production-quality eight-second proof for every scene bible.

The harness deliberately separates paid image preparation from paid Veo submission so
the complete start-frame set can be reviewed before video spend. Each scene stores its
own resumable manifest, and a persisted Veo submission intent is never resubmitted when
the provider response is ambiguous.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.caption_aligner import align_transcript_to_script  # noqa: E402
from app.adapters.caption_renderer import burn_captions  # noqa: E402
from app.adapters.deepgram_client import get_deepgram_client  # noqa: E402
from app.adapters.vertex_ai_client import get_vertex_ai_client  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.errors import ValidationError  # noqa: E402
from app.features.characters.actor_identity import actor_identity_reference_ready  # noqa: E402
from app.features.characters.queries import list_actor_identities  # noqa: E402
from app.features.characters.scene_reference import SCENE_BIBLES  # noqa: E402
from app.features.scenes.handlers import generate_canonical_scene_asset  # noqa: E402
from app.features.shot_frames.identity_qa import (  # noqa: E402
    evaluate_scene_plate_identity,
    evaluate_video_actor_identity,
)
from app.features.shot_frames.wheelchair_scene_plate import (  # noqa: E402
    ShotFrameReference,
    generate_scene_plate_candidates,
)
from app.features.shot_production.composer import evaluate_take_transcript  # noqa: E402
from app.features.shot_production.planner import (  # noqa: E402
    EditorialBeat,
    estimate_speech_seconds,
)
from app.features.shot_production.prompts import (  # noqa: E402
    EFFECTIVE_NEGATIVE_PROMPT,
    VEO_MODEL,
    build_veo_take_prompt,
)
from app.features.shot_production.runner import load_video_uri  # noqa: E402
from app.features.shot_production.visual_qa import evaluate_scene_continuity  # noqa: E402
from scripts.run_semantic_ugc_live_smoke import _create_contact_sheet, _probe_media  # noqa: E402


OUTPUT_ROOT = REPO_ROOT / "output" / "all-scenes-realism-e2e-2026-09-22"
KITCHEN_PROOF_ROOT = REPO_ROOT / "output" / "kitchen-lived-in-e2e-2026-09-17"
KITCHEN_PLATE = KITCHEN_PROOF_ROOT / "photographic-integration-v13b" / "candidate-1.png"
KITCHEN_VIDEO = KITCHEN_PROOF_ROOT / "production-integration-v13b-1080" / "final-captioned.mp4"
KITCHEN_RAW = KITCHEN_PROOF_ROOT / "production-integration-v13b-1080" / "veo-raw.mp4"
KITCHEN_CONTACT = KITCHEN_PROOF_ROOT / "production-integration-v13b-1080" / "identity-contact-sheet.jpg"

SCENE_SCRIPTS = {
    "bathroom_accessibility_a": "Ein gut platzierter Haltegriff gibt Sicherheit und erleichtert viele Bewegungen im Badezimmer spürbar.",
    "car_transfer_residential_a": "Eine ruhige Transfertechnik macht das Einsteigen ins Auto sicherer und spart im Alltag Kraft.",
    "home_living_room_advice_a": "Kleine Anpassungen im Wohnzimmer schaffen Bewegungsfreiheit und machen den Alltag deutlich entspannter.",
    "hallway_stairlift_a": "Ein passender Treppenlift verbindet die Etagen sicher und erhält mehr Selbstständigkeit zu Hause.",
    "entryway_ramp_a": "Eine niedrige Schwellenrampe macht den Hauseingang zugänglicher und erleichtert jeden täglichen Weg.",
    "bedroom_accessibility_a": "Ein gut eingestelltes Pflegebett erleichtert das Aufstehen und schafft mehr Sicherheit im Schlafzimmer.",
    "garden_patio_a": "Ein barrierefreier Gartenplatz bringt Erholung nach draußen und bleibt trotzdem bequem erreichbar.",
    "home_kitchen_advice_a": "Ein aufgeräumter Arbeitsbereich in der Küche spart Kraft und macht alltägliche Handgriffe deutlich leichter.",
    "home_dining_nook_advice_a": "Ein gut erreichbarer Essplatz schafft Komfort und macht gemeinsame Mahlzeiten deutlich entspannter.",
    "home_office_advice_a": "Ein übersichtlicher Arbeitsplatz spart Wege und macht konzentriertes Arbeiten deutlich angenehmer.",
}

VIDEO_PRICE_PER_SECOND_USD = 0.40
VIDEO_DURATION_SECONDS = 8
VIDEO_RESOLUTION = "1080p"


def _hash(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValidationError("Scenario manifest must contain one JSON object.")
    return value


def _download(url: str) -> bytes:
    response = httpx.get(url, follow_redirects=True, timeout=90.0)
    response.raise_for_status()
    if not response.content:
        raise ValidationError("Reference image download returned no bytes.")
    return response.content


def _image_mime(payload: bytes) -> str:
    with Image.open(BytesIO(payload)) as image:
        detected = str(image.format or "").upper()
    return "image/jpeg" if detected == "JPEG" else "image/png"


def _active_actor() -> Any:
    matches = [row for row in list_actor_identities() if row.is_active and actor_identity_reference_ready(row)]
    if len(matches) != 1:
        raise ValidationError(
            "All-scene proof requires exactly one active image-reference-ready actor.",
            {"active_ready_count": len(matches)},
        )
    return matches[0]


def _actor_references(output_root: Path) -> tuple[ShotFrameReference, ShotFrameReference, dict[str, Any]]:
    actor = _active_actor()
    reference_dir = output_root / "actor-references"
    reference_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    references = []
    for role, url in (
        ("actor_front", actor.reference_front_image_url),
        ("actor_three_quarter", actor.reference_three_quarter_image_url),
    ):
        payload = _download(str(url))
        mime_type = _image_mime(payload)
        extension = ".jpg" if mime_type == "image/jpeg" else ".png"
        path = reference_dir / f"{role}{extension}"
        path.write_bytes(payload)
        digest = _hash(payload)
        references.append(ShotFrameReference(role=role, mime_type=mime_type, image_bytes=payload))
        rows.append({"role": role, "path": str(path), "mime_type": mime_type, "sha256": digest})
    metadata = {"actor_identity_id": actor.id, "actor_name": actor.name, "references": rows}
    _write_json(reference_dir / "manifest.json", metadata)
    return references[0], references[1], metadata


def _prepare_kitchen(output_root: Path) -> dict[str, Any]:
    if not all(path.is_file() for path in (KITCHEN_PLATE, KITCHEN_VIDEO, KITCHEN_RAW, KITCHEN_CONTACT)):
        raise ValidationError("Validated kitchen proof artifacts are missing.")
    scene_dir = output_root / "home_kitchen_advice_a"
    scene_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for source, name in (
        (KITCHEN_PLATE, "scene-plate.png"),
        (KITCHEN_RAW, "veo-raw.mp4"),
        (KITCHEN_VIDEO, "final-captioned.mp4"),
        (KITCHEN_CONTACT, "identity-contact-sheet.jpg"),
    ):
        destination = scene_dir / name
        shutil.copy2(source, destination)
        artifacts[name] = {"path": str(destination), "sha256": _hash(destination.read_bytes())}
    manifest = {
        "scene_key": "home_kitchen_advice_a",
        "scene_bible_version": 2,
        "status": "completed_reused_validated_production_proof",
        "paid_video_submissions": 0,
        "source": str(KITCHEN_PROOF_ROOT),
        "artifacts": artifacts,
    }
    _write_json(scene_dir / "manifest.json", manifest)
    return manifest


def prepare_scene(
    *,
    scene_key: str,
    output_root: Path,
    actor_references: tuple[ShotFrameReference, ShotFrameReference],
) -> dict[str, Any]:
    scene_dir = output_root / scene_key
    scene_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = scene_dir / "manifest.json"
    existing = _load_json(manifest_path)
    plate_path = scene_dir / "scene-plate.png"
    if existing.get("scene_identity_qa", {}).get("passed") is True and plate_path.is_file():
        print(f"[PREPARED] {scene_key}", flush=True)
        return existing

    bible = SCENE_BIBLES[scene_key]
    print(f"[BACKGROUND] {scene_key}", flush=True)
    asset = generate_canonical_scene_asset(
        scene_key=scene_key,
        correlation_id=f"all_scenes_background_{uuid4()}",
        force=False,
    )
    location_bytes = _download(str(asset.image_url))
    location_mime = _image_mime(location_bytes)
    location_path = scene_dir / ("location.jpg" if location_mime == "image/jpeg" else "location.png")
    location_path.write_bytes(location_bytes)

    print(f"[SCENE PLATE] {scene_key}", flush=True)
    settings = get_settings()
    canonical_anchor = None
    if existing.get("scene_identity_qa", {}).get("passed") is False and KITCHEN_PLATE.is_file():
        anchor_bytes = KITCHEN_PLATE.read_bytes()
        canonical_anchor = ShotFrameReference(
            role="canonical_scene_plate",
            mime_type=_image_mime(anchor_bytes),
            image_bytes=anchor_bytes,
        )
        print(f"[CANONICAL ANCHOR RETRY] {scene_key}", flush=True)
    result = generate_scene_plate_candidates(
        actor_references=actor_references,
        location_reference=ShotFrameReference(
            role="location",
            mime_type=location_mime,
            image_bytes=location_bytes,
        ),
        canonical_scene_plate=canonical_anchor,
        scene=bible.scene_identity,
        wardrobe="cream crewneck knit sweater with natural folds and no logos or jewelry",
        candidate_count=1,
        image_model=settings.semantic_scene_plate_model,
        image_size=settings.semantic_scene_plate_image_size,
        traffic_key=f"all-scenes-{scene_key}",
    )
    candidate = result.candidates[0]
    plate_path.write_bytes(candidate.image_bytes)
    qa = evaluate_scene_plate_identity(
        {"mime_type": actor_references[0].mime_type, "image_bytes": actor_references[0].image_bytes},
        {"mime_type": actor_references[1].mime_type, "image_bytes": actor_references[1].image_bytes},
        {"mime_type": candidate.mime_type, "image_bytes": candidate.image_bytes},
        model=settings.semantic_scene_identity_gate_model,
        minimum_confidence=settings.semantic_scene_identity_min_confidence,
        location=settings.semantic_scene_identity_gate_location,
    )
    manifest = {
        **existing,
        "scene_key": scene_key,
        "scene_name": bible.name,
        "scene_bible_version": bible.version,
        "status": "prepared" if qa.passed else "scene_identity_failed",
        "location": {
            "path": str(location_path),
            "sha256": _hash(location_bytes),
            "asset_id": asset.id,
            "provider_model": asset.provider_model,
        },
        "scene_plate": {
            "path": str(plate_path),
            "sha256": _hash(candidate.image_bytes),
            "mime_type": candidate.mime_type,
            "provider_model": candidate.provider_model,
            "generation_contract": settings.semantic_scene_plate_contract_version,
            "prompt": candidate.prompt,
        },
        "scene_identity_qa": asdict(qa),
        "script": SCENE_SCRIPTS[scene_key],
        "paid_video_submissions": int(existing.get("paid_video_submissions") or 0),
    }
    _write_json(manifest_path, manifest)
    print(f"[IDENTITY {'PASS' if qa.passed else 'FAIL'}] {scene_key} confidence={qa.confidence:.2f}", flush=True)
    return manifest


def render_plate_contact_sheet(output_root: Path) -> Path:
    tiles = []
    font = ImageFont.load_default(size=22)
    for scene_key, bible in SCENE_BIBLES.items():
        plate_path = output_root / scene_key / "scene-plate.png"
        if not plate_path.is_file():
            continue
        with Image.open(plate_path) as source:
            image = ImageOps.fit(source.convert("RGB"), (270, 480), method=Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (300, 540), "white")
        tile.paste(image, (15, 45))
        ImageDraw.Draw(tile).text((15, 12), bible.name, fill="black", font=font)
        tiles.append(tile)
    columns = 5
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 300, rows * 540), "#ddd8ce")
    for index, tile in enumerate(tiles):
        sheet.paste(tile, ((index % columns) * 300, (index // columns) * 540))
    path = output_root / "scene-plate-contact-sheet.jpg"
    sheet.save(path, quality=92)
    return path


def run_video(
    *,
    scene_key: str,
    output_root: Path,
    actor_references: tuple[ShotFrameReference, ShotFrameReference],
    poll_interval: float,
) -> dict[str, Any]:
    scene_dir = output_root / scene_key
    manifest_path = scene_dir / "manifest.json"
    manifest = _load_json(manifest_path)
    if str(manifest.get("status") or "").startswith("completed"):
        print(f"[COMPLETED] {scene_key}", flush=True)
        return manifest
    if manifest.get("scene_identity_qa", {}).get("passed") is not True:
        raise ValidationError("Scene plate has not passed identity QA.", {"scene_key": scene_key})

    plate_path = Path(manifest["scene_plate"]["path"])
    plate_bytes = plate_path.read_bytes()
    expected_hash = str(manifest["scene_plate"]["sha256"])
    if _hash(plate_bytes) != expected_hash:
        raise ValidationError("Scene plate checksum changed before Veo submission.")

    script = SCENE_SCRIPTS[scene_key]
    word_count = len(script.rstrip(".!?").split())
    beat = EditorialBeat(
        index=0,
        text=script,
        word_count=word_count,
        estimated_speech_seconds=estimate_speech_seconds(word_count),
        provider_duration_seconds=VIDEO_DURATION_SECONDS,
    )
    prompt = build_veo_take_prompt(beat)
    request = {
        "model": VEO_MODEL,
        "prompt": prompt,
        "negative_prompt": EFFECTIVE_NEGATIVE_PROMPT,
        "approved_master_sha256": expected_hash,
        "aspect_ratio": "9:16",
        "duration_seconds": VIDEO_DURATION_SECONDS,
        "resolution": VIDEO_RESOLUTION,
        "generate_audio": True,
        "sample_count": 1,
        "seed": 840317 + list(SCENE_BIBLES).index(scene_key),
    }
    submission = manifest.get("submission") if isinstance(manifest.get("submission"), dict) else {}
    state = str(submission.get("state") or "not_started")
    vertex = get_vertex_ai_client()
    if state == "not_started":
        if int(manifest.get("paid_video_submissions") or 0) >= 1:
            raise ValidationError("Scenario already consumed its one-submission video budget.")
        correlation_id = f"all_scenes_8s_{scene_key}_{expected_hash[:12]}"
        manifest["request"] = request
        manifest["submission"] = {"state": "intent_persisted", "correlation_id": correlation_id}
        manifest["paid_video_submissions"] = 1
        manifest["status"] = "video_submission_intent_persisted"
        _write_json(manifest_path, manifest)
        print(f"[VEO SUBMIT] {scene_key}", flush=True)
        try:
            accepted = vertex.submit_image_video(
                prompt=prompt,
                image_bytes=plate_bytes,
                mime_type=str(manifest["scene_plate"]["mime_type"]),
                correlation_id=correlation_id,
                aspect_ratio="9:16",
                duration_seconds=VIDEO_DURATION_SECONDS,
                model=VEO_MODEL,
                negative_prompt=EFFECTIVE_NEGATIVE_PROMPT,
                seed=request["seed"],
                resolution=VIDEO_RESOLUTION,
                generate_audio=True,
                sample_count=1,
            )
        except Exception as exc:
            manifest = _load_json(manifest_path)
            manifest["status"] = "video_submission_unknown_no_retry"
            manifest["submission"]["error"] = str(exc)[:1000]
            _write_json(manifest_path, manifest)
            raise
        operation_id = str(accepted.get("operation_id") or "")
        if not operation_id:
            raise ValidationError("Veo accepted response has no operation id.")
        manifest = _load_json(manifest_path)
        manifest["submission"].update({"state": "accepted", "operation_id": operation_id})
        manifest["status"] = "video_processing"
        _write_json(manifest_path, manifest)
    elif state != "accepted":
        raise ValidationError("Scenario has an unresolved Veo submission and cannot retry.")

    manifest = _load_json(manifest_path)
    operation_id = str(manifest["submission"]["operation_id"])
    correlation_id = str(manifest["submission"]["correlation_id"])
    while True:
        result = vertex.check_operation_status(operation_id=operation_id, correlation_id=correlation_id)
        if result.get("error") or result.get("status") == "failed":
            manifest["status"] = "provider_failed_no_retry"
            manifest["submission"]["provider_error"] = result.get("error")
            _write_json(manifest_path, manifest)
            raise ValidationError("Veo operation failed; automatic retry is forbidden.")
        if result.get("done"):
            video_uri = str(result.get("video_uri") or "")
            if not video_uri:
                raise ValidationError("Completed Veo operation returned no video URI.")
            raw_bytes = load_video_uri(video_uri)
            break
        print(f"[VEO POLL] {scene_key} status={result.get('status')}", flush=True)
        time.sleep(max(1.0, poll_interval))

    raw_path = scene_dir / "veo-raw.mp4"
    raw_path.write_bytes(raw_bytes)
    raw_probe = _probe_media(raw_path)
    transcript = get_deepgram_client().transcribe(
        audio_bytes=raw_bytes,
        correlation_id=f"{correlation_id}_transcript",
    )
    transcript_qa = evaluate_take_transcript(beat, transcript, other_beats=[])
    aligned = align_transcript_to_script(transcript=transcript, script=script)
    captioned_temp = Path(
        burn_captions(
            video_path=str(raw_path),
            transcript=aligned,
            correlation_id=f"{correlation_id}_captions",
            video_width=raw_probe["width"],
            video_height=raw_probe["height"],
        )
    )
    final_path = scene_dir / "final-captioned.mp4"
    shutil.copy2(captioned_temp, final_path)
    captioned_temp.unlink(missing_ok=True)
    final_probe = _probe_media(final_path)
    contact = _create_contact_sheet(final_path, scene_dir / "identity-contact-sheet.jpg")
    contact_bytes = Path(contact["path"]).read_bytes()
    settings = get_settings()
    video_identity = evaluate_video_actor_identity(
        {"mime_type": actor_references[0].mime_type, "image_bytes": actor_references[0].image_bytes},
        {"mime_type": actor_references[1].mime_type, "image_bytes": actor_references[1].image_bytes},
        {"mime_type": "image/jpeg", "image_bytes": contact_bytes},
        model=settings.semantic_scene_identity_gate_model,
        minimum_confidence=settings.semantic_video_identity_min_confidence,
    )
    continuity = evaluate_scene_continuity(
        {"mime_type": str(manifest["scene_plate"]["mime_type"]), "image_bytes": plate_bytes},
        {"mime_type": "image/jpeg", "image_bytes": contact_bytes},
        model=settings.semantic_scene_identity_gate_model,
    )
    manifest = _load_json(manifest_path)
    manifest.update(
        {
            "status": "completed" if video_identity.passed and continuity.passed else "completed_review_required",
            "artifacts": {
                "raw_video": {"path": str(raw_path), "sha256": _hash(raw_bytes), "probe": raw_probe},
                "captioned_video": {
                    "path": str(final_path),
                    "sha256": _hash(final_path.read_bytes()),
                    "probe": final_probe,
                },
                "identity_contact_sheet": contact,
            },
            "transcript": {
                "full_text": transcript.full_text,
                "words": [asdict(word) for word in transcript.words],
                "qa": asdict(transcript_qa),
            },
            "video_identity_qa": asdict(video_identity),
            "scene_continuity_qa": asdict(continuity),
        }
    )
    _write_json(manifest_path, manifest)
    print(
        f"[VIDEO {manifest['status'].upper()}] {scene_key} "
        f"identity={video_identity.confidence:.2f} continuity={continuity.confidence:.2f} "
        f"wer={transcript_qa.word_error_rate:.3f}",
        flush=True,
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("prepare", "video", "all"), default="prepare")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--confirm-paid-plan", action="store_true")
    parser.add_argument("--max-video-budget-usd", type=float, default=28.80)
    parser.add_argument("--poll-interval", type=float, default=10.0)
    parser.add_argument("--prepare-spacing", type=float, default=0.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    payable_scene_count = len(SCENE_BIBLES) - 1
    expected_video_spend = payable_scene_count * VIDEO_DURATION_SECONDS * VIDEO_PRICE_PER_SECOND_USD
    if args.max_video_budget_usd + 1e-9 < expected_video_spend:
        raise ValidationError(
            "Configured video budget is below the nine-scene production plan.",
            {"required_usd": expected_video_spend, "configured_usd": args.max_video_budget_usd},
        )
    if args.stage in {"video", "all"} and not args.confirm_paid_plan:
        raise ValidationError("Video stage requires --confirm-paid-plan.")

    actor_front, actor_three_quarter, actor_metadata = _actor_references(output_root)
    actor_references = (actor_front, actor_three_quarter)
    summary: dict[str, Any] = {
        "output_root": str(output_root),
        "actor": actor_metadata,
        "video_plan": {
            "payable_scenes": payable_scene_count,
            "reused_scenes": 1,
            "maximum_video_spend_usd": expected_video_spend,
            "resolution": VIDEO_RESOLUTION,
            "duration_seconds": VIDEO_DURATION_SECONDS,
            "model": VEO_MODEL,
        },
        "scenes": {},
    }

    if args.stage in {"prepare", "all"}:
        summary["scenes"]["home_kitchen_advice_a"] = _prepare_kitchen(output_root)
        for scene_key in SCENE_BIBLES:
            if scene_key == "home_kitchen_advice_a":
                continue
            prior_manifest = _load_json(output_root / scene_key / "manifest.json")
            already_prepared = (
                prior_manifest.get("scene_identity_qa", {}).get("passed") is True
                and Path(str(prior_manifest.get("scene_plate", {}).get("path") or "")).is_file()
            )
            try:
                summary["scenes"][scene_key] = prepare_scene(
                    scene_key=scene_key,
                    output_root=output_root,
                    actor_references=actor_references,
                )
            except Exception as exc:  # noqa: BLE001 - retain independent scene evidence
                summary["scenes"][scene_key] = {"status": "prepare_failed", "error": str(exc)}
                print(f"[PREPARE FAIL] {scene_key}: {exc}", flush=True)
            if args.prepare_spacing > 0 and not already_prepared:
                time.sleep(args.prepare_spacing)
        summary["scene_plate_contact_sheet"] = str(render_plate_contact_sheet(output_root))

    if args.stage in {"video", "all"}:
        for scene_key in SCENE_BIBLES:
            if scene_key == "home_kitchen_advice_a":
                summary["scenes"][scene_key] = _load_json(output_root / scene_key / "manifest.json")
                continue
            try:
                summary["scenes"][scene_key] = run_video(
                    scene_key=scene_key,
                    output_root=output_root,
                    actor_references=actor_references,
                    poll_interval=args.poll_interval,
                )
            except Exception as exc:  # noqa: BLE001 - never retry an ambiguous paid operation
                summary["scenes"][scene_key] = {"status": "video_failed", "error": str(exc)}
                print(f"[VIDEO FAIL] {scene_key}: {exc}", flush=True)

    _write_json(output_root / "summary.json", summary)
    print(json.dumps({"output_root": str(output_root), "summary": str(output_root / 'summary.json')}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
