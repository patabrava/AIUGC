"""
Lippe Lift Studio Batches Database Queries
Database operations for batches.
Per Constitution § V: Locality & Vertical Slices
"""

import time
from typing import List, Optional, Dict, Any
from datetime import datetime
import httpx
from postgrest.exceptions import APIError
from app.adapters.supabase_client import get_supabase
from app.core.states import BatchState, validate_state_transition
from app.core.errors import NotFoundError, StateTransitionError, ThirdPartyError, ValidationError
from app.core.logging import get_logger
from app.features.characters.actor_identity import actor_identity_is_ready, is_character_consistency_mode
from app.features.characters.queries import get_active_actor_identity
from app.features.topics.queries import create_post_for_batch
from app.features.shot_production.duration import build_semantic_duration_contract

logger = get_logger(__name__)

_QUERY_RETRY_DELAYS = (0.15, 0.35, 0.75)
BATCH_LIST_FIELDS = (
    "id,brand,state,creation_mode,post_type_counts,manual_post_count,"
    "target_length_tier,target_duration_seconds,video_pipeline_route,"
    "created_at,updated_at,archived"
)
POSTS_SUMMARY_FIELDS = "id,post_type"
VIDEO_SUBMISSION_STARTED_STATUSES = {
    "submitted",
    "processing",
    "completed",
    "extended_submitted",
    "extended_processing",
}


def _actor_identity_snapshot_payload(actor_identity) -> Dict[str, Any]:
    return {
        "actor_identity_id": actor_identity.id,
        "name": actor_identity.name,
        "provider": actor_identity.provider,
        "provider_lora_id": actor_identity.provider_lora_id,
        "provider_lora_name": actor_identity.provider_lora_name,
        "training_completed_at": (
            actor_identity.training_completed_at.isoformat()
            if actor_identity.training_completed_at
            else None
        ),
    }


