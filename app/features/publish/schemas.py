"""
FLOW-FORGE Publish Schemas
Pydantic models for publish planning and social media dispatch.
Per Constitution § II: Validated Boundaries
Per Canon § 3.2: S7_PUBLISH_PLAN state
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


class SocialNetwork(str, Enum):
    """Supported social media networks."""
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"


class PublishStatus(str, Enum):
    """Publishing status for a post."""
    PENDING = "pending"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class PostScheduleRequest(BaseModel):
    """Request to schedule a single post."""
    post_id: str = Field(..., description="Post ID to schedule")
    scheduled_at: datetime = Field(..., description="Scheduled publish time in UTC")
    social_networks: List[SocialNetwork] = Field(
        ..., 
        min_length=1,
        description="Selected social networks (TikTok, Instagram, Facebook)"
    )
    
    @field_validator('scheduled_at')
    @classmethod
    def validate_future_time(cls, v: datetime) -> datetime:
        """Ensure scheduled time is in the future."""
        if v <= datetime.utcnow():
            raise ValueError("Scheduled time must be in the future")
        return v
    
    @field_validator('social_networks')
    @classmethod
    def validate_unique_networks(cls, v: List[SocialNetwork]) -> List[SocialNetwork]:
        """Ensure no duplicate networks."""
        if len(v) != len(set(v)):
            raise ValueError("Duplicate social networks not allowed")
        return v


class BatchPublishPlanRequest(BaseModel):
    """Request to set publish plan for entire batch."""
    schedules: List[PostScheduleRequest] = Field(
        ...,
        description="List of post schedules"
    )
    
    @field_validator('schedules')
    @classmethod
    def validate_min_gap(cls, v: List[PostScheduleRequest]) -> List[PostScheduleRequest]:
        """
        Validate minimum 30-minute gap between posts.
        Per Canon § 7.3: Min gap: 30 minutes
        """
        if len(v) < 2:
            return v
        
        # Sort by scheduled time
        sorted_schedules = sorted(v, key=lambda s: s.scheduled_at)
        
        for i in range(1, len(sorted_schedules)):
            prev_time = sorted_schedules[i - 1].scheduled_at
            curr_time = sorted_schedules[i].scheduled_at
            gap_minutes = (curr_time - prev_time).total_seconds() / 60
            
            if gap_minutes < 30:
                raise ValueError(
                    f"Posts must be at least 30 minutes apart. "
                    f"Gap between posts is {gap_minutes:.1f} minutes"
                )
        
        return v


class UpdatePostScheduleRequest(BaseModel):
    """Request to update schedule for a single post."""
    scheduled_at: Optional[datetime] = Field(None, description="New scheduled time in UTC")
    social_networks: Optional[List[SocialNetwork]] = Field(None, description="Updated social networks")
    
    @field_validator('scheduled_at')
    @classmethod
    def validate_future_time(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure scheduled time is in the future."""
        if v is not None and v <= datetime.utcnow():
            raise ValueError("Scheduled time must be in the future")
        return v


class PostScheduleResponse(BaseModel):
    """Response for post schedule."""
    post_id: str
    topic_title: str
    scheduled_at: Optional[datetime]
    social_networks: List[str]
    publish_status: str
    platform_ids: Optional[Dict[str, str]] = None
    
    class Config:
        from_attributes = True


class BatchPublishPlanResponse(BaseModel):
    """Response for batch publish plan."""
    batch_id: str
    total_posts: int
    scheduled_posts: int
    pending_posts: int
    schedules: List[PostScheduleResponse]


class SuggestTimesRequest(BaseModel):
    """Request to suggest optimal publish times."""
    batch_id: str = Field(..., description="Batch ID to suggest times for")
    start_date: Optional[datetime] = Field(
        None,
        description="Start date for suggestions (defaults to tomorrow)"
    )
    timezone: str = Field(
        default="Europe/Berlin",
        description="Timezone for scheduling (per Canon § 6.5)"
    )


class SuggestedTime(BaseModel):
    """A suggested publish time."""
    datetime_utc: datetime
    datetime_local: str
    reason: str = Field(..., description="Why this time was suggested")


class SuggestTimesResponse(BaseModel):
    """Response with suggested publish times."""
    suggestions: List[SuggestedTime]
    timezone: str


class ConfirmPublishRequest(BaseModel):
    """Request to confirm and dispatch batch to social platforms."""
    batch_id: str = Field(..., description="Batch ID to publish")
    confirm: bool = Field(default=True, description="Confirmation flag")


class PublishResult(BaseModel):
    """Result of publishing a single post."""
    post_id: str
    success: bool
    platform_ids: Optional[Dict[str, str]] = None
    errors: Optional[Dict[str, str]] = None


class ConfirmPublishResponse(BaseModel):
    """Response after confirming publish."""
    batch_id: str
    total_posts: int
    published_count: int
    failed_count: int
    results: List[PublishResult]
