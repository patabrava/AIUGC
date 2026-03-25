"""Preview live generated caption bundles for the most recent researched topics/posts."""

from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
env_candidates = [
    ROOT / ".env",
    ROOT.parent / "AIUGC" / ".env",
]
env_path = next((candidate for candidate in env_candidates if candidate.exists()), None)
for line in env_path.read_text().splitlines() if env_path else []:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    os.environ.setdefault(key, value)

from app.adapters.supabase_client import get_supabase
from app.features.topics.captions import attach_caption_bundle


def _load_recent_samples(limit: int = 3) -> List[Dict[str, Any]]:
    supabase = get_supabase().client
    samples: List[Dict[str, Any]] = []

    dossier_rows = (
        supabase.table("topic_research_dossiers")
        .select("normalized_payload")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    for row in dossier_rows:
        payload = row.get("normalized_payload") or {}
        samples.append(
            {
                "topic_title": payload.get("seed_topic") or payload.get("topic") or "Unbekanntes Thema",
                "post_type": "value",
                "seed_data": {
                    "script": "Kontext aus dem Forschungsdossier, noch ohne finalen Dialog.",
                    "description": payload.get("source_summary") or "",
                    "strict_seed": {"facts": list(payload.get("facts") or [])[:4]},
                },
            }
        )

    post_rows = (
        supabase.table("posts")
        .select("topic_title,post_type,seed_data")
        .order("created_at", desc=True)
        .limit(12)
        .execute()
        .data
        or []
    )
    for row in post_rows:
        seed_data = dict(row.get("seed_data") or {})
        if not (seed_data.get("dialog_script") or seed_data.get("script")):
            continue
        samples.append(
            {
                "topic_title": row.get("topic_title") or "Unbekanntes Thema",
                "post_type": row.get("post_type") or "value",
                "seed_data": seed_data,
            }
        )
        if len(samples) >= limit:
            break
    return samples[:limit]


if __name__ == "__main__":
    for sample in _load_recent_samples():
        enriched = attach_caption_bundle(
            sample["seed_data"],
            topic_title=sample["topic_title"],
            post_type=sample["post_type"],
            script_fallback=sample["seed_data"].get("script") or "",
            context=sample["seed_data"].get("description") or sample["seed_data"].get("caption") or "",
        )
        bundle = enriched.get("caption_bundle") or {}
        print(f"TOPIC: {sample['topic_title']}")
        print(f"POST_TYPE: {sample['post_type']}")
        print(f"SELECTED: {bundle.get('selected_key')}")
        for variant in bundle.get("variants") or []:
            print(f"\n[{variant.get('key')}]")
            print(variant.get("body") or "")
        print("\nVALID: yes")
        print("-" * 80)
