"""Topics hub service helpers."""

from __future__ import annotations

import asyncio
import json
import random
import re
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.adapters.llm_client import get_llm_client
from app.core.logging import get_logger
from app.core.video_profiles import get_duration_profile
from app.core.errors import FlowForgeException, ThirdPartyError, ValidationError
from app.features.topics.bank_warmup import run_single_seed_topic_warmup
from app.features.topics.deduplication import calculate_topic_similarity
from app.features.topics.prompts import get_topic_bank
from app.features.topics.queries import (
    create_topic_research_run,
    get_all_topics_from_registry,
    get_topic_registry_by_id,
    get_topic_research_run,
    get_topic_scripts_for_dossier,
    get_topic_scripts_for_registry,
    list_topic_research_runs,
    list_topic_suggestions,
    store_topic_bank_entry,
    update_topic_research_run,
    upsert_topic_script_variants,
)
from app.features.topics.seed_builders import build_research_seed_data
from app.features.topics.topic_validation import sanitize_metadata_text, sanitize_spoken_fragment

logger = get_logger(__name__)

TOPIC_RESEARCH_TASKS: Dict[str, asyncio.Task] = {}
_UUID_LIKE_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_TOPIC_NEW_WINDOW = timedelta(hours=24)
_WARMUP_SEED_TOPIC_COUNT = 3
_CANONICAL_TIERS = (8, 16, 32)


def _topic_bank_rows() -> List[Dict[str, Any]]:
    bank = get_topic_bank()
    topics: List[Dict[str, Any]] = []
    for index, raw_topic in enumerate(list(bank.get("topics") or []), start=1):
        title = str(raw_topic).strip()
        if not title:
            continue
        topics.append(
            {
                "id": f"topic-bank-{index}",
                "title": title,
                "script": title,
                "rotation": title,
                "cta": "",
                "post_type": "bank",
                "source": "topic_bank.yaml",
                "script_count": 0,
            }
        )
    return topics


def _registry_or_topic_bank() -> List[Dict[str, Any]]:
    topics = get_all_topics_from_registry()
    return topics if topics else _topic_bank_rows()


def get_random_topic() -> Optional[Dict[str, Any]]:
    """Return the topic with the fewest scripts (least coverage)."""
    topics = get_all_topics_from_registry()
    if not topics:
        return None
    scored = []
    for topic in topics:
        if str(topic.get("id") or "").startswith("topic-bank-"):
            scripts = []
        else:
            scripts = get_topic_scripts_for_registry(topic["id"])
        scored.append((len(scripts), topic))
    scored.sort(key=lambda pair: pair[0])
    count, topic = scored[0]
    return {**topic, "script_count": count}


def fuzzy_match_topic(query: str, threshold: float = 0.35) -> Optional[Dict[str, Any]]:
    """Find the most similar existing topic to a query string, if above threshold."""
    topics = get_all_topics_from_registry()
    if not topics:
        return None
    best_score = 0.0
    best_topic = None
    for topic in topics:
        score = calculate_topic_similarity(
            title1=query, rotation1=query, cta1=query,
            title2=topic.get("title", ""),
            rotation2=topic.get("rotation", ""),
            cta2=topic.get("cta", ""),
        )
        if score > best_score:
            best_score = score
            best_topic = topic
    if best_score < threshold or best_topic is None:
        return None
    scripts = get_topic_scripts_for_registry(best_topic["id"])
    return {**best_topic, "script_count": len(scripts), "similarity_score": best_score}


def _fetch_all_script_counts() -> Dict[str, int]:
    """Fetch script counts for all topics in a single query."""
    from app.features.topics.queries import _fetch_topic_script_rows
    try:
        all_scripts = _fetch_topic_script_rows()
    except Exception as exc:
        logger.warning("topic_script_counts_unavailable", error=str(exc))
        return {}
    counts: Dict[str, int] = {}
    for script in all_scripts:
        rid = str(script.get("topic_registry_id") or "")
        counts[rid] = counts.get(rid, 0) + 1
    return counts


def _count_topic_scripts(topic_id: str, bulk_counts: Dict[str, int]) -> int:
    count = int(bulk_counts.get(topic_id, 0) or 0)
    if count > 0 or not _UUID_LIKE_RE.match(topic_id or ""):
        return count
    try:
        return len(get_topic_scripts_for_registry(topic_id))
    except Exception:
        return count


def _parse_utc_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _generated_topic_sort_key(topic: Dict[str, Any], fresh_cutoff: datetime) -> tuple:
    last_harvested_at = _parse_utc_timestamp(topic.get("last_harvested_at") or topic.get("created_at"))
    is_new = bool(last_harvested_at and last_harvested_at >= fresh_cutoff)
    freshness_rank = 0 if is_new else 1
    recency_rank = -(last_harvested_at.timestamp() if last_harvested_at else 0.0)
    title_rank = str(topic.get("title") or "").lower()
    return (freshness_rank, recency_rank, title_rank)


def _mark_topic_research_task(run_id: str, task: asyncio.Task) -> None:
    TOPIC_RESEARCH_TASKS[run_id] = task


