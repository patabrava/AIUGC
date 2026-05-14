from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.supabase_client import get_supabase  # noqa: E402


BLOCKING_STATUSES = {"underlength", "overlength", "missing_script", "missing_tier", "video_generated_from_bad_script"}


def _note(row: Dict[str, Any]) -> str:
    return (
        f"duration_contract_failure: {row.get('post_type')} {row.get('target_length_tier')}s "
        f"has {row.get('word_count')} words; expected {row.get('min_words')}-{row.get('max_words')}; "
        f"status={row.get('status')}"
    )


def build_repair_update(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if row.get("status") not in BLOCKING_STATUSES:
        return None
    table = row.get("table")
    row_id = row.get("row_id")
    if not table or not row_id:
        return None
    note = _note(row)
    if table == "topic_scripts":
        return {
            "table": "topic_scripts",
            "row_id": row_id,
            "payload": {
                "audit_status": "needs_repair",
                "quality_notes": note,
            },
        }
    if table == "posts":
        seed_patch = {
            "script_review_status": "pending",
            "duration_contract_status": "needs_repair",
            "duration_contract_note": note,
        }
        metadata_patch = {
            "duration_contract_status": "needs_repair",
            "duration_contract_note": note,
            "duration_contract_checked_at": datetime.now(timezone.utc).isoformat(),
        }
        payload: Dict[str, Any] = {}
        if not row.get("has_video"):
            payload["video_prompt_json"] = None
        return {
            "table": "posts",
            "row_id": row_id,
            "payload": payload,
            "seed_patch": seed_patch,
            "metadata_patch": metadata_patch,
        }
    return None


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows") if isinstance(data, dict) else data
    return [row for row in rows if isinstance(row, dict)]


def _apply_update(update: Dict[str, Any]) -> None:
    sb = get_supabase().client
    table = update["table"]
    row_id = update["row_id"]
    payload = dict(update.get("payload") or {})
    if table == "posts":
        current_rows = sb.table("posts").select("seed_data,video_metadata").eq("id", row_id).limit(1).execute().data or []
        if not current_rows:
            return
        current = current_rows[0]
        seed_data = current.get("seed_data") or {}
        metadata = current.get("video_metadata") or {}
        if not isinstance(seed_data, dict):
            seed_data = {}
        if not isinstance(metadata, dict):
            metadata = {}
        seed_data.update(update.get("seed_patch") or {})
        metadata.update(update.get("metadata_patch") or {})
        payload["seed_data"] = seed_data
        payload["video_metadata"] = metadata
    sb.table(table).update(payload).eq("id", row_id).execute()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit_json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    updates = [update for row in _load_rows(Path(args.audit_json)) for update in [build_repair_update(row)] if update]
    output_path = Path(args.output) if args.output else Path(args.audit_json).with_name("duration-contract-repair-plan.json")
    output_path.write_text(json.dumps({"updates": updates}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"repair_updates={len(updates)} plan={output_path}")

    if args.apply:
        for update in updates:
            _apply_update(update)
        print(f"applied={len(updates)}")
    else:
        print("dry_run=true; pass --apply only after reviewing the repair plan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
