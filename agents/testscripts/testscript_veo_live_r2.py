"""
Live VEO to Cloudflare R2 testscript.
Submits one Veo 3.1 generation using the current prompt template, waits for completion,
downloads the resulting video, uploads it to Cloudflare R2, and prints the public URL.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.adapters.storage_client import get_storage_client
from app.adapters.veo_client import get_veo_client
from app.core.logging import configure_logging
from app.features.posts.prompt_builder import build_video_prompt_from_seed
from app.features.videos.handlers import _build_provider_prompt_request


ASPECT_RATIO = "9:16"
RESOLUTION = "720p"
POLL_INTERVAL_SECONDS = 15
MAX_POLLS = 40

SEED_DATA = {
    "script": (
        "Wenn enge Tueren stressig sind, helfen mir kleine Routinen im Alltag. "
        "Damit bleibe ich entspannt und komme trotzdem sicher weiter."
    )
}


def main() -> int:
    configure_logging()

    prompt = build_video_prompt_from_seed(SEED_DATA)
    prompt_request = _build_provider_prompt_request(prompt, "veo_3_1")

    prompt_text = prompt_request["prompt_text"] or ""
    negative_prompt = prompt_request["negative_prompt"]

    print("=== Live VEO Prompt Preview ===")
    print(prompt_text)
    print()
    print("=== Live VEO negativePrompt ===")
    print(negative_prompt or "")
    print()

    correlation_id = f"live_veo_r2_{uuid4().hex[:10]}"
    veo_client = get_veo_client()
    storage_client = get_storage_client()

    submission = veo_client.submit_video_generation(
        prompt=prompt_text,
        negative_prompt=negative_prompt,
        correlation_id=correlation_id,
        aspect_ratio=ASPECT_RATIO,
        resolution=RESOLUTION,
    )

    operation_id = submission["operation_id"]
    print(f"Submitted operation: {operation_id}")

    for poll_index in range(1, MAX_POLLS + 1):
        time.sleep(POLL_INTERVAL_SECONDS)
        status = veo_client.check_operation_status(
            operation_id=operation_id,
            correlation_id=correlation_id,
        )
        print(f"Poll {poll_index}/{MAX_POLLS}: {status['status']}")

        if not status["done"]:
            continue

        video_data = status.get("video_data") or {}
        video_uri = video_data.get("video_uri")
        if not video_uri:
            raise RuntimeError("VEO completed without returning a video URI")

        video_bytes = veo_client.download_video(
            video_uri=video_uri,
            correlation_id=correlation_id,
        )

        upload = storage_client.upload_video(
            video_bytes=video_bytes,
            file_name=f"{correlation_id}.mp4",
            correlation_id=correlation_id,
        )

        print("=== Live VEO Upload Result ===")
        print(f"Operation ID: {operation_id}")
        print(f"Storage key: {upload['storage_key']}")
        print(f"Public URL: {upload['url']}")
        print(f"Size bytes: {upload['size']}")
        return 0

    raise TimeoutError(
        f"VEO operation {operation_id} did not complete within "
        f"{MAX_POLLS * POLL_INTERVAL_SECONDS} seconds"
    )


if __name__ == "__main__":
    raise SystemExit(main())
