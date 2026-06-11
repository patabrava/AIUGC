"""Inspect what a batch's posts actually submitted to the video provider (read-only).

Confirms, per post: the pipeline route, segment count + op statuses, the shared seed, and the
reference-image metadata that was persisted at submit time. Use it to verify a segmented-route
Character Consistency run attached identical reference anchors (count 3) across every 8s segment.

Usage (from the worktree, venv active so .env credentials load):

    python scripts/inspect_segmented_batch.py --batch 6e97ac46-b873-4632-9d3d-5cbd968cf726
    python scripts/inspect_segmented_batch.py --batch <id> --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.supabase_client import get_supabase  # noqa: E402

# Reference-related keys may live at the top level of video_metadata or nested under
# provider_metadata, depending on the path that persisted them. Dig through both.
_REFERENCE_KEYS = (
    "reference_images_enabled",
    "reference_image_count",
    "reference_image_roles",
    "actor_identity_id",
    "source",
    "reference_images_skipped_reason",
    "canonical_scene_key",
    "canonical_scene_image_url",
)


def _dig(metadata: Dict[str, Any], key: str) -> Any:
    if key in metadata:
        return metadata[key]
    nested = metadata.get("provider_metadata")
    if isinstance(nested, dict) and key in nested:
        return nested[key]
    return None


def _inspect_post(post: Dict[str, Any]) -> Dict[str, Any]:
    metadata = post.get("video_metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    ops = metadata.get("veo_segment_ops") or []
    op_statuses = [op.get("status") for op in ops] if isinstance(ops, list) else []
    return {
        "post_id": post.get("id"),
        "video_status": post.get("video_status"),
        "video_provider": post.get("video_provider"),
        "provider_model": metadata.get("provider_model") or _dig(metadata, "provider_model"),
        "video_pipeline_route": metadata.get("video_pipeline_route"),
        "veo_segment_count": metadata.get("veo_segment_count"),
        "segment_ops_count": len(ops) if isinstance(ops, list) else 0,
        "segment_op_statuses": op_statuses,
        "veo_seed": metadata.get("veo_seed"),
        "segment_prompt_count": len(metadata.get("veo_segment_prompts") or []),
        "references": {key: _dig(metadata, key) for key in _REFERENCE_KEYS},
        "video_metadata_keys": sorted(metadata.keys()),
    }


def _print_human(batch: Dict[str, Any], reports: list[Dict[str, Any]]) -> None:
    print(f"BATCH {batch.get('id')}")
    print(f"  creation_mode      = {batch.get('creation_mode')}")
    print(f"  actor_identity_id  = {batch.get('actor_identity_id')}")
    print(f"  target_length_tier = {batch.get('target_length_tier')}")
    print(f"  posts              = {len(reports)}")
    print()
    for r in reports:
        refs = r["references"]
        print(f"POST {r['post_id']}  [{r['video_status']}]  provider={r['video_provider']}  model={r['provider_model']}")
        print(f"     route={r['video_pipeline_route']}  segment_count={r['veo_segment_count']}  "
              f"ops={r['segment_ops_count']} {r['segment_op_statuses']}  seed={r['veo_seed']}  "
              f"prompts={r['segment_prompt_count']}")
        print(f"     refs: enabled={refs.get('reference_images_enabled')}  count={refs.get('reference_image_count')}  "
              f"roles={refs.get('reference_image_roles')}  source={refs.get('source')}")
        print(f"           actor_identity_id={refs.get('actor_identity_id')}  "
              f"skipped={refs.get('reference_images_skipped_reason')}")
        if refs.get("canonical_scene_key"):
            print(f"           scene_key={refs.get('canonical_scene_key')}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a batch's video submissions (read-only).")
    parser.add_argument("--batch", required=True, help="Batch id to inspect.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    supabase = get_supabase().client
    batch_resp = supabase.table("batches").select("*").eq("id", args.batch).execute()
    if not batch_resp.data:
        print(f"Batch {args.batch} not found.", file=sys.stderr)
        return 1
    batch = batch_resp.data[0]
    posts = supabase.table("posts").select("*").eq("batch_id", args.batch).execute().data or []
    reports = [_inspect_post(p) for p in posts]

    if args.json:
        print(json.dumps({"batch": {k: batch.get(k) for k in ("id", "creation_mode", "actor_identity_id", "target_length_tier")}, "posts": reports}, indent=2, default=str))
    else:
        _print_human(batch, reports)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
