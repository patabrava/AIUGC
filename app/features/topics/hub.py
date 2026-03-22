"""Topics hub service helpers."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger
from app.core.video_profiles import get_duration_profile
from app.core.errors import FlowForgeException, ThirdPartyError, ValidationError
from app.features.topics.agents import (
    build_seed_payload,
    convert_research_item_to_topic,
    extract_seed_strict_extractor,
    generate_dialog_scripts,
    generate_topic_research_dossier,
    generate_topic_script_candidate,
)
from app.features.topics.deduplication import calculate_topic_similarity, deduplicate_topics
from app.features.topics.prompts import pick_topic_bank_topics
from app.features.topics.queries import (
    create_topic_research_run,
    get_all_topics_from_registry,
    get_topic_registry_by_id,
    get_topic_research_run,
    get_topic_scripts_for_registry,
    list_topic_research_runs,
    list_topic_suggestions,
    store_topic_bank_entry,
    update_topic_research_run,
    upsert_topic_script_variants,
)

logger = get_logger(__name__)

TOPIC_RUN_TASKS: Dict[str, asyncio.Task] = {}


def get_random_topic() -> Optional[Dict[str, Any]]:
    """Return the topic with the fewest scripts (least coverage)."""
    topics = get_all_topics_from_registry()
    if not topics:
        return None
    scored = []
    for topic in topics:
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
    all_scripts = _fetch_topic_script_rows()
    counts: Dict[str, int] = {}
    for script in all_scripts:
        rid = str(script.get("topic_registry_id") or "")
        counts[rid] = counts.get(rid, 0) + 1
    return counts


def build_launch_hub_payload(request) -> Dict[str, Any]:
    """Build a simplified payload for the launch-focused hub."""
    filters = parse_topic_filters(request)
    topics = [
        topic
        for topic in get_all_topics_from_registry()
        if _topic_search_match(topic, filters["search"])
        and (filters["post_type"] is None or str(topic.get("post_type") or "") == filters["post_type"])
    ]
    # Enrich with script counts in a single batch query (avoids N+1)
    script_counts = _fetch_all_script_counts()
    enriched = []
    for topic in topics:
        enriched.append({**topic, "script_count": script_counts.get(topic["id"], 0)})
    if filters.get("only_with_scripts"):
        enriched = [t for t in enriched if t["script_count"] > 0]
    enriched.sort(key=lambda t: t["script_count"])
    runs = list_topic_research_runs(limit=5)
    active_runs = [run for run in runs if run.get("status") == "running"]
    return {
        "filters": filters,
        "topics": enriched,
        "total_topics": len(enriched),
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
            "language",
        )
    ).lower()
    return search.lower() in haystack


def _topic_has_tier(topic: Dict[str, Any], target_length_tier: Optional[int]) -> bool:
    if target_length_tier is None:
        return True
    if get_topic_scripts_for_registry(topic["id"], target_length_tier):
        return True
    tiers = {int(tier) for tier in topic.get("target_length_tiers") or [] if str(tier).strip().isdigit()}
    return target_length_tier in tiers if tiers else False


def _topic_to_detail(topic: Dict[str, Any]) -> Dict[str, Any]:
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
    topics = [
        topic
        for topic in get_all_topics_from_registry()
        if _topic_search_match(topic, filters["search"])
        and (filters["post_type"] is None or str(topic.get("post_type") or "") == filters["post_type"])
        and _topic_has_tier(topic, filters["target_length_tier"])
    ]
    topics.sort(key=lambda row: str(row.get("last_harvested_at") or row.get("created_at") or ""), reverse=True)

    scripts = list_topic_suggestions(
        target_length_tier=filters["target_length_tier"],
        limit=50,
        post_type=filters["post_type"],
    )
    if filters["only_with_scripts"]:
        scripts = [script for script in scripts if script.get("script")]

    runs = list_topic_research_runs(limit=12, status=filters["status"])
    selected_topic = None
    if filters["topic_id"]:
        for topic in topics:
            if topic.get("id") == filters["topic_id"]:
                selected_topic = _topic_to_detail(topic)
                break
    if selected_topic is None and topics:
        selected_topic = _topic_to_detail(topics[0])

    selected_scripts: List[Dict[str, Any]] = []
    if selected_topic:
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
        "total_topics": len(topics),
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
                "caption": seed_payload.get("caption"),
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
        "source_summary": str(lane.get("source_summary") or research_dossier.get("source_summary") or "").strip(),
        "facts": list(lane.get("facts") or research_dossier.get("facts") or []),
        "angle_options": [str(lane.get("angle") or "").strip()] + [
            str(item).strip()
            for item in list(research_dossier.get("angle_options") or [])
            if str(item).strip() and str(item).strip() != str(lane.get("angle") or "").strip()
        ],
        "risk_notes": list(lane.get("risk_notes") or research_dossier.get("risk_notes") or []),
        "disclaimer": str(lane.get("disclaimer") or research_dossier.get("disclaimer") or "").strip(),
        "lane_candidates": [lane],
        "lane_candidate": lane,
    }


def _source_bank_from_dossier(research_dossier: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"title": source.get("title"), "url": source.get("url")}
        for source in list(research_dossier.get("sources") or [])
        if isinstance(source, dict) and source.get("url")
    ]


def _persist_topic_bank_row(
    *,
    title: str,
    target_length_tier: int,
    research_dossier: Dict[str, Any],
    prompt1_item,
    dialog_scripts,
    post_type: str,
    seed_payload: Dict[str, Any],
) -> Dict[str, Any]:
    script_variants = _build_script_variants(
        topic_title=title,
        post_type=post_type,
        target_length_tier=target_length_tier,
        research_dossier=research_dossier,
        prompt1_item=prompt1_item,
        dialog_scripts=dialog_scripts,
        seed_payload=seed_payload,
    )
    source_bank = _source_bank_from_dossier(research_dossier)
    stored_row = store_topic_bank_entry(
        title=title,
        topic_script=prompt1_item.script,
        post_type=post_type,
        target_length_tier=target_length_tier,
        research_payload=research_dossier,
        source_bank=source_bank,
        script_bank={},
        seed_payloads={},
    )
    topic_research_dossier_id = str(stored_row.get("topic_research_dossier_id") or stored_row.get("research_dossier_id") or "").strip() or None
    upsert_topic_script_variants(
        topic_registry_id=stored_row["id"],
        title=stored_row["title"],
        post_type=post_type,
        target_length_tier=target_length_tier,
        topic_research_dossier_id=topic_research_dossier_id,
        variants=script_variants,
    )
    return stored_row


def _harvest_seed_topic_to_bank(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
    existing_topics: List[Dict[str, Any]],
    collected_topics: List[Dict[str, Any]],
    progress_callback: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    research_dossier = generate_topic_research_dossier(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
        progress_callback=progress_callback,
    ).model_dump(mode="json")
    stored_rows: List[Dict[str, Any]] = []
    for lane_candidate in list(research_dossier.get("lane_candidates") or []):
        lane_dossier = _build_lane_dossier(research_dossier, lane_candidate)
        prompt1_item = generate_topic_script_candidate(
            post_type=post_type,
            target_length_tier=target_length_tier,
            dossier=lane_dossier,
            lane_candidate=lane_candidate,
        )
        topic_data = convert_research_item_to_topic(prompt1_item)
        lane_title = str(lane_candidate.get("title") or prompt1_item.topic or topic_data.title).strip()
        dedupe_candidate = {
            "title": lane_title,
            "rotation": topic_data.rotation,
            "cta": topic_data.cta,
        }
        unique_candidates = deduplicate_topics(
            [dedupe_candidate],
            existing_topics + collected_topics,
            threshold=0.35,
        )
        if not unique_candidates:
            logger.info("topic_bank_lane_deduped", seed_topic=seed_topic, lane_title=lane_title)
            continue

        if post_type == "value":
            dialog_scripts = _build_value_dialog_scripts_from_prompt1(prompt1_item)
        else:
            dialog_scripts = generate_dialog_scripts(
                topic=lane_title,
                scripts_required=1,
                dossier=lane_dossier,
                profile=get_duration_profile(target_length_tier),
            )
        strict_seed = extract_seed_strict_extractor(topic_data)
        source_info = (lane_dossier.get("sources") or [{}])[0] if lane_dossier.get("sources") else {}
        seed_payload = build_seed_payload(
            prompt1_item,
            strict_seed,
            dialog_scripts,
            source_title=str(source_info.get("title") or lane_title or prompt1_item.topic).strip() or None,
            source_url=str(source_info.get("url") or "").strip() or None,
            source_summary=str(lane_dossier.get("source_summary") or prompt1_item.caption or prompt1_item.source_summary or "").strip() or None,
        )
        stored_row = _persist_topic_bank_row(
            title=lane_title,
            target_length_tier=target_length_tier,
            research_dossier=lane_dossier,
            prompt1_item=prompt1_item,
            dialog_scripts=dialog_scripts,
            post_type=post_type,
            seed_payload=seed_payload,
        )
        dedupe_record = {
            "id": stored_row.get("id"),
            "title": lane_title,
            "rotation": topic_data.rotation,
            "cta": topic_data.cta,
        }
        existing_topics.append(dedupe_record)
        collected_topics.append(dedupe_record)
        stored_rows.append(stored_row)
    return stored_rows


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
            "trigger_source": trigger_source,
        },
    )

    tiers = [target_length_tier] if target_length_tier else [8, 16, 32]
    all_stored_topics: List[Dict[str, Any]] = []

    for i, tier in enumerate(tiers):
        if progress_callback:
            progress_callback(stage="researching", stage_label=f"Tier {tier}s ({i+1}/{len(tiers)})", detail_message=f"Starting deep research for {tier}s tier...")
        stored_topics = _harvest_seed_topic_to_bank(
            seed_topic=topic["title"],
            post_type=post_type,
            target_length_tier=tier,
            existing_topics=get_all_topics_from_registry(),
            collected_topics=[],
            progress_callback=progress_callback,
        )
        if progress_callback:
            progress_callback(stage="collecting", stage_label=f"Tier {tier}s ({i+1}/{len(tiers)})", detail_message=f"Tier {tier}s complete — {len(stored_topics)} topics stored.")
        all_stored_topics.extend(stored_topics)

    run_summary = {
        "topic_registry_id": topic_registry_id,
        "topic_title": topic["title"],
        "stored_topic_ids": [row["id"] for row in all_stored_topics],
        "stored_count": len(all_stored_topics),
        "target_length_tier": target_length_tier,
        "tiers_processed": tiers,
        "post_type": post_type,
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

    # Initialize live progress tracking
    from app.features.topics.handlers import start_seeding_interaction, update_seeding_progress
    start_seeding_interaction(batch_id=run_row["id"], brand=topic.get("title", ""), expected_posts=0)

    def _run_progress_callback(*args, **kwargs):
        """Handle both Gemini dict callbacks and pipeline kwargs callbacks."""
        if args and isinstance(args[0], dict):
            # Gemini LLM callback: progress_callback({"provider_status": ..., "detail_message": ...})
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
            # Pipeline stage callback: progress_callback(stage="researching", ...)
            update_seeding_progress(run_row["id"], **kwargs)

    async def runner() -> None:
        try:
            await asyncio.to_thread(
                _run_topic_research_pipeline_sync,
                run_id=run_row["id"],
                topic_registry_id=topic_registry_id,
                target_length_tier=resolved_tier,
                trigger_source=trigger_source,
                post_type=resolved_post_type,
                progress_callback=_run_progress_callback,
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
                    "target_length_tier": resolved_tier,
                    "trigger_source": trigger_source,
                },
            )
            update_seeding_progress(run_row["id"], stage="failed", stage_label="Research failed", status="failed", detail_message=str(exc.message if hasattr(exc, 'message') else exc))
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
                    "target_length_tier": resolved_tier,
                    "trigger_source": trigger_source,
                },
            )
            update_seeding_progress(run_row["id"], stage="failed", stage_label="Research failed", status="failed", detail_message=str(exc.message if hasattr(exc, 'message') else exc))
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
                    "target_length_tier": resolved_tier,
                    "trigger_source": trigger_source,
                },
            )
            update_seeding_progress(run_row["id"], stage="failed", stage_label="Research failed", status="failed", detail_message=str(exc.message if hasattr(exc, 'message') else exc))
            logger.exception(
                "topic_research_run_failed",
                run_id=run_row["id"],
                topic_registry_id=topic_registry_id,
                error=str(exc),
            )

    task = asyncio.create_task(runner())
    TOPIC_RUN_TASKS[run_row["id"]] = task

    def _cleanup(_task: asyncio.Task) -> None:
        TOPIC_RUN_TASKS.pop(run_row["id"], None)

    task.add_done_callback(_cleanup)
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

    try:
        existing_topics = get_all_topics_from_registry()
        collected_topics: List[Dict[str, Any]] = []
        for post_type, count in post_type_counts.items():
            if count <= 0:
                continue
            seed_topics = pick_topic_bank_topics(
                count,
                seed=hash((trigger_source, post_type, target_length_tier, count)),
            )
            for seed_topic in seed_topics:
                stored_rows = _harvest_seed_topic_to_bank(
                    seed_topic=seed_topic,
                    post_type=post_type,
                    target_length_tier=target_length_tier,
                    existing_topics=existing_topics,
                    collected_topics=collected_topics,
                )
                stored_by_type[post_type] += len(stored_rows)
                stored_topics.extend(stored_rows)

        update_topic_research_run(
            run_row["id"],
            status="completed",
            result_summary={
                "trigger_source": trigger_source,
                "target_length_tier": target_length_tier,
                "stored_by_type": dict(stored_by_type),
                "stored_topics": [topic["id"] for topic in stored_topics],
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

    return {
        "run_id": run_row["id"],
        "stored_by_type": dict(stored_by_type),
        "stored_topics": stored_topics,
        "target_length_tier": target_length_tier,
    }
