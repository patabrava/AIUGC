"""Generate still candidates and stop before Veo 3.1.

Example:
    python scripts/generate_shot_frame_candidates.py \
      --actor-front /tmp/actor-front.png \
      --actor-three-quarter /tmp/actor-three-quarter.png \
      --location /tmp/location.png \
      --output-dir /tmp/shot-frame-candidates
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from pathlib import Path
from typing import Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.features.shot_frames.service import (  # noqa: E402
    RAW_CAMERA_SYSTEM_PROMPT_PATH,
    ShotFrameReference,
    generate_shot_frame_candidates,
)

DEFAULT_SCRIPT = (
    "Als Rollstuhlfahrer kennst du das: Normgerechte Rampen sind oft ein versteckter Marathon für deine Kraft."
)
DEFAULT_CHARACTER_DESCRIPTION = (
    "38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight "
    "natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; "
    "hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in "
    "a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; "
    "a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; "
    "gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin "
    "texture; slim build with relaxed upright posture."
)
DEFAULT_SCENE = (
    "The exact supplied home_living_room_advice_a room: warm off-white wall, beige curtain on the left, pale oak "
    "floor, narrow light-oak side table on actor-right, white mug, and terracotta rubber plant."
)


def _read_reference(path: Path, role: str) -> ShotFrameReference:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return ShotFrameReference(role=role, mime_type=mime_type, image_bytes=path.read_bytes())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Nano Banana shot-frame candidates; never calls Veo.")
    parser.add_argument("--actor-front", type=Path, required=True)
    parser.add_argument("--actor-three-quarter", type=Path, required=True)
    parser.add_argument("--location", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--actor-name", default="AYRA Actor Long Character")
    parser.add_argument("--character-description", default=DEFAULT_CHARACTER_DESCRIPTION)
    parser.add_argument("--script", default=DEFAULT_SCRIPT)
    parser.add_argument("--scene", default=DEFAULT_SCENE)
    parser.add_argument("--wardrobe", default="The cream knit sweater from Image 1; never the blazer from Image 2.")
    parser.add_argument("--candidate-count", type=int, default=3)
    parser.add_argument("--image-model", default="gemini-3.1-flash-image")
    args = parser.parse_args()

    for path in (args.actor_front, args.actor_three_quarter, args.location):
        if not path.is_file():
            parser.error(f"Reference image does not exist: {path}")

    result = generate_shot_frame_candidates(
        script=args.script,
        actor_name=args.actor_name,
        character_description=args.character_description,
        scene_description=args.scene,
        wardrobe_description=args.wardrobe,
        actor_references=[
            _read_reference(args.actor_front, "actor_front"),
            _read_reference(args.actor_three_quarter, "actor_three_quarter"),
        ],
        location_reference=_read_reference(args.location, "location"),
        candidate_count=args.candidate_count,
        image_model=args.image_model,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for candidate in result.candidates:
        suffix = ".jpg" if candidate.mime_type == "image/jpeg" else ".png"
        output_path = args.output_dir / f"candidate-{candidate.index}{suffix}"
        output_path.write_bytes(candidate.image_bytes)
        files.append(
            {
                "index": candidate.index,
                "path": str(output_path.resolve()),
                "mime_type": candidate.mime_type,
                "provider_model": candidate.provider_model,
                "sha256": hashlib.sha256(candidate.image_bytes).hexdigest(),
            }
        )

    input_paths: Dict[str, Path] = {
        "actor_front": args.actor_front,
        "actor_three_quarter": args.actor_three_quarter,
        "location": args.location,
    }
    manifest = {
        "status": "awaiting_human_approval",
        "veo_submitted": False,
        "script": args.script,
        "actor_name": args.actor_name,
        "character_description": args.character_description,
        "scene_description": args.scene,
        "wardrobe_description": args.wardrobe,
        "prompt_writer_system_sha256": _sha256(RAW_CAMERA_SYSTEM_PROMPT_PATH),
        "prompt_writer_output": result.prompt_writer_output,
        "composition_prompt": result.composition_prompt,
        "inputs": {
            role: {"path": str(path.resolve()), "sha256": _sha256(path)} for role, path in input_paths.items()
        },
        "candidates": files,
    }
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"manifest": str(manifest_path.resolve()), "candidates": files}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
