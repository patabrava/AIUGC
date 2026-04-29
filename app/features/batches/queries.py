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
from app.core.errors import NotFoundError, StateTransitionError, ThirdPartyError
from app.core.logging import get_logger
from app.features.topics.queries import create_post_for_batch

logger = get_logger(__name__)

_QUERY_RETRY_DELAYS = (0.15, 0.35, 0.75)
BATCH_LIST_FIELDS = (
    "id,brand,state,post_type_counts,target_length_tier,created_at,updated_at,archived"
)
POSTS_SUMMARY_FIELDS = "id,post_type"


def _execute_with_retry(operation_name: str, callback):
    last_error: Optional[Exception] = None
    for attempt, delay in enumerate((0.0, *_QUERY_RETRY_DELAYS), start=1):
        if delay:
            time.sleep(delay)
        try:
            return callback()
        except httpx.RequestError as exc:
            last_error = exc
            logger.warning(
                "batch_query_retryable_request_error",
                operation=operation_name,
                attempt=attempt,
                error=str(exc),
            )
    raise ThirdPartyError(
        message=f"Failed to load batch data for {operation_name}",
        details={"operation": operation_name, "error": str(last_error) if last_error else "unknown"},
    )


def create_batch(
    brand: str,
    post_type_counts: Optional[Dict[str, int]],
    target_length_tier: int = 8,
    *,
    creation_mode: str = "automated",
    manual_post_count: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Create a new batch in S1_SETUP state.
    Per Canon § 3.2: S1_SETUP is initial state.
    """
    supabase = get_supabase()
    
    batch_data = {
        "brand": brand,
        "state": BatchState.S1_SETUP.value,
        "creation_mode": creation_mode,
        "post_type_counts": post_type_counts or {},
        "manual_post_count": manual_post_count,
        "target_length_tier": target_length_tier,
        "archived": False
    }

    legacy_batch_data = {
        "brand": brand,
        "state": BatchState.S1_SETUP.value,
        "post_type_counts": post_type_counts or {},
        "target_length_tier": target_length_tier,
        "archived": False,
    }

    try:
        response = supabase.client.table("batches").insert(batch_data).execute()
    except APIError as exc:
        error_text = str(exc)
        if exc.code == "PGRST204" and "creation_mode" in error_text:
            logger.warning(
                "batch_creation_mode_column_missing_fallback",
                error=error_text,
                batch_brand=brand,
            )
            response = supabase.client.table("batches").insert(legacy_batch_data).execute()
        else:
            raise

    if not response.data:
        raise Exception("Failed to create batch")

    batch = response.data[0]
    if creation_mode:
        batch = {
            **batch,
            "creation_mode": creation_mode,
            "manual_post_count": manual_post_count,
        }
    
    logger.info(
        "batch_created",
        batch_id=batch["id"],
        brand=brand,
        state=batch["state"],
        creation_mode=creation_mode,
    )
    
    return batch


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
    new_batch = create_batch(
        brand,
        original.get("post_type_counts") or {},
        original.get("target_length_tier") or 8,
        creation_mode=str(original.get("creation_mode") or "automated"),
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
