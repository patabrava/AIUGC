"""
FLOW-FORGE Topics Handlers
FastAPI route handlers for topic discovery.
Per Constitution § V: Locality & Vertical Slices
"""

from fastapi import APIRouter, HTTPException, status, Header
from typing import Optional, Dict, Any

from app.features.topics.schemas import (
    DiscoverTopicsRequest,
    TopicListResponse,
    TopicResponse
)
from app.features.topics.agents import (
    generate_topics_research_agent,
    generate_dialog_scripts,
    extract_seed_strict_extractor,
    convert_research_item_to_topic,
    build_seed_payload,
)
from app.features.topics.deduplication import deduplicate_topics
from app.features.topics.queries import (
    get_all_topics_from_registry,
    add_topic_to_registry,
    create_post_for_batch,
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

            created_for_type = 0
            generation_attempts = 0
            max_generation_attempts = 4

            while created_for_type < count and generation_attempts < max_generation_attempts:
                generation_attempts += 1

                items = generate_topics_research_agent(
                    brand=batch["brand"],
                    post_type=post_type,
                    count=count * 2
                )

                # transform items into TopicData
                topic_data = [convert_research_item_to_topic(item) for item in items]
                topics_dict = [
                    {
                        "title": data.title,
                        "rotation": data.rotation,
                        "cta": data.cta,
                        "spoken_duration": float(data.spoken_duration),
                        "__item_index": idx,
                    }
                    for idx, data in enumerate(topic_data)
                ]

                # Deduplicate across registry and previous selections
                unique_topics = deduplicate_topics(
                    topics_dict,
                    existing_topics + all_generated_topics,
                    threshold=0.35
                )

                for topic_dict in unique_topics:
                    if created_for_type >= count:
                        break

                    idx = topic_dict["__item_index"]
                    original_item = items[idx]
                    topic_model = topic_data[idx]
                    topic_title = topic_model.title

                    try:
                        dialog_scripts = generate_dialog_scripts(
                            brand=batch["brand"],
                            topic=original_item.topic
                        )
                        seed = extract_seed_strict_extractor(topic_model)

                        seed_payload = build_seed_payload(
                            original_item,
                            strict_seed=seed,
                            dialog_scripts=dialog_scripts
                        )

                        add_topic_to_registry(
                            title=topic_model.title,
                            rotation=topic_model.rotation,
                            cta=topic_model.cta
                        )

                        post = create_post_for_batch(
                            batch_id=request.batch_id,
                            post_type=post_type,
                            topic_title=topic_model.title,
                            topic_rotation=topic_model.rotation,
                            topic_cta=topic_model.cta,
                            spoken_duration=float(topic_model.spoken_duration),
                            seed_data=seed_payload
                        )

                        created_posts.append(post)
                        created_for_type += 1

                        dedup_topic_record: Dict[str, Any] = {
                            "title": topic_model.title,
                            "rotation": topic_model.rotation,
                            "cta": topic_model.cta,
                            "spoken_duration": float(topic_model.spoken_duration),
                        }
                        all_generated_topics.append(dedup_topic_record)
                        existing_topics.append(dedup_topic_record)

                        logger.info(
                            "topic_processed_successfully",
                            batch_id=request.batch_id,
                            post_type=post_type,
                            topic_title=topic_title[:50],
                            generation_attempt=generation_attempts
                        )
                    except Exception as topic_error:
                        logger.error(
                            "topic_processing_failed",
                            batch_id=request.batch_id,
                            post_type=post_type,
                            topic_title=topic_title[:50],
                            generation_attempt=generation_attempts,
                            error=str(topic_error)
                        )
                        continue

            if created_for_type < count:
                raise ValidationError(
                    message="Unable to generate required posts for post type",
                    details={
                        "post_type": post_type,
                        "required": count,
                        "created": created_for_type,
                        "generation_attempts": generation_attempts
                    }
                )

        # Validate minimum posts created
        if len(created_posts) == 0:
            raise ValidationError(
                message="No posts were successfully created",
                details={
                    "requested_counts": post_type_counts,
                    "topics_attempted": len(all_generated_topics)
                }
            )

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
    except ValidationError as exc:
        logger.error(
            "topic_discovery_validation_error",
            batch_id=request.batch_id,
            message=exc.message,
            details=exc.details,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "ok": False,
                "code": "validation_error",
                "message": exc.message,
                "details": exc.details,
            },
        )
    except Exception as e:
        logger.exception(
            "topic_discovery_failed",
            batch_id=request.batch_id,
            error=str(e)
        )
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
        from app.features.batches.queries import list_batches

        batches, _ = list_batches(archived=False, limit=100, offset=0)
        seeded = []
        for batch in batches:
            if batch["state"] != BatchState.S1_SETUP.value:
                continue
            request_payload = DiscoverTopicsRequest(batch_id=batch["id"], count=10)
            seeded.append(batch["id"])
            await discover_topics_endpoint(request_payload)

        logger.info(
            "cron_topic_discovery_triggered",
            seeded_batches=seeded
        )
        return SuccessResponse(
            data={
                "message": "Cron job executed successfully",
                "seeded_batches": seeded,
            }
        )
    
    except Exception as e:
        logger.exception("cron_topic_discovery_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cron job failed"
        )
