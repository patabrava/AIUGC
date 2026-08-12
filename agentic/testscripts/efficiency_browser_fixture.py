"""Serve the real UI with deterministic in-memory review data for browser regression checks."""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("BYPASS_AUTH_IN_DEVELOPMENT", "true")
os.environ.setdefault("ENVIRONMENT", "development")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_posts_script_review import _FakeSupabase  # noqa: E402

from app.features.batches import handlers as batch_handlers  # noqa: E402
from app.features.posts import handlers as post_handlers  # noqa: E402
from app.features.publish import handlers as publish_handlers  # noqa: E402
from app.features.semantic_videos import queries as semantic_video_queries  # noqa: E402
from app.main import app  # noqa: E402


BATCH_ID = "00000000-0000-0000-0000-000000000101"
SCRIPT = (
    "Dieser klare Einstieg zeigt den Vorteil. "
    "Die praktische Lösung spart jeden Tag Zeit und hält Abläufe übersichtlich."
)

storage = {
    "batches": [
        {
            "id": BATCH_ID,
            "brand": "Efficiency Browser Fixture",
            "state": "S2_SEEDED",
            "creation_mode": "semantic_ugc",
            "post_type_counts": {"value": 2, "lifestyle": 0, "product": 0},
            "manual_post_count": None,
            "target_length_tier": None,
            "target_duration_seconds": 8,
            "video_pipeline_route": "semantic_video",
            "created_at": "2026-08-12T12:00:00Z",
            "updated_at": "2026-08-12T12:00:00Z",
            "archived": False,
            "meta_connection": {},
        }
    ],
    "posts": [
        {
            "id": f"00000000-0000-0000-0000-00000000020{index}",
            "batch_id": BATCH_ID,
            "post_type": "value",
            "topic_title": f"Efficiency script {index}",
            "topic_rotation": SCRIPT,
            "topic_cta": "Mehr erfahren",
            "spoken_duration": 8.0,
            "state": "S2_SEEDED",
            "seed_data": {
                "script": SCRIPT,
                "script_review_status": "pending",
                "target_length_tier": 8,
                "canonical_topic": f"Efficiency script {index}",
            },
            "video_prompt_json": None,
            "video_status": "pending",
            "video_url": None,
            "video_metadata": {},
            "publish_status": "pending",
            "blog_enabled": False,
            "blog_status": "disabled",
            "created_at": "2026-08-12T12:00:00Z",
            "updated_at": "2026-08-12T12:00:00Z",
        }
        for index in (1, 2)
    ],
}
fake_supabase = _FakeSupabase(storage)


def _batch_records(_batch_id: str):
    batch = deepcopy(storage["batches"][0])
    posts = deepcopy(storage["posts"])
    return (
        batch,
        {"posts_count": len(posts), "posts_by_state": {"value": len(posts)}},
        posts,
        {},
        {},
        [],
    )


async def _tiktok_state():
    return {"status": "unavailable", "publish_ready": False, "draft_ready": False}


batch_handlers.list_batches = lambda **_kwargs: (deepcopy(storage["batches"]), 1)
batch_handlers._load_batch_detail_records = _batch_records
batch_handlers.get_tiktok_publish_state = _tiktok_state
post_handlers.get_supabase = lambda: fake_supabase
publish_handlers.get_supabase = lambda: fake_supabase
publish_handlers.get_tiktok_publish_state = _tiktok_state
semantic_video_queries.get_run_by_post = lambda _post_id: None


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("EFFICIENCY_FIXTURE_PORT", "8012")),
        log_level="warning",
    )
