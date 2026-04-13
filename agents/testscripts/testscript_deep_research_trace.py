"""
Run one live deep-research + script-generation harvest and capture a full Gemini trace.

Writes a markdown artifact at project root: test_log_1.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Keep runtime logs enabled without transport-level noise.
os.environ["LOG_LEVEL"] = "INFO"

from app.adapters.llm_client import get_llm_client
from app.adapters.supabase_client import get_supabase
from app.core.logging import configure_logging
from app.features.topics.handlers import harvest_topics_to_bank_sync


def _load_local_env_file() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        if key not in os.environ:
            os.environ[key] = value


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(value)


def _require_env(name: str) -> None:
    if not os.getenv(name):
        raise RuntimeError(f"Missing required environment variable: {name}")


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _json(value)


def run_trace(*, post_type_counts: Dict[str, int], target_length_tier: int, trigger_source: str) -> Dict[str, Any]:
    configure_logging()
    _load_local_env_file()

    if not os.getenv("GEMINI_API_KEY") and os.getenv("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = os.environ["GEMINI_API_KEY"]
    if not os.getenv("SUPABASE_KEY") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        os.environ["SUPABASE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    if not os.getenv("SUPABASE_SERVICE_KEY") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        os.environ["SUPABASE_SERVICE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

    for required in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "GEMINI_API_KEY"):
        _require_env(required)

    llm = get_llm_client()
    trace_entries: List[Dict[str, Any]] = []
    call_index = 0

    orig_deep_research = llm.generate_gemini_deep_research
    orig_json = llm.generate_gemini_json
    orig_text = llm.generate_gemini_text

    def traced_deep_research(*args: Any, **kwargs: Any) -> str:
        nonlocal call_index
        call_index += 1
        entry: Dict[str, Any] = {
            "index": call_index,
            "method": "generate_gemini_deep_research",
            "request": {
                "prompt": kwargs.get("prompt"),
                "system_prompt": kwargs.get("system_prompt"),
                "agent": kwargs.get("agent"),
                "timeout_seconds": kwargs.get("timeout_seconds"),
                "poll_interval_seconds": kwargs.get("poll_interval_seconds"),
                "metadata": kwargs.get("metadata"),
            },
        }
        try:
            response = orig_deep_research(*args, **kwargs)
            entry["response"] = {"raw_text": response, "length": len(response or "")}
            trace_entries.append(entry)
            return response
        except Exception as exc:
            entry["error"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            trace_entries.append(entry)
            raise

    def traced_json(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        nonlocal call_index
        call_index += 1
        entry: Dict[str, Any] = {
            "index": call_index,
            "method": "generate_gemini_json",
            "request": {
                "prompt": kwargs.get("prompt"),
                "system_prompt": kwargs.get("system_prompt"),
                "json_schema": kwargs.get("json_schema"),
                "model": kwargs.get("model"),
                "max_tokens": kwargs.get("max_tokens"),
                "temperature": kwargs.get("temperature"),
            },
        }
        try:
            response = orig_json(*args, **kwargs)
            entry["response"] = {"raw_json": response}
            trace_entries.append(entry)
            return response
        except Exception as exc:
            entry["error"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            trace_entries.append(entry)
            raise

    def traced_text(*args: Any, **kwargs: Any) -> str:
        nonlocal call_index
        call_index += 1
        entry: Dict[str, Any] = {
            "index": call_index,
            "method": "generate_gemini_text",
            "request": {
                "prompt": kwargs.get("prompt"),
                "system_prompt": kwargs.get("system_prompt"),
                "model": kwargs.get("model"),
                "max_tokens": kwargs.get("max_tokens"),
                "temperature": kwargs.get("temperature"),
            },
        }
        try:
            response = orig_text(*args, **kwargs)
            entry["response"] = {"raw_text": response, "length": len(response or "")}
            trace_entries.append(entry)
            return response
        except Exception as exc:
            entry["error"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            trace_entries.append(entry)
            raise

    llm.generate_gemini_deep_research = traced_deep_research  # type: ignore[assignment]
    llm.generate_gemini_json = traced_json  # type: ignore[assignment]
    llm.generate_gemini_text = traced_text  # type: ignore[assignment]

    run_result: Dict[str, Any] = {}
    run_error: Dict[str, Any] | None = None
    run_row: Dict[str, Any] | None = None

    try:
        run_result = harvest_topics_to_bank_sync(
            post_type_counts=post_type_counts,
            target_length_tier=target_length_tier,
            trigger_source=trigger_source,
        )
    except Exception as exc:
        run_error = {
            "type": exc.__class__.__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        llm.generate_gemini_deep_research = orig_deep_research  # type: ignore[assignment]
        llm.generate_gemini_json = orig_json  # type: ignore[assignment]
        llm.generate_gemini_text = orig_text  # type: ignore[assignment]

    run_id = run_result.get("run_id")
    if run_id:
        try:
            supabase = get_supabase().client
            rows = (
                supabase.table("topic_research_runs")
                .select("*")
                .eq("id", run_id)
                .execute()
                .data
                or []
            )
            if rows:
                run_row = rows[0]
        except Exception:
            run_row = {"error": "Failed to read topic_research_runs row", "traceback": traceback.format_exc()}

    deep_research_count = sum(1 for entry in trace_entries if entry.get("method") == "generate_gemini_deep_research")
    json_count = sum(1 for entry in trace_entries if entry.get("method") == "generate_gemini_json")
    text_count = sum(1 for entry in trace_entries if entry.get("method") == "generate_gemini_text")

    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "cwd": str(PROJECT_ROOT),
            "log_level": os.getenv("LOG_LEVEL"),
            "has_supabase_url": bool(os.getenv("SUPABASE_URL")),
            "has_supabase_service_key": bool(os.getenv("SUPABASE_SERVICE_KEY")),
            "has_gemini_api_key": bool(os.getenv("GEMINI_API_KEY")),
        },
        "requested_run": {
            "post_type_counts": post_type_counts,
            "target_length_tier": target_length_tier,
            "trigger_source": trigger_source,
        },
        "run_result": run_result,
        "run_error": run_error,
        "run_row": run_row,
        "trace_count": len(trace_entries),
        "deep_research_count": deep_research_count,
        "json_count": json_count,
        "text_count": text_count,
        "trace_entries": trace_entries,
    }


def write_markdown(report: Dict[str, Any], output_path: Path) -> None:
    lines: List[str] = []
    lines.append("# Test Log 1 - Deep Research Trace")
    lines.append("")
    lines.append(f"- Generated at (UTC): `{datetime.now(timezone.utc).isoformat()}`")
    lines.append("")
    lines.append("## Environment")
    lines.append("")
    lines.append("```json")
    lines.append(_json(report.get("environment")))
    lines.append("```")
    lines.append("")
    lines.append("## Run Result")
    lines.append("")
    lines.append("```json")
    lines.append(_json(report.get("run_result")))
    lines.append("```")
    lines.append("")
    lines.append("## Requested Run")
    lines.append("")
    lines.append("```json")
    lines.append(_json(report.get("requested_run")))
    lines.append("```")
    lines.append("")
    if report.get("run_error"):
        lines.append("## Run Error")
        lines.append("")
        lines.append("```json")
        lines.append(_json(report.get("run_error")))
        lines.append("```")
        lines.append("")
    if report.get("run_row"):
        lines.append("## Persisted `topic_research_runs` Row")
        lines.append("")
        lines.append("```json")
        lines.append(_json(report.get("run_row")))
        lines.append("```")
        lines.append("")
    lines.append("## Gemini Trace")
    lines.append("")

    trace_entries = report.get("trace_entries") or []
    for entry in trace_entries:
        lines.append(f"### Call {entry.get('index')} - `{entry.get('method')}`")
        lines.append("")
        lines.append("#### Request")
        lines.append("")
        lines.append("```json")
        lines.append(_json(entry.get("request")))
        lines.append("```")
        lines.append("")
        if entry.get("response") is not None:
            response = entry.get("response") or {}
            if isinstance(response, dict) and "raw_text" in response:
                lines.append("#### Response (raw text)")
                lines.append("")
                lines.append("```text")
                lines.append(_to_text(response.get("raw_text")))
                lines.append("```")
                lines.append("")
            else:
                lines.append("#### Response (json)")
                lines.append("")
                lines.append("```json")
                lines.append(_json(response))
                lines.append("```")
                lines.append("")
        if entry.get("error") is not None:
            lines.append("#### Error")
            lines.append("")
            lines.append("```json")
            lines.append(_json(entry.get("error")))
            lines.append("```")
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deep research trace runner")
    parser.add_argument("--output", default="test_log_1.md", help="Markdown file path relative to project root")
    parser.add_argument("--value-count", type=int, default=1)
    parser.add_argument("--product-count", type=int, default=0)
    parser.add_argument("--lifestyle-count", type=int, default=0)
    parser.add_argument("--target-tier", type=int, default=16)
    parser.add_argument("--trigger-source", default="deep_research_trace")
    args = parser.parse_args()

    output_path = PROJECT_ROOT / args.output
    report = run_trace(
        post_type_counts={
            "value": max(args.value_count, 0),
            "product": max(args.product_count, 0),
            "lifestyle": max(args.lifestyle_count, 0),
        },
        target_length_tier=args.target_tier,
        trigger_source=args.trigger_source,
    )
    run_result = report.get("run_result") or {}
    seed_topics_used = list(run_result.get("seed_topics_used") or [])
    stored_topic_ids = list(run_result.get("stored_topics") or [])
    scripts_persisted_by_tier = dict(run_result.get("scripts_persisted_by_tier") or {})

    if report.get("deep_research_count") != 3:
        raise RuntimeError(f"Expected exactly 3 Deep Research calls, got: {report.get('deep_research_count')}")
    if int(report.get("json_count") or 0) != 0:
        raise RuntimeError(f"Expected zero Gemini JSON calls in the value warm-up path, got: {report.get('json_count')}")
    if len(seed_topics_used) != 3 or len(set(seed_topics_used)) != 3:
        raise RuntimeError(f"Expected 3 unique seed topics, got: {seed_topics_used}")
    if int(run_result.get("dossiers_completed") or 0) != 3:
        raise RuntimeError(f"Expected 3 completed dossiers, got: {_json(run_result)}")
    if int(run_result.get("lanes_persisted") or 0) < 3:
        raise RuntimeError(f"Expected multiple persisted lanes, got: {_json(run_result)}")
    for tier in ("8", "16", "32"):
        if int(scripts_persisted_by_tier.get(tier) or 0) < 1:
            raise RuntimeError(f"Expected canonical coverage for tier {tier}, got: {_json(run_result)}")

    if not stored_topic_ids:
        raise RuntimeError(f"Expected stored topic ids in run result, got: {_json(run_result)}")

    supabase = get_supabase().client
    for topic_id in stored_topic_ids:
        rows = (
            supabase.table("topic_scripts")
            .select("*")
            .eq("topic_registry_id", topic_id)
            .execute()
            .data
            or []
        )
        canonical_rows = [row for row in rows if str(row.get("bucket") or "") == "canonical"]
        if not canonical_rows:
            raise RuntimeError(f"Expected canonical rows for {topic_id}, got: {_json(rows)}")
        for tier in (8, 16, 32):
            if not any(int(row.get("target_length_tier") or 0) == tier for row in canonical_rows):
                raise RuntimeError(f"Missing canonical tier {tier} for {topic_id}, got: {_json(canonical_rows)}")

    write_markdown(report, output_path)
    print(f"TRACE_WRITTEN={output_path}")
    print(f"TRACE_CALLS={report.get('trace_count')}")
    print(f"DEEP_RESEARCH_CALLS={report.get('deep_research_count')}")
    if report.get("run_error"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
