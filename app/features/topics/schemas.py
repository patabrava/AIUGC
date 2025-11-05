"""
FLOW-FORGE Topics Schemas
Pydantic models for topic discovery and validation.
Per Constitution § II: Validated Boundaries
"""

from pydantic import BaseModel, Field, validator
from typing import List, Optional
from decimal import Decimal


class TopicData(BaseModel):
    """Topic data extracted from research."""
    title: str = Field(..., min_length=1, max_length=200, description="Topic title")
    rotation: str = Field(..., min_length=1, max_length=500, description="Rotation/hook text")
    cta: str = Field(..., min_length=1, max_length=200, description="Call to action")
    spoken_duration: Decimal = Field(..., ge=0, le=8, description="Spoken duration in seconds (≤8s)")
    
    @validator('title', 'rotation', 'cta')
    def validate_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()
    
    @validator('spoken_duration')
    def validate_duration(cls, v):
        if v > 8:
            raise ValueError("Spoken duration must be ≤8 seconds")
        return v


class SeedData(BaseModel):
    """Seed data extracted from topic (factual only)."""
    facts: List[str] = Field(..., description="Factual information extracted from topic")
    source_context: Optional[str] = Field(None, description="Source context for reference")
    
    @validator('facts')
    def validate_facts(cls, v):
        if not v:
            raise ValueError("At least one fact is required")
        return v


class DiscoverTopicsRequest(BaseModel):
    """Request to discover topics for a batch."""
    batch_id: str = Field(..., description="Batch ID to discover topics for")
    count: int = Field(default=10, ge=1, le=100, description="Number of topics to discover")


class TopicResponse(BaseModel):
    """Topic response model."""
    id: str
    title: str
    rotation: str
    cta: str
    first_seen_at: str
    last_used_at: str
    use_count: int


class TopicListResponse(BaseModel):
    """List of topics response."""
    topics: List[TopicResponse]
    total: int


class DeduplicationResult(BaseModel):
    """Result of deduplication check."""
    is_duplicate: bool
    similarity_score: float
    matched_topic_id: Optional[str] = None
    reason: Optional[str] = None