def _clear_topic_research_task(run_id: str, task: asyncio.Task) -> None:
    current = TOPIC_RESEARCH_TASKS.get(run_id)
    if current is task:
        TOPIC_RESEARCH_TASKS.pop(run_id, None)


def is_topic_research_active(run_id: str) -> bool:
    task = TOPIC_RESEARCH_TASKS.get(run_id)
    if not task:
        return False
    if task.done():
        TOPIC_RESEARCH_TASKS.pop(run_id, None)
        return False
    return True


async def _run_topic_research_task(
    *,
    run_row: Dict[str, Any],
    topic_registry_id: str,
    target_length_tier: Optional[int],
    trigger_source: str,
    post_type: str,
    progress_callback: Optional[Any] = None,
) -> None:
    topic = get_topic_registry_by_id(topic_registry_id)

    from app.features.topics.handlers import start_seeding_interaction, update_seeding_progress

    start_seeding_interaction(batch_id=run_row["id"], brand=topic.get("title", ""), expected_posts=0)

    def _run_progress_callback(*args, **kwargs):
        """Handle both Gemini dict callbacks and pipeline kwargs callbacks."""
        if args and isinstance(args[0], dict):
            update = args[0]
            provider_status = str(update.get("provider_status") or "").upper()
            stage = "retry_wait" if update.get("is_retrying") else "researching"
            stage_label = (
                "Retrying Gemini research" if update.get("is_retrying")
                else "Gemini deep research finished" if provider_status in {"DONE", "COMPLETED", "SUCCEEDED"}
                else "Gemini deep research is running"
            )
            update_seeding_progress(
                run_row["id"],
                stage=stage,
                stage_label=stage_label,
                detail_message=update.get("detail_message") or "Gemini is researching...",
                is_retrying=bool(update.get("is_retrying")),
                retry_message=update.get("retry_message"),
                provider_interaction_id=update.get("provider_interaction_id"),
                provider_status=provider_status or None,
            )
        else:
            update_seeding_progress(run_row["id"], **kwargs)

    def _combined_progress_callback(*args, **kwargs):
        _run_progress_callback(*args, **kwargs)
        if progress_callback is not None:
            progress_callback(*args, **kwargs)

    try:
        await asyncio.to_thread(
            _run_topic_research_pipeline_sync,
            run_id=run_row["id"],
            topic_registry_id=topic_registry_id,
            target_length_tier=target_length_tier,
            trigger_source=trigger_source,
            post_type=post_type,
            progress_callback=_combined_progress_callback,
        )
        update_seeding_progress(run_row["id"], stage="completed", stage_label="Research complete", status="completed")
    except ValidationError as exc:
        update_topic_research_run(
            run_row["id"],
            status="failed",
            error_message=exc.message,
            result_summary={
                "topic_registry_id": topic_registry_id,
                "topic_title": topic["title"],
                "target_length_tier": target_length_tier,
                "trigger_source": trigger_source,
            },
        )
        update_seeding_progress(run_row["id"], stage="failed", stage_label="Research failed", status="failed", detail_message=str(exc.message if hasattr(exc, "message") else exc))
        logger.warning(
            "topic_research_run_validation_failed",
            run_id=run_row["id"],
            error=exc.message,
            details=exc.details,
        )
    except ThirdPartyError as exc:
        update_topic_research_run(
            run_row["id"],
            status="failed",
            error_message=exc.message,
            result_summary={
                "topic_registry_id": topic_registry_id,
                "topic_title": topic["title"],
                "target_length_tier": target_length_tier,
                "trigger_source": trigger_source,
            },
        )
        update_seeding_progress(run_row["id"], stage="failed", stage_label="Research failed", status="failed", detail_message=str(exc.message if hasattr(exc, "message") else exc))
        logger.warning(
            "topic_research_run_third_party_failed",
            run_id=run_row["id"],
            error=exc.message,
            details=exc.details,
        )
    except Exception as exc:
        update_topic_research_run(
            run_row["id"],
            status="failed",
            error_message=str(exc),
            result_summary={
                "topic_registry_id": topic_registry_id,
                "topic_title": topic["title"],
                "target_length_tier": target_length_tier,
                "trigger_source": trigger_source,
            },
        )
        update_seeding_progress(run_row["id"], stage="failed", stage_label="Research failed", status="failed", detail_message=str(exc.message if hasattr(exc, "message") else exc))
        logger.exception(
            "topic_research_run_failed",
            run_id=run_row["id"],
            topic_registry_id=topic_registry_id,
            error=str(exc),
        )
    finally:
        task = TOPIC_RESEARCH_TASKS.get(run_row["id"])
        if task is not None:
            _clear_topic_research_task(run_row["id"], task)


