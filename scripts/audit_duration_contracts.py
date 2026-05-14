from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.adapters.supabase_client import get_supabase  # noqa: E402
from app.core.video_profiles import (  # noqa: E402
    estimate_duration_from_word_count,
    get_script_duration_bounds,
    script_word_count,
)
from app.features.topics.topic_validation import resolve_effective_script_text  # noqa: E402


BLOCKING_STATUSES = {
    "underlength",
    "overlength",
    "missing_script",
    "missing_tier",
    "video_generated_from_bad_script",
}


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _status(*, script: str, post_type: str, target_length_tier: Any, has_video: bool) -> Dict[str, Any]:
    if target_length_tier in (None, ""):
        return {"target_length_tier": None, "status": "missing_tier", "word_count": 0, "min_words": None, "max_words": None}
    tier = int(target_length_tier)
    try:
        min_words, max_words = get_script_duration_bounds(post_type, tier)
    except ValueError:
        word_count = script_word_count(script)
        return {
            "target_length_tier": tier,
            "status": "not_applicable",
            "word_count": word_count,
            "min_words": None,
            "max_words": None,
            "estimated_duration_s": estimate_duration_from_word_count(word_count),
        }
    word_count = script_word_count(script)
    if not script.strip():
        status = "missing_script"
    elif word_count < min_words:
        status = "underlength"
    elif word_count > max_words:
        status = "overlength"
    else:
        status = "valid"
    if has_video and status in {"underlength", "overlength", "missing_script"}:
        status = "video_generated_from_bad_script"
    return {
        "target_length_tier": tier,
        "status": status,
        "word_count": word_count,
        "min_words": min_words,
        "max_words": max_words,
        "estimated_duration_s": estimate_duration_from_word_count(word_count),
    }


def audit_post_row(row: Dict[str, Any]) -> Dict[str, Any]:
    seed_data = _as_dict(row.get("seed_data"))
    video_prompt = _as_dict(row.get("video_prompt_json"))
    metadata = _as_dict(row.get("video_metadata"))
    tier = seed_data.get("target_length_tier") or row.get("target_length_tier") or metadata.get("target_length_tier")
    script = resolve_effective_script_text(seed_data, video_prompt)
    has_video = bool(
        row.get("video_url")
        or row.get("video_operation_id")
        or str(row.get("video_status") or "") in {"completed", "submitted", "processing", "extended_submitted", "extended_processing"}
    )
    base = _status(script=script, post_type=row.get("post_type") or "value", target_length_tier=tier, has_video=has_video)
    duration_contract_status = seed_data.get("duration_contract_status") or metadata.get("duration_contract_status")
    if duration_contract_status == "needs_repair" and base.get("status") in BLOCKING_STATUSES:
        base["status"] = "needs_repair"
    base.update(
        {
            "table": "posts",
            "row_id": row.get("id"),
            "post_type": row.get("post_type") or "value",
            "has_video": has_video,
            "video_status": row.get("video_status"),
            "duration_contract_status": duration_contract_status,
            "script_preview": script[:180],
        }
    )
    return base


def audit_topic_script_row(row: Dict[str, Any]) -> Dict[str, Any]:
    script = str(row.get("script") or "")
    base = _status(
        script=script,
        post_type=row.get("post_type") or "value",
        target_length_tier=row.get("target_length_tier"),
        has_video=False,
    )
    if row.get("audit_status") == "needs_repair" and base.get("status") in BLOCKING_STATUSES:
        base["status"] = "needs_repair"
    base.update(
        {
            "table": "topic_scripts",
            "row_id": row.get("id"),
            "post_type": row.get("post_type") or "value",
            "script_preview": script[:180],
            "audit_status": row.get("audit_status"),
        }
    )
    return base


def _fetch_all(table: str, fields: str, page_size: int = 1000) -> Iterable[Dict[str, Any]]:
    sb = get_supabase().client
    offset = 0
    while True:
        response = sb.table(table).select(fields).range(offset, offset + page_size - 1).execute()
        rows = response.data or []
        for row in rows:
            yield row
        if len(rows) < page_size:
            break
        offset += page_size


def _write_json(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "table", "row_id", "post_type", "target_length_tier", "word_count",
        "min_words", "max_words", "estimated_duration_s", "status",
        "has_video", "video_status", "duration_contract_status", "audit_status", "script_preview",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="output/audits")
    parser.add_argument("--format", choices=["json", "csv", "both"], default="both")
    args = parser.parse_args()

    rows: List[Dict[str, Any]] = []
    for row in _fetch_all("posts", "id,post_type,seed_data,video_prompt_json,video_metadata,video_url,video_operation_id,video_status"):
        rows.append(audit_post_row(row))
    for row in _fetch_all("topic_scripts", "id,post_type,target_length_tier,script,audit_status"):
        rows.append(audit_topic_script_row(row))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    if args.format in {"json", "both"}:
        _write_json(output_dir / f"duration-contract-audit-{stamp}.json", rows)
    if args.format in {"csv", "both"}:
        _write_csv(output_dir / f"duration-contract-audit-{stamp}.csv", rows)

    blocking = [row for row in rows if row.get("status") in BLOCKING_STATUSES]
    print(f"audited={len(rows)} blocking={len(blocking)}")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
