"""
Lippe Lift Studio Batches Schemas
Pydantic models for batch validation.
Per Constitution § II: Validated Boundaries
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator, validator
from typing import Dict, Optional, List, Any, Literal
from datetime import datetime
from app.core.states import BatchState
from app.features.shot_production.duration import build_semantic_duration_contract


class PostTypeCounts(BaseModel):
    """Post type distribution for a batch."""
    value: int = Field(ge=0, le=100, description="Number of value posts")
    lifestyle: int = Field(ge=0, le=100, description="Number of lifestyle posts")
    product: int = Field(ge=0, le=100, description="Number of product posts")
    
    @validator('*')
    def validate_counts(cls, v):
        if v < 0:
            raise ValueError("Count cannot be negative")
        return v
    
    @property
    def total(self) -> int:
        """Total number of posts."""
        return self.value + self.lifestyle + self.product


class CreateBatchRequest(BaseModel):
    """Request to create a new batch."""
    brand: str = Field(..., min_length=1, max_length=100, description="Brand name")
    creation_mode: Literal[
        "automated",
        "manual",
        "manual_character_consistency",
        "character_consistency",
        "character_consistency_light",
        "character_consistency_mid",
        "semantic_ugc",
    ] = Field(
        default="automated",
        description="Batch creation mode, including the duration-driven semantic_ugc route.",
    )
    post_type_counts: Optional[PostTypeCounts] = Field(
        default=None,
        description="Post type distribution for automated batches",
    )
    manual_post_count: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description="Number of blank manual drafts to create",
    )
    target_length_tier: Optional[int] = Field(
        default=8,
        ge=8,
        le=32,
        description="Target video duration tier for the batch"
    )
    target_duration_seconds: Optional[int] = Field(
        default=None,
        strict=True,
        description="Exact requested duration for Semantic UGC batches",
    )
    
    @validator('brand')
    def validate_brand(cls, v):
        if not v.strip():
            raise ValueError("Brand name cannot be empty")
        return v.strip()
    
    @validator('post_type_counts', always=True)
    def validate_total_posts(cls, v):
        if v is None:
            return v
        if v.total == 0:
            raise ValueError("Total post count must be greater than 0")
        if v.total > 100:
            raise ValueError("Total post count cannot exceed 100")
        return v

    @validator('manual_post_count', always=True)
    def validate_manual_post_count(cls, v, values):
        if values.get("creation_mode") in {"manual", "manual_character_consistency"} and v is None:
            raise ValueError("Manual post count must be provided for manual batches")
        return v

    @validator('post_type_counts', always=True)
    def validate_creation_mode_contract(cls, v, values):
        creation_mode = values.get("creation_mode") or "automated"
        if creation_mode in {"automated", "character_consistency", "character_consistency_light", "character_consistency_mid", "semantic_ugc"} and v is None:
            raise ValueError("Post type counts are required for automated and character consistency batches")
        return v

    @validator('target_length_tier')
    def validate_target_length_tier(cls, v):
        if v is None:
            return v
        if v not in (8, 16, 32):
            raise ValueError("Target length tier must be one of 8, 16, or 32")
        return v

    @model_validator(mode="after")
    def validate_duration_authority(self):
        if self.creation_mode == "semantic_ugc":
            if self.target_duration_seconds is None:
                raise ValueError("Target duration seconds are required for Semantic UGC batches")
            build_semantic_duration_contract(self.target_duration_seconds)
            self.target_length_tier = None
            return self

        if self.target_duration_seconds is not None:
            raise ValueError("Target duration seconds are only valid for Semantic UGC batches")
        if self.target_length_tier is None:
            raise ValueError("Target length tier is required for legacy batch modes")
        return self


class BatchResponse(BaseModel):
    """Batch response model."""
    id: str
    brand: str
    state: BatchState
    creation_mode: str = "automated"
    character_snapshot: Optional[Dict[str, Any]] = None
    actor_identity_id: Optional[str] = None
    actor_identity_snapshot: Optional[Dict[str, Any]] = None
    scene_plan: Optional[Dict[str, str]] = None
    post_type_counts: Dict[str, int]
    manual_post_count: Optional[int] = None
    target_length_tier: Optional[int] = None
    target_duration_seconds: Optional[int] = None
    video_pipeline_route: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    archived: bool
    
    class Config:
        from_attributes = True


class BatchListResponse(BaseModel):
    """List of batches response."""
    batches: List[BatchResponse]
    total: int


class PostDetail(BaseModel):
    """Post detail model for batch view."""
    id: str
    post_type: Optional[str] = None
    topic_title: str
    topic_rotation: str
    topic_cta: str
    spoken_duration: float
    state: Optional[str] = None
    seed_data: Optional[Dict[str, Any]] = None
    video_prompt_json: Optional[Dict[str, Any]] = None
    video_status: Optional[str] = None
    video_url: Optional[str] = None
    video_metadata: Optional[Dict[str, Any]] = None
    video_operation_id: Optional[str] = None
    video_provider: Optional[str] = None
    scene_reference_image_id: Optional[str] = None
    scene_reference_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    identity_gate_result: Optional[Dict[str, Any]] = None
    qa_pass: Optional[bool] = None
    qa_notes: Optional[str] = None
    qa_auto_checks: Optional[Dict[str, Any]] = None
    scheduled_at: Optional[datetime] = None
    social_networks: Optional[List[str]] = None
    publish_caption: Optional[str] = None
    publish_status: Optional[str] = None
    platform_ids: Optional[Dict[str, str]] = None
    publish_results: Optional[Dict[str, Any]] = None
    blog_enabled: bool = False
    blog_status: str = "disabled"
    blog_content: Optional[Dict[str, Any]] = None
    blog_webflow_item_id: Optional[str] = None
    blog_scheduled_at: Optional[datetime] = None
    blog_published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    tiktok_settings: Optional[Dict[str, Any]] = None


class BatchDetailResponse(BaseModel):
    """Detailed batch response with posts."""
    id: str
    brand: str
    state: BatchState
    creation_mode: str = "automated"
    character_snapshot: Optional[Dict[str, Any]] = None
    actor_identity_id: Optional[str] = None
    actor_identity_snapshot: Optional[Dict[str, Any]] = None
    scene_plan: Optional[Dict[str, str]] = None
    post_type_counts: Dict[str, int]
    manual_post_count: Optional[int] = None
    target_length_tier: Optional[int] = None
    target_duration_seconds: Optional[int] = None
    video_pipeline_route: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    archived: bool
    meta_connection: Optional[Dict[str, Any]] = None
    tiktok_connection: Optional[Dict[str, Any]] = None
    posts_count: int
    posts_by_state: Dict[str, int]
    posts: List[PostDetail]
    tiktok_defaults: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class AdvanceStateRequest(BaseModel):
    """Request to advance batch state."""
    target_state: BatchState = Field(..., description="Target state to transition to")


class DuplicateBatchRequest(BaseModel):
    """Request to duplicate a batch."""
    new_brand: Optional[str] = Field(None, description="New brand name (optional)")


class ArchiveBatchRequest(BaseModel):
    """Request to archive/unarchive a batch."""
    archived: bool = Field(..., description="Archive status")
