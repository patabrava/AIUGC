"""FLOW-FORGE Topics Database Queries."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.adapters.supabase_client import get_supabase
from app.core.errors import NotFoundError
from app.core.logging import get_logger

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
    if not script:
        script_bank = normalized.get("script_bank") or {}
        if isinstance(script_bank, dict):
            for tier_key in ("32", "16", "8"):
                variants = script_bank.get(tier_key) or []
                if variants:
                    candidate = variants[0]
                    if isinstance(candidate, dict):
                        script = str(candidate.get("script") or "").strip()
                        if script:
                            break
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
    normalized["target_length_tiers"] = list(normalized.get("target_length_tiers") or [])
    normalized["script_bank"] = normalized.get("script_bank") or {}
    normalized["seed_payloads"] = normalized.get("seed_payloads") or {}
    normalized["source_bank"] = normalized.get("source_bank") or []
    normalized["research_payload"] = normalized.get("research_payload") or {}
    normalized["language"] = normalized.get("language") or "de"
    normalized["first_seen_at"] = normalized.get("first_seen_at") or normalized.get("created_at") or normalized.get("last_harvested_at")
    normalized["last_used_at"] = normalized.get("last_used_at") or normalized.get("updated_at") or normalized.get("last_harvested_at")
    return normalized


def _normalize_script_row(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(row or {})
    normalized["source_urls"] = list(normalized.get("source_urls") or [])
    normalized["use_count"] = int(normalized.get("use_count") or 0)
    return normalized


def _normalize_dossier_row(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(row or {})
    normalized["normalized_payload"] = normalized.get("normalized_payload") or {}
    normalized["created_at"] = normalized.get("created_at")
    normalized["updated_at"] = normalized.get("updated_at")
    return normalized


def _merge_unique_source_bank(*banks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen = set()
    for bank in banks:
        for item in list(bank or []):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            if not url:
                continue
            key = (title, url)
            if key in seen:
                continue
            seen.add(key)
            merged.append({"title": title, "url": url})
    return merged


def _merge_script_bank(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    tier_keys = set((existing or {}).keys()) | set((incoming or {}).keys())
    for tier_key in tier_keys:
        variants: List[Dict[str, Any]] = []
        seen_scripts = set()
        for bank in (existing or {}, incoming or {}):
            for variant in list(bank.get(tier_key) or []):
                if not isinstance(variant, dict):
                    continue
                script = str(variant.get("script") or "").strip()
                if not script or script in seen_scripts:
                    continue
                seen_scripts.add(script)
                variants.append(variant)
        if variants:
            merged[str(tier_key)] = variants
    return merged


def _merge_seed_payloads(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing or {})
    for tier_key, payload in (incoming or {}).items():
        merged[str(tier_key)] = payload
    return merged


def get_all_topics_from_registry() -> List[Dict[str, Any]]:
    """Get all topics from the registry for deduplication and hub browsing."""
    supabase = get_supabase()
    response = supabase.client.table("topic_registry").select("*").execute()
    return [_normalize_registry_row(row) for row in (response.data or [])]


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
    research_payload: Optional[Dict[str, Any]] = None,
    source_bank: Optional[List[Dict[str, Any]]] = None,
    script_bank: Optional[Dict[str, Any]] = None,
    seed_payloads: Optional[Dict[str, Any]] = None,
    target_length_tiers: Optional[List[int]] = None,
    language: str = "de",
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
        "target_length_tiers": normalized.get("target_length_tiers") or [],
        "script_bank": normalized.get("script_bank") or {},
        "source_bank": normalized.get("source_bank") or [],
        "seed_payloads": normalized.get("seed_payloads") or {},
        "research_payload": normalized.get("research_payload") or {},
        "source_urls": [
            {
                "title": source.get("title"),
                "url": source.get("url"),
            }
            for source in list(normalized.get("source_bank") or [])
            if isinstance(source, dict) and source.get("url")
        ],
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
    hydrated["target_length_tiers"] = registry.get("target_length_tiers") or [hydrated.get("target_length_tier")]
    hydrated["script_bank"] = registry.get("script_bank") or {}
    hydrated["source_bank"] = registry.get("source_bank") or []
    hydrated["seed_payloads"] = registry.get("seed_payloads") or {}
    hydrated["research_payload"] = registry.get("research_payload") or {}
    hydrated["source_urls"] = hydrated.get("source_urls") or [
        {"title": item.get("title"), "url": item.get("url")}
        for item in list(hydrated.get("source_bank") or [])
        if isinstance(item, dict) and item.get("url")
    ]
    tier_key = str(hydrated.get("target_length_tier") or "")
    hydrated["seed_payload"] = hydrated.get("seed_payload") or (hydrated["seed_payloads"].get(tier_key) if tier_key else None)
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
    seed_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Create a post record for a batch with topic and seed data."""
    supabase = get_supabase()
    
    post_data = {
        "batch_id": batch_id,
        "post_type": post_type,
        "topic_title": topic_title,
        "topic_rotation": topic_rotation,
        "topic_cta": topic_cta,
        "spoken_duration": spoken_duration,
        "seed_data": seed_data
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
        "raw_prompt": raw_prompt or "",
        "raw_response": raw_response or "",
        "prompt_name": prompt_name,
        "prompt_version": prompt_version,
    }
    response = supabase.client.table("topic_research_dossiers").insert(payload).execute()
    if not response.data:
        raise RuntimeError("Failed to create topic research dossier")
    return _normalize_dossier_row(response.data[0])


