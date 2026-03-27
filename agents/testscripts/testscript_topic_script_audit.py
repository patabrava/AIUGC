"""
EYE testscript: audit persisted topic_scripts rows for spoken-copy contamination.

Fails if Supabase contains:
- research-label leakage
- citation residue
- malformed artifact tails
- incomplete trailing clauses
- canonical value/product rows outside the tier word/sentence envelope
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.adapters.supabase_client import get_supabase
from app.features.topics.topic_validation import (
    detect_spoken_copy_issues,
    get_prompt1_sentence_bounds,
    get_prompt1_word_bounds,
)


def _word_count(text: Any) -> int:
    import re

    return len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", str(text or "")))


def _sentence_count(text: Any) -> int:
    import re

    cleaned = str(text or "").strip()
    if not cleaned:
        return 0
    return len([segment for segment in re.split(r"(?<=[.!?])\s+", cleaned) if segment.strip()])


def _load_rows() -> List[Dict[str, Any]]:
    sb = get_supabase()
    response = (
        sb.client.table("topic_scripts")
        .select("id,title,post_type,target_length_tier,bucket,lane_key,script")
        .order("created_at", desc=True)
        .execute()
    )
    return list(response.data or [])


def main() -> int:
    rows = _load_rows()
    issue_rows: List[Dict[str, Any]] = []
    issue_kinds = Counter()

    for row in rows:
        script = str(row.get("script") or "").strip()
        row_issues = list(detect_spoken_copy_issues(script))
        tier = int(row.get("target_length_tier") or 0)
        bucket = str(row.get("bucket") or "").strip()
        post_type = str(row.get("post_type") or "").strip()
        if bucket == "canonical" and post_type in {"value", "product"}:
            min_words, max_words = get_prompt1_word_bounds(tier)
            min_sentences, max_sentences = get_prompt1_sentence_bounds(tier)
            word_count = _word_count(script)
            sentence_count = _sentence_count(script)
            if word_count < min_words or word_count > max_words:
                row_issues.append(
                    {
                        "kind": "canonical_word_envelope",
                        "word_count": word_count,
                        "expected_words": [min_words, max_words],
                    }
                )
            if sentence_count < min_sentences or sentence_count > max_sentences:
                row_issues.append(
                    {
                        "kind": "canonical_sentence_envelope",
                        "sentence_count": sentence_count,
                        "expected_sentences": [min_sentences, max_sentences],
                    }
                )
        if not row_issues:
            continue
        for issue in row_issues:
            issue_kinds[issue["kind"]] += 1
        issue_rows.append(
            {
                "id": row.get("id"),
                "title": row.get("title"),
                "post_type": post_type,
                "target_length_tier": tier,
                "bucket": bucket,
                "lane_key": row.get("lane_key"),
                "issues": row_issues,
                "script": script,
            }
        )

    summary = {
        "total_rows": len(rows),
        "issue_count": len(issue_rows),
        "issues_by_kind": dict(issue_kinds),
        "rows": issue_rows,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if issue_rows else 0


if __name__ == "__main__":
    raise SystemExit(main())
