"""Stress test for FLOW-FORGE script generation (8s/16s/32s).

Real LLM, real Supabase reads (topic_registry + topic_research_dossiers),
zero DB writes — `upsert_topic_script_variants` is patched to capture
into a thread-local box so production data is untouched.

Usage:
    cd /Users/.../AIUGC && set -a && . ./.env && set +a && \
        .venv/bin/python tests/stress_test_script_generation.py
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.video_profiles import get_duration_profile  # noqa: E402
from app.features.topics.queries import (  # noqa: E402
    get_all_topics_from_registry,
    get_topic_research_dossiers,
)
from app.features.topics.topic_validation import (  # noqa: E402
    _script_word_count,
    compute_bigram_jaccard,
    count_spoken_sentences,
    detect_metadata_bleed,
    detect_spoken_copy_issues,
    get_prompt1_sentence_bounds,
    get_prompt1_word_bounds,
)
from app.features.topics.variant_expansion import expand_topic_variants  # noqa: E402

_capture_box = threading.local()


def _fake_upsert(*, variants, **_kwargs):
    sink = getattr(_capture_box, "variants", None)
    if sink is not None:
        sink.extend(list(variants))
    return []


@contextmanager
def stress_test_isolation():
    with patch(
        "app.features.topics.variant_expansion.upsert_topic_script_variants",
        side_effect=_fake_upsert,
    ):
        yield


@dataclass
class ShotResult:
    shot_id: str
    phase: str
    tier: int
    post_type: str
    topic_id: str
    topic_title: str
    framework: str
    hook_style: str
    started_at: float
    duration_s: float
    success: bool  # blocking-issue-free (production-usable)
    strict_pass: bool = False  # also passes every spec bound
    error: Optional[str] = None
    script_text: Optional[str] = None
    word_count: int = 0
    char_count_no_spaces: int = 0
    sentence_count: int = 0
    blocking_issues: List[str] = field(default_factory=list)
    soft_issues: List[str] = field(default_factory=list)


# Char overshoot tolerance: anything beyond this fraction of the spec breaks
# TTS timing because the spoken script can't fit in the target video duration.
CHAR_OVERSHOOT_HARD_LIMIT = 1.5


def validate_script(
    script_text: str, tier: int, source_summary: str = ""
) -> Tuple[List[str], List[str]]:
    """Return (blocking_issues, soft_issues).

    Blocking = would actually break a video (incomplete clause, no script,
    missing punctuation, metadata bleed, char count >1.5× max).
    Soft = informational drift from the spec (slight word/sentence/char
    count variance) — surfaced but not used for pass/fail.
    """
    blocking: List[str] = []
    soft: List[str] = []

    if not script_text or not script_text.strip():
        blocking.append("empty_script")
        return blocking, soft

    profile = get_duration_profile(tier)
    word_count = _script_word_count(script_text)
    char_count_no_spaces = len(script_text.replace(" ", "").replace("\t", "").replace("\n", ""))
    sentence_count = count_spoken_sentences(script_text)

    if script_text.strip()[-1] not in ".!?":
        blocking.append("missing_terminal_punctuation")

    bleed = detect_metadata_bleed(script_text, source_summary=source_summary or "")
    if bleed:
        blocking.append(f"metadata_bleed:{bleed.get('field')}")

    copy_issues = detect_spoken_copy_issues(script_text)
    if copy_issues:
        issue_kinds = sorted({c.get("kind", "unknown") for c in copy_issues})
        blocking.append("spoken_copy_issues:" + ",".join(issue_kinds))

    char_hard_limit = int(profile.prompt1_max_chars_no_spaces * CHAR_OVERSHOOT_HARD_LIMIT)
    if char_count_no_spaces > char_hard_limit:
        blocking.append(
            f"char_count_far_exceeded ({char_count_no_spaces}>{char_hard_limit})"
        )
    elif char_count_no_spaces > profile.prompt1_max_chars_no_spaces:
        soft.append(
            f"char_count_slightly_over ({char_count_no_spaces}>{profile.prompt1_max_chars_no_spaces})"
        )

    min_w, max_w = get_prompt1_word_bounds(tier)
    if word_count < min_w:
        soft.append(f"word_count_low ({word_count}<{min_w})")
    elif word_count > max_w:
        soft.append(f"word_count_high ({word_count}>{max_w})")

    min_s, max_s = get_prompt1_sentence_bounds(tier)
    if sentence_count < min_s:
        soft.append(f"sentence_count_low ({sentence_count}<{min_s})")
    elif sentence_count > max_s:
        soft.append(f"sentence_count_high ({sentence_count}>{max_s})")

    return blocking, soft


_TRANSPORT_ERROR_TOKENS = (
    "RemoteProtocolError",
    "LocalProtocolError",
    "ConnectionTerminated",
    "StreamIDTooLow",
    "ReadError",
    "WriteError",
    "ConnectError",
    "Broken pipe",
    "Connection reset",
    "Connection aborted",
    "Connection refused",
    "EOF",
    # postgrest-py wraps Supabase HTML 5xx pages as APIError "JSON could not be generated"
    "APIError",
    "JSON could not be generated",
    "<html>",
    # h2 stream-tracker corruption surfaces as KeyError(int)
    "KeyError",
)


def _looks_like_transport_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    msg = str(exc)
    if any(tok in name for tok in _TRANSPORT_ERROR_TOKENS):
        return True
    if any(tok in msg for tok in _TRANSPORT_ERROR_TOKENS):
        return True
    return False


def _call_expand_with_transport_retry(
    *,
    topic_registry_id: str,
    title: str,
    post_type: str,
    target_length_tier: int,
    max_attempts: int = 6,
) -> Dict[str, Any]:
    """Wrap expand_topic_variants with a retry on Supabase/Vertex transport
    flakiness (HTTP/2 stream collapses, postgrest connection-pool churn,
    Supabase 5xx HTML error pages parsed as APIError). Backoff escalates to
    ~16 s on the last attempt so Supabase has time to drop the dead
    connection from its pool before we hit it again.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(max_attempts):
        try:
            return expand_topic_variants(
                topic_registry_id=topic_registry_id,
                title=title,
                post_type=post_type,
                target_length_tier=target_length_tier,
                count=1,
                dry_run=False,
            )
        except Exception as exc:  # noqa: BLE001
            if not _looks_like_transport_error(exc):
                raise
            last_exc = exc
            if attempt < max_attempts - 1:
                base = min(16.0, 0.5 * (2 ** attempt))  # 0.5, 1, 2, 4, 8, 16
                time.sleep(base + random.uniform(0.0, base * 0.25))
                continue
            raise
    assert last_exc is not None
    raise last_exc