def schedule_topic_research_run(
    *,
    run_row: Dict[str, Any],
    topic_registry_id: str,
    target_length_tier: Optional[int],
    trigger_source: str,
    post_type: str,
    reason: str,
    progress_callback: Optional[Any] = None,
) -> bool:
    run_id = str(run_row["id"])
    if is_topic_research_active(run_id):
        logger.info("topic_research_already_active", run_id=run_id, reason=reason)
        return False

    task = asyncio.create_task(
        _run_topic_research_task(
            run_row=run_row,
            topic_registry_id=topic_registry_id,
            target_length_tier=target_length_tier,
            trigger_source=trigger_source,
            post_type=post_type,
            progress_callback=progress_callback,
        )
    )
    _mark_topic_research_task(run_id, task)

    def _cleanup(_task: asyncio.Task) -> None:
        _clear_topic_research_task(run_id, _task)

    task.add_done_callback(_cleanup)
    logger.info("topic_research_scheduled", run_id=run_id, topic_registry_id=topic_registry_id, reason=reason)
    return True


def recover_stalled_topic_research_runs(limit: int = 1, max_age_hours: int = 6) -> List[str]:
    recovered: List[str] = []
    runs = list_topic_research_runs(limit=max(limit * 10, 25), status="running")
    newest_allowed_created_at = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    for run in runs:
        if len(recovered) >= limit:
            break
        run_id = str(run.get("id") or "").strip()
        topic_registry_id = str(run.get("topic_registry_id") or "").strip()
        if not run_id or not topic_registry_id:
            continue
        if is_topic_research_active(run_id):
            continue
        created_at = _parse_utc_timestamp(run.get("created_at"))
        if created_at is None or created_at < newest_allowed_created_at:
            continue
        if any(str(run.get(key) or "").strip() for key in ("raw_prompt", "raw_response", "provider_interaction_id")):
            continue
        topic = get_topic_registry_by_id(topic_registry_id)
        target_length_tier = run.get("target_length_tier")
        if target_length_tier is not None:
            try:
                target_length_tier = int(target_length_tier)
            except (TypeError, ValueError):
                target_length_tier = None
        if schedule_topic_research_run(
            run_row=run,
            topic_registry_id=topic_registry_id,
            target_length_tier=target_length_tier,
            trigger_source=str(run.get("trigger_source") or "startup_recovery"),
            post_type=str(run.get("post_type") or topic.get("post_type") or "value"),
            reason="startup_recovery",
        ):
            recovered.append(run_id)
    return recovered


def build_launch_hub_payload(request) -> Dict[str, Any]:
    """Build a simplified payload for the launch-focused hub."""
    filters = parse_topic_filters(request)
    topics = [
        topic
        for topic in _topic_bank_rows()
        if _topic_search_match(topic, filters["search"])
        and (filters["post_type"] is None or str(topic.get("post_type") or "") == filters["post_type"])
    ]
    topics.sort(key=lambda row: str(row.get("title") or "").lower())
    generated_topics = [
        topic
        for topic in get_all_topics_from_registry()
        if _topic_search_match(topic, filters["search"])
        and (filters["post_type"] is None or str(topic.get("post_type") or "") == filters["post_type"])
        and _topic_has_tier(topic, filters["target_length_tier"])
    ]
    generated_topics.sort(key=lambda row: str(row.get("last_harvested_at") or row.get("created_at") or ""), reverse=True)

    script_counts = _fetch_all_script_counts()
    generated_topics = [{**topic, "script_count": _count_topic_scripts(str(topic["id"]), script_counts)} for topic in generated_topics]
    if filters.get("only_with_scripts"):
        generated_topics = [t for t in generated_topics if t["script_count"] > 0]

    if filters["topic_mode"] == "generated":
        topics = generated_topics
    runs = list_topic_research_runs(limit=5)
    active_runs = [run for run in runs if run.get("status") == "running"]
    return {
        "filters": filters,
        "topics": topics,
        "basic_topics": topics if filters["topic_mode"] == "basic" else _topic_bank_rows(),
        "generated_topics": generated_topics,
        "total_topics": len(topics),
        "basic_topic_count": len(_topic_bank_rows()),
        "generated_topic_count": len(generated_topics),
        "active_runs": active_runs,
    }


def _wants_html(request) -> bool:
    hx_header = request.headers.get("HX-Request")
    if hx_header and hx_header.lower() == "true":
        return True
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "application/xhtml+xml" in accept


def _parse_boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_topic_filters(request) -> Dict[str, Any]:
    params = request.query_params
    target_length_tier = params.get("target_length_tier")
    script_usage = str(params.get("script_usage") or "all").strip().lower()
    topic_mode = str(params.get("topic_mode") or "basic").strip().lower()
    if topic_mode not in {"basic", "generated"}:
        topic_mode = "basic"
    if script_usage not in {"all", "used", "unused"}:
        script_usage = "all"
    return {
        "search": str(params.get("search") or "").strip(),
        "post_type": str(params.get("post_type") or "").strip() or None,
        "target_length_tier": int(target_length_tier) if target_length_tier and str(target_length_tier).isdigit() else None,
        "topic_id": str(params.get("topic_id") or "").strip() or None,
        "run_id": str(params.get("run_id") or "").strip() or None,
        "status": str(params.get("status") or "").strip() or None,
        "script_usage": script_usage,
        "topic_mode": topic_mode,
        "only_with_scripts": _parse_boolish(params.get("only_with_scripts") or ""),
    }


