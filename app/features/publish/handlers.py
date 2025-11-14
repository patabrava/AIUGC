"""
FLOW-FORGE Publish Handlers
FastAPI endpoints for S7_PUBLISH_PLAN state management.
Per Constitution § VII: State Machine Discipline
Per Canon § 3.2: S7_PUBLISH_PLAN → S8_COMPLETE transition
"""

import structlog
from fastapi import APIRouter, HTTPException, Request
from datetime import datetime, timedelta
from typing import List, Dict, Any
from zoneinfo import ZoneInfo

from app.core.errors import SuccessResponse, ErrorResponse
from app.core.states import BatchState
from app.features.publish.schemas import (
    PostScheduleRequest,
    BatchPublishPlanRequest,
    UpdatePostScheduleRequest,
    PostScheduleResponse,
    BatchPublishPlanResponse,
    SuggestTimesRequest,
    SuggestTimesResponse,
    SuggestedTime,
    ConfirmPublishRequest,
    ConfirmPublishResponse,
    PublishResult,
    SocialNetwork,
)
from app.adapters.supabase_client import get_supabase

logger = structlog.get_logger()
router = APIRouter(prefix="/publish", tags=["publish"])


def get_post_schedules(batch_id: str) -> List[Dict[str, Any]]:
    """
    Get all post schedules for a batch.
    Per Constitution § IX: Observable Implementation
    """
    supabase = get_supabase()
    
    response = supabase.client.table("posts").select(
        "id, topic_title, scheduled_at, social_networks, publish_status, platform_ids"
    ).eq("batch_id", batch_id).execute()
    
    return response.data


def update_post_schedule(
    post_id: str,
    scheduled_at: datetime = None,
    social_networks: List[str] = None
) -> Dict[str, Any]:
    """
    Update schedule for a single post.
    Per Constitution § III: Deterministic Execution
    """
    supabase = get_supabase()
    
    update_data = {}
    if scheduled_at is not None:
        update_data["scheduled_at"] = scheduled_at.isoformat()
    if social_networks is not None:
        update_data["social_networks"] = social_networks
    
    if update_data:
        update_data["publish_status"] = "scheduled"
        
        response = supabase.client.table("posts").update(update_data).eq(
            "id", post_id
        ).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail=f"Post {post_id} not found")
        
        return response.data[0]
    
    return {}


