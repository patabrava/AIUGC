"""
EYE testscript: end-to-end topic-bank harvest and length-first discovery.

Runs on the configured Supabase + Gemini stack:
1. harvest one 16-second value topic and one 16-second lifestyle topic into the durable topic bank
2. verify a completed topic_research_runs row and length-aware suggestions exist
3. create a 16-second batch that requests one value post
4. run topic discovery and confirm the post is created from the stored bank
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.adapters.supabase_client import get_supabase
from app.core.logging import configure_logging
from app.core.states import BatchState
from app.features.batches.queries import create_batch, get_batch_by_id
from app.features.topics.handlers import discover_topics_for_batch, harvest_topics_to_bank_sync
from app.features.topics.queries import list_topic_suggestions


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
    ):
        _require_env(required)

    harvest = harvest_topics_to_bank_sync(
        post_type_counts={"value": 1, "lifestyle": 1, "product": 0},
        target_length_tier=16,
        trigger_source="e2e_topic_bank",
    )
    print("HARVEST_START")
    print(_json(harvest))
    print("HARVEST_END")

    if harvest.get("stored_by_type", {}).get("value", 0) < 1:
        raise RuntimeError(f"Expected at least one stored value topic, got: {_json(harvest)}")
    if harvest.get("stored_by_type", {}).get("lifestyle", 0) < 1:
        raise RuntimeError(f"Expected at least one stored lifestyle topic, got: {_json(harvest)}")

    suggestions = list_topic_suggestions(target_length_tier=16, limit=5, post_type="value")
    print("SUGGESTIONS_START")
    print(_json([{"id": row["id"], "title": row["title"]} for row in suggestions]))
    print("SUGGESTIONS_END")
    if not suggestions:
        raise RuntimeError("Expected at least one stored 16-second value suggestion")

    supabase = get_supabase().client
    run_id = harvest.get("run_id")
    if not run_id:
        raise RuntimeError(f"Harvest result missing run_id: {_json(harvest)}")

    run_rows = (
        supabase.table("topic_research_runs")
        .select("*")
        .eq("id", run_id)
        .execute()
        .data
        or []
    )
    if len(run_rows) != 1 or run_rows[0].get("status") != "completed":
        raise RuntimeError(f"Expected one completed topic research run for {run_id}, got: {_json(run_rows)}")

    dossier_rows = (
        supabase.table("topic_research_dossiers")
        .select("*")
        .eq("target_length_tier", 16)
        .execute()
        .data
        or []
    )
    if not dossier_rows:
        raise RuntimeError(f"Expected at least one normalized research dossier row, got: {_json(dossier_rows)}")

    recent_value_rows = (
        supabase.table("topic_registry")
        .select("*")
        .eq("post_type", "value")
        .order("last_harvested_at", desc=True)
        .limit(5)
        .execute()
        .data
        or []
    )
    rich_row = None
    for row in recent_value_rows:
        tier_variants = (
            supabase.table("topic_scripts")
            .select("*")
            .eq("topic_registry_id", row["id"])
            .eq("target_length_tier", 16)
            .execute()
            .data
            or []
        )
        if len(tier_variants) >= 3:
            rich_row = row
            topic_script_rows = tier_variants
            break
    if rich_row is None:
        raise RuntimeError(
            "Expected one recently harvested value topic with a richer topic_scripts bank, got: "
            f"{_json(recent_value_rows)}"
        )
    if len(topic_script_rows) != 3:
        raise RuntimeError(f"Expected 3 stored 16-second script variants, got {len(topic_script_rows)}")

    print("RICH_ROW_START")
    print(
        _json(
            {
                "id": rich_row.get("id"),
                "title": rich_row.get("title"),
                "script_count_16": len(topic_script_rows),
                "first_script_source_urls": (topic_script_rows[0] or {}).get("source_urls", []),
            }
        )
    )
    print("RICH_ROW_END")

    first_variant = topic_script_rows[0]
    if not first_variant.get("source_urls"):
        raise RuntimeError(
            "Expected script variants to preserve source_urls in topic_scripts, got: "
            f"{_json(first_variant)}"
        )

    stored_rows = (
        supabase.table("topic_registry")
        .select("*")
        .eq("id", rich_row["id"])
        .execute()
        .data
        or []
    )
    if len(stored_rows) != 1:
        raise RuntimeError(f"Expected one raw topic registry row, got: {_json(stored_rows)}")
    stored_row = stored_rows[0]
    if "script" not in stored_row or not str(stored_row.get("script") or "").strip():
        raise RuntimeError(f"Expected script-only registry storage, got: {_json(stored_row)}")
    if "rotation" in stored_row or "cta" in stored_row:
        raise RuntimeError(f"Expected rotation/cta to be removed from registry, got: {_json(stored_row)}")
    # Legacy columns (script_bank, seed_payloads, research_payload) have been dropped from topic_registry.

    batch_brand = f"EYE topic bank 16s {datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    batch = create_batch(
        brand=batch_brand,
        post_type_counts={"value": 1, "lifestyle": 0, "product": 0},
        target_length_tier=16,
    )
    print(f"BATCH_ID={batch['id']}")
    print(f"BATCH_BRAND={batch_brand}")

    discovery = await discover_topics_for_batch(batch["id"])
    print("DISCOVERY_START")
    print(_json(discovery))
    print("DISCOVERY_END")

    posts = (
        supabase.table("posts")
        .select("*")
        .eq("batch_id", batch["id"])
        .execute()
        .data
        or []
    )
    if len(posts) != 1:
        raise RuntimeError(f"Expected exactly one discovered post, got {len(posts)}")

    post = posts[0]
    seed_data: Dict[str, Any] = post.get("seed_data") or {}
    if isinstance(seed_data, str):
        seed_data = json.loads(seed_data)

    final_batch = get_batch_by_id(batch["id"])
    print("POST_START")
    print(_json(post))
    print("POST_END")
    print(f"FINAL_BATCH_STATE={final_batch['state']}")

    if final_batch["state"] != BatchState.S2_SEEDED.value:
        raise RuntimeError(f"Expected batch to advance to S2_SEEDED, got {final_batch['state']}")
    if post.get("post_type") != "value":
        raise RuntimeError(f"Expected a value post, got {post.get('post_type')}")
    if int(seed_data.get("target_length_tier") or 0) != 16:
        raise RuntimeError(f"Expected target_length_tier=16 in seed_data, got: {_json(seed_data)}")
    # script_bank column has been dropped from topic_registry.
    if "dialog_script" not in seed_data or "strict_seed" not in seed_data:
        raise RuntimeError(f"Expected dialog_script and strict_seed in seed_data, got: {_json(seed_data)}")
    if post.get("topic_title") not in {row["title"] for row in suggestions}:
        raise RuntimeError(
            "Expected discovered post to use a stored suggestion title. "
            f"post={post.get('topic_title')} suggestions={_json([row['title'] for row in suggestions])}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
