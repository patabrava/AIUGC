"""Stress harness for the value-post seeding flow.

Drives `POST /batches` with value posts repeatedly and polls
`GET /batches/{id}/status` until each reaches a terminal stage.
On the first explicit failure, dumps the surrounding uvicorn log
window so the traceback is easy to find.

Sequential by design — we want to reproduce the user's failure,
not stress concurrency.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

BASE_URL = os.environ.get("STRESS_BASE_URL", "http://127.0.0.1:8000")
HERE = Path(__file__).resolve().parent
UVICORN_LOG = HERE / "uvicorn.log"
RESULTS_LOG = HERE / "results.jsonl"
SUMMARY_FILE = HERE / "summary.md"

TERMINAL_STAGES = {"completed", "failed"}


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _post_batch(brand: str, value_count: int, target_tier: int) -> Dict[str, Any]:
    body = {
        "brand": brand,
        "creation_mode": "automated",
        "post_type_counts": {
            "value": value_count,
            "lifestyle": 0,
            "product": 0,
        },
        "target_length_tier": target_tier,
    }
    r = requests.post(
        f"{BASE_URL}/batches",
        json=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"]


def _get_status(batch_id: str) -> Dict[str, Any]:
    r = requests.get(f"{BASE_URL}/batches/{batch_id}/status", timeout=15)
    r.raise_for_status()
    return r.json()["data"]


def _format_progress(progress: Optional[Dict[str, Any]]) -> str:
    if not progress:
        return "(no progress)"
    return (
        f"stage={progress.get('stage')} "
        f"posts={progress.get('posts_created', 0)}/{progress.get('expected_posts', 0)} "
        f"label={progress.get('stage_label')!r} "
        f"detail={progress.get('detail_message')!r}"
    )


def _poll_until_terminal(
    batch_id: str,
    *,
    max_seconds: int,
    coverage_pending_grace: int,
) -> Dict[str, Any]:
    start = time.time()
    last_log = 0.0
    last_stage = None
    while True:
        elapsed = time.time() - start
        if elapsed > max_seconds:
            status = _get_status(batch_id)
            status["_timed_out"] = True
            return status

        try:
            status = _get_status(batch_id)
        except requests.RequestException as exc:
            print(f"  [{int(elapsed)}s] status poll error: {exc}", flush=True)
            time.sleep(3)
            continue

        progress = status.get("progress") or {}
        stage = progress.get("stage")

        if stage != last_stage:
            print(f"  [{int(elapsed)}s] {_format_progress(progress)}", flush=True)
            last_log = elapsed
            last_stage = stage
        elif elapsed - last_log >= 10:
            print(f"  [{int(elapsed)}s] {_format_progress(progress)}", flush=True)
            last_log = elapsed

        if stage in TERMINAL_STAGES:
            return status

        if stage == "coverage_pending" and elapsed >= coverage_pending_grace:
            status["_coverage_pending_timeout"] = True
            return status

        time.sleep(2)


def _slice_uvicorn_log(window_lines: int = 250) -> str:
    if not UVICORN_LOG.exists():
        return "(uvicorn log missing)"
    try:
        with UVICORN_LOG.open("r") as fh:
            tail = fh.readlines()[-window_lines:]
    except OSError as exc:
        return f"(unable to read uvicorn log: {exc})"
    return "".join(tail)


def _grep_traceback_window(batch_id: str) -> str:
    """Best-effort: find log lines mentioning this batch_id + an exception trail."""
    if not UVICORN_LOG.exists():
        return ""
    with UVICORN_LOG.open("r") as fh:
        lines = fh.readlines()
    matches: List[int] = []
    for idx, line in enumerate(lines):
        if batch_id in line or "batch_autoseed_unexpected_error" in line or "Traceback" in line:
            matches.append(idx)
    if not matches:
        return ""
    start = max(0, matches[0] - 5)
    end = min(len(lines), matches[-1] + 30)
    return "".join(lines[start:end])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--value-count", type=int, default=5)
    parser.add_argument("--target-tier", type=int, default=8)
    parser.add_argument("--max-seconds", type=int, default=420)
    parser.add_argument(
        "--coverage-pending-grace",
        type=int,
        default=180,
        help="If batch sits in coverage_pending this long, give up the run.",
    )
    parser.add_argument(
        "--stop-on-failure",
        action="store_true",
        default=True,
        help="Stop the whole stress run on the first explicit failure.",
    )
    parser.add_argument(
        "--continue-through-failures",
        action="store_true",
        help="Override --stop-on-failure and aggregate all failures.",
    )
    args = parser.parse_args()

    if args.continue_through_failures:
        args.stop_on_failure = False

    print(f"=== Stress run @ {_now()} ===", flush=True)
    print(
        f"runs={args.runs} value/run={args.value_count} tier={args.target_tier}s "
        f"max={args.max_seconds}s base={BASE_URL}",
        flush=True,
    )

    summary: List[Dict[str, Any]] = []
    first_failure_index: Optional[int] = None

    for run_idx in range(1, args.runs + 1):
        brand = f"stress-{datetime.now().strftime('%H%M%S')}-r{run_idx}-{uuid.uuid4().hex[:6]}"
        print(f"\n[run {run_idx}/{args.runs}] creating batch brand={brand}", flush=True)
        run_start = time.time()
        try:
            batch = _post_batch(brand, args.value_count, args.target_tier)
        except Exception as exc:
            print(f"  POST /batches failed: {exc}", flush=True)
            summary.append(
                {
                    "run": run_idx,
                    "brand": brand,
                    "batch_id": None,
                    "outcome": "post_failed",
                    "error": str(exc),
                    "duration_s": time.time() - run_start,
                }
            )
            with RESULTS_LOG.open("a") as fh:
                fh.write(json.dumps(summary[-1]) + "\n")
            if args.stop_on_failure:
                first_failure_index = run_idx
                break
            continue

        batch_id = batch["id"]
        print(f"  batch_id={batch_id} state={batch.get('state')}", flush=True)

        status = _poll_until_terminal(
            batch_id,
            max_seconds=args.max_seconds,
            coverage_pending_grace=args.coverage_pending_grace,
        )
        progress = (status or {}).get("progress") or {}
        stage = progress.get("stage")
        posts_created = progress.get("posts_created", 0)
        expected_posts = progress.get("expected_posts", 0)
        duration = time.time() - run_start

        if stage == "completed":
            outcome = "completed"
        elif stage == "failed":
            outcome = "failed"
        elif status.get("_coverage_pending_timeout"):
            outcome = "coverage_pending_timeout"
        elif status.get("_timed_out"):
            outcome = "wall_timeout"
        else:
            outcome = "unknown"

        entry = {
            "run": run_idx,
            "brand": brand,
            "batch_id": batch_id,
            "outcome": outcome,
            "stage": stage,
            "posts_created": posts_created,
            "expected_posts": expected_posts,
            "detail_message": progress.get("detail_message"),
            "duration_s": round(duration, 1),
        }
        summary.append(entry)
        with RESULTS_LOG.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        print(f"  -> outcome={outcome} {posts_created}/{expected_posts} in {duration:.0f}s", flush=True)

        if outcome == "failed":
            print(f"\n=== TRACEBACK WINDOW for batch {batch_id} ===", flush=True)
            window = _grep_traceback_window(batch_id) or _slice_uvicorn_log(window_lines=200)
            print(window, flush=True)
            print("=== END WINDOW ===", flush=True)
            if args.stop_on_failure:
                first_failure_index = run_idx
                break

    # final summary
    print(f"\n=== Summary @ {_now()} ===", flush=True)
    completed = sum(1 for s in summary if s["outcome"] == "completed")
    failed = sum(1 for s in summary if s["outcome"] == "failed")
    other = len(summary) - completed - failed
    print(f"completed={completed} failed={failed} other={other} total={len(summary)}", flush=True)

    md_lines = [
        f"# Seeding stress run — {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"- target: `POST /batches` with `{{value: {args.value_count}}}` × {args.runs} runs",
        f"- tier: {args.target_tier}s",
        f"- completed: {completed} / {len(summary)}",
        f"- failed: {failed}",
        f"- other (coverage timeout / wall timeout): {other}",
        "",
        "## Per-run",
        "",
        "| # | outcome | posts | dur | detail |",
        "|---|---|---|---|---|",
    ]
    for s in summary:
        md_lines.append(
            f"| {s['run']} | {s['outcome']} | "
            f"{s.get('posts_created', 0)}/{s.get('expected_posts', 0)} | "
            f"{s.get('duration_s', 0)}s | "
            f"{(s.get('detail_message') or s.get('error') or '')[:120]} |"
        )
    SUMMARY_FILE.write_text("\n".join(md_lines))

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
