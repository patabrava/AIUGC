"""
FLOW-FORGE Batches Schemas
Pydantic models for batch validation.
Per Constitution § II: Validated Boundaries
"""

from __future__ import annotations

from pydantic import BaseModel, Field, validator
from typing import Dict, Optional, List, Any
from datetime import datetime
from app.core.states import BatchState


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
    post_type_counts: PostTypeCounts = Field(..., description="Post type distribution")
    
    @validator('brand')
    def validate_brand(cls, v):
        if not v.strip():
            raise ValueError("Brand name cannot be empty")
        return v.strip()
    
    @validator('post_type_counts')
    def validate_total_posts(cls, v):
        if v.total == 0:
            raise ValueError("Total post count must be greater than 0")
        if v.total > 100:
            raise ValueError("Total post count cannot exceed 100")
        return v


class BatchResponse(BaseModel):
    """Batch response model."""
    id: str
    brand: str
    state: BatchState
    post_type_counts: Dict[str, int]
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
    post_type: str
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
    qa_pass: Optional[bool] = None
    qa_notes: Optional[str] = None
    qa_auto_checks: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BatchDetailResponse(BaseModel):
    """Detailed batch response with posts."""
    id: str
    brand: str
    state: BatchState
    post_type_counts: Dict[str, int]
    created_at: datetime
    updated_at: datetime
    archived: bool
    posts_count: int
    posts_by_state: Dict[str, int]
    posts: List[PostDetail]
    
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
