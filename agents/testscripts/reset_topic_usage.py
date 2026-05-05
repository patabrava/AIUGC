"""Back up and reset topic usage counters for live stress tests.

Generated artifact. Run from the repo root with the project virtualenv:
    python agents/testscripts/reset_topic_usage.py reset
    python agents/testscripts/reset_topic_usage.py restore --backup agents/testscripts/topic_usage_backup_...
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.supabase_client import get_supabase


TABLES = {
    "topic_registry": "id,use_count,last_used_at",
    "topic_scripts": "id,use_count,last_used_at",
}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fetch_all(table: str, fields: str, page_size: int = 1000) -> List[Dict[str, Any]]:
    client = get_supabase().client
    rows: List[Dict[str, Any]] = []
    start = 0
    while True:
        end = start + page_size - 1
        response = client.table(table).select(fields).range(start, end).execute()
        page = list(response.data or [])
        rows.extend(page)
        if len(page) < page_size:
            return rows
        start += page_size


def _backup_payload() -> Dict[str, Any]:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": {table: _fetch_all(table, fields) for table, fields in TABLES.items()},
    }


def reset_usage(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    backup_path = output_dir / f"topic_usage_backup_{_utc_stamp()}.json"
    backup = _backup_payload()
    backup_path.write_text(json.dumps(backup, indent=2, ensure_ascii=False), encoding="utf-8")

    client = get_supabase().client
    for table, rows in backup["tables"].items():
        for row in rows:
            payload: Dict[str, Any] = {"use_count": 0}
            if table == "topic_scripts":
                payload["last_used_at"] = None
            client.table(table).update(payload).eq("id", row["id"]).execute()

    summary = {
        table: {
            "rows": len(rows),
            "previously_used": sum(1 for row in rows if int(row.get("use_count") or 0) > 0 or row.get("last_used_at")),
        }
        for table, rows in backup["tables"].items()
    }
    print(json.dumps({"backup": str(backup_path), "summary": summary}, indent=2))
    return backup_path


def restore_usage(backup_path: Path) -> None:
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    client = get_supabase().client
    for table, rows in (backup.get("tables") or {}).items():
        for row in rows:
            client.table(table).update(
                {
                    "use_count": int(row.get("use_count") or 0),
                    "last_used_at": row.get("last_used_at"),
                }
            ).eq("id", row["id"]).execute()
    print(json.dumps({"restored": str(backup_path)}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["reset", "restore"])
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("agents/testscripts"))
    args = parser.parse_args()

    if args.action == "reset":
        reset_usage(args.output_dir)
        return
    if not args.backup:
        raise SystemExit("--backup is required for restore")
    restore_usage(args.backup)


if __name__ == "__main__":
    main()
