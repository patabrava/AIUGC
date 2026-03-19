"""
EYE testscript: full 16-second value-post workflow through the live Veo pipeline.

Runs on the current branch against the configured Supabase + Gemini/Veo + R2 stack:
1. create one batch with target_length_tier=16
2. run topic discovery
3. approve the generated script
4. build the prompt
5. submit video generation
6. poll until the extended Veo chain completes
7. print batch/post/video artifacts
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.adapters.supabase_client import get_supabase
from app.core.logging import configure_logging
from app.core.states import BatchState
from app.features.batches.queries import create_batch, get_batch_by_id, update_batch_state
from app.features.posts.handlers import build_post_prompt
from app.features.topics.handlers import discover_topics_for_batch
from app.features.videos.handlers import BatchVideoGenerationRequest, generate_all_videos
from workers.video_poller import process_video_operation


POLL_INTERVAL_SECONDS = 20
MAX_POLLS = 80


def _require_env(name: str) -> None:
    if not os.getenv(name):
        raise RuntimeError(f"Missing required environment variable: {name}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, indent=2)


async def main() -> int:
    configure_logging()

    if not os.getenv("GOOGLE_AI_API_KEY") and os.getenv("GEMINI_API_KEY"):
        os.environ["GOOGLE_AI_API_KEY"] = os.environ["GEMINI_API_KEY"]
    if not os.getenv("SUPABASE_KEY") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        os.environ["SUPABASE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    if not os.getenv("SUPABASE_SERVICE_KEY") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        os.environ["SUPABASE_SERVICE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    for required in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_KEY",
        "GOOGLE_AI_API_KEY",
        "CLOUDFLARE_R2_ACCOUNT_ID",
        "CLOUDFLARE_R2_ACCESS_KEY_ID",
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
        "CLOUDFLARE_R2_BUCKET_NAME",
        "CLOUDFLARE_R2_PUBLIC_BASE_URL",
    ):
        _require_env(required)

    batch_brand = f"EYE value 16s {datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    batch = create_batch(
        brand=batch_brand,
        post_type_counts={"value": 1, "lifestyle": 0, "product": 0},
        target_length_tier=16,
    )
    batch_id = batch["id"]
    print(f"BATCH_ID={batch_id}")
    print(f"BATCH_BRAND={batch_brand}")

    discovery = await discover_topics_for_batch(batch_id)
    print(f"DISCOVERY={_json(discovery)}")

    supabase = get_supabase().client
    posts_response = supabase.table("posts").select("*").eq("batch_id", batch_id).execute()
    posts = posts_response.data or []
    if len(posts) != 1:
        raise RuntimeError(f"Expected exactly 1 post, found {len(posts)}")

    post = posts[0]
    post_id = post["id"]
    seed_data: Dict[str, Any] = post.get("seed_data") or {}
    if isinstance(seed_data, str):
        seed_data = json.loads(seed_data)

    print(f"POST_ID={post_id}")
    print("SCRIPT_START")
    print(seed_data.get("script", ""))
    print("SCRIPT_END")

    seed_data["script_review_status"] = "approved"
    seed_data.pop("video_excluded", None)
    supabase.table("posts").update({"seed_data": seed_data}).eq("id", post_id).execute()
    update_batch_state(batch_id, BatchState.S4_SCRIPTED)

    await build_post_prompt(post_id)

    submission_request = BatchVideoGenerationRequest(
        aspect_ratio="9:16",
        resolution="720p",
    )
    submission = await generate_all_videos(batch_id, submission_request)
    print(f"SUBMISSION={_json(submission.data)}")

    for poll_index in range(1, MAX_POLLS + 1):
        refreshed = supabase.table("posts").select("*").eq("id", post_id).execute().data[0]
        print(
            f"POLL {poll_index}/{MAX_POLLS} "
            f"status={refreshed.get('video_status')} "
            f"op={refreshed.get('video_operation_id')}"
        )

        if refreshed.get("video_status") == "completed":
            break
        if refreshed.get("video_status") == "failed":
            raise RuntimeError(f"Video generation failed: {_json(refreshed.get('video_metadata'))}")

        process_video_operation(refreshed)
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        raise TimeoutError(f"Video generation did not complete within {MAX_POLLS * POLL_INTERVAL_SECONDS} seconds")

    final_post = supabase.table("posts").select("*").eq("id", post_id).execute().data[0]
    final_batch = get_batch_by_id(batch_id)
    final_metadata = final_post.get("video_metadata") or {}

    print(f"FINAL_BATCH_STATE={final_batch.get('state')}")
    print(f"FINAL_POST_STATUS={final_post.get('video_status')}")
    print(f"FINAL_VIDEO_URL={final_post.get('video_url')}")
    print("FINAL_VIDEO_METADATA_START")
    print(_json(final_metadata))
    print("FINAL_VIDEO_METADATA_END")

    if not final_post.get("video_url"):
        raise RuntimeError("Completed post is missing video_url")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
