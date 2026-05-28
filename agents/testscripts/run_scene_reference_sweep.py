from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.magnific_client import get_magnific_client
from app.core.config import get_settings
from app.core.errors import FlowForgeException
from app.features.characters import queries
from app.features.characters.actor_identity import actor_identity_training_ready
from app.features.characters.scene_reference import (
    REQUIRED_SCENE_REFERENCE_ANGLES,
    SCENE_BIBLES,
    SCENE_REFERENCE_IDENTITY_STRENGTH,
    SCENE_REFERENCE_RESOLUTION,
    build_scene_reference_prompt_for_angle,
    scene_reference_style_loras_for,
)

SWEEP_PROFILES = {
    "baseline": {"engine": "magnific_sparkle", "creative_detailing": 18, "fixed_generation": False, "style_loras": False},
    "fixed-low-detail": {"engine": "magnific_sparkle", "creative_detailing": 8, "fixed_generation": True, "style_loras": False},
    "style-low-detail": {"engine": "magnific_sparkle", "creative_detailing": 8, "fixed_generation": True, "style_loras": True},
    "style-sharpy": {"engine": "magnific_sharpy", "creative_detailing": 8, "fixed_generation": True, "style_loras": True},
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-") or "item"


def _extract_image_url(task: dict[str, Any]) -> Optional[str]:
    for key in ("image_url", "url", "output_url"):
        if task.get(key):
            return str(task[key])
    images = task.get("generated") or task.get("images") or task.get("outputs") or task.get("result")
    if isinstance(images, list):
        for item in images:
            if isinstance(item, str):
                return item
            if isinstance(item, dict):
                found = _extract_image_url(item)
                if found:
                    return found
    if isinstance(images, dict):
        return _extract_image_url(images)
    return None


def _status_text(task: dict[str, Any]) -> str:
    for key in ("status", "state", "phase"):
        if task.get(key):
            return str(task[key])
    return "unknown"


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "task_id",
        "id",
        "status",
        "state",
        "phase",
        "image_url",
        "url",
        "output_url",
        "generated",
        "images",
        "outputs",
        "result",
        "error",
        "message",
    )
    return {key: task[key] for key in allowed if key in task}


def _scene_style_config(settings: Any) -> str:
    return str(
        getattr(settings, "scene_reference_style_loras", None)
        or os.environ.get("SCENE_REFERENCE_STYLE_LORAS")
        or ""
    )


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(connect=20, read=120, write=20, pool=20), follow_redirects=True) as http:
        response = http.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)


def _provider_error_text(exc: Exception) -> str:
    if isinstance(exc, FlowForgeException):
        details = {
            key: value
            for key, value in exc.details.items()
            if key in {"provider", "path", "status_code", "body", "correlation_id", "error"}
        }
        return f"{type(exc).__name__}: {exc.message}; details={json.dumps(details, sort_keys=True)}"
    return f"{type(exc).__name__}: {exc}"


def _submit_mystic_with_retries(
    *,
    client: Any,
    prompt: str,
    lora_id: str,
    strength: int,
    correlation_id: str,
    resolution: str,
    fixed_generation: bool,
    extra_options: dict[str, Any],
    style_loras: list[dict[str, Any]],
    attempts: int,
    backoff_seconds: float,
) -> tuple[dict[str, Any] | None, str | None]:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return (
                client.create_mystic_scene_reference(
                    prompt=prompt,
                    lora_id=str(lora_id),
                    strength=strength,
                    correlation_id=f"{correlation_id}-submit{attempt}",
                    resolution=resolution,
                    fixed_generation=fixed_generation,
                    extra_options=extra_options,
                    style_loras=style_loras,
                ),
                None,
            )
        except Exception as exc:  # noqa: BLE001 - operator script must preserve paid-run evidence
            last_error = _provider_error_text(exc)
            print(json.dumps({"submit_retry": attempt, "attempts": attempts, "correlation_id": correlation_id, "error": last_error}))
            if attempt < attempts:
                time.sleep(backoff_seconds * attempt)
    return None, last_error or "submit failed"


def _fit_image(path: Path, size: tuple[int, int]) -> Any:
    image = Image.open(path).convert("RGB")
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    image.thumbnail(size, resampling)
    canvas = Image.new("RGB", size, "white")
    canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    return canvas


