"""
FLOW-FORGE Topics Schemas
Pydantic models for topic discovery and validation.
Per Constitution § II: Validated Boundaries
"""

from pydantic import BaseModel, Field, validator, HttpUrl
from typing import Any, Dict, List, Optional, Literal
from decimal import Decimal


class TopicData(BaseModel):
    """Topic data extracted from research."""
    title: str = Field(..., min_length=1, max_length=200, description="Topic title")
    rotation: str = Field(..., min_length=1, max_length=500, description="Rotation/hook text")
    cta: str = Field(..., min_length=1, max_length=200, description="Call to action")
    spoken_duration: Decimal = Field(..., ge=0, le=6, description="Spoken duration in seconds (≤6s)")
    
    @validator('title', 'rotation', 'cta')
    def validate_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()
    
    @validator('spoken_duration')
    def validate_duration(cls, v):
        if v > 6:
            raise ValueError("Spoken duration must be ≤6 seconds")
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


class ResearchLaneCandidate(BaseModel):
    """Structured lane candidate returned by the dossier prompt."""
    lane_key: str = Field(..., min_length=1, max_length=80)
    lane_family: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=2, max_length=240)
    angle: str = Field(..., min_length=2, max_length=400)
    priority: int = Field(..., ge=1, le=20)
    framework_candidates: List[str] = Field(default_factory=list, max_length=4)
    source_summary: str = Field(..., min_length=10, max_length=500)
    facts: List[str] = Field(default_factory=list, max_length=10)
    risk_notes: List[str] = Field(default_factory=list, max_length=5)
    disclaimer: str = Field(..., min_length=5, max_length=200)
    lane_overlap_warnings: List[str] = Field(default_factory=list, max_length=5)
    suggested_length_tiers: List[int] = Field(default_factory=list, max_length=3)


class ResearchDossier(BaseModel):
    """Durable research dossier used by the topic bank and hub."""
    cluster_id: str = Field(..., min_length=2, max_length=120)
    topic: str = Field(..., min_length=2, max_length=240)
    anchor_topic: str = Field(..., min_length=2, max_length=240)
    seed_topic: str = Field(..., min_length=2, max_length=240)
    cluster_summary: str = Field(..., min_length=20, max_length=1200)
    framework_candidates: List[str] = Field(..., min_length=1, max_length=4)
    sources: List[ResearchAgentSource] = Field(default_factory=list, min_length=1, max_length=8)
    source_summary: str = Field(..., min_length=35, max_length=1200)
    facts: List[str] = Field(default_factory=list, min_length=1, max_length=20)
    angle_options: List[str] = Field(default_factory=list, min_length=1, max_length=10)
    risk_notes: List[str] = Field(default_factory=list, min_length=1, max_length=10)
    disclaimer: str = Field(..., min_length=5, max_length=240)
    lane_candidates: List[ResearchLaneCandidate] = Field(default_factory=list, min_length=1, max_length=12)

    @validator("framework_candidates", "facts", "angle_options", "risk_notes", each_item=False)
    def validate_non_empty_lists(cls, v):
        if not v:
            raise ValueError("At least one entry is required")
        return v


class ResearchAgentItem(BaseModel):
    """Validated result item from PROMPT_1."""
    topic: str = Field(..., min_length=2, max_length=400, description="Chosen topic from pool")
    framework: Literal["PAL", "Testimonial", "Transformation"]
    sources: List[ResearchAgentSource] = Field(..., min_length=1, max_length=2, description="Supporting sources")
    script: str = Field(..., min_length=10, max_length=400, description="Spoken script (≤6s)")
    source_summary: str = Field(..., min_length=35, max_length=500, description="Summary for IG caption")
    estimated_duration_s: int = Field(..., ge=1, le=6, description="Ceiling of word_count/2.6")
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
    items: List[ResearchAgentItem] = Field(..., min_length=1, max_length=10)


