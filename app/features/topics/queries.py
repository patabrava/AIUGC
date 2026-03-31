"""FLOW-FORGE Topics Database Queries."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional

from app.adapters.supabase_client import get_supabase, SupabaseAdapter

# Module-level singleton placeholder used by patchable test seams; initialize lazily.
supabase: Optional[SupabaseAdapter] = None
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.features.topics.captions import resolve_selected_caption
from app.features.topics.topic_validation import (
    detect_metadata_bleed,
    detect_spoken_copy_issues,
    get_prompt1_sentence_bounds,
    get_prompt1_word_bounds,
    sanitize_metadata_text,
    sanitize_spoken_fragment,
)

logger = get_logger(__name__)


def _extract_cta(script: str) -> str:
    text = str(script or "").strip()
    if not text:
        return ""
    import re

    sentences = re.findall(r"[^.!?]*[.!?]", text)
    if sentences:
        return sentences[-1].strip()
    words = text.split()
    return " ".join(words[-4:]).strip()


def _normalize_registry_row(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(row or {})
    script = str(normalized.get("script") or normalized.get("rotation") or "").strip()
    rotation = str(normalized.get("rotation") or "").strip()
    cta = str(normalized.get("cta") or "").strip()
    if script and (not rotation or not cta):
        derived_cta = _extract_cta(script)
        derived_rotation = script[: -len(derived_cta)].rstrip(" -–—,:;") if derived_cta and script.endswith(derived_cta) else script
        rotation = rotation or derived_rotation.strip() or script
        cta = cta or derived_cta or script

    normalized["script"] = script or rotation or cta
    normalized["rotation"] = rotation or normalized["script"]
    normalized["cta"] = cta or _extract_cta(normalized["script"])
    normalized["title"] = str(normalized.get("title") or "").strip()
    normalized["post_type"] = normalized.get("post_type")
    normalized["first_seen_at"] = normalized.get("first_seen_at") or normalized.get("created_at") or normalized.get("last_harvested_at")
    normalized["last_used_at"] = normalized.get("last_used_at") or normalized.get("updated_at") or normalized.get("last_harvested_at")
    return normalized


def _normalize_script_row(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(row or {})
    normalized["source_urls"] = list(normalized.get("source_urls") or [])
    normalized["use_count"] = int(normalized.get("use_count") or 0)
    return normalized


def _normalize_script_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _count_script_words(text: Any) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", str(text or "")))


def _count_script_sentences(text: Any) -> int:
    cleaned = str(text or "").strip()
    if not cleaned:
        return 0
    return len([segment for segment in re.split(r"(?<=[.!?])\s+", cleaned) if segment.strip()])


def _fetch_topic_script_rows(
    *,
    target_length_tier: Optional[int] = None,
    topic_registry_id: Optional[str] = None,
    topic_research_dossier_id: Optional[str] = None,
    post_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    supabase = get_supabase()
    last_error: Optional[Exception] = None
    for relation in ("v_topic_scripts_resolved", "topic_scripts"):
        try:
            query = supabase.client.table(relation).select("*")
            if topic_registry_id is not None:
                query = query.eq("topic_registry_id", topic_registry_id)
            if topic_research_dossier_id is not None:
                query = query.eq("topic_research_dossier_id", topic_research_dossier_id)
            if target_length_tier is not None:
                query = query.eq("target_length_tier", target_length_tier)
            if post_type:
                query = query.eq("post_type", post_type)
            response = query.execute()
            return [_normalize_script_row(row) for row in (response.data or [])]
        except Exception as exc:
            last_error = exc
            logger.warning("topic_scripts_relation_fallback", relation=relation, error=str(exc))
    if last_error is not None:
        raise last_error
    return []


def _normalize_dossier_row(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(row or {})
    normalized["normalized_payload"] = normalized.get("normalized_payload") or {}
    normalized["created_at"] = normalized.get("created_at")
    normalized["updated_at"] = normalized.get("updated_at")
    return normalized



def get_all_topics_from_registry() -> List[Dict[str, Any]]:
    """Get all topics from the registry for deduplication and hub browsing."""
    try:
        supabase = get_supabase()
        response = supabase.client.table("topic_registry").select("*").execute()
        return [_normalize_registry_row(row) for row in (response.data or [])]
    except Exception as exc:
        logger.warning("topic_registry_fetch_failed", error=str(exc))
        return []


def get_topic_registry_by_id(topic_registry_id: str) -> Dict[str, Any]:
    supabase = get_supabase()
    response = supabase.client.table("topic_registry").select("*").eq("id", topic_registry_id).limit(1).execute()
    if not response.data:
        raise NotFoundError(message="Topic not found", details={"topic_registry_id": topic_registry_id})
    return _normalize_registry_row(response.data[0])


def _insert_registry_row(payload: Dict[str, Any]) -> Dict[str, Any]:
    supabase = get_supabase()
    response = supabase.client.table("topic_registry").insert(payload).execute()
    if not response.data:
        raise RuntimeError("Failed to insert topic registry row")
    return _normalize_registry_row(response.data[0])


def add_topic_to_registry(
    title: str,
    rotation: Optional[str] = None,
    cta: Optional[str] = None,
    *,
    script: Optional[str] = None,
    post_type: Optional[str] = None,
    last_harvested_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Add or update a slim topic registry entry."""
    topic_script = str(script or rotation or cta or "").strip()
    if not topic_script:
        raise ValueError("A topic script or rotation is required")

    topic_payload: Dict[str, Any] = {
        "title": title,
        "script": topic_script,
        "use_count": 1,
        "post_type": post_type,
        "last_harvested_at": (last_harvested_at or datetime.now(timezone.utc)).isoformat(),
    }

    # Remove keys with None so the payload works across both the legacy and the current schema.
    topic_payload = {key: value for key, value in topic_payload.items() if value is not None}

    try:
        inserted = _insert_registry_row(topic_payload)
        logger.info("topic_added_to_registry", topic_id=inserted["id"], title=title[:50])
        return inserted
    except Exception as exc:
        error_str = str(exc).lower()
        logger.warning("topic_registry_insert_failed", title=title[:50], error=str(exc))
        if "unique" in error_str or "duplicate" in error_str or "constraint" in error_str:
            supabase = get_supabase()
            existing = supabase.client.table("topic_registry").select("*").eq("title", title).limit(1).execute()
            if existing.data:
                existing_row = _normalize_registry_row(existing.data[0])
                current_count = int(existing_row.get("use_count") or 0)
                updated = supabase.client.table("topic_registry").update(
                    {
                        "use_count": current_count + 1,
                        "last_used_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).eq("id", existing_row["id"]).execute()
                if updated.data:
                    logger.info(
                        "topic_use_count_incremented",
                        topic_id=existing_row["id"],
                        new_count=current_count + 1,
                    )
                    return _normalize_registry_row(updated.data[0])
            logger.error(
                "topic_registry_unexpected_error",
                title=title[:50],
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise


def _registry_row_to_topic_suggestion(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_registry_row(row)
    script = normalized["script"]
    return {
        "id": normalized["id"],
        "topic_registry_id": normalized["id"],
        "title": normalized["title"],
        "rotation": normalized["rotation"],
        "cta": normalized["cta"],
        "script": script,
        "spoken_duration": normalized.get("spoken_duration")
        or max(1, int(round(max(len(script.split()), 1) / 2.6))),
        "post_type": normalized.get("post_type"),
        "source_urls": [],
        "last_harvested_at": normalized.get("last_harvested_at"),
        "created_at": normalized.get("created_at"),
        "updated_at": normalized.get("updated_at"),
    }


def _hydrate_script_suggestion(
    script_row: Dict[str, Any],
    registry_row: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    hydrated = dict(script_row)
    registry = _normalize_registry_row(registry_row or {})
    hydrated["id"] = hydrated.get("id") or registry.get("id")
    hydrated["topic_registry_id"] = hydrated.get("topic_registry_id") or registry.get("id")
    hydrated["title"] = str(hydrated.get("title") or registry.get("title") or "").strip()
    hydrated["rotation"] = registry.get("rotation") or hydrated.get("script") or ""
    hydrated["cta"] = registry.get("cta") or _extract_cta(str(hydrated.get("script") or ""))
    hydrated["source_urls"] = hydrated.get("source_urls") or []
    hydrated["seed_payload"] = hydrated.get("seed_payload") or {}
    hydrated["spoken_duration"] = hydrated.get("estimated_duration_s") or max(
        1, int(round(max(len(str(hydrated.get("script") or "").split()), 1) / 2.6))
    )
    hydrated["last_harvested_at"] = registry.get("last_harvested_at")
    hydrated["created_at"] = hydrated.get("created_at") or registry.get("created_at")
    hydrated["updated_at"] = hydrated.get("updated_at") or registry.get("updated_at")
    return hydrated


def create_post_for_batch(
    batch_id: str,
    post_type: str,
    topic_title: str,
    topic_rotation: str,
    topic_cta: str,
    spoken_duration: float,
    seed_data: Dict[str, Any],
    target_length_tier: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a post record for a batch with topic and seed data."""
    supabase = get_supabase()
    resolved_seed_data = dict(seed_data or {})
    if target_length_tier is not None and "target_length_tier" not in resolved_seed_data:
        resolved_seed_data["target_length_tier"] = target_length_tier
    
    post_data = {
        "batch_id": batch_id,
        "post_type": post_type,
        "topic_title": topic_title,
        "topic_rotation": topic_rotation,
        "topic_cta": topic_cta,
        "spoken_duration": spoken_duration,
        "seed_data": resolved_seed_data,
        "publish_caption": resolve_selected_caption(resolved_seed_data),
    }
    
    response = supabase.client.table("posts").insert(post_data).execute()
    
    if not response.data:
        raise Exception("Failed to create post")
    
    logger.info(
        "post_created",
        post_id=response.data[0]["id"],
        batch_id=batch_id,
        post_type=post_type
    )
    
    return response.data[0]


def create_topic_research_dossier(
    *,
    topic_research_run_id: Optional[str],
    topic_registry_id: Optional[str],
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
    cluster_id: str,
    topic: str,
    anchor_topic: str,
    normalized_payload: Dict[str, Any],
    raw_prompt: Optional[str] = None,
    raw_response: Optional[str] = None,
    prompt_name: str = "prompt1_research",
    prompt_version: str = "1",
) -> Dict[str, Any]:
    supabase = get_supabase()
    payload = {
        "topic_research_run_id": topic_research_run_id,
        "topic_registry_id": topic_registry_id,
        "seed_topic": seed_topic,
        "post_type": post_type,
        "target_length_tier": target_length_tier,
        "cluster_id": cluster_id,
        "topic": topic,
        "anchor_topic": anchor_topic,
        "normalized_payload": normalized_payload,
        "prompt_name": prompt_name,
        "prompt_version": prompt_version,
    }
    if raw_prompt is not None:
        payload["raw_prompt"] = raw_prompt
    if raw_response is not None:
        payload["raw_response"] = raw_response
    response = supabase.client.table("topic_research_dossiers").insert(payload).execute()
    if not response.data:
        raise RuntimeError("Failed to create topic research dossier")
    return _normalize_dossier_row(response.data[0])


def get_topic_scripts_for_registry(
    topic_registry_id: str,
    target_length_tier: Optional[int] = None,
) -> List[Dict[str, Any]]:
    rows = _fetch_topic_script_rows(
        topic_registry_id=topic_registry_id,
        target_length_tier=target_length_tier,
    )
    if rows:
        return rows
    return []


def get_topic_scripts_for_dossier(
    topic_research_dossier_id: str,
    target_length_tier: Optional[int] = None,
) -> List[Dict[str, Any]]:
    return _fetch_topic_script_rows(
        topic_research_dossier_id=topic_research_dossier_id,
        target_length_tier=target_length_tier,
    )


def list_topic_scripts_for_registry(topic_registry_id: str, target_length_tier: Optional[int] = None) -> List[Dict[str, Any]]:
    return get_topic_scripts_for_registry(topic_registry_id, target_length_tier=target_length_tier)


def list_topic_suggestions(
    target_length_tier: Optional[int] = None,
    limit: int = 50,
    post_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    registry_rows = get_all_topics_from_registry()
    registry_by_id = {str(row.get("id")): row for row in registry_rows}
    try:
        rows = _fetch_topic_script_rows(
            target_length_tier=target_length_tier,
            post_type=post_type,
        )
        if rows:
            rows.sort(
                key=lambda row: (
                    -int(row.get("use_count") or 0),
                    str(row.get("last_used_at") or ""),
                    str(row.get("created_at") or ""),
                ),
            )
            suggestions: List[Dict[str, Any]] = []
            seen_topic_ids = set()
            for row in rows:
                topic_registry_id = str(row.get("topic_registry_id") or "")
                if not topic_registry_id or topic_registry_id in seen_topic_ids:
                    continue
                seen_topic_ids.add(topic_registry_id)
                suggestions.append(_hydrate_script_suggestion(row, registry_by_id.get(topic_registry_id)))
                if len(suggestions) >= limit:
                    break
            if suggestions:
                return suggestions
    except Exception as exc:
        logger.warning("topic_scripts_query_failed", error=str(exc))

    suggestions = []
    for row in registry_rows:
        if post_type and row.get("post_type") and row.get("post_type") != post_type:
            continue
        suggestions.append(_registry_row_to_topic_suggestion(row))
    suggestions.sort(
        key=lambda row: (
            str(row.get("last_harvested_at") or row.get("created_at") or ""),
            str(row.get("title") or ""),
        ),
        reverse=True,
    )
    return suggestions[:limit]


def list_topic_research_runs(
    limit: int = 20,
    status: Optional[str] = None,
    topic_registry_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    supabase = get_supabase()
    query = supabase.client.table("topic_research_runs").select("*").order("created_at", desc=True).limit(limit)
    if status:
        query = query.eq("status", status)
    response = query.execute()
    rows = response.data or []
    if topic_registry_id:
        rows = [
            row
            for row in rows
            if str(row.get("topic_registry_id") or (row.get("result_summary") or {}).get("topic_registry_id") or "") == topic_registry_id
        ]
    return rows[:limit]


def create_topic_research_run(
    *,
    trigger_source: str,
    requested_counts: Dict[str, Any],
    target_length_tier: Optional[int],
    topic_registry_id: Optional[str],
    seed_topic: Optional[str] = None,
    post_type: Optional[str] = None,
    raw_prompt: Optional[str] = None,
    raw_response: Optional[str] = None,
    provider_interaction_id: Optional[str] = None,
    normalized_payload: Optional[Dict[str, Any]] = None,
    dossier_id: Optional[str] = None,
) -> Dict[str, Any]:
    supabase = get_supabase()
    payload = {
        "trigger_source": trigger_source,
        "status": "running",
        "requested_counts": requested_counts,
        "target_length_tier": target_length_tier,
        "topic_registry_id": topic_registry_id,
        "seed_topic": seed_topic,
        "post_type": post_type,
        "raw_prompt": raw_prompt or "",
        "raw_response": raw_response or "",
        "provider_interaction_id": provider_interaction_id,
        "normalized_payload": normalized_payload or {},
        "dossier_id": dossier_id,
        "result_summary": {"topic_registry_id": topic_registry_id} if topic_registry_id else {},
        "error_message": "",
    }
    response = supabase.client.table("topic_research_runs").insert(payload).execute()
    if not response.data:
        raise RuntimeError("Failed to create topic research run")
    return response.data[0]


def update_topic_research_run(
    run_id: str,
    *,
    status: Optional[str] = None,
    result_summary: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    dossier_id: Optional[str] = None,
) -> Dict[str, Any]:
    supabase = get_supabase()
    update_payload: Dict[str, Any] = {}
    if status is not None:
        update_payload["status"] = status
    if result_summary is not None:
        update_payload["result_summary"] = result_summary
    if error_message is not None:
        update_payload["error_message"] = error_message
    if dossier_id is not None:
        update_payload["dossier_id"] = dossier_id
    if not update_payload:
        return get_topic_research_run(run_id)
    response = supabase.client.table("topic_research_runs").update(update_payload).eq("id", run_id).execute()
    if not response.data:
        raise NotFoundError(message="Research run not found", details={"run_id": run_id})
    return response.data[0]


def get_topic_research_run(run_id: str) -> Dict[str, Any]:
    supabase = get_supabase()
    response = supabase.client.table("topic_research_runs").select("*").eq("id", run_id).limit(1).execute()
    if not response.data:
        raise NotFoundError(message="Research run not found", details={"run_id": run_id})
    return response.data[0]


def get_topic_research_dossiers(
    *,
    topic_registry_id: Optional[str] = None,
    topic_research_run_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    supabase = get_supabase()
    query = supabase.client.table("topic_research_dossiers").select("*").order("created_at", desc=True).limit(limit)
    if topic_registry_id:
        query = query.eq("topic_registry_id", topic_registry_id)
    if topic_research_run_id:
        query = query.eq("topic_research_run_id", topic_research_run_id)
    response = query.execute()
    return [_normalize_dossier_row(row) for row in (response.data or [])]


def get_researched_topic_texts(*, limit: int = 500) -> List[str]:
    """Return unique historical research seed/topic texts for future dedupe."""
    supabase = get_supabase()
    try:
        response = (
            supabase.client.table("topic_research_dossiers")
            .select("seed_topic, topic, anchor_topic")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        logger.warning("topic_research_texts_fetch_failed", error=str(exc))
        return []

    values: List[str] = []
    seen: set[str] = set()
    for row in response.data or []:
        for field in ("seed_topic", "topic", "anchor_topic"):
            text = str((row or {}).get(field) or "").strip()
            if not text:
                continue
            signature = _normalize_script_text(text)
            if not signature or signature in seen:
                continue
            seen.add(signature)
            values.append(text)
    return values


def store_topic_bank_entry(
    *,
    title: str,
    topic_script: str,
    post_type: str,
    target_length_tier: int,
    research_payload: Dict[str, Any],
    language: str = "de",
    topic_research_run_id: Optional[str] = None,
    topic_research_dossier_id: Optional[str] = None,
    raw_prompt: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> Dict[str, Any]:
    row = add_topic_to_registry(
        title=title,
        script=topic_script,
        post_type=post_type,
    )
    dossier = create_topic_research_dossier(
        topic_research_run_id=topic_research_run_id,
        topic_registry_id=row["id"],
        seed_topic=str(research_payload.get("seed_topic") or title),
        post_type=post_type,
        target_length_tier=target_length_tier,
        cluster_id=str(research_payload.get("cluster_id") or ""),
        topic=str(research_payload.get("topic") or title),
        anchor_topic=str(research_payload.get("anchor_topic") or title),
        normalized_payload=research_payload or {},
        raw_prompt=raw_prompt,
        raw_response=raw_response,
    )
    merged_row = dict(row)
    merged_row["research_dossier_id"] = dossier["id"]
    merged_row["topic_research_dossier_id"] = topic_research_dossier_id or dossier["id"]
    return merged_row


def upsert_topic_script_variants(
    *,
    topic_registry_id: str,
    title: str,
    post_type: str,
    target_length_tier: int,
    topic_research_dossier_id: Optional[str] = None,
    variants: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    supabase = get_supabase()
    existing_rows = (
        supabase.client.table("topic_scripts")
        .select("*")
        .eq("topic_registry_id", topic_registry_id)
        .execute()
        .data
        or []
    )
    dossier_rows: List[Dict[str, Any]] = []
    if topic_research_dossier_id:
        dossier_rows = (
            supabase.client.table("topic_scripts")
            .select("*")
            .eq("topic_research_dossier_id", topic_research_dossier_id)
            .execute()
            .data
            or []
        )
    global_rows = (
        supabase.client.table("topic_scripts")
        .select("id, topic_registry_id, target_length_tier, script")
        .execute()
        .data
        or []
    )

    combined_rows = []
    seen_ids = set()
    for row in existing_rows + dossier_rows:
        row_id = str(row.get("id") or "")
        if row_id and row_id in seen_ids:
            continue
        if row_id:
            seen_ids.add(row_id)
        combined_rows.append(row)

    existing_signatures_by_tier: Dict[int, set[str]] = {}
    existing_lane_signatures_by_tier: Dict[tuple[int, str], set[str]] = {}
    existing_exact_rows: Dict[tuple[int, str, str], Dict[str, Any]] = {}
    for row in combined_rows:
        script = str(row.get("script") or "").strip()
        if not script:
            continue
        tier = int(row.get("target_length_tier") or target_length_tier or 0)
        signature = _normalize_script_text(script)
        bucket = str(row.get("bucket") or "").strip()
        lane_key = str(row.get("lane_key") or "").strip()
        if signature:
            existing_signatures_by_tier.setdefault(tier, set()).add(signature)
            if topic_research_dossier_id and lane_key and str(row.get("topic_research_dossier_id") or "") == str(topic_research_dossier_id):
                existing_lane_signatures_by_tier.setdefault((tier, lane_key), set()).add(signature)
        if bucket and lane_key:
            existing_exact_rows[(tier, bucket, lane_key)] = row
    global_signatures_by_tier: Dict[int, Dict[str, str]] = {}
    for row in global_rows:
        script = str(row.get("script") or "").strip()
        if not script:
            continue
        signature = _normalize_script_text(script)
        tier = int(row.get("target_length_tier") or 0)
        topic_id = str(row.get("topic_registry_id") or "").strip()
        if signature and tier:
            global_signatures_by_tier.setdefault(tier, {}).setdefault(signature, topic_id)

    stored_variants: List[Dict[str, Any]] = []
    duplicate_scripts_skipped = 0
    for variant in variants:
        raw_script = str(variant.get("script") or "").strip()
        script = sanitize_spoken_fragment(raw_script, ensure_terminal=True)
        if not script:
            logger.warning(
                "topic_script_integrity_rejected",
                topic_registry_id=topic_registry_id,
                topic_research_dossier_id=topic_research_dossier_id,
                target_length_tier=int(variant.get("target_length_tier") or target_length_tier or 0),
                bucket=str(variant.get("bucket") or "").strip(),
                lane_key=str(variant.get("lane_key") or "").strip(),
                reason="empty_after_sanitization",
            )
            continue
        script_signature = _normalize_script_text(script)
        lane_key = str(variant.get("lane_key") or "").strip()
        tier = int(variant.get("target_length_tier") or target_length_tier or 0)
        bucket = str(variant.get("bucket") or "").strip()
        post_type_value = str(variant.get("post_type") or post_type or "").strip()
        script_issues = detect_spoken_copy_issues(script)
        if script_issues:
            logger.warning(
                "topic_script_integrity_rejected",
                topic_registry_id=topic_registry_id,
                topic_research_dossier_id=topic_research_dossier_id,
                target_length_tier=tier,
                bucket=bucket,
                lane_key=lane_key,
                reason="spoken_copy_issues",
                issues=script_issues,
                script_preview=script[:240],
            )
            continue
        bleed_issue = detect_metadata_bleed(
            script,
            source_summary=str(variant.get("source_summary") or ""),
            cluster_summary=str(variant.get("cluster_summary") or ""),
        )
        if bleed_issue:
            logger.warning(
                "topic_script_integrity_rejected",
                topic_registry_id=topic_registry_id,
                topic_research_dossier_id=topic_research_dossier_id,
                target_length_tier=tier,
                bucket=bucket,
                lane_key=lane_key,
                reason="metadata_bleed",
                bleed_field=bleed_issue.get("field"),
                bleed_window=bleed_issue.get("window"),
                script_preview=script[:240],
            )
            continue
        if bucket == "canonical" and post_type_value in {"value", "product"}:
            min_words, max_words = get_prompt1_word_bounds(tier)
            min_sentences, max_sentences = get_prompt1_sentence_bounds(tier)
            word_count = _count_script_words(script)
            sentence_count = _count_script_sentences(script)
            if (
                word_count < min_words
                or word_count > max_words
                or sentence_count < min_sentences
                or sentence_count > max_sentences
            ):
                logger.warning(
                    "topic_script_integrity_rejected",
                    topic_registry_id=topic_registry_id,
                    topic_research_dossier_id=topic_research_dossier_id,
                    target_length_tier=tier,
                    bucket=bucket,
                    lane_key=lane_key,
                    reason="canonical_envelope_mismatch",
                    word_count=word_count,
                    sentence_count=sentence_count,
                    expected_words=[min_words, max_words],
                    expected_sentences=[min_sentences, max_sentences],
                    script_preview=script[:240],
                )
                continue
        if script_signature and script_signature in existing_signatures_by_tier.get(tier, set()):
            logger.info(
                "topic_script_duplicate_skipped",
                topic_registry_id=topic_registry_id,
                target_length_tier=tier,
                bucket=bucket,
                lane_key=lane_key,
            )
            duplicate_scripts_skipped += 1
            continue
        global_existing_topic_id = global_signatures_by_tier.get(tier, {}).get(script_signature)
        if script_signature and global_existing_topic_id and global_existing_topic_id != topic_registry_id:
            logger.info(
                "topic_script_global_duplicate_skipped",
                topic_registry_id=topic_registry_id,
                existing_topic_registry_id=global_existing_topic_id,
                target_length_tier=tier,
                bucket=bucket,
                lane_key=lane_key,
            )
            duplicate_scripts_skipped += 1
            continue
        if topic_research_dossier_id and lane_key:
            same_lane_duplicate = script_signature in existing_lane_signatures_by_tier.get((tier, lane_key), set())
            if same_lane_duplicate:
                logger.info(
                    "topic_script_lane_duplicate_skipped",
                    topic_research_dossier_id=topic_research_dossier_id,
                    target_length_tier=tier,
                    bucket=bucket,
                    lane_key=lane_key,
                )
                duplicate_scripts_skipped += 1
                continue
        exact_row = existing_exact_rows.get((tier, bucket, lane_key))
        if exact_row is not None:
            existing_signature = _normalize_script_text(exact_row.get("script"))
            if existing_signature and existing_signature == script_signature:
                logger.info(
                    "topic_script_existing_row_skipped",
                    topic_registry_id=topic_registry_id,
                    topic_research_dossier_id=topic_research_dossier_id,
                    target_length_tier=tier,
                    bucket=bucket,
                    lane_key=lane_key,
                )
                duplicate_scripts_skipped += 1
                continue
            logger.info(
                "topic_script_conflict_skipped",
                topic_registry_id=topic_registry_id,
                topic_research_dossier_id=topic_research_dossier_id,
                target_length_tier=tier,
                bucket=bucket,
                lane_key=lane_key,
            )
            duplicate_scripts_skipped += 1
            continue
        payload = {
            "topic_registry_id": topic_registry_id,
            "topic_research_dossier_id": topic_research_dossier_id,
            "post_type": post_type_value or post_type,
            "title": title,
            "script": script,
            "target_length_tier": tier,
            "bucket": bucket,
            "hook_style": variant.get("hook_style") or "default",
            "framework": variant.get("framework") or "PAL",
            "tone": sanitize_metadata_text(variant.get("tone"), max_sentences=1),
            "estimated_duration_s": variant.get("estimated_duration_s"),
            "lane_key": variant.get("lane_key"),
            "lane_family": variant.get("lane_family"),
            "cluster_id": variant.get("cluster_id"),
            "anchor_topic": variant.get("anchor_topic"),
            "disclaimer": sanitize_metadata_text(variant.get("disclaimer"), max_sentences=1),
            "source_summary": sanitize_metadata_text(variant.get("source_summary")),
            "primary_source_url": variant.get("primary_source_url"),
            "primary_source_title": variant.get("primary_source_title"),
            "source_urls": variant.get("source_urls") or [],
            "seed_payload": variant.get("seed_payload") or {},
            "quality_score": variant.get("quality_score"),
            "quality_notes": variant.get("quality_notes") or sanitize_metadata_text(variant.get("quality_notes"), max_sentences=2),
            "use_count": int(variant.get("use_count") or 0),
            "last_used_at": variant.get("last_used_at"),
        }
        response = supabase.client.table("topic_scripts").insert(payload).execute()
        if response.data:
            normalized = _normalize_script_row(response.data[0])
            stored_variants.append(normalized)
            existing_exact_rows[(tier, bucket, lane_key)] = normalized
            normalized_signature = _normalize_script_text(script)
            if normalized_signature:
                existing_signatures_by_tier.setdefault(tier, set()).add(normalized_signature)
                if topic_research_dossier_id and lane_key:
                    existing_lane_signatures_by_tier.setdefault((tier, lane_key), set()).add(normalized_signature)
    return stored_variants


def get_existing_variant_pairs(
    *,
    topic_registry_id: str,
    target_length_tier: int,
    post_type: str,
) -> List[Dict[str, Any]]:
    """Return existing (framework, hook_style) pairs for a topic/tier/post_type."""
    supabase = get_supabase()
    response = (
        supabase.client.table("topic_scripts")
        .select("framework, hook_style")
        .eq("topic_registry_id", topic_registry_id)
        .eq("target_length_tier", target_length_tier)
        .eq("post_type", post_type)
        .execute()
    )
    return response.data or []


def get_posts_by_batch(batch_id: str) -> List[Dict[str, Any]]:
    """Get all posts for a batch."""
    supabase = get_supabase()
    
    response = supabase.client.table("posts").select("*").eq("batch_id", batch_id).execute()
    
    return response.data


def count_posts_by_batch_and_type(batch_id: str, post_type: str) -> int:
    """Count posts for a batch by type."""
    supabase = get_supabase()
    
    response = supabase.client.table("posts").select("id", count="exact").eq("batch_id", batch_id).eq("post_type", post_type).execute()
    
    return response.count or 0


# ── Cron Run Tracking ────────────────────────────────────────────

def create_cron_run(
    *,
    topics_requested: int,
    seed_source: str,
) -> Dict[str, Any]:
    """Create a new cron run record with status='running'."""
    sb = get_supabase()
    result = sb.client.table("topic_research_cron_runs").insert({
        "topics_requested": topics_requested,
        "seed_source": seed_source,
        "status": "running",
    }).execute()
    return result.data[0]


def update_cron_run(
    run_id: str,
    *,
    status: str,
    topics_completed: int = 0,
    topics_failed: int = 0,
    topic_ids: Optional[List[str]] = None,
    error_message: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update a cron run record on completion or failure."""
    payload: Dict[str, Any] = {"status": status}
    if status in ("completed", "failed"):
        payload["completed_at"] = datetime.now(timezone.utc).isoformat()
    payload["topics_completed"] = topics_completed
    payload["topics_failed"] = topics_failed
    if topic_ids is not None:
        payload["topic_ids"] = topic_ids
    if error_message is not None:
        payload["error_message"] = error_message
    if details is not None:
        payload["details"] = details
    sb = get_supabase()
    result = sb.client.table("topic_research_cron_runs").update(
        payload
    ).eq("id", run_id).execute()
    return result.data[0]


def get_latest_cron_run(*, status: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get the most recent cron run, optionally filtered by status."""
    sb = get_supabase()
    query = sb.client.table("topic_research_cron_runs").select("*").order(
        "started_at", desc=True
    ).limit(1)
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return result.data[0] if result.data else None


def get_cron_run_stats() -> Dict[str, Any]:
    """Get aggregate stats across all cron runs."""
    sb = get_supabase()
    result = sb.client.table("topic_research_cron_runs").select(
        "status,topics_completed"
    ).execute()
    rows = result.data or []
    total_runs = len(rows)
    total_topics = sum(r.get("topics_completed", 0) for r in rows)
    return {"total_runs": total_runs, "total_topics_researched": total_topics}


def get_unaudited_scripts(*, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch topic_scripts rows where quality_score is NULL (unaudited)."""
    response = (
        supabase.client.table("topic_scripts")
        .select("id, title, script, target_length_tier, post_type, bucket, lane_key, source_summary, cluster_id")
        .is_("quality_score", "null")
        .limit(limit)
        .execute()
    )
    return list(response.data or [])


def update_script_quality(*, script_id: str, quality_score: int, quality_notes: str) -> None:
    """Write audit results to a topic_scripts row."""
    supabase.client.table("topic_scripts").update(
        {"quality_score": quality_score, "quality_notes": quality_notes}
    ).eq("id", script_id).execute()
