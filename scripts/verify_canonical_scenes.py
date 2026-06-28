"""Verify the canonical scene catalog and preview per-post scene routing.

Section 1 (--catalog): lists every SceneBible and whether its canonical plate exists in
canonical_scene_assets (status + image URL), so you can confirm all scenes are generated.

Section 2 (--posts N): pulls the N most recent posts and runs the exact resolver the video
submission path uses, showing which canonical scene plate each upcoming video would get,
plus a distribution summary so you can sanity-check routing on your real content.

Usage (from AIUGC/, with the venv active so .env credentials load):

    python scripts/verify_canonical_scenes.py
    python scripts/verify_canonical_scenes.py --posts 50
    python scripts/verify_canonical_scenes.py --batch <batch_id>
    python scripts/verify_canonical_scenes.py --no-posts   # catalog only
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.supabase_client import get_supabase  # noqa: E402
from app.features.characters.scene_reference import (  # noqa: E402
    NEUTRAL_SCENE_POOL,
    SCENE_BIBLES,
    SPECIALIZED_SCENE_ROUTES,
)
from app.features.scenes import queries as scene_queries  # noqa: E402

_SPECIALIZED_IDS = {scene_id for scene_id, _reason, _tokens in SPECIALIZED_SCENE_ROUTES}


def _category(scene_id: str) -> str:
    if scene_id in _SPECIALIZED_IDS:
        return "specialized"
    if scene_id in NEUTRAL_SCENE_POOL:
        return "neutral-pool"
    return "other"


def _as_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def print_catalog() -> None:
    print("=" * 78)
    print("CANONICAL SCENE CATALOG")
    print("=" * 78)
    missing = 0
    for scene_id in SCENE_BIBLES:
        try:
            record = scene_queries.get_canonical_scene_asset(scene_key=scene_id)
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERR ] {scene_id:28s} {_category(scene_id):13s} {exc}")
            missing += 1
            continue
        if record and record.status == "generated" and record.image_url:
            print(f"  [ OK ] {scene_id:28s} {_category(scene_id):13s} v{record.scene_bible_version}  {record.image_url}")
        else:
            status = record.status if record else "MISSING"
            print(f"  [MISS] {scene_id:28s} {_category(scene_id):13s} status={status}")
            missing += 1
    print(f"\n  {len(SCENE_BIBLES) - missing}/{len(SCENE_BIBLES)} scenes generated; {missing} missing.\n")


def preview_routing(limit: int, batch_id: str | None) -> None:
    print("=" * 78)
    print(f"ROUTING PREVIEW (most recent {limit} posts{f', batch {batch_id}' if batch_id else ''})")
    print("=" * 78)
    query = (
        get_supabase()
        .client.table("posts")
        .select("id,post_type,topic_title,seed_data,video_prompt_json,batch_id,created_at")
    )
    if batch_id:
        query = query.eq("batch_id", batch_id)
    rows = query.order("created_at", desc=True).limit(limit).execute().data or []

    if not rows:
        print("  (no posts found)\n")
        return

    distribution: Counter[str] = Counter()
    for post in rows:
        seed_data = _as_dict(post.get("seed_data"))
        video_prompt = _as_dict(post.get("video_prompt_json"))
        # Mirror production: the post's topic_title is surfaced to the resolver for routing.
        routing_seed_data = {**seed_data, "topic_title": seed_data.get("topic_title") or post.get("topic_title") or ""}
        scene_key = scene_queries.resolve_canonical_scene_key(
            scene_text=str(video_prompt.get("scene") or ""),
            prompt_text=str(video_prompt.get("veo_prompt") or ""),
            post_type=str(post.get("post_type") or ""),
            seed_data=routing_seed_data,
            target_length_tier=int(seed_data.get("target_length_tier") or 8),
        )
        distribution[scene_key] += 1
        topic = str(post.get("topic_title") or seed_data.get("canonical_topic") or "")[:52]
        print(f"  {scene_key:28s} <- [{str(post.get('post_type') or '?'):9s}] {topic}")

    print("\n  Distribution across these posts:")
    for scene_key, count in distribution.most_common():
        print(f"    {count:4d}  {scene_key} ({_category(scene_key)})")
    print(f"\n  {len(distribution)} distinct scenes used across {len(rows)} posts.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify canonical scenes and preview routing.")
    parser.add_argument("--posts", type=int, default=30, help="How many recent posts to route (default 30).")
    parser.add_argument("--batch", default=None, help="Limit the routing preview to one batch_id.")
    parser.add_argument("--no-posts", action="store_true", help="Catalog only; skip the routing preview.")
    args = parser.parse_args()

    print_catalog()
    if not args.no_posts:
        preview_routing(limit=args.posts, batch_id=args.batch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