def _topic_search_match(topic: Dict[str, Any], search: str) -> bool:
    if not search:
        return True
    haystack = " ".join(
        str(topic.get(key) or "")
        for key in (
            "title",
            "script",
            "rotation",
            "cta",
            "post_type",
        )
    ).lower()
    return search.lower() in haystack


def _topic_has_tier(topic: Dict[str, Any], target_length_tier: Optional[int]) -> bool:
    if target_length_tier is None:
        return True
    if str(topic.get("id") or "").startswith("topic-bank-"):
        return True
    return bool(get_topic_scripts_for_registry(topic["id"], target_length_tier))


def _topic_to_detail(topic: Dict[str, Any]) -> Dict[str, Any]:
    if str(topic.get("id") or "").startswith("topic-bank-"):
        return {
            **topic,
            "bank_summary": [],
            "scripts": [],
        }
    scripts = get_topic_scripts_for_registry(topic["id"])
    bank_summary = []
    counts: Dict[str, int] = {}
    for script_row in scripts:
        tier_key = str(script_row.get("target_length_tier") or "")
        counts[tier_key] = counts.get(tier_key, 0) + 1
    for tier_key, variant_count in sorted(counts.items()):
        bank_summary.append(
            {
                "tier": int(tier_key) if tier_key.isdigit() else tier_key,
                "variant_count": variant_count,
            }
        )
    return {
        **topic,
        "bank_summary": bank_summary,
        "scripts": scripts,
    }


def _coerce_script_usage(script: Dict[str, Any]) -> str:
    return "used" if int(script.get("use_count") or 0) > 0 else "unused"


