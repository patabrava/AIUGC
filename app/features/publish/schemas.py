"""
Lippe Lift Studio Publish Schemas
Pydantic models for publish planning and social media dispatch.
Per Constitution § II: Validated Boundaries
Per Canon § 3.2: S7_PUBLISH_PLAN state
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Literal, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
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


def _normalize_utc_datetime(value: datetime) -> datetime:
    """Normalize schedule timestamps to UTC-aware datetimes for safe comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostScheduleRequest(BaseModel):
    """Request to schedule a single post."""
    post_id: str = Field(..., description="Post ID to schedule")
    scheduled_at: datetime = Field(..., description="Scheduled publish time in UTC")
    publish_caption: str = Field(..., min_length=1, max_length=2200, description="Shared caption/description")
    social_networks: List[SocialNetwork] = Field(
        ..., 
        min_length=1,
        description="Selected social networks (Instagram, Facebook)"
    )
    
    @field_validator('scheduled_at')
    @classmethod
    def validate_future_time(cls, v: datetime) -> datetime:
        """Ensure scheduled time is in the future."""
        normalized = _normalize_utc_datetime(v)
        if normalized <= datetime.now(timezone.utc):
            raise ValueError("Scheduled time must be in the future")
        return normalized
    
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
    publish_caption: Optional[str] = Field(None, min_length=1, max_length=2200, description="Updated shared caption")
    social_networks: Optional[List[SocialNetwork]] = Field(None, description="Updated social networks")
    
    @field_validator('scheduled_at')
    @classmethod
    def validate_future_time(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure scheduled time is in the future."""
        if v is None:
            return v
        normalized = _normalize_utc_datetime(v)
        if normalized <= datetime.now(timezone.utc):
            raise ValueError("Scheduled time must be in the future")
        return normalized


class PostNowRequest(BaseModel):
    """Request to publish one post immediately to selected networks."""
    post_id: str = Field(..., description="Post ID to publish now")
    publish_caption: str = Field(..., min_length=1, max_length=2200, description="Caption for immediate publish")
    social_networks: List[SocialNetwork] = Field(
        ...,
        min_length=1,
        description="Selected social networks (Instagram, Facebook, TikTok)",
    )
    tiktok_settings: Optional["TikTokPostSettings"] = Field(
        default=None,
        description="Required TikTok post settings when direct posting is available",
    )

    @field_validator("social_networks")
    @classmethod
    def validate_unique_networks(cls, v: List[SocialNetwork]) -> List[SocialNetwork]:
        if len(v) != len(set(v)):
            raise ValueError("Duplicate social networks not allowed")
        return v


class MetaTargetSelectionRequest(BaseModel):
    """Request to select the Page/Instagram pair for a batch."""
    page_id: str = Field(..., min_length=1, description="Facebook Page ID to bind to the batch")


class PostScheduleResponse(BaseModel):
    """Response for post schedule."""
    post_id: str
    topic_title: str
    scheduled_at: Optional[datetime]
    publish_caption: str = ""
    social_networks: List[str]
    publish_status: str
    platform_ids: Optional[Dict[str, str]] = None
    publish_results: Optional[Dict[str, Any]] = None
    
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
    """Request to confirm and arm batch dispatch."""
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


class TikTokAccountResponse(BaseModel):
    """Public TikTok account state for the sandbox operator."""
    id: Optional[str] = None
    platform: Optional[str] = None
    open_id: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    scope: Optional[str] = None
    environment: Optional[str] = None
    access_token_expires_at: Optional[datetime] = None
    refresh_token_expires_at: Optional[datetime] = None
    status: str = "disconnected"
    publish_ready: bool = False
    draft_ready: bool = False
    readiness_status: str = "disconnected"
    readiness_reason: Optional[str] = None
    scope_flags: Optional[Dict[str, bool]] = None
    creator_info: Optional[Dict[str, Any]] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Batch Arm schemas (Canon § 7.3)
# ---------------------------------------------------------------------------


class SlotSpec(BaseModel):
    day: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    time: str = Field(..., description="Time in HH:MM 24h format")

    @field_validator("time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("time must be in HH:MM 24h format")
        return v


class PostArmSpec(BaseModel):
    post_id: str
    caption: str = Field(..., min_length=1, max_length=2200)
    time_override: Optional[str] = None
    networks_override: Optional[List[str]] = None


class BatchArmRequest(BaseModel):
    week_start: str = Field(..., description="ISO date YYYY-MM-DD")
    slots: List[SlotSpec] = Field(..., min_length=1, max_length=5)
    default_networks: List[str] = Field(..., min_length=1)
    posts: List[PostArmSpec] = Field(..., min_length=1)
    timezone: str = Field(default="Europe/Berlin", description="IANA timezone for schedule times")

    @model_validator(mode="after")
    def validate_min_gap(self) -> "BatchArmRequest":
        """Canon 7.3: enforce 30-minute minimum gap between any two scheduled posts."""
        from zoneinfo import ZoneInfo

        day_offsets = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        tz = ZoneInfo(self.timezone)
        base = datetime.strptime(self.week_start, "%Y-%m-%d")
        times = []
        for i, post in enumerate(self.posts):
            if post.time_override:
                dt = datetime.strptime(post.time_override, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
            elif i < len(self.slots):
                slot = self.slots[i]
                h, m = int(slot.time[:2]), int(slot.time[3:])
                dt = base.replace(hour=h, minute=m, tzinfo=tz) + timedelta(days=day_offsets[slot.day])
            else:
                continue
            times.append(dt)
        times.sort()
        for a, b in zip(times, times[1:]):
            if (b - a) < timedelta(minutes=30):
                raise ValueError("Posts must be at least 30 minutes apart (Canon 7.3)")
        return self


class BatchArmResponse(BaseModel):
    ok: bool = True
    armed_count: int
    scheduled_posts: List[dict]


class TikTokUploadDraftRequest(BaseModel):
    """Upload one generated post as a TikTok draft."""
    post_id: str = Field(..., min_length=1, description="Post id for the generated video")
    caption: Optional[str] = Field(default=None, max_length=2200, description="Optional TikTok draft caption")


ALLOWED_TIKTOK_PRIVACY_LEVELS = frozenset(
    {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}
)


class TikTokPostSettings(BaseModel):
    """TikTok Content Posting API required per-post fields."""

    title: str = Field(..., max_length=90, description="TikTok post title, shown above the video")
    privacy_level: str = Field(..., description="One of TikTok's privacy_level_options")
    allow_comment: bool = Field(default=False)
    allow_duet: bool = Field(default=False)
    allow_stitch: bool = Field(default=False)
    commercial_disclosure: bool = Field(default=False)
    your_brand: bool = Field(default=False)
    branded_content: bool = Field(default=False)
    consent_acknowledged: bool = Field(default=False)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("privacy_level")
    @classmethod
    def privacy_level_known(cls, value: str) -> str:
        if value not in ALLOWED_TIKTOK_PRIVACY_LEVELS:
            raise ValueError(
                f"privacy_level must be one of {sorted(ALLOWED_TIKTOK_PRIVACY_LEVELS)}"
            )
        return value

    @model_validator(mode="after")
    def validate_disclosure_consistency(self) -> "TikTokPostSettings":
        if self.commercial_disclosure and not (self.your_brand or self.branded_content):
            raise ValueError(
                "commercial_disclosure requires at least one of your_brand or branded_content"
            )
        if self.branded_content and self.privacy_level == "SELF_ONLY":
            raise ValueError("branded_content cannot use SELF_ONLY privacy level")
        if not self.commercial_disclosure and (self.your_brand or self.branded_content):
            raise ValueError(
                "your_brand/branded_content require commercial_disclosure to be true"
            )
        if not self.consent_acknowledged:
            raise ValueError("consent_acknowledged must be true before TikTok posting")
        return self


class TikTokBatchDefaults(BaseModel):
    """Optional batch-level defaults; copied into per-post settings on first edit."""

    title_template: Optional[str] = Field(default=None, max_length=90)
    privacy_level: Optional[str] = Field(default=None)
    allow_comment: bool = Field(default=False)
    allow_duet: bool = Field(default=False)
    allow_stitch: bool = Field(default=False)
    commercial_disclosure: bool = Field(default=False)
    your_brand: bool = Field(default=False)
    branded_content: bool = Field(default=False)

    @field_validator("privacy_level")
    @classmethod
    def privacy_level_known(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        if value not in ALLOWED_TIKTOK_PRIVACY_LEVELS:
            raise ValueError(
                f"privacy_level must be one of {sorted(ALLOWED_TIKTOK_PRIVACY_LEVELS)}"
            )
        return value


class TikTokPublishRequest(BaseModel):
    """Post one generated video directly to TikTok."""
    post_id: str = Field(..., min_length=1, description="Post id for the generated video")
    caption: Optional[str] = Field(default=None, max_length=2200, description="Optional TikTok post caption")
    title: str = Field(..., min_length=1, max_length=90, description="TikTok post title")
    privacy_level: str = Field(..., min_length=1, description="TikTok privacy level chosen from creator_info")
    allow_comment: bool = Field(default=False, description="Allow comments for the TikTok post")
    allow_duet: bool = Field(default=False, description="Allow duet for the TikTok post")
    allow_stitch: bool = Field(default=False, description="Allow stitch for the TikTok post")
    your_brand: bool = Field(default=False, description="TikTok commercial disclosure: creator's own brand")
    branded_content: bool = Field(default=False, description="TikTok commercial disclosure: third-party branded content")
    consent_acknowledged: bool = Field(default=False, description="Creator accepted TikTok posting confirmation")

    @model_validator(mode="after")
    def validate_direct_post_consent(self) -> "TikTokPublishRequest":
        if not self.consent_acknowledged:
            raise ValueError("consent_acknowledged must be true before TikTok posting")
        if self.branded_content and self.privacy_level == "SELF_ONLY":
            raise ValueError("branded_content cannot use SELF_ONLY privacy level")
        return self


class TikTokPublishJobResponse(BaseModel):
    """Public TikTok publish job state."""
    id: str
    connected_account_id: str
    media_asset_id: str
    platform: str
    caption: str
    post_mode: str
    tiktok_publish_id: Optional[str] = None
    status: str
    request_payload_json: Dict[str, Any]
    response_payload_json: Dict[str, Any]
    error_message: str
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None


# Resolve the forward reference used by PostNowRequest on import so Pydantic
# can build the schema consistently in production environments.
PostNowRequest.model_rebuild(_types_namespace={"Optional": Optional, "TikTokPostSettings": TikTokPostSettings})
