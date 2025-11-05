"""
FLOW-FORGE Batches Schemas
Pydantic models for batch validation.
Per Constitution § II: Validated Boundaries
"""

from pydantic import BaseModel, Field, validator
from typing import Dict, Optional, List
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