def _batch_has_started_video_submission(batch_id: str) -> bool:
    supabase = get_supabase()
    response = (
        supabase.client.table("posts")
        .select("id,video_status")
        .eq("batch_id", batch_id)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return any(str(row.get("video_status") or "").strip() in VIDEO_SUBMISSION_STARTED_STATUSES for row in rows)


def sync_character_consistency_batch_actor(
    batch: Dict[str, Any],
    *,
    correlation_id: str,
    active_actor=None,
) -> Dict[str, Any]:
    if not is_character_consistency_mode(batch.get("creation_mode")):
        return batch

    resolved_actor = active_actor or get_active_actor_identity()
    if not actor_identity_is_ready(resolved_actor):
        raise ValidationError(
            "Character Consistency batch requires a ready active ActorIdentity before video generation.",
            {"batch_id": batch.get("id"), "settings_url": "/settings/actor"},
        )

    current_actor_identity_id = str(batch.get("actor_identity_id") or "").strip()
    snapshot = batch.get("actor_identity_snapshot") if isinstance(batch.get("actor_identity_snapshot"), dict) else {}
    snapshot_matches = (
        str(snapshot.get("actor_identity_id") or "").strip() == resolved_actor.id
        and str(snapshot.get("provider_lora_id") or "").strip() == str(resolved_actor.provider_lora_id or "").strip()
        and str(snapshot.get("provider_lora_name") or "").strip() == str(resolved_actor.provider_lora_name or "").strip()
    )
    if current_actor_identity_id == resolved_actor.id and snapshot_matches:
        return batch

    batch_id = str(batch.get("id") or "").strip()
    if not batch_id:
        raise ValidationError("Character Consistency batch is missing an id.", {"batch": batch})

    if current_actor_identity_id and current_actor_identity_id != resolved_actor.id and _batch_has_started_video_submission(batch_id):
        raise ValidationError(
            "This Character Consistency batch already started video generation with a different actor. "
            "Create or duplicate a new batch before submitting more videos with the newly selected actor.",
            {
                "batch_id": batch_id,
                "batch_actor_identity_id": current_actor_identity_id,
                "active_actor_identity_id": resolved_actor.id,
                "settings_url": "/settings/actor",
            },
        )

    now = datetime.now().isoformat()
    payload = {
        "actor_identity_id": resolved_actor.id,
        "actor_identity_snapshot": _actor_identity_snapshot_payload(resolved_actor),
        "updated_at": now,
    }
    supabase = get_supabase().client
    response = supabase.table("batches").update(payload).eq("id", batch_id).execute()
    supabase.table("posts").update(
        {
            "scene_reference_image_id": None,
            "identity_gate_result": None,
        }
    ).eq("batch_id", batch_id).execute()
    logger.info(
        "character_consistency_batch_actor_synced",
        correlation_id=correlation_id,
        batch_id=batch_id,
        previous_actor_identity_id=current_actor_identity_id or None,
        actor_identity_id=resolved_actor.id,
    )
    updated_row = (getattr(response, "data", None) or [None])[0] or {}
    return {
        **batch,
        **updated_row,
        "actor_identity_id": resolved_actor.id,
        "actor_identity_snapshot": payload["actor_identity_snapshot"],
    }


def sync_pending_character_consistency_batches_to_actor(*, active_actor, correlation_id: str) -> int:
    if not actor_identity_is_ready(active_actor):
        return 0

    response = (
        get_supabase()
        .client.table("batches")
        .select("*")
        .eq("archived", False)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    synced = 0
    for row in rows:
        if not is_character_consistency_mode(row.get("creation_mode")):
            continue
        try:
            updated = sync_character_consistency_batch_actor(
                row,
                correlation_id=correlation_id,
                active_actor=active_actor,
            )
        except ValidationError as exc:
            logger.info(
                "character_consistency_batch_actor_sync_skipped",
                correlation_id=correlation_id,
                batch_id=row.get("id"),
                actor_identity_id=row.get("actor_identity_id"),
                active_actor_identity_id=active_actor.id,
                reason=exc.message,
            )
            continue
        if str(updated.get("actor_identity_id") or "").strip() == active_actor.id:
            synced += 1
    return synced


def _execute_with_retry(operation_name: str, callback):
    last_error: Optional[Exception] = None
    for attempt, delay in enumerate((0.0, *_QUERY_RETRY_DELAYS), start=1):
        if delay:
            time.sleep(delay)
        try:
            return callback()
        except httpx.TimeoutException as exc:
            logger.warning(
                "batch_query_timeout",
                operation=operation_name,
                attempt=attempt,
                error=str(exc),
            )
            raise ThirdPartyError(
                message=f"Database unavailable while loading batch data for {operation_name}",
                details={"operation": operation_name, "error": str(exc)},
            ) from exc
        except httpx.RequestError as exc:
            last_error = exc
            logger.warning(
                "batch_query_retryable_request_error",
                operation=operation_name,
                attempt=attempt,
                error=str(exc),
            )
    raise ThirdPartyError(
        message=f"Database unavailable while loading batch data for {operation_name}",
        details={"operation": operation_name, "error": str(last_error) if last_error else "unknown"},
    )


def _insert_batch_row(payload: Dict[str, Any], legacy_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    supabase = get_supabase()
    try:
        response = supabase.client.table("batches").insert(payload).execute()
    except APIError as exc:
        error_text = str(exc)
        if exc.code == "PGRST204" and legacy_payload is not None:
            logger.warning(
                "batch_insert_schema_missing_fallback",
                error=error_text,
                omitted_fields=sorted(set(payload) - set(legacy_payload)),
            )
            response = supabase.client.table("batches").insert(legacy_payload).execute()
        else:
            raise

    if not response.data:
        raise Exception("Failed to create batch")
    return response.data[0]


def create_batch(
    brand: str,
    post_type_counts: Optional[Dict[str, int]],
    target_length_tier: Optional[int] = 8,
    *,
    target_duration_seconds: Optional[int] = None,
    creation_mode: str = "automated",
    manual_post_count: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create a new batch in S1_SETUP state.
    Per Canon § 3.2: S1_SETUP is initial state.
    """
    is_semantic_ugc = creation_mode == "semantic_ugc"
    if is_semantic_ugc:
        if target_duration_seconds is None:
            raise ValidationError(
                "Semantic UGC batches require target_duration_seconds.",
                {"creation_mode": creation_mode},
            )
        build_semantic_duration_contract(target_duration_seconds)
        target_length_tier = None
        video_pipeline_route = "semantic_ugc"
    else:
        if target_duration_seconds is not None:
            raise ValidationError(
                "target_duration_seconds is only valid for Semantic UGC batches.",
                {"creation_mode": creation_mode},
            )
        if target_length_tier is None:
            raise ValidationError(
                "Legacy batch modes require target_length_tier.",
                {"creation_mode": creation_mode},
            )
        video_pipeline_route = None

    batch_data = {
        "brand": brand,
        "state": BatchState.S1_SETUP.value,
        "creation_mode": creation_mode,
        "post_type_counts": post_type_counts or {},
        "manual_post_count": manual_post_count,
        "target_length_tier": target_length_tier,
        "target_duration_seconds": target_duration_seconds,
        "video_pipeline_route": video_pipeline_route,
        "archived": False
    }

    if is_character_consistency_mode(creation_mode):
        actor_identity = get_active_actor_identity()
        if not actor_identity_is_ready(actor_identity):
            raise ValidationError(
                "Cannot create a Character Consistency batch: no ready active ActorIdentity is selected. "
                "Open /settings/actor, select a ready actor, then create the batch again.",
                {"creation_mode": creation_mode, "settings_url": "/settings/actor"},
            )
        batch_data["actor_identity_id"] = actor_identity.id
        batch_data["actor_identity_snapshot"] = _actor_identity_snapshot_payload(actor_identity)
        batch_data["character_snapshot"] = None
        batch_data["scene_plan"] = None

    legacy_batch_data = {
        "brand": brand,
        "state": BatchState.S1_SETUP.value,
        "post_type_counts": post_type_counts or {},
        "target_length_tier": target_length_tier,
        "archived": False,
    }

    batch = _insert_batch_row(
        batch_data,
        None if is_semantic_ugc else legacy_batch_data,
    )
    if creation_mode:
        batch = {
            **batch,
            "creation_mode": creation_mode,
            "manual_post_count": manual_post_count,
            "target_length_tier": target_length_tier,
            "target_duration_seconds": target_duration_seconds,
            "video_pipeline_route": video_pipeline_route,
        }
        for key in ("actor_identity_id", "actor_identity_snapshot", "character_snapshot", "scene_plan"):
            if key in batch_data:
                batch[key] = batch_data[key]
    
    logger.info(
        "batch_created",
        batch_id=batch["id"],
        brand=brand,
        state=batch["state"],
        creation_mode=creation_mode,
    )
    
    return batch


def update_batch_scene_plan(*, batch_id: str, scene_plan: Dict[str, str]) -> None:
    supabase = get_supabase()
    supabase.client.table("batches").update({"scene_plan": scene_plan}).eq("id", batch_id).execute()


def create_manual_draft_posts(
    batch_id: str,
    manual_post_count: int,
    target_length_tier: int,
) -> List[Dict[str, Any]]:
    """Create blank draft posts for a manual batch."""
    created: List[Dict[str, Any]] = []
    for index in range(manual_post_count):
        created.append(
            create_post_for_batch(
                batch_id=batch_id,
                # Keep the legacy DB check satisfied; the freeform manual type lives in seed_data.
                post_type="value",
                topic_title=f"Manual Draft {index + 1}",
                topic_rotation="",
                topic_cta="",
                spoken_duration=0,
                seed_data={
                    "script": "",
                    "script_review_status": "pending",
                    "manual_draft": True,
                    "manual_post_type": "",
                },
                target_length_tier=target_length_tier,
            )
        )
    return created


def get_batch_by_id(batch_id: str) -> Dict[str, Any]:
    """Get batch by ID."""
    supabase = get_supabase()

    response = _execute_with_retry(
        "get_batch_by_id",
        lambda: supabase.client.table("batches").select("*").eq("id", batch_id).execute(),
    )
    
    if not response.data:
        raise NotFoundError(
            message=f"Batch not found",
            details={"batch_id": batch_id}
        )
    
    return response.data[0]


def list_batches(
    archived: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0
) -> tuple[List[Dict[str, Any]], int]:
    """List batches with optional filtering."""
    supabase = get_supabase()

    query = supabase.client.table("batches").select(BATCH_LIST_FIELDS, count="exact")
    
    if archived is not None:
        query = query.eq("archived", archived)
    
    query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
    
    response = _execute_with_retry("list_batches", query.execute)
    
    return response.data, response.count or 0


def update_batch_state(batch_id: str, target_state: BatchState) -> Dict[str, Any]:
    """
    Update batch state with validation.
    Per Constitution § VII: State Machine Discipline
    """
    # Get current batch
    batch = get_batch_by_id(batch_id)
    current_state = BatchState(batch["state"])
    
    # Validate transition
    validate_state_transition(current_state, target_state)
    
    # Update state
    supabase = get_supabase()
    response = supabase.client.table("batches").update({
        "state": target_state.value
    }).eq("id", batch_id).execute()
    
    if not response.data:
        raise Exception("Failed to update batch state")
    
    updated_batch = response.data[0]
    
    logger.info(
        "batch_state_updated",
        batch_id=batch_id,
        from_state=current_state.value,
        to_state=target_state.value
    )
    
    return updated_batch


def archive_batch(batch_id: str, archived: bool) -> Dict[str, Any]:
    """Archive or unarchive a batch."""
    supabase = get_supabase()
    
    response = supabase.client.table("batches").update({
        "archived": archived
    }).eq("id", batch_id).execute()
    
    if not response.data:
        raise NotFoundError(
            message=f"Batch not found",
            details={"batch_id": batch_id}
        )
    
    logger.info(
        "batch_archived",
        batch_id=batch_id,
        archived=archived
    )
    
    return response.data[0]


def duplicate_batch(batch_id: str, new_brand: Optional[str] = None) -> Dict[str, Any]:
    """Duplicate a batch with a new brand name."""
    # Get original batch
    original = get_batch_by_id(batch_id)
    
    # Create new batch
    brand = new_brand or f"{original['brand']} (Copy)"
    creation_mode = str(original.get("creation_mode") or "automated")
    target_length_tier = (
        original.get("target_length_tier")
        if creation_mode == "semantic_ugc"
        else original.get("target_length_tier") or 8
    )
    new_batch = create_batch(
        brand,
        original.get("post_type_counts") or {},
        target_length_tier,
        target_duration_seconds=original.get("target_duration_seconds"),
        creation_mode=creation_mode,
        manual_post_count=original.get("manual_post_count"),
    )
    
    logger.info(
        "batch_duplicated",
        original_batch_id=batch_id,
        new_batch_id=new_batch["id"]
    )
    
    return new_batch


def get_batch_posts_summary(batch_id: str) -> Dict[str, Any]:
    """Get summary of posts for a batch."""
    supabase = get_supabase()

    # Get all posts for batch
    response = _execute_with_retry(
        "get_batch_posts_summary",
        lambda: supabase.client.table("posts").select(POSTS_SUMMARY_FIELDS).eq("batch_id", batch_id).execute(),
    )
    
    posts = response.data
    posts_count = len(posts)
    
    # Count posts by type
    posts_by_type = {}
    for post in posts:
        post_type = post["post_type"]
        posts_by_type[post_type] = posts_by_type.get(post_type, 0) + 1
    
    return {
        "posts_count": posts_count,
        "posts_by_state": posts_by_type
    }
