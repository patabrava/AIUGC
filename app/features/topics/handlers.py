"""
FLOW-FORGE Topics Handlers
FastAPI route handlers for topic discovery.
Per Constitution § V: Locality & Vertical Slices
"""

from fastapi import APIRouter, HTTPException, status, Header
from typing import Optional

from app.features.topics.schemas import (
    DiscoverTopicsRequest,
    TopicListResponse,
    TopicResponse
)
from app.features.topics.agents import (
    generate_topics_research_agent,
    extract_seed_strict_extractor
)
from app.features.topics.deduplication import deduplicate_topics
from app.features.topics.queries import (
    get_all_topics_from_registry,
    add_topic_to_registry,
    create_post_for_batch,
    count_posts_by_batch_and_type
)
from app.features.batches.queries import get_batch_by_id, update_batch_state
from app.core.states import BatchState
from app.core.errors import FlowForgeException, SuccessResponse, ValidationError
from app.core.logging import get_logger
from app.core.config import get_settings

logger = get_logger(__name__)

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/discover", response_model=SuccessResponse)
async def discover_topics_endpoint(request: DiscoverTopicsRequest):
    """
    Discover topics for a batch and create posts.
    Transitions batch from S1_SETUP to S2_SEEDED.
    Per Canon § 3.2: S1_SETUP → S2_SEEDED
    """
    try:
        # Get batch
        batch = get_batch_by_id(request.batch_id)
        
        # Verify batch is in S1_SETUP state
        if batch["state"] != BatchState.S1_SETUP.value:
            raise ValidationError(
                message="Batch must be in S1_SETUP state for topic discovery",
                details={"current_state": batch["state"], "required_state": "S1_SETUP"}
            )
        
        # Get post type counts
        post_type_counts = batch["post_type_counts"]
        
        # Get existing topics for deduplication
        existing_topics = get_all_topics_from_registry()
        
        all_generated_topics = []
        created_posts = []
        
        # Generate topics for each post type
        for post_type, count in post_type_counts.items():
            if count == 0:
                continue
            
            logger.info(
                "generating_topics",
                batch_id=request.batch_id,
                post_type=post_type,
                count=count
            )
            
            # Generate topics using research agent
            topics = generate_topics_research_agent(
                brand=batch["brand"],
                post_type=post_type,
                count=count * 2  # Generate 2x to account for deduplication
            )
            
            # Convert to dict format for deduplication
            topics_dict = [
                {
                    "title": t.title,
                    "rotation": t.rotation,
                    "cta": t.cta,
                    "spoken_duration": float(t.spoken_duration)
                }
                for t in topics
            ]
            
            # Deduplicate
            unique_topics = deduplicate_topics(
                topics_dict,
                existing_topics + all_generated_topics,
                threshold=0.7
            )
            
            # Take only the needed count
            selected_topics = unique_topics[:count]
            
            # Create posts for each topic
            for topic_dict in selected_topics:
                # Find original TopicData object
                topic = next(
                    t for t in topics 
                    if t.title == topic_dict["title"]
                )
                
                # Extract seed data
                seed = extract_seed_strict_extractor(topic)
                
                # Add to registry
                add_topic_to_registry(
                    title=topic.title,
                    rotation=topic.rotation,
                    cta=topic.cta
                )
                
                # Create post
                post = create_post_for_batch(
                    batch_id=request.batch_id,
                    post_type=post_type,
                    topic_title=topic.title,
                    topic_rotation=topic.rotation,
                    topic_cta=topic.cta,
                    spoken_duration=float(topic.spoken_duration),
                    seed_data=seed.model_dump()
                )
                
                created_posts.append(post)
                all_generated_topics.append(topic_dict)
        
        # Update batch state to S2_SEEDED
        updated_batch = update_batch_state(request.batch_id, BatchState.S2_SEEDED)
        
        logger.info(
            "topic_discovery_complete",
            batch_id=request.batch_id,
            posts_created=len(created_posts),
            new_state=updated_batch["state"]
        )
        
        return SuccessResponse(
            data={
                "batch_id": request.batch_id,
                "posts_created": len(created_posts),
                "state": updated_batch["state"],
                "topics": all_generated_topics
            }
        )
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("topic_discovery_failed", batch_id=request.batch_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to discover topics"
        )


@router.get("", response_model=SuccessResponse)
async def list_topics_endpoint(limit: int = 50, offset: int = 0):
    """List topics from registry."""
    try:
        topics = get_all_topics_from_registry()
        
        # Apply pagination
        paginated = topics[offset:offset + limit]
        
        topic_responses = [
            TopicResponse(
                id=t["id"],
                title=t["title"],
                rotation=t["rotation"],
                cta=t["cta"],
                first_seen_at=t["first_seen_at"],
                last_used_at=t["last_used_at"],
                use_count=t["use_count"]
            )
            for t in paginated
        ]
        
        return SuccessResponse(
            data=TopicListResponse(topics=topic_responses, total=len(topics))
        )
    
    except Exception as e:
        logger.exception("list_topics_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list topics"
        )


@router.post("/cron/discover", response_model=SuccessResponse)
async def cron_topic_discovery(
    authorization: Optional[str] = Header(None)
):
    """
    Vercel Cron endpoint for automated topic discovery.
    Runs every 6 hours to discover topics for batches in S1_SETUP.
    Per Implementation Guide: Vercel Cron
    """
    settings = get_settings()
    
    # Verify cron secret
    if not authorization or authorization != f"Bearer {settings.cron_secret}":
        logger.warning("cron_unauthorized_access", auth_header=authorization)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized"
        )
    
    try:
        # This is a placeholder for automated discovery
        # In production, this would find batches in S1_SETUP and run discovery
        logger.info("cron_topic_discovery_triggered")
        
        return SuccessResponse(
            data={
                "message": "Cron job executed successfully",
                "timestamp": "2025-11-05T11:54:00Z"
            }
        )
    
    except Exception as e:
        logger.exception("cron_topic_discovery_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cron job failed"
        )