class DialogScripts(BaseModel):
    """Structured set of dialog scripts from PROMPT_2."""
    problem_agitate_solution: List[str] = Field(..., min_length=1, max_length=5)
    testimonial: List[str] = Field(..., min_length=1, max_length=5)
    transformation: List[str] = Field(..., min_length=1, max_length=5)
    description: Optional[str] = Field(None, min_length=35, max_length=500, description="Social media description (35-80 words)")

    @validator("problem_agitate_solution", "testimonial", "transformation", each_item=True)
    def validate_complete_script(cls, v: str) -> str:
        script = v.strip()
        if not script:
            raise ValueError("Script cannot be empty")
        if "\n" in script:
            raise ValueError("Script must be a single spoken line")
        if script[-1] not in ".!?":
            raise ValueError("Script must end with terminal punctuation")
        return script

    @validator("description")
    def validate_description_present(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            raise ValueError("Description is required")
        return v.strip()


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


class TopicResearchRunRequest(BaseModel):
    """Request to launch a new topic research run."""
    topic_registry_id: str = Field(..., min_length=1)
    target_length_tier: Optional[int] = Field(default=None, ge=8, le=32)
    trigger_source: str = Field(default="manual", min_length=1, max_length=80)
    post_type: Optional[str] = Field(default=None, min_length=1, max_length=30)


class TopicResearchRunResponse(BaseModel):
    """Persisted topic research run row."""
    id: str
    trigger_source: str
    status: str
    requested_counts: Dict[str, Any]
    target_length_tier: Optional[int] = None
    result_summary: Dict[str, Any]
    error_message: str
    created_at: str
    updated_at: str


class TopicScriptVariant(BaseModel):
    """Script variant stored in the topic bank."""
    id: str
    topic_registry_id: str
    post_type: Optional[str] = None
    title: str
    script: str
    target_length_tier: int
    bucket: Optional[str] = None
    hook_style: Optional[str] = None
    framework: Optional[str] = None
    tone: Optional[str] = None
    estimated_duration_s: Optional[int] = None
    lane_key: Optional[str] = None
    lane_family: Optional[str] = None
    cluster_id: Optional[str] = None
    anchor_topic: Optional[str] = None
    disclaimer: Optional[str] = None
    source_summary: Optional[str] = None
    primary_source_url: Optional[str] = None
    primary_source_title: Optional[str] = None
    source_urls: List[Dict[str, Any]] = Field(default_factory=list)
    use_count: int = 0
    last_used_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DeduplicationResult(BaseModel):
    """Result of deduplication check."""
    is_duplicate: bool
    similarity_score: float
    matched_topic_id: Optional[str] = None
    reason: Optional[str] = None


# JSON Schema for PROMPT_1 Structured Outputs
# Per Constitution § XII: Agent Prompt Discipline - Schema-first validation
PROMPT1_JSON_SCHEMA = {
    "name": "research_topics",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Topic title, max 10 words"
                        },
                        "framework": {
                            "type": "string",
                            "enum": ["PAL", "Testimonial", "Transformation"]
                        },
                        "sources": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 2,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "url": {"type": "string"}
                                },
                                "required": ["title", "url"],
                                "additionalProperties": False
                            }
                        },
                        "script": {
                            "type": "string",
                            "description": "MUST be EXACTLY 12-15 words, one sentence"
                        },
                        "source_summary": {
                            "type": "string",
                            "description": "35-80 words with 3 hashtags at end"
                        },
                        "estimated_duration_s": {
                            "type": "integer",
                            "minimum": 5,
                            "maximum": 6
                        },
                        "tone": {"type": "string"},
                        "disclaimer": {"type": "string"}
                    },
                    "required": [
                        "topic", "framework", "sources", "script",
                        "source_summary", "estimated_duration_s", "tone", "disclaimer"
                    ],
                    "additionalProperties": False
                }
            }
        },
        "required": ["items"],
        "additionalProperties": False
    }
}


# JSON Schema for PROMPT_2 Structured Outputs
PROMPT2_JSON_SCHEMA = {
    "name": "dialog_scripts",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "problem_agitate_solution": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {"type": "string"}
            },
            "testimonial": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {"type": "string"}
            },
            "transformation": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {"type": "string"}
            },
            "description": {
                "type": "string",
                "description": "35-80 words with 3 hashtags"
            }
        },
        "required": ["problem_agitate_solution", "testimonial", "transformation", "description"],
        "additionalProperties": False
    }
}


PROMPT1_RESEARCH_JSON_SCHEMA = {
    "name": "topic_research_dossier",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "cluster_id": {"type": "string"},
            "topic": {"type": "string"},
            "anchor_topic": {"type": "string"},
            "seed_topic": {"type": "string"},
            "cluster_summary": {"type": "string"},
            "framework_candidates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {"type": "string"},
            },
            "sources": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                    },
                    "required": ["title", "url"],
                    "additionalProperties": False,
                },
            },
            "source_summary": {"type": "string"},
            "facts": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {"type": "string"},
            },
            "angle_options": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {"type": "string"},
            },
            "risk_notes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {"type": "string"},
            },
            "disclaimer": {"type": "string"},
            "lane_candidates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "lane_key": {"type": "string"},
                        "lane_family": {"type": "string"},
                        "title": {"type": "string"},
                        "angle": {"type": "string"},
                        "priority": {"type": "integer"},
                        "framework_candidates": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 4,
                            "items": {"type": "string"},
                        },
                        "source_summary": {"type": "string"},
                        "facts": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 10,
                            "items": {"type": "string"},
                        },
                        "risk_notes": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 5,
                            "items": {"type": "string"},
                        },
                        "disclaimer": {"type": "string"},
                        "lane_overlap_warnings": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "suggested_length_tiers": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                    },
                    "required": [
                        "lane_key",
                        "lane_family",
                        "title",
                        "angle",
                        "priority",
                        "framework_candidates",
                        "source_summary",
                        "facts",
                        "risk_notes",
                        "disclaimer",
                        "lane_overlap_warnings",
                        "suggested_length_tiers",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "cluster_id",
            "topic",
            "anchor_topic",
            "seed_topic",
            "cluster_summary",
            "framework_candidates",
            "sources",
            "source_summary",
            "facts",
            "angle_options",
            "risk_notes",
            "disclaimer",
            "lane_candidates",
        ],
        "additionalProperties": False,
    },
}
