"""Read-only sweep: detect surviving ASCII-transliterated German tokens.

Scans every text and JSON column we know could carry generated copy. Reports per-table /
per-column counts of rows that still contain whole-word transliterations like "fuer",
"spaeter", "Huerden", etc. Word-boundary regex so we don't flag "Steuer", "kaufen", etc.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from typing import Any, Dict, Iterable, List, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ["SUPABASE_KEY"]
)
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backfill_umlauts import REPLACEMENTS  # noqa: E402

ASCII_FORMS = [src for src, _ in REPLACEMENTS if src.lower() != src.lower().replace("ss", "ß")]
# Keep the original list semantics: ASCII strings only. Filter no-ops.
ASCII_FORMS = [src for src, dst in REPLACEMENTS if src != dst]
WORD_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in ASCII_FORMS) + r")\b")

# (table, columns to scan as text/json — we collect every text-ish field).
TARGETS: List[Tuple[str, List[str]]] = [
    ("posts", [
        "publish_caption", "topic_title", "topic_rotation", "topic_cta",
        "seed_data", "video_prompt_json", "publish_results", "video_metadata",
        "blog_content",
    ]),
    ("topic_registry", ["title", "script", "canonical_topic", "merge_reason"]),
    ("topic_scripts", [
        "title", "script", "anchor_topic", "disclaimer", "source_summary",
        "primary_source_title", "seed_payload", "quality_notes",
    ]),
    ("topic_research_runs", [
        "seed_topic", "result_summary", "error_message",
        "raw_prompt", "raw_response", "normalized_payload",
    ]),
    ("topic_research_dossiers", [
        "seed_topic", "topic", "anchor_topic",
        "raw_prompt", "raw_response", "normalized_payload",
    ]),
    ("topic_research_cron_runs", ["error_message", "details"]),
    ("video_prompt_audit", ["prompt_text", "negative_prompt"]),
    ("batches", ["brand", "character_snapshot", "scene_plan"]),
    ("characters", ["name"]),
]


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        import json as _json
        return _json.dumps(value, ensure_ascii=False)
    return str(value)


def fetch_pages(table: str, columns: List[str]) -> Iterable[Dict[str, Any]]:
    select_clause = ",".join(["id", *columns])
    page = 500
    offset = 0
    with httpx.Client(timeout=30.0) as client:
        while True:
            resp = client.get(
                f"{SUPABASE_URL}/rest/v1/{table}",
                headers=HEADERS,
                params={
                    "select": select_clause,
                    "limit": str(page),
                    "offset": str(offset),
                    "order": "id.asc",
                },
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                return
            for row in rows:
                yield row
            if len(rows) < page:
                return
            offset += page


def main() -> int:
    total_offending_rows = 0
    overall_word_counter: Counter = Counter()
    print(f"Scanning {len(TARGETS)} tables for ASCII-transliterated German tokens")
    print("=" * 78)
    samples_to_show = 3
    for table, columns in TARGETS:
        per_col: Counter = Counter()
        per_word: Counter = Counter()
        offending_rows: List[Tuple[str, str, List[str]]] = []
        try:
            scanned = 0
            for row in fetch_pages(table, columns):
                scanned += 1
                for col in columns:
                    text = stringify(row.get(col))
                    if not text:
                        continue
                    hits = WORD_RE.findall(text)
                    if hits:
                        per_col[col] += 1
                        for h in hits:
                            per_word[h] += 1
                        if len(offending_rows) < samples_to_show:
                            offending_rows.append((row["id"], col, list(dict.fromkeys(hits))))
        except httpx.HTTPStatusError as exc:
            print(f"[skip] {table}: {exc.response.status_code}")
            continue

        n_rows = sum(per_col.values())
        total_offending_rows += n_rows
        overall_word_counter.update(per_word)
        status = "CLEAN" if n_rows == 0 else "DIRTY"
        print(f"\n[{status}] {table}  scanned={scanned}  offending_column_hits={n_rows}")
        if per_col:
            for col, c in per_col.most_common():
                print(f"     {col}: {c} rows")
            print(f"     words: {dict(per_word.most_common(10))}")
            for rid, col, words in offending_rows:
                print(f"     sample id={rid[:8]} col={col} words={words}")

    print("\n" + "=" * 78)
    print(f"TOTAL OFFENDING COLUMN-HITS: {total_offending_rows}")
    if overall_word_counter:
        print(f"Top words across all tables: {dict(overall_word_counter.most_common(15))}")
    return 0 if total_offending_rows == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
