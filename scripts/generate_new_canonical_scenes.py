"""Generate the canonical scene plate images for the expanded scene catalog.

Each canonical scene is a person-free environment plate generated on Vertex AI with
Gemini 3 Pro Image ("Nano Banana Pro") and stored in the configured image bucket. This
script generates the 7 scenes added in the catalog expansion (or any scene_keys passed on
the command line) by calling the same code path the API endpoint uses.

Usage (from AIUGC/, with the venv active so .env credentials load):

    python scripts/generate_new_canonical_scenes.py
    python scripts/generate_new_canonical_scenes.py --force
    python scripts/generate_new_canonical_scenes.py hallway_stairlift_a garden_patio_a
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.features.scenes.handlers import generate_canonical_scene_asset  # noqa: E402

NEW_CANONICAL_SCENES = [
    "hallway_stairlift_a",
    "entryway_ramp_a",
    "bedroom_accessibility_a",
    "garden_patio_a",
    "home_kitchen_advice_a",
    "home_dining_nook_advice_a",
    "home_office_advice_a",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate canonical scene plate images.")
    parser.add_argument(
        "scene_keys",
        nargs="*",
        default=NEW_CANONICAL_SCENES,
        help="Scene keys to generate (default: the 7 newly added scenes).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if a generated plate already exists for the scene.",
    )
    args = parser.parse_args()

    failures = 0
    for scene_key in args.scene_keys:
        correlation_id = str(uuid4())
        try:
            record = generate_canonical_scene_asset(
                scene_key=scene_key,
                correlation_id=correlation_id,
                force=args.force,
            )
        except Exception as exc:  # noqa: BLE001 - report and continue with the rest
            failures += 1
            print(f"[FAIL] {scene_key}: {exc}")
            continue
        print(f"[OK]   {scene_key}: status={record.status} model={record.provider_model}")
        print(f"       {record.image_url}")

    print(f"\nDone: {len(args.scene_keys) - failures} generated, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
