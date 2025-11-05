"""
FLOW-FORGE Topics Database Queries
Database operations for topics and topic registry.
Per Constitution § V: Locality & Vertical Slices
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from app.adapters.supabase_client import get_supabase
from app.core.logging import get_logger
from app.core.errors import NotFoundError

logger = get_logger(__name__)


def get_all_topics_from_registry() -> List[Dict[str, Any]]:
    """Get all topics from the registry for deduplication."""
    supabase = get_supabase()
    
    response = supabase.client.table("topic_registry").select("*").execute()
    
    return response.data


def add_topic_to_registry(
    title: str,
    rotation: str,
    cta: str
) -> Dict[str, Any]:
    """
    Add a new topic to the registry.
    If topic already exists (unique constraint), increment use_count.
    """
    supabase = get_supabase()
    
    try:
        # Try to insert new topic
        topic_data = {
            "title": title,
            "rotation": rotation,
            "cta": cta,
            "use_count": 1
        }
        
        response = supabase.client.table("topic_registry").insert(topic_data).execute()
        
        if response.data:
            logger.info(
                "topic_added_to_registry",
                topic_id=response.data[0]["id"],
                title=title[:50]
            )
            return response.data[0]
    
    except Exception as e:
        # If unique constraint violation, update existing
        logger.info(
            "topic_exists_in_registry",
            title=title[:50],
            error=str(e)
        )
        
        # Find existing topic and increment use_count
        existing = supabase.client.table("topic_registry").select("*").eq("title", title).eq("rotation", rotation).eq("cta", cta).execute()
        
        if existing.data:
            topic_id = existing.data[0]["id"]
            current_count = existing.data[0]["use_count"]
            
            updated = supabase.client.table("topic_registry").update({
                "use_count": current_count + 1,
                "last_used_at": datetime.utcnow().isoformat()
            }).eq("id", topic_id).execute()
            
            logger.info(
                "topic_use_count_incremented",
                topic_id=topic_id,
                new_count=current_count + 1
            )
            
            return updated.data[0]
    
    raise Exception("Failed to add topic to registry")


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