def _sort_topic_scripts(scripts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _sort_key(row: Dict[str, Any]) -> tuple:
        return (
            int(row.get("use_count") or 0) > 0,
            -(int(row.get("use_count") or 0)),
            str(row.get("last_used_at") or ""),
            str(row.get("created_at") or ""),
            str(row.get("script") or ""),
        )

    return sorted(list(scripts or []), key=_sort_key)


def _group_scripts_by_usage(scripts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unused = []
    used = []
    for script in _sort_topic_scripts(scripts):
        if _coerce_script_usage(script) == "used":
            used.append(script)
        else:
            unused.append(script)

    groups = [
        {
            "key": "unused",
            "label": "Unused scripts",
            "description": "Highest priority coverage gaps for the bank.",
            "count": len(unused),
            "scripts": unused,
        },
        {
            "key": "used",
            "label": "Used scripts",
            "description": "Scripts already proven in prior batches.",
            "count": len(used),
            "scripts": used,
        },
    ]
    return [group for group in groups if group["count"] > 0]


def build_topic_hub_payload(request) -> Dict[str, Any]:
    filters = parse_topic_filters(request)
    basic_topics = [
        topic
        for topic in _topic_bank_rows()
        if _topic_search_match(topic, filters["search"])
        and (filters["post_type"] is None or str(topic.get("post_type") or "") == filters["post_type"])
    ]
    basic_topics.sort(key=lambda row: str(row.get("title") or "").lower())

    generated_topics = [
        topic
        for topic in get_all_topics_from_registry()
        if _topic_search_match(topic, filters["search"])
        and (filters["post_type"] is None or str(topic.get("post_type") or "") == filters["post_type"])
        and _topic_has_tier(topic, filters["target_length_tier"])
    ]
    fresh_cutoff = datetime.now(timezone.utc) - _TOPIC_NEW_WINDOW
    generated_topics.sort(key=lambda row: _generated_topic_sort_key(row, fresh_cutoff))
    script_counts = _fetch_all_script_counts()
    generated_topics = [
        {
            **topic,
            "script_count": _count_topic_scripts(str(topic["id"]), script_counts),
            "is_new": bool((_parse_utc_timestamp(topic.get("last_harvested_at") or topic.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= fresh_cutoff),
        }
        for topic in generated_topics
    ]

    scripts = list_topic_suggestions(
        target_length_tier=filters["target_length_tier"],
        limit=50,
        post_type=filters["post_type"],
    )
    if filters["only_with_scripts"]:
        scripts = [script for script in scripts if script.get("script")]

    runs = list_topic_research_runs(limit=12, status=filters["status"])
    selected_topic = None
    topics = basic_topics if filters["topic_mode"] == "basic" else generated_topics
    if filters["topic_id"]:
        for topic in generated_topics + basic_topics:
            if topic.get("id") == filters["topic_id"]:
                selected_topic = _topic_to_detail(topic)
                break
    if selected_topic is None and topics and filters["topic_mode"] == "basic":
        selected_topic = _topic_to_detail(topics[0])
    if selected_topic is None and topics and filters["topic_mode"] == "generated":
        selected_topic = _topic_to_detail(topics[0])

    selected_scripts: List[Dict[str, Any]] = []
    if selected_topic:
        if str(selected_topic.get("id") or "").startswith("topic-bank-"):
            selected_scripts = []
        else:
            selected_scripts = get_topic_scripts_for_registry(
                selected_topic["id"],
                filters["target_length_tier"],
            )

    selected_scripts = _sort_topic_scripts(selected_scripts)
    selected_script_groups = _group_scripts_by_usage(selected_scripts)
    script_usage_filter = filters["script_usage"]
    if script_usage_filter in {"used", "unused"}:
        selected_script_groups = [group for group in selected_script_groups if group["key"] == script_usage_filter]
        selected_scripts = [script for group in selected_script_groups for script in group["scripts"]]

    active_runs = [run for run in runs if run.get("status") == "running"]
    completed_runs = [run for run in runs if run.get("status") != "running"]

    return {
        "filters": filters,
        "topics": topics,
        "basic_topics": basic_topics,
        "generated_topics": generated_topics,
        "total_topics": len(topics),
        "basic_topic_count": len(basic_topics),
        "generated_topic_count": len(generated_topics),
        "scripts": scripts,
        "selected_topic": selected_topic,
        "selected_scripts": selected_scripts,
        "selected_script_groups": selected_script_groups,
        "script_usage_filter": script_usage_filter,
        "runs": runs,
        "active_runs": active_runs,
        "completed_runs": completed_runs,
    }


def _build_script_variants(
    *,
    topic_title: str,
    post_type: str,
    target_length_tier: int,
    research_dossier: Dict[str, Any],
    prompt1_item,
    dialog_scripts,
    seed_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    source_urls = [
        {"title": source.get("title"), "url": source.get("url")}
        for source in list(research_dossier.get("sources") or [])
        if isinstance(source, dict) and source.get("url")
    ]
    categories = [
        ("problem_agitate_solution", dialog_scripts.problem_agitate_solution),
        ("testimonial", dialog_scripts.testimonial),
        ("transformation", dialog_scripts.transformation),
    ]
    variants: List[Dict[str, Any]] = []
    for bucket_name, scripts in categories:
        script = next((str(script).strip() for script in list(scripts or []) if str(script).strip()), "")
        if not script:
            continue
        variants.append(
            {
                "bucket": bucket_name,
                "hook_style": bucket_name.replace("_", "-"),
                "framework": research_dossier.get("framework_candidates", ["PAL"])[0] if research_dossier.get("framework_candidates") else "PAL",
                "tone": "direkt, freundlich, empowernd, du-Form",
                "estimated_duration_s": max(1, round(len(script.split()) / 2.6)),
                "lane_key": str((research_dossier.get("lane_candidate") or {}).get("lane_key") or ""),
                "lane_family": str((research_dossier.get("lane_candidate") or {}).get("lane_family") or ""),
                "cluster_id": research_dossier.get("cluster_id"),
                "anchor_topic": research_dossier.get("anchor_topic"),
                "disclaimer": research_dossier.get("disclaimer"),
                "source_summary": research_dossier.get("source_summary"),
                "primary_source_url": source_urls[0]["url"] if source_urls else None,
                "primary_source_title": source_urls[0]["title"] if source_urls else None,
                "source_urls": source_urls,
                "caption": seed_payload.get("research_caption") or seed_payload.get("caption"),
                "script": script,
                "quality_notes": "",
                "seed_payload": seed_payload,
            }
        )
    return variants


def _build_value_dialog_scripts_from_prompt1(prompt1_item) -> Any:
    script = str(prompt1_item.script or "").strip()
    caption = str(prompt1_item.caption or "").strip()
    fallback_description = caption or script
    return SimpleNamespace(**{
        "problem_agitate_solution": [script],
        "testimonial": [script],
        "transformation": [script],
        "description": fallback_description,
    })


def _normalize_topic_signature(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _choose_unique_seed_topics(seed_topics: List[str], *, count: int, seed: Optional[int] = None) -> List[str]:
    unique_topics = []
    seen = set()
    for topic in seed_topics:
        normalized = _normalize_topic_signature(topic)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_topics.append(str(topic).strip())
    if count <= 0 or not unique_topics:
        return []
    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    rng.shuffle(unique_topics)
    return unique_topics[:count]


def _generate_fallback_seed_topics(missing_count: int, *, post_type: str) -> List[str]:
    if missing_count <= 0:
        return []
    llm = get_llm_client()
    prompt = (
        "Erzeuge genau "
        f"{missing_count} neue, kurze, einzigartige deutsche Seed-Topics fuer einen {post_type}-Warm-up-Lauf. "
        "Antworte nur als JSON-Array von Strings. "
        "Keine Duplikate zu typischen UGC-Themen, keine Deep-Research-Fakten, keine Erklaerungen."
    )
    response = llm.generate_gemini_json(
        prompt=prompt,
        system_prompt="You generate short German seed topics only.",
        json_schema={
            "type": "array",
            "items": {"type": "string"},
            "minItems": missing_count,
            "maxItems": missing_count,
        },
        max_tokens=800,
    )
    topics = [str(item).strip() for item in list(response or []) if str(item).strip()]
    return _choose_unique_seed_topics(topics, count=missing_count)


def _select_warmup_seed_topics(
    *,
    post_type: str,
    seed_topic_count: int = _WARMUP_SEED_TOPIC_COUNT,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    payload = get_topic_bank()
    catalog = [str(topic).strip() for topic in list(payload.get("topics") or []) if str(topic).strip()]
    chosen = _choose_unique_seed_topics(catalog, count=seed_topic_count, seed=seed)
    seed_generation_used = False
    if len(chosen) < seed_topic_count:
        fallback_topics = _generate_fallback_seed_topics(seed_topic_count - len(chosen), post_type=post_type)
        seed_generation_used = bool(fallback_topics)
        for topic in fallback_topics:
            if len(chosen) >= seed_topic_count:
                break
            normalized = _normalize_topic_signature(topic)
            if normalized and normalized not in {_normalize_topic_signature(item) for item in chosen}:
                chosen.append(topic)
    return {
        "seed_topics": chosen[:seed_topic_count],
        "seed_generation_used": seed_generation_used,
    }


def _build_canonical_script_variant(
    *,
    prompt1_item,
    lane_candidate: Dict[str, Any],
    research_dossier: Dict[str, Any],
    tier: int,
    post_type: str,
    seed_payload: Dict[str, Any],
) -> Dict[str, Any]:
    source_urls = [
        {"title": source.get("title"), "url": source.get("url")}
        for source in list(research_dossier.get("sources") or [])
        if isinstance(source, dict) and source.get("url")
    ]
    return {
        "bucket": "canonical",
        "target_length_tier": tier,
        "hook_style": str(lane_candidate.get("lane_family") or "canonical").strip() or "canonical",
        "framework": str(prompt1_item.framework or "PAL").strip() or "PAL",
        "tone": sanitize_metadata_text(prompt1_item.tone or "direkt, freundlich, empowernd, du-Form", max_sentences=1),
        "estimated_duration_s": int(getattr(prompt1_item, "estimated_duration_s", 0) or 0) or tier,
        "lane_key": str(lane_candidate.get("lane_key") or "").strip(),
        "lane_family": str(lane_candidate.get("lane_family") or "").strip(),
        "cluster_id": str(research_dossier.get("cluster_id") or "").strip(),
        "anchor_topic": str(research_dossier.get("anchor_topic") or lane_candidate.get("title") or "").strip(),
        "disclaimer": sanitize_metadata_text(prompt1_item.disclaimer or research_dossier.get("disclaimer") or "", max_sentences=1),
        "source_summary": sanitize_metadata_text(
            research_dossier.get("source_summary") or lane_candidate.get("source_summary") or ""
        ),
        "primary_source_url": source_urls[0]["url"] if source_urls else None,
        "primary_source_title": source_urls[0]["title"] if source_urls else None,
        "source_urls": source_urls,
        "seed_payload": seed_payload,
        "quality_score": getattr(prompt1_item, "quality_score", None),
        "quality_notes": getattr(prompt1_item, "quality_notes", None) or "",
        "script": sanitize_spoken_fragment(prompt1_item.script or "", ensure_terminal=True),
        "post_type": post_type,
    }


def _build_lane_dossier(research_dossier: Dict[str, Any], lane_candidate: Dict[str, Any]) -> Dict[str, Any]:
    lane = dict(lane_candidate or {})
    return {
        "cluster_id": research_dossier.get("cluster_id"),
        "topic": str(lane.get("title") or research_dossier.get("topic") or "").strip(),
        "anchor_topic": research_dossier.get("anchor_topic"),
        "seed_topic": research_dossier.get("seed_topic") or research_dossier.get("topic"),
        "cluster_summary": research_dossier.get("cluster_summary"),
        "framework_candidates": list(lane.get("framework_candidates") or research_dossier.get("framework_candidates") or []),
        "sources": list(research_dossier.get("sources") or []),
        "source_summary": sanitize_metadata_text(lane.get("source_summary") or research_dossier.get("source_summary") or ""),
        "facts": list(lane.get("facts") or research_dossier.get("facts") or []),
        "angle_options": [str(lane.get("angle") or "").strip()] + [
            str(item).strip()
            for item in list(research_dossier.get("angle_options") or [])
            if str(item).strip() and str(item).strip() != str(lane.get("angle") or "").strip()
        ],
        "risk_notes": list(lane.get("risk_notes") or research_dossier.get("risk_notes") or []),
        "disclaimer": sanitize_metadata_text(lane.get("disclaimer") or research_dossier.get("disclaimer") or "", max_sentences=1),
        "lane_candidates": [lane],
        "lane_candidate": lane,
    }


def _persist_topic_bank_row(
    *,
    title: str,
    target_length_tier: int,
    research_dossier: Dict[str, Any],
    prompt1_item,
    dialog_scripts,
    post_type: str,
    seed_payload: Dict[str, Any],
    variants: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    script_variants = variants if variants is not None else _build_script_variants(
        topic_title=title,
        post_type=post_type,
        target_length_tier=target_length_tier,
        research_dossier=research_dossier,
        prompt1_item=prompt1_item,
        dialog_scripts=dialog_scripts,
        seed_payload=seed_payload,
    )
    stored_row = store_topic_bank_entry(
        title=title,
        topic_script=sanitize_spoken_fragment(prompt1_item.script, ensure_terminal=True),
        post_type=post_type,
        target_length_tier=target_length_tier,
        research_payload=research_dossier,
    )
    topic_research_dossier_id = str(stored_row.get("topic_research_dossier_id") or stored_row.get("research_dossier_id") or "").strip() or None
    stored_variants = upsert_topic_script_variants(
        topic_registry_id=stored_row["id"],
        title=stored_row["title"],
        post_type=post_type,
        target_length_tier=target_length_tier,
        topic_research_dossier_id=topic_research_dossier_id,
        variants=script_variants,
    )
    if not isinstance(stored_variants, list):
        stored_variants = []
    return {
        "stored_row": stored_row,
        "stored_variants": stored_variants,
    }


def _harvest_seed_topic_to_bank(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: Optional[int] = None,
    existing_topics: List[Dict[str, Any]],
    collected_topics: List[Dict[str, Any]],
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    return run_single_seed_topic_warmup(
        seed_topic=seed_topic,
        post_type=post_type,
        existing_topics=existing_topics,
        collected_topics=collected_topics,
        target_length_tier=target_length_tier,
        progress_callback=progress_callback,
    )


def _run_topic_research_pipeline_sync(
    *,
    run_id: str,
    topic_registry_id: str,
    target_length_tier: Optional[int],
    trigger_source: str,
    post_type: str,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    topic = get_topic_registry_by_id(topic_registry_id)
    update_topic_research_run(
        run_id,
        status="running",
        result_summary={
            "topic_registry_id": topic_registry_id,
            "topic_title": topic["title"],
            "target_length_tier": target_length_tier,
            "requested_target_length_tier": target_length_tier,
            "trigger_source": trigger_source,
        },
    )

    if progress_callback:
        progress_callback(
            stage="researching",
            stage_label="Starting canonical bank warm-up",
            detail_message="Starting deep research for the canonical 8/16/32 warm-up set.",
        )
    harvest_summary = _harvest_seed_topic_to_bank(
        seed_topic=topic["title"],
        post_type=post_type,
        target_length_tier=target_length_tier,
        existing_topics=get_all_topics_from_registry(),
        collected_topics=[],
        progress_callback=progress_callback,
    )
    if isinstance(harvest_summary, list):
        all_stored_topics = list(harvest_summary)
        harvest_summary = {
            "seed_topic": topic["title"],
            "post_type": post_type,
            "requested_target_length_tier": target_length_tier,
            "tiers_processed": list(_CANONICAL_TIERS),
            "dossiers_completed": 0,
            "lanes_seen": len(all_stored_topics),
            "lanes_persisted": len(all_stored_topics),
            "scripts_persisted_by_tier": {str(tier): 0 for tier in _CANONICAL_TIERS},
            "duplicate_scripts_skipped": 0,
            "stored_rows": all_stored_topics,
            "seed_topics_used": [topic["title"]],
        }
    else:
        all_stored_topics = list(harvest_summary.get("stored_rows") or [])
    if progress_callback:
        progress_callback(
            stage="collecting",
            stage_label="Canonical bank warm-up complete",
            detail_message=f"Warm-up complete — {len(all_stored_topics)} lane rows stored with canonical 8/16/32 coverage.",
        )

    run_summary = {
        "topic_registry_id": topic_registry_id,
        "topic_title": topic["title"],
        "stored_topic_ids": [row["id"] for row in all_stored_topics],
        "stored_count": len(all_stored_topics),
        "target_length_tier": target_length_tier,
        "requested_target_length_tier": target_length_tier,
        "tiers_processed": list(harvest_summary.get("tiers_processed") or _CANONICAL_TIERS),
        "post_type": post_type,
        "seed_topics_used": list(harvest_summary.get("seed_topics_used") or [topic["title"]]),
        "dossiers_completed": int(harvest_summary.get("dossiers_completed") or 0),
        "lanes_seen": int(harvest_summary.get("lanes_seen") or 0),
        "lanes_persisted": int(harvest_summary.get("lanes_persisted") or 0),
        "scripts_persisted_by_tier": dict(harvest_summary.get("scripts_persisted_by_tier") or {}),
        "duplicate_scripts_skipped": int(harvest_summary.get("duplicate_scripts_skipped") or 0),
        "research_source": str(harvest_summary.get("research_source") or "provider"),
    }
    update_topic_research_run(
        run_id,
        status="completed",
        result_summary=run_summary,
        error_message="",
    )
    return run_summary


async def launch_topic_research_run(
    *,
    topic_registry_id: str,
    target_length_tier: Optional[int],
    trigger_source: str,
    post_type: Optional[str] = None,
    progress_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    topic = get_topic_registry_by_id(topic_registry_id)
    resolved_tier = int(target_length_tier) if target_length_tier else None
    resolved_post_type = post_type or str(topic.get("post_type") or "value")
    requested_counts = {"topics": 1, resolved_post_type: 1}
    run_row = create_topic_research_run(
        trigger_source=trigger_source,
        requested_counts=requested_counts,
        target_length_tier=resolved_tier,
        topic_registry_id=topic_registry_id,
    )
    schedule_topic_research_run(
        run_row=run_row,
        topic_registry_id=topic_registry_id,
        target_length_tier=resolved_tier,
        trigger_source=trigger_source,
        post_type=resolved_post_type,
        reason="manual_launch",
        progress_callback=progress_callback,
    )
    return {
        "run": run_row,
        "topic": topic,
        "status_url": f"/topics/runs/{run_row['id']}",
    }


def harvest_topics_to_bank_sync(
    *,
    post_type_counts: Dict[str, int],
    target_length_tier: int,
    trigger_source: str,
) -> Dict[str, Any]:
    run_row = create_topic_research_run(
        trigger_source=trigger_source,
        requested_counts=post_type_counts,
        target_length_tier=target_length_tier,
        topic_registry_id=None,
    )
    stored_by_type: Dict[str, int] = defaultdict(int)
    stored_topics: List[Dict[str, Any]] = []
    seed_topics_used: List[str] = []
    run_summary: Dict[str, Any] = {
        "trigger_source": trigger_source,
        "target_length_tier": target_length_tier,
        "stored_by_type": {},
        "stored_topics": [],
        "seed_topics_used": [],
        "dossiers_completed": 0,
        "lanes_seen": 0,
        "lanes_persisted": 0,
        "scripts_persisted_by_tier": {str(tier): 0 for tier in _CANONICAL_TIERS},
        "duplicate_scripts_skipped": 0,
        "seed_generation_used": False,
    }

    try:
        existing_topics = get_all_topics_from_registry()
        collected_topics: List[Dict[str, Any]] = []
        for post_type, count in post_type_counts.items():
            if count <= 0:
                continue
            seed_topic_count = 3 if count <= 3 else 4 if count <= 6 else 5
            warmup = _select_warmup_seed_topics(
                post_type=post_type,
                seed_topic_count=seed_topic_count,
                seed=hash((trigger_source, post_type, target_length_tier, count)),
            )
            seed_topics = list(warmup.get("seed_topics") or [])
            run_summary["seed_generation_used"] = run_summary["seed_generation_used"] or bool(warmup.get("seed_generation_used"))
            for seed_topic in seed_topics:
                if seed_topic not in seed_topics_used:
                    seed_topics_used.append(seed_topic)
                harvest_summary = _harvest_seed_topic_to_bank(
                    seed_topic=seed_topic,
                    post_type=post_type,
                    existing_topics=existing_topics,
                    collected_topics=collected_topics,
                )
                stored_by_type[post_type] += int(harvest_summary.get("lanes_persisted") or 0)
                run_summary["dossiers_completed"] += int(harvest_summary.get("dossiers_completed") or 0)
                run_summary["lanes_seen"] += int(harvest_summary.get("lanes_seen") or 0)
                run_summary["lanes_persisted"] += int(harvest_summary.get("lanes_persisted") or 0)
                run_summary["duplicate_scripts_skipped"] += int(harvest_summary.get("duplicate_scripts_skipped") or 0)
                for tier, count_value in dict(harvest_summary.get("scripts_persisted_by_tier") or {}).items():
                    run_summary["scripts_persisted_by_tier"][str(tier)] += int(count_value or 0)
                stored_rows = list(harvest_summary.get("stored_rows") or [])
                stored_topics.extend(stored_rows)

        run_summary["seed_topics_used"] = seed_topics_used
        run_summary["stored_by_type"] = dict(stored_by_type)
        run_summary["stored_topics"] = [topic["id"] for topic in stored_topics]
        update_topic_research_run(
            run_row["id"],
            status="completed",
            result_summary={
                "trigger_source": trigger_source,
            "target_length_tier": target_length_tier,
            "requested_target_length_tier": target_length_tier,
            "stored_by_type": dict(stored_by_type),
            "stored_topics": [topic["id"] for topic in stored_topics],
            "seed_topics_used": seed_topics_used,
            "dossiers_completed": run_summary["dossiers_completed"],
            "lanes_seen": run_summary["lanes_seen"],
                "lanes_persisted": run_summary["lanes_persisted"],
                "scripts_persisted_by_tier": run_summary["scripts_persisted_by_tier"],
                "duplicate_scripts_skipped": run_summary["duplicate_scripts_skipped"],
                "seed_generation_used": run_summary["seed_generation_used"],
            },
            error_message="",
        )
    except Exception as exc:
        update_topic_research_run(
            run_row["id"],
            status="failed",
            error_message=str(exc),
            result_summary={
                "trigger_source": trigger_source,
                "target_length_tier": target_length_tier,
                "stored_by_type": dict(stored_by_type),
            },
        )
        raise

    run_summary["run_id"] = run_row["id"]
    return run_summary