def _make_contact_sheet(rows: list[dict[str, Any]], output_path: Path, title: str) -> bool:
    if Image is None or ImageDraw is None or ImageFont is None:
        return False
    thumb = (260, 462)
    label_h = 50
    gap = 14
    margin = 22
    cols = len(REQUIRED_SCENE_REFERENCE_ANGLES)
    groups = sorted({(row["profile"], row["set_index"]) for row in rows})
    width = margin * 2 + cols * thumb[0] + (cols - 1) * gap
    height = margin * 2 + 42 + len(groups) * (thumb[1] + label_h) + max(0, len(groups) - 1) * gap
    sheet = Image.new("RGB", (width, height), (246, 244, 238))
    draw = ImageDraw.Draw(sheet)
    try:
        font_title = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 22)
        font_label = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 13)
    except Exception:
        font_title = ImageFont.load_default()
        font_label = ImageFont.load_default()
    draw.text((margin, margin), title, fill=(30, 30, 28), font=font_title)
    index = {(row["profile"], row["set_index"], row["angle_key"]): row for row in rows}
    for group_index, (profile, set_index) in enumerate(groups):
        y = margin + 42 + group_index * (thumb[1] + label_h + gap)
        for col, angle in enumerate(REQUIRED_SCENE_REFERENCE_ANGLES):
            x = margin + col * (thumb[0] + gap)
            row = index.get((profile, set_index, angle.key))
            draw.rectangle((x - 1, y - 1, x + thumb[0], y + thumb[1]), outline=(70, 70, 64), width=1)
            if row and row.get("image_path"):
                sheet.paste(_fit_image(Path(row["image_path"]), thumb), (x, y))
                label = f"{profile} set {set_index} | {angle.key} | {str(row.get('task_id') or '')[:8]}"
            else:
                draw.rectangle((x, y, x + thumb[0], y + thumb[1]), fill=(218, 214, 204))
                label = f"{profile} set {set_index} | {angle.key} | missing"
            draw.text((x, y + thumb[1] + 8), label, fill=(32, 32, 30), font=font_label)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=94)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run paid Magnific scene-reference sweep.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sets-per-scene", type=int, default=1)
    parser.add_argument("--profiles", default="baseline,fixed-low-detail,style-low-detail,style-sharpy")
    parser.add_argument("--scenes", default=",".join(SCENE_BIBLES.keys()))
    parser.add_argument("--poll-attempts", type=int, default=60)
    parser.add_argument("--poll-sleep", type=int, default=10)
    parser.add_argument("--submit-attempts", type=int, default=3)
    parser.add_argument("--submit-backoff", type=float, default=8.0)
    parser.add_argument("--submit-sleep", type=float, default=2.0)
    args = parser.parse_args()

    settings = get_settings()
    actor = queries.get_active_actor_identity()
    if not actor or not actor_identity_training_ready(actor):
        print(json.dumps({"error": "active_actor_not_ready"}))
        return 1
    if not actor.provider_lora_id or not actor.provider_lora_name:
        print(json.dumps({"error": "active_actor_missing_provider_refs", "actor_id": actor.id}))
        return 1

    selected_profiles = [profile.strip() for profile in args.profiles.split(",") if profile.strip()]
    selected_scenes = [scene.strip() for scene in args.scenes.split(",") if scene.strip()]
    for profile in selected_profiles:
        if profile not in SWEEP_PROFILES:
            print(json.dumps({"error": "unknown_profile", "profile": profile, "known_profiles": sorted(SWEEP_PROFILES)}))
            return 1
    for scene_id in selected_scenes:
        if scene_id not in SCENE_BIBLES:
            print(json.dumps({"error": "unknown_scene", "scene_id": scene_id, "known_scenes": sorted(SCENE_BIBLES)}))
            return 1

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    client = get_magnific_client()
    rows: list[dict[str, Any]] = []
    style_config = _scene_style_config(settings)
    warnings: list[dict[str, str]] = []

    for scene_id in selected_scenes:
        if any(SWEEP_PROFILES[profile]["style_loras"] for profile in selected_profiles):
            resolved_style_loras = scene_reference_style_loras_for(scene_id, style_config)
            if not resolved_style_loras:
                warning = {
                    "scene_id": scene_id,
                    "message": "style profile selected but no style LoRA configured; style profiles will run actor-only",
                }
                warnings.append(warning)
                print(json.dumps({"warning": warning}))

    print(json.dumps({"output_dir": str(out_dir), "actor_id": actor.id, "actor_lora": actor.provider_lora_name}))
    for scene_id in selected_scenes:
        for profile in selected_profiles:
            profile_options = SWEEP_PROFILES[profile]
            for set_index in range(1, args.sets_per_scene + 1):
                for angle in REQUIRED_SCENE_REFERENCE_ANGLES:
                    style_loras = scene_reference_style_loras_for(scene_id, style_config) if profile_options["style_loras"] else []
                    prompt = build_scene_reference_prompt_for_angle(
                        actor_name=actor.name,
                        scene_key=scene_id,
                        wardrobe_key="everyday_sweater",
                        post_type="",
                        angle_key=angle.key,
                        provider_lora_name=actor.provider_lora_name,
                    )
                    correlation_id = f"scene-sweep-{scene_id}-{profile}-set{set_index}-{angle.key}-{int(time.time())}"
                    task, submit_error = _submit_mystic_with_retries(
                        client=client,
                        prompt=prompt,
                        lora_id=str(actor.provider_lora_id),
                        strength=SCENE_REFERENCE_IDENTITY_STRENGTH,
                        correlation_id=correlation_id,
                        resolution=SCENE_REFERENCE_RESOLUTION,
                        fixed_generation=bool(profile_options["fixed_generation"]),
                        extra_options={
                            "engine": profile_options["engine"],
                            "creative_detailing": profile_options["creative_detailing"],
                        },
                        style_loras=style_loras,
                        attempts=max(1, args.submit_attempts),
                        backoff_seconds=max(0.0, args.submit_backoff),
                    )
                    row = {
                        "scene_id": scene_id,
                        "profile": profile,
                        "set_index": set_index,
                        "angle_key": angle.key,
                        "task_id": str((task or {}).get("task_id") or (task or {}).get("id") or ""),
                        "status": _status_text(task or {}),
                        "image_url": _extract_image_url(task or {}),
                        "image_path": None,
                        "error": submit_error,
                        "style_loras": style_loras,
                        "request": (task or {}).get("_request_payload"),
                        "task": _public_task(task or {}),
                    }
                    rows.append(row)
                    print(json.dumps({"submitted": {key: row[key] for key in ("scene_id", "profile", "set_index", "angle_key", "task_id", "status")}}))
                    time.sleep(max(0.0, args.submit_sleep))

    pending = [row for row in rows if row["task_id"] and not row["image_url"]]
    for attempt in range(1, args.poll_attempts + 1):
        if not pending:
            break
        print(json.dumps({"poll_attempt": attempt, "pending": len(pending)}))
        next_pending: list[dict[str, Any]] = []
        for row in pending:
            try:
                task = client.get_mystic_task(task_id=row["task_id"], correlation_id=f"scene-sweep-poll-{row['task_id']}-{attempt}")
                row["status"] = _status_text(task)
                row["task"] = _public_task(task)
                image_url = _extract_image_url(task)
                if image_url:
                    row["image_url"] = image_url
                elif row["status"].lower() in {"failed", "error", "cancelled", "canceled"}:
                    row["error"] = f"provider status {row['status']}"
                else:
                    next_pending.append(row)
            except Exception as exc:
                row["error"] = f"poll failed: {type(exc).__name__}: {exc}"
                next_pending.append(row)
        pending = next_pending
        if pending:
            time.sleep(args.poll_sleep)

    for row in rows:
        if not row.get("image_url"):
            row["error"] = row.get("error") or "image not ready before timeout"
            continue
        image_path = out_dir / row["scene_id"] / row["profile"] / f"set-{row['set_index']}" / f"{row['angle_key']}.png"
        try:
            _download(str(row["image_url"]), image_path)
            row["image_path"] = str(image_path)
        except Exception as exc:
            row["error"] = f"download failed: {type(exc).__name__}: {exc}"

    contact_sheets = []
    for scene_id in selected_scenes:
        scene_rows = [row for row in rows if row["scene_id"] == scene_id]
        if any(row.get("image_path") for row in scene_rows):
            sheet_path = out_dir / f"contact_sheet_{_slug(scene_id)}.jpg"
            if _make_contact_sheet(scene_rows, sheet_path, scene_id):
                contact_sheets.append(str(sheet_path))

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(out_dir),
        "actor": {"id": actor.id, "provider_lora_name": actor.provider_lora_name},
        "profiles": {profile: SWEEP_PROFILES[profile] for profile in selected_profiles},
        "contact_sheets": contact_sheets,
        "pillow_contact_sheets_available": Image is not None,
        "warnings": warnings,
        "items": rows,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "downloaded": sum(1 for row in rows if row.get("image_path")), "errors": sum(1 for row in rows if row.get("error"))}))
    return 2 if any(row.get("error") for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