def run_one_shot(
    *,
    phase: str,
    tier: int,
    post_type: str,
    topics: List[Dict[str, Any]],
) -> ShotResult:
    topic = random.choice(topics)
    topic_id = topic["id"]
    title = topic.get("title") or ""
    shot_id = f"{phase}_t{tier}_{post_type}_{topic_id[:8]}_{random.randint(1000, 9999)}"
    started_at = time.time()

    _capture_box.variants = []
    try:
        result = _call_expand_with_transport_retry(
            topic_registry_id=topic_id,
            title=title,
            post_type=post_type,
            target_length_tier=tier,
        )
        duration = time.time() - started_at
        captured = list(getattr(_capture_box, "variants", []) or [])

        if not captured:
            return ShotResult(
                shot_id=shot_id,
                phase=phase,
                tier=tier,
                post_type=post_type,
                topic_id=topic_id,
                topic_title=title[:80],
                framework="",
                hook_style="",
                started_at=started_at,
                duration_s=duration,
                success=False,
                strict_pass=False,
                error=f"no_variant_emitted (generated={result.get('generated', 0)})",
            )

        v = captured[-1]
        script_text = str(v.get("script") or "")
        framework = str(v.get("framework") or "")
        hook_style = str(v.get("hook_style") or "")
        source_summary = str(v.get("source_summary") or "")

        blocking, soft = validate_script(script_text, tier, source_summary=source_summary)
        return ShotResult(
            shot_id=shot_id,
            phase=phase,
            tier=tier,
            post_type=post_type,
            topic_id=topic_id,
            topic_title=title[:80],
            framework=framework,
            hook_style=hook_style,
            started_at=started_at,
            duration_s=duration,
            success=(len(blocking) == 0),
            strict_pass=(len(blocking) == 0 and len(soft) == 0),
            script_text=script_text,
            word_count=_script_word_count(script_text),
            char_count_no_spaces=len(script_text.replace(" ", "").replace("\n", "")),
            sentence_count=count_spoken_sentences(script_text),
            blocking_issues=blocking,
            soft_issues=soft,
        )
    except Exception as exc:  # noqa: BLE001
        duration = time.time() - started_at
        return ShotResult(
            shot_id=shot_id,
            phase=phase,
            tier=tier,
            post_type=post_type,
            topic_id=topic_id,
            topic_title=title[:80],
            framework="",
            hook_style="",
            started_at=started_at,
            duration_s=duration,
            success=False,
            strict_pass=False,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        _capture_box.variants = []


def _print_shot_line(r: ShotResult) -> None:
    if r.success and r.strict_pass:
        status = "PASS"
    elif r.success:
        status = "OK"  # production-usable, soft-spec drift
    else:
        status = "FAIL"
    parts = [
        f"  [{status:<4}] {r.phase:<14} t={r.tier:>2}s",
        f"{r.duration_s:>5.1f}s",
        f"w={r.word_count:>3} s={r.sentence_count:>1} c={r.char_count_no_spaces:>3}",
    ]
    if r.error:
        parts.append(f"err={r.error[:80]}")
    elif r.blocking_issues:
        parts.append("BLOCK=" + ";".join(r.blocking_issues))
    elif r.soft_issues:
        parts.append("soft=" + ";".join(r.soft_issues))
    else:
        parts.append("clean")
    print("  ".join(parts), flush=True)


def select_value_topics_with_dossiers(
    topics: List[Dict[str, Any]], target: int = 60
) -> List[Dict[str, Any]]:
    value_topics = [t for t in topics if (t.get("post_type") or "value") == "value"]
    random.shuffle(value_topics)
    matched: List[Dict[str, Any]] = []
    for t in value_topics:
        if len(matched) >= target:
            break
        try:
            if get_topic_research_dossiers(topic_registry_id=t["id"]):
                matched.append(t)
        except Exception:  # noqa: BLE001
            continue
    return matched


def write_reports(
    results: List[ShotResult],
    output_dir: Path,
    timestamp: str,
    duplicate_pairs: List[Tuple[int, int, float]],
    successful: List[ShotResult],
    config: Dict[str, Any],
) -> Tuple[Path, Path]:
    jsonl_path = output_dir / f"stress_test_{timestamp}.jsonl"
    md_path = output_dir / f"stress_test_{timestamp}.md"

    with jsonl_path.open("w") as f:
        for r in results:
            f.write(json.dumps(asdict(r), default=str, ensure_ascii=False) + "\n")

    by_phase_tier: Dict[Tuple[str, int], List[ShotResult]] = {}
    for r in results:
        phase_root = r.phase.split("_r")[0]
        by_phase_tier.setdefault((phase_root, r.tier), []).append(r)

    error_counts: Dict[str, int] = {}
    for r in results:
        if not r.error:
            continue
        key = r.error.split(":")[0]
        error_counts[key] = error_counts.get(key, 0) + 1

    lines: List[str] = []
    lines.append(f"# Script Generation Stress Test — {timestamp}")
    lines.append("")
    lines.append("## Configuration")
    lines.append("")
    for k, v in config.items():
        lines.append(f"- `{k}` = `{v}`")
    lines.append("")

    total = len(results)
    clean = sum(1 for r in results if r.success)
    strict = sum(1 for r in results if r.strict_pass)
    pct = clean / total * 100 if total else 0.0
    strict_pct = strict / total * 100 if total else 0.0
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- Total shots: **{total}**")
    lines.append(f"- Production-usable (no blocking issues): **{clean} ({pct:.1f}%)**")
    lines.append(f"- Strict-spec pass (also matches every DurationProfile bound): **{strict} ({strict_pct:.1f}%)**")
    lines.append(f"- Duplicate pairs (Jaccard ≥ 0.58, same tier): **{len(duplicate_pairs)}**")
    lines.append("")

    lines.append("## Per phase × tier")
    lines.append("")
    lines.append("| Phase | Tier | Shots | Usable | Strict | Blocking | Soft | Errors | p50 (s) | p95 (s) |")
    lines.append("|-------|------|-------|--------|--------|----------|------|--------|---------|---------|")
    for (phase, tier), shots in sorted(by_phase_tier.items()):
        shots_usable = sum(1 for s in shots if s.success)
        shots_strict = sum(1 for s in shots if s.strict_pass)
        shots_blocking = sum(1 for s in shots if s.blocking_issues and not s.error)
        shots_soft = sum(1 for s in shots if s.success and s.soft_issues)
        shots_errors = sum(1 for s in shots if s.error)
        durations = sorted(s.duration_s for s in shots)
        if not durations:
            continue
        p50 = durations[len(durations) // 2]
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
        lines.append(
            f"| {phase} | {tier}s | {len(shots)} | {shots_usable} | {shots_strict} | "
            f"{shots_blocking} | {shots_soft} | {shots_errors} | {p50:.1f} | {p95:.1f} |"
        )
    lines.append("")

    lines.append("## Blocking issues (fail production-usable bar)")
    lines.append("")
    blocking_counts: Dict[str, int] = {}
    for r in results:
        for iss in r.blocking_issues:
            key = iss.split(" ")[0].split(":")[0]
            blocking_counts[key] = blocking_counts.get(key, 0) + 1
    if blocking_counts:
        for k, v in sorted(blocking_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("None — every generated script is production-usable.")
    lines.append("")

    lines.append("## Soft drift (informational; would fail strict spec)")
    lines.append("")
    soft_counts: Dict[str, int] = {}
    for r in results:
        for iss in r.soft_issues:
            key = iss.split(" ")[0].split(":")[0]
            soft_counts[key] = soft_counts.get(key, 0) + 1
    if soft_counts:
        for k, v in sorted(soft_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("None.")
    lines.append("")

    lines.append("## Errors (exceptions, not quality issues)")
    lines.append("")
    if error_counts:
        for k, v in sorted(error_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("None.")
    lines.append("")

    lines.append("## Cross-shot duplicates (Jaccard ≥ 0.58, same tier)")
    lines.append("")
    if duplicate_pairs:
        for i, j, jac in duplicate_pairs[:15]:
            a = successful[i]
            b = successful[j]
            lines.append(
                f"- pair ({a.shot_id} ↔ {b.shot_id}) tier={a.tier}s jaccard={jac:.3f}"
            )
            lines.append(f"  - A: {a.script_text[:140]}")
            lines.append(f"  - B: {b.script_text[:140]}")
    else:
        lines.append("None.")
    lines.append("")

    failed = [r for r in results if not r.success]
    if failed:
        lines.append(f"## Failure samples (first 15 of {len(failed)})")
        lines.append("")
        for r in failed[:15]:
            lines.append(f"### {r.shot_id} ({r.tier}s · {r.post_type})")
            lines.append("")
            lines.append(f"- topic: `{r.topic_title}`")
            lines.append(f"- framework: `{r.framework}` · hook: `{r.hook_style}`")
            lines.append(f"- duration: {r.duration_s:.1f}s")
            if r.error:
                lines.append(f"- error: `{r.error[:300]}`")
            if r.blocking_issues:
                lines.append(f"- blocking: `{r.blocking_issues}`")
            if r.soft_issues:
                lines.append(f"- soft: `{r.soft_issues}`")
            if r.script_text:
                lines.append(f"- script: `{r.script_text[:300]}`")
            lines.append("")

    lines.append("## Per-tier sample (first 3 clean per tier)")
    lines.append("")
    by_tier_clean: Dict[int, List[ShotResult]] = {}
    for r in results:
        if r.success and r.script_text:
            by_tier_clean.setdefault(r.tier, []).append(r)
    for tier in sorted(by_tier_clean.keys()):
        lines.append(f"### Tier {tier}s")
        lines.append("")
        for r in by_tier_clean[tier][:3]:
            lines.append(f"- words={r.word_count} sents={r.sentence_count} fwk={r.framework}")
            lines.append(f"  - `{r.script_text}`")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return jsonl_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phases",
        default="smoke,concurrency,heavy",
        help="comma-sep phases (smoke,concurrency,heavy)",
    )
    parser.add_argument("--smoke-tiers", default="8,16,32")
    parser.add_argument("--concurrency-parallel", type=int, default=6)
    parser.add_argument("--heavy-parallel", type=int, default=8)
    parser.add_argument("--heavy-rounds", type=int, default=3)
    parser.add_argument("--topic-pool-size", type=int, default=60)
    parser.add_argument("--output-dir", default="tasks")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    started_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print("=" * 80)
    print("FLOW-FORGE Script Generation Stress Test")
    print(f"started_at: {started_iso}")
    print(f"args: {vars(args)}")
    print("=" * 80, flush=True)

    print("Loading topics from registry...", flush=True)
    all_topics = get_all_topics_from_registry()
    print(f"  total topics: {len(all_topics)}", flush=True)

    print(f"Filtering value topics with dossiers (target {args.topic_pool_size})...", flush=True)
    topic_pool = select_value_topics_with_dossiers(all_topics, target=args.topic_pool_size)
    print(f"  pool size: {len(topic_pool)}", flush=True)
    if len(topic_pool) < 5:
        print("ERROR: not enough value topics with dossiers; abort.", flush=True)
        return 1

    phases_to_run = [p.strip() for p in args.phases.split(",") if p.strip()]
    smoke_tiers = [int(x) for x in args.smoke_tiers.split(",")]

    results: List[ShotResult] = []
    total_started_at = time.time()

    with stress_test_isolation():
        if "smoke" in phases_to_run:
            print("\n[PHASE 1] smoke — 1 sequential per tier", flush=True)
            for tier in smoke_tiers:
                r = run_one_shot(phase="smoke", tier=tier, post_type="value", topics=topic_pool)
                results.append(r)
                _print_shot_line(r)

        if "concurrency" in phases_to_run:
            n = args.concurrency_parallel
            print(f"\n[PHASE 2] concurrency — {n} parallel × 3 tiers in one batch", flush=True)
            jobs: List[Tuple[int, str]] = []
            for tier in (8, 16, 32):
                jobs.extend([(tier, "value")] * n)
            random.shuffle(jobs)
            with ThreadPoolExecutor(max_workers=n * 3) as pool:
                futures = [
                    pool.submit(run_one_shot, phase="concurrency", tier=t, post_type=pt, topics=topic_pool)
                    for (t, pt) in jobs
                ]
                for fut in as_completed(futures):
                    r = fut.result()
                    results.append(r)
                    _print_shot_line(r)

        if "heavy" in phases_to_run:
            n = args.heavy_parallel
            rounds = args.heavy_rounds
            total = n * rounds * 3
            print(f"\n[PHASE 3] heavy — {n} parallel × {rounds} rounds × 3 tiers ({total} calls)", flush=True)
            for round_n in range(rounds):
                print(f"  -- round {round_n + 1}/{rounds} --", flush=True)
                jobs = []
                for tier in (8, 16, 32):
                    jobs.extend([(tier, "value")] * n)
                random.shuffle(jobs)
                with ThreadPoolExecutor(max_workers=n * 3) as pool:
                    futures = [
                        pool.submit(
                            run_one_shot,
                            phase=f"heavy_r{round_n + 1}",
                            tier=t,
                            post_type=pt,
                            topics=topic_pool,
                        )
                        for (t, pt) in jobs
                    ]
                    round_results = []
                    for fut in as_completed(futures):
                        r = fut.result()
                        round_results.append(r)
                        results.append(r)
                        _print_shot_line(r)
                ok = sum(1 for r in round_results if r.success)
                print(f"  round {round_n + 1}: {ok}/{len(round_results)} clean", flush=True)

    total_elapsed = time.time() - total_started_at

    print("\nCross-shot duplicate analysis...", flush=True)
    successful = [r for r in results if r.success and r.script_text]
    duplicate_pairs: List[Tuple[int, int, float]] = []
    for i in range(len(successful)):
        for j in range(i + 1, len(successful)):
            if successful[i].tier != successful[j].tier:
                continue
            jac = compute_bigram_jaccard(successful[i].script_text or "", successful[j].script_text or "")
            if jac >= 0.58:
                duplicate_pairs.append((i, j, jac))
    duplicate_pairs.sort(key=lambda x: -x[2])
    print(f"  duplicate pairs ≥0.58: {len(duplicate_pairs)}", flush=True)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    config = {
        "started_at": started_iso,
        "elapsed_s": round(total_elapsed, 1),
        "phases": phases_to_run,
        "concurrency_parallel": args.concurrency_parallel,
        "heavy_parallel": args.heavy_parallel,
        "heavy_rounds": args.heavy_rounds,
        "topic_pool_size": len(topic_pool),
        "seed": args.seed,
    }
    jsonl_path, md_path = write_reports(
        results, output_dir, timestamp, duplicate_pairs, successful, config
    )

    total = len(results)
    clean = sum(1 for r in results if r.success)
    strict = sum(1 for r in results if r.strict_pass)
    pct = clean / total * 100 if total else 0.0
    strict_pct = strict / total * 100 if total else 0.0
    print(f"\nWrote raw: {jsonl_path}", flush=True)
    print(f"Wrote summary: {md_path}", flush=True)
    print("\n" + "=" * 80, flush=True)
    print(
        f"VERDICT: production-usable {clean}/{total} ({pct:.1f}%) · "
        f"strict-spec {strict}/{total} ({strict_pct:.1f}%) · elapsed {total_elapsed:.0f}s",
        flush=True,
    )
    print("=" * 80, flush=True)

    return 0 if pct >= 90.0 else 2


if __name__ == "__main__":
    sys.exit(main())
