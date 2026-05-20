from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.adapters.llm_client import get_llm_client
from app.adapters.storage_client import get_storage_client


PROMPTS = [
    "Photorealistic vertical portrait of the same adult female wheelchair user, calm smile, cream sweater, neutral home background, natural window light, no text, no logo.",
    "Photorealistic three-quarter portrait of the same adult female wheelchair user, beige blazer over white top, compact home office, natural skin texture, no text, no logo.",
    "Photorealistic side-profile portrait of the same adult female wheelchair user, light grey cardigan, bright accessible bathroom background, no text, no logo.",
    "Photorealistic full-body seated portrait of the same adult female wheelchair user, cream sweater, modern living room, hands visible, no text, no logo.",
    "Photorealistic close-up of the same adult female wheelchair user, casual blazer, parked compact car background, soft overcast daylight, no text, no logo.",
    "Photorealistic medium shot of the same adult female wheelchair user, home cardigan, tidy product-friendly home interior, no text, no logo.",
    "Photorealistic portrait of the same adult female wheelchair user, cream sweater, direct-to-camera UGC expression, warm neutral wall, no text, no logo.",
    "Photorealistic three-quarter seated portrait of the same adult female wheelchair user, beige blazer, subtle smile, soft side light, no text, no logo.",
]


def main() -> int:
    if os.getenv("AIUGC_LIVE_NANOBANANA_REFS") != "1":
        print("Set AIUGC_LIVE_NANOBANANA_REFS=1 to run paid Gemini/NanoBanana image generation.")
        return 2

    output_dir = Path(os.getenv("ACTOR_REF_OUTPUT_DIR", "output/actor_identity_refs")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    correlation_id = f"nanobanana_actor_refs_{uuid4()}"
    llm = get_llm_client()
    storage = get_storage_client()
    manifest = {
        "correlation_id": correlation_id,
        "model": os.getenv("ACTOR_REF_GEMINI_MODEL", "nanobanana-2"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "images": [],
    }

    for idx, prompt in enumerate(PROMPTS, start=1):
        result = llm.generate_gemini_image(
            prompt=prompt,
            model=manifest["model"],
            temperature=0.7,
        )
        suffix = "jpg" if result["mime_type"] == "image/jpeg" else "png"
        file_name = f"actor-training-{idx:02d}.{suffix}"
        local_path = output_dir / file_name
        local_path.write_bytes(result["image_bytes"])
        upload = storage.upload_image(
            image_bytes=result["image_bytes"],
            file_name=file_name,
            correlation_id=correlation_id,
            content_type=result["mime_type"],
        )
        manifest["images"].append(
            {
                "index": idx,
                "prompt": prompt,
                "local_path": str(local_path),
                "public_url": upload["url"],
                "storage_key": upload["storage_key"],
                "mime_type": result["mime_type"],
                "size": len(result["image_bytes"]),
            }
        )
        print(f"{idx}: {upload['url']}")

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"manifest={manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
