"""
FLOW-FORGE Batches Database Queries
Database operations for batches.
Per Constitution § V: Locality & Vertical Slices
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from app.adapters.supabase_client import get_supabase
from app.core.states import BatchState, validate_state_transition
from app.core.errors import NotFoundError, StateTransitionError
from app.core.logging import get_logger

logger = get_logger(__name__)


def create_batch(brand: str, post_type_counts: Dict[str, int]) -> Dict[str, Any]:
    """
    Create a new batch in S1_SETUP state.
    Per Canon § 3.2: S1_SETUP is initial state.
    """
    supabase = get_supabase()
    
    batch_data = {
        "brand": brand,
        "state": BatchState.S1_SETUP.value,
        "post_type_counts": post_type_counts,
        "archived": False
    }
    
    response = supabase.client.table("batches").insert(batch_data).execute()
    
    if not response.data:
        raise Exception("Failed to create batch")
    
    batch = response.data[0]
    
    logger.info(
        "batch_created",
        batch_id=batch["id"],
        brand=brand,
        state=batch["state"]
    )
    
    return batch


def get_batch_by_id(batch_id: str) -> Dict[str, Any]:
    """Get batch by ID."""
    supabase = get_supabase()
    
    response = supabase.client.table("batches").select("*").eq("id", batch_id).execute()
    
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
    
    query = supabase.client.table("batches").select("*", count="exact")
    
    if archived is not None:
        query = query.eq("archived", archived)
    
    query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
    
    response = query.execute()
    
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
    new_batch = create_batch(brand, original["post_type_counts"])
    
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
    response = supabase.client.table("posts").select("*").eq("batch_id", batch_id).execute()
    
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