@router.post("/posts/{post_id}/schedule", response_model=SuccessResponse)
async def schedule_post(post_id: str, request: PostScheduleRequest):
    """
    Schedule a single post for publishing.
    Per Canon § 7.3: Future times, min gap validation handled by schema.
    """
    correlation_id = f"schedule_post_{post_id}"
    
    try:
        logger.info(
            "scheduling_post",
            correlation_id=correlation_id,
            post_id=post_id,
            scheduled_at=request.scheduled_at.isoformat(),
            social_networks=[n.value for n in request.social_networks]
        )
        
        # Update post schedule
        updated_post = update_post_schedule(
            post_id=post_id,
            scheduled_at=request.scheduled_at,
            social_networks=[n.value for n in request.social_networks]
        )
        
        logger.info(
            "post_scheduled",
            correlation_id=correlation_id,
            post_id=post_id,
            scheduled_at=updated_post.get("scheduled_at")
        )
        
        return SuccessResponse(
            data={
                "post_id": post_id,
                "scheduled_at": updated_post.get("scheduled_at"),
                "social_networks": updated_post.get("social_networks"),
                "publish_status": updated_post.get("publish_status")
            }
        )
    
    except Exception as e:
        logger.error(
            "schedule_post_failed",
            correlation_id=correlation_id,
            post_id=post_id,
            error=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/posts/{post_id}/schedule", response_model=SuccessResponse)
async def update_schedule(post_id: str, request: UpdatePostScheduleRequest):
    """
    Update existing schedule for a post.
    Per Constitution § XIII: Idempotency & Recovery
    """
    correlation_id = f"update_schedule_{post_id}"
    
    try:
        logger.info(
            "updating_post_schedule",
            correlation_id=correlation_id,
            post_id=post_id
        )
        
        # Convert social network enums to strings if provided
        social_networks = None
        if request.social_networks:
            social_networks = [n.value for n in request.social_networks]
        
        updated_post = update_post_schedule(
            post_id=post_id,
            scheduled_at=request.scheduled_at,
            social_networks=social_networks
        )
        
        return SuccessResponse(data={"post": updated_post})
    
    except Exception as e:
        logger.error(
            "update_schedule_failed",
            correlation_id=correlation_id,
            post_id=post_id,
            error=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batches/{batch_id}/plan", response_model=SuccessResponse)
async def set_batch_publish_plan(batch_id: str, request: BatchPublishPlanRequest):
    """
    Set publish plan for entire batch.
    Per Canon § 7.3: Validation (future times, spacing, overlaps)
    Per Constitution § VII: Explicit state guards
    """
    correlation_id = f"batch_publish_plan_{batch_id}"
    
    try:
        logger.info(
            "setting_batch_publish_plan",
            correlation_id=correlation_id,
            batch_id=batch_id,
            num_schedules=len(request.schedules)
        )
        
        # Verify batch is in S7_PUBLISH_PLAN state
        supabase = get_supabase()
        batch_response = supabase.client.table("batches").select("state").eq(
            "id", batch_id
        ).execute()
        
        if not batch_response.data:
            raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
        
        current_state = batch_response.data[0].get("state")
        if current_state != BatchState.S7_PUBLISH_PLAN.value:
            raise HTTPException(
                status_code=409,
                detail=f"Batch must be in S7_PUBLISH_PLAN state (current: {current_state})"
            )
        
        # Update all post schedules
        updated_count = 0
        for schedule in request.schedules:
            update_post_schedule(
                post_id=schedule.post_id,
                scheduled_at=schedule.scheduled_at,
                social_networks=[n.value for n in schedule.social_networks]
            )
            updated_count += 1
        
        logger.info(
            "batch_publish_plan_set",
            correlation_id=correlation_id,
            batch_id=batch_id,
            updated_count=updated_count
        )
        
        return SuccessResponse(
            data={
                "batch_id": batch_id,
                "updated_count": updated_count,
                "message": f"Publish plan set for {updated_count} posts"
            }
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "set_batch_publish_plan_failed",
            correlation_id=correlation_id,
            batch_id=batch_id,
            error=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/batches/{batch_id}/plan", response_model=BatchPublishPlanResponse)
async def get_batch_publish_plan(batch_id: str):
    """
    Get current publish plan for batch.
    Per Constitution § IX: Observable Implementation
    """
    try:
        schedules = get_post_schedules(batch_id)
        
        scheduled_count = sum(1 for s in schedules if s.get("scheduled_at"))
        pending_count = len(schedules) - scheduled_count
        
        return BatchPublishPlanResponse(
            batch_id=batch_id,
            total_posts=len(schedules),
            scheduled_posts=scheduled_count,
            pending_posts=pending_count,
            schedules=[
                PostScheduleResponse(
                    post_id=s["id"],
                    topic_title=s.get("topic_title", "Untitled"),
                    scheduled_at=s.get("scheduled_at"),
                    social_networks=s.get("social_networks", []),
                    publish_status=s.get("publish_status", "pending"),
                    platform_ids=s.get("platform_ids")
                )
                for s in schedules
            ]
        )
    
    except Exception as e:
        logger.error("get_batch_publish_plan_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batches/{batch_id}/suggest-times", response_model=SuggestTimesResponse)
async def suggest_publish_times(batch_id: str, request: SuggestTimesRequest):
    """
    Suggest optimal publish times using Engagement Scheduler agent.
    Per Canon § 6.5: TZ=Europe/Berlin, min_gap=30min, no 00:00-06:00
    Per Constitution § XII: Agent Prompt Discipline
    
    NOTE: This is a simplified implementation. Full LLM-based agent TBD.
    """
    correlation_id = f"suggest_times_{batch_id}"
    
    try:
        logger.info(
            "suggesting_publish_times",
            correlation_id=correlation_id,
            batch_id=batch_id,
            timezone=request.timezone
        )
        
        # Get number of posts in batch
        supabase = get_supabase()
        posts_response = supabase.client.table("posts").select("id").eq(
            "batch_id", batch_id
        ).execute()
        
        num_posts = len(posts_response.data)
        
        # Simple heuristic: suggest high-engagement times
        # Peak times: 12:00, 15:00, 18:00, 20:00 (Europe/Berlin)
        peak_hours = [12, 15, 18, 20]
        
        start_date = request.start_date or (datetime.utcnow() + timedelta(days=1))
        berlin_tz = ZoneInfo(request.timezone)
        
        suggestions = []
        current_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        
        for i in range(num_posts):
            hour = peak_hours[i % len(peak_hours)]
            
            # Create datetime in Berlin timezone
            local_dt = current_date.replace(hour=hour, minute=0, tzinfo=berlin_tz)
            utc_dt = local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            
            suggestions.append(
                SuggestedTime(
                    datetime_utc=utc_dt,
                    datetime_local=local_dt.strftime("%Y-%m-%d %H:%M %Z"),
                    reason=f"Peak engagement time ({hour}:00 {request.timezone})"
                )
            )
            
            # Move to next peak time or next day
            if (i + 1) % len(peak_hours) == 0:
                current_date += timedelta(days=1)
        
        logger.info(
            "publish_times_suggested",
            correlation_id=correlation_id,
            batch_id=batch_id,
            num_suggestions=len(suggestions)
        )
        
        return SuggestTimesResponse(
            suggestions=suggestions,
            timezone=request.timezone
        )
    
    except Exception as e:
        logger.error(
            "suggest_publish_times_failed",
            correlation_id=correlation_id,
            batch_id=batch_id,
            error=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batches/{batch_id}/confirm", response_model=ConfirmPublishResponse)
async def confirm_publish(batch_id: str, request: ConfirmPublishRequest):
    """
    Confirm and dispatch batch to social platforms.
    Per Canon § 3.2: S7_PUBLISH_PLAN → S8_COMPLETE
    Per Constitution § VII: State Machine Discipline
    
    NOTE: This is a placeholder. Actual social platform dispatch TBD.
    """
    correlation_id = f"confirm_publish_{batch_id}"
    
    try:
        logger.info(
            "confirming_publish",
            correlation_id=correlation_id,
            batch_id=batch_id
        )
        
        supabase = get_supabase()
        
        # Verify batch state
        batch_response = supabase.client.table("batches").select("state").eq(
            "id", batch_id
        ).execute()
        
        if not batch_response.data:
            raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
        
        current_state = batch_response.data[0].get("state")
        if current_state != BatchState.S7_PUBLISH_PLAN.value:
            raise HTTPException(
                status_code=409,
                detail=f"Batch must be in S7_PUBLISH_PLAN state (current: {current_state})"
            )
        
        # Get all scheduled posts
        posts_response = supabase.client.table("posts").select(
            "id, scheduled_at, social_networks"
        ).eq("batch_id", batch_id).execute()
        
        posts = posts_response.data
        
        # Validate all posts have schedules
        unscheduled = [p for p in posts if not p.get("scheduled_at")]
        if unscheduled:
            raise HTTPException(
                status_code=400,
                detail=f"{len(unscheduled)} posts are not scheduled"
            )
        
        # TODO: Actual dispatch to social platforms
        # For now, mark all as "scheduled" and advance batch to S8_COMPLETE
        results = []
        for post in posts:
            # Placeholder: simulate successful publish
            platform_ids = {
                "tiktok": f"tiktok_{post['id'][:8]}",
                "instagram": f"ig_{post['id'][:8]}"
            }
            
            supabase.client.table("posts").update({
                "publish_status": "scheduled",
                "platform_ids": platform_ids
            }).eq("id", post["id"]).execute()
            
            results.append(
                PublishResult(
                    post_id=post["id"],
                    success=True,
                    platform_ids=platform_ids
                )
            )
        
        # Advance batch to S8_COMPLETE
        supabase.client.table("batches").update({
            "state": BatchState.S8_COMPLETE.value,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", batch_id).execute()
        
        logger.info(
            "batch_published",
            correlation_id=correlation_id,
            batch_id=batch_id,
            total_posts=len(posts),
            new_state=BatchState.S8_COMPLETE.value
        )
        
        return ConfirmPublishResponse(
            batch_id=batch_id,
            total_posts=len(posts),
            published_count=len(results),
            failed_count=0,
            results=results
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "confirm_publish_failed",
            correlation_id=correlation_id,
            batch_id=batch_id,
            error=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))
