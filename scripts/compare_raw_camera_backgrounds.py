"""Generate isolated Raw Camera background treatments beside current scene plates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, Optional

import httpx
from PIL import Image, ImageOps

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.features.characters.scene_reference import get_scene_bible  # noqa: E402
from app.features.scenes import queries as scene_queries  # noqa: E402
from app.features.scenes.background_comparison import (  # noqa: E402
    RawCameraBackgroundResult,
    compose_side_by_side,
    generate_raw_camera_background,
    render_comparison_index,
)

DEFAULT_SCENE_KEYS = (
    "home_living_room_advice_a",
    "bathroom_accessibility_a",
    "car_transfer_residential_a",
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _as_png(payload: bytes) -> bytes:
    with Image.open(BytesIO(payload)) as source:
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        output = BytesIO()
        normalized.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _download_image(url: str) -> bytes:
    response = httpx.get(url, follow_redirects=True, timeout=60.0)
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("Control image download returned an empty body")
    return response.content


def _load_asset(scene_key: str):
    return scene_queries.get_canonical_scene_asset(scene_key=scene_key)


def run_comparison(
    *,
    scene_keys: Iterable[str],
    output_root: Path,
    load_asset: Callable[[str], object | None] = _load_asset,
    download_image: Callable[[str], bytes] = _download_image,
    generate: Callable[..., RawCameraBackgroundResult] = generate_raw_camera_background,
    run_name: Optional[str] = None,
) -> tuple[Path, list[str]]:
    resolved_run_name = run_name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / resolved_run_name
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "production_assets_updated": False,
        "comparison": {"left": "reality_first_prompt_v1", "right": "raw_camera_casting_realism"},
        "scenes": [],
    }
    html_rows: list[dict[str, str]] = []
    failures: list[str] = []

    for requested_scene_key in scene_keys:
        scene_key = get_scene_bible(requested_scene_key).scene_id
        scene_dir = run_dir / scene_key
        scene_dir.mkdir(parents=True, exist_ok=False)
        scene_record: dict = {"scene_key": scene_key, "status": "failed"}
        try:
            asset = load_asset(scene_key)
            if (
                asset is None
                or str(getattr(asset, "status", "")) != "generated"
                or not str(getattr(asset, "image_url", "") or "")
            ):
                raise RuntimeError(f"Scene {scene_key} requires an existing generated control asset")
            control_source = download_image(str(getattr(asset, "image_url")))
            control_png = _as_png(control_source)
            treatment = generate(scene_key=scene_key)
            treatment_png = _as_png(treatment.image_bytes)
            bible = get_scene_bible(scene_key)
            comparison_png = compose_side_by_side(
                control_bytes=control_png,
                treatment_bytes=treatment_png,
                scene_name=bible.name,
            )

            (scene_dir / "current.png").write_bytes(control_png)
            (scene_dir / "raw-camera.png").write_bytes(treatment_png)
            (scene_dir / "side-by-side.png").write_bytes(comparison_png)
            (scene_dir / "prompt-writer-brief.txt").write_text(
                treatment.prompt_writer_brief + "\n", encoding="utf-8"
            )
            (scene_dir / "raw-camera-prompt.txt").write_text(
                treatment.prompt_writer_output + "\n", encoding="utf-8"
            )
            scene_record = {
                "scene_key": scene_key,
                "scene_name": bible.name,
                "status": "generated",
                "control": {
                    "asset_id": str(getattr(asset, "id")),
                    "image_url": str(getattr(asset, "image_url")),
                    "provider_model": str(getattr(asset, "provider_model", "")),
                    "system_prompt_name": str(getattr(asset, "system_prompt_name", "")),
                    "scene_bible_version": int(getattr(asset, "scene_bible_version", 0)),
                    "sha256": _sha256(control_png),
                    "path": f"{scene_key}/current.png",
                },
                "treatment": {
                    "provider_model": treatment.provider_model,
                    "prompt_system": "raw_camera_casting_system_prompt.txt",
                    "sha256": _sha256(treatment_png),
                    "path": f"{scene_key}/raw-camera.png",
                    "prompt_path": f"{scene_key}/raw-camera-prompt.txt",
                },
                "comparison_path": f"{scene_key}/side-by-side.png",
            }
            html_rows.append(
                {
                    "scene_key": scene_key,
                    "scene_name": bible.name,
                    "control_path": f"{scene_key}/current.png",
                    "treatment_path": f"{scene_key}/raw-camera.png",
                    "comparison_path": f"{scene_key}/side-by-side.png",
                }
            )
        except Exception as exc:  # noqa: BLE001 - preserve remaining independent comparisons
            failures.append(scene_key)
            scene_record["error"] = str(exc)
        manifest["scenes"].append(scene_record)

    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "index.html").write_text(render_comparison_index(html_rows), encoding="utf-8")
    return run_dir, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare current canonical backgrounds with Raw Camera treatments.")
    parser.add_argument("scene_keys", nargs="*", default=DEFAULT_SCENE_KEYS)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "output" / "background-reference-comparison",
    )
    args = parser.parse_args()
    run_dir, failures = run_comparison(scene_keys=args.scene_keys, output_root=args.output_root)
    print(
        json.dumps(
            {
                "run_dir": str(run_dir.resolve()),
                "index": str((run_dir / "index.html").resolve()),
                "requested": len(args.scene_keys),
                "succeeded": len(args.scene_keys) - len(failures),
                "failed": failures,
                "production_assets_updated": False,
            },
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
