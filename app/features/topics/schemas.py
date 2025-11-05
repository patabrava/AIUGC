"""
FLOW-FORGE Topics Schemas
Pydantic models for topic discovery and validation.
Per Constitution § II: Validated Boundaries
"""

from pydantic import BaseModel, Field, validator, HttpUrl
from typing import List, Optional, Literal
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


class ResearchAgentSource(BaseModel):
    """Source metadata returned by PROMPT_1."""
    title: str = Field(..., min_length=2, max_length=400, description="Source title")
    url: HttpUrl = Field(..., description="Source URL")


class ResearchAgentItem(BaseModel):
    """Validated result item from PROMPT_1."""
    topic: str = Field(..., min_length=2, max_length=400, description="Chosen topic from pool")
    framework: Literal["PAL", "Testimonial", "Transformation"]
    sources: List[ResearchAgentSource] = Field(..., min_length=1, max_length=2, description="Supporting sources")
    script: str = Field(..., min_length=10, max_length=400, description="Spoken script (≤8s)")
    source_summary: str = Field(..., min_length=35, max_length=500, description="Summary for IG caption")
    estimated_duration_s: int = Field(..., ge=1, le=8, description="Ceiling of word_count/2.6")
    tone: str = Field(..., min_length=5, max_length=120, description="Tone descriptor")
    disclaimer: str = Field(..., min_length=5, max_length=200, description="Compliance disclaimer")

    @validator("script")
    def validate_script_line(cls, v: str) -> str:
        if "\n" in v.strip():
            raise ValueError("Script must be a single spoken line")
        return v.strip()

    def word_count(self) -> int:
        return len(self.script.split())


class ResearchAgentBatch(BaseModel):
    """Wrapper for PROMPT_1 batch output."""
    items: List[ResearchAgentItem] = Field(..., min_length=10, max_length=10)


class DialogScripts(BaseModel):
    """Structured set of dialog scripts from PROMPT_2."""
    problem_agitate_solution: List[str] = Field(..., min_length=5, max_length=5)
    testimonial: List[str] = Field(..., min_length=5, max_length=5)
    transformation: List[str] = Field(..., min_length=5, max_length=5)


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