def get_topic_scripts_for_registry(
    topic_registry_id: str,
    target_length_tier: Optional[int] = None,
) -> List[Dict[str, Any]]:
    supabase = get_supabase()
    query = supabase.client.table("topic_scripts").select("*").eq("topic_registry_id", topic_registry_id)
    if target_length_tier is not None:
        query = query.eq("target_length_tier", target_length_tier)
    response = query.execute()
    return [_normalize_script_row(row) for row in (response.data or [])]


def list_topic_scripts_for_registry(topic_registry_id: str, target_length_tier: Optional[int] = None) -> List[Dict[str, Any]]:
    return get_topic_scripts_for_registry(topic_registry_id, target_length_tier=target_length_tier)


def list_topic_suggestions(
    target_length_tier: Optional[int] = None,
    limit: int = 50,
    post_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    registry_rows = get_all_topics_from_registry()
    registry_by_id = {str(row.get("id")): row for row in registry_rows}
    supabase = get_supabase()
    try:
        query = supabase.client.table("topic_scripts").select("*")
        if target_length_tier is not None:
            query = query.eq("target_length_tier", target_length_tier)
        if post_type:
            query = query.eq("post_type", post_type)
        response = query.execute()
        rows = [_normalize_script_row(row) for row in (response.data or [])]
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
        if target_length_tier is not None:
            tiers = set(int(tier) for tier in row.get("target_length_tiers") or [])
            if tiers and target_length_tier not in tiers:
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
        rows = [row for row in rows if str((row.get("result_summary") or {}).get("topic_registry_id") or "") == topic_registry_id]
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
) -> Dict[str, Any]:
    supabase = get_supabase()
    update_payload: Dict[str, Any] = {}
    if status is not None:
        update_payload["status"] = status
    if result_summary is not None:
        update_payload["result_summary"] = result_summary
    if error_message is not None:
        update_payload["error_message"] = error_message
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


def store_topic_bank_entry(
    *,
    title: str,
    topic_script: str,
    post_type: str,
    target_length_tier: int,
    research_payload: Dict[str, Any],
    source_bank: List[Dict[str, Any]],
    script_bank: Dict[str, Any],
    seed_payloads: Dict[str, Any],
    language: str = "de",
    topic_research_run_id: Optional[str] = None,
    raw_prompt: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> Dict[str, Any]:
    row = add_topic_to_registry(
        title=title,
        script=topic_script,
        post_type=post_type,
        language=language,
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
    return merged_row


def upsert_topic_script_variants(
    *,
    topic_registry_id: str,
    title: str,
    post_type: str,
    target_length_tier: int,
    variants: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    supabase = get_supabase()
    existing_rows = (
        supabase.client.table("topic_scripts")
        .select("*")
        .eq("topic_registry_id", topic_registry_id)
        .eq("target_length_tier", target_length_tier)
        .execute()
        .data
        or []
    )
    existing_by_script = {
        str(row.get("script") or "").strip(): row
        for row in existing_rows
        if str(row.get("script") or "").strip()
    }
    stored_variants: List[Dict[str, Any]] = []
    for variant in variants:
        script = str(variant.get("script") or "").strip()
        if not script:
            continue
        payload = {
            "topic_registry_id": topic_registry_id,
            "post_type": post_type,
            "title": title,
            "script": script,
            "target_length_tier": target_length_tier,
            "bucket": variant.get("bucket"),
            "hook_style": variant.get("hook_style"),
            "framework": variant.get("framework"),
            "tone": variant.get("tone"),
            "estimated_duration_s": variant.get("estimated_duration_s"),
            "lane_key": variant.get("lane_key"),
            "lane_family": variant.get("lane_family"),
            "cluster_id": variant.get("cluster_id"),
            "anchor_topic": variant.get("anchor_topic"),
            "disclaimer": variant.get("disclaimer"),
            "source_summary": variant.get("source_summary"),
            "primary_source_url": variant.get("primary_source_url"),
            "primary_source_title": variant.get("primary_source_title"),
            "source_urls": variant.get("source_urls") or [],
            "seed_payload": variant.get("seed_payload") or {},
            "quality_notes": variant.get("quality_notes") or "",
            "use_count": int(variant.get("use_count") or 0),
            "last_used_at": variant.get("last_used_at"),
        }
        existing = existing_by_script.get(script)
        if existing:
            response = (
                supabase.client.table("topic_scripts")
                .update(payload)
                .eq("id", existing["id"])
                .execute()
            )
        else:
            response = supabase.client.table("topic_scripts").insert(payload).execute()
        if response.data:
            normalized = _normalize_script_row(response.data[0])
            stored_variants.append(normalized)
            existing_by_script[script] = normalized
    return stored_variants


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
