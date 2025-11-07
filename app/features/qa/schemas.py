"""
FLOW-FORGE QA Schemas
Pydantic models for QA operations.
Per Constitution § II: Validated Boundaries
Per Canon § 7.2: Video Validation Rules
"""

from typing import Optional
from pydantic import BaseModel, Field


class AutoQAChecks(BaseModel):
    """
    Automated QA check results.
    Per Canon § 7.2: Duration 8s (±0.5s), Resolution 1080p min, Aspect ratio 9:16
    """
    duration_valid: bool = Field(..., description="Duration within 7.5s - 8.5s range")
    duration_actual: Optional[float] = Field(None, description="Actual duration in seconds")
    duration_expected: float = Field(8.0, description="Expected duration in seconds")
    
    resolution_valid: bool = Field(..., description="Resolution meets minimum 1080p (720 height)")
    resolution_actual: Optional[str] = Field(None, description="Actual resolution (e.g., '1080x1920')")
    resolution_expected: str = Field("720x1280 minimum", description="Expected resolution")
    
    aspect_ratio_valid: bool = Field(..., description="Aspect ratio matches requested format")
    aspect_ratio_actual: Optional[str] = Field(None, description="Actual aspect ratio")
    aspect_ratio_expected: Optional[str] = Field(None, description="Expected aspect ratio")
    
    file_accessible: bool = Field(..., description="Video file is accessible at URL")
    file_size_bytes: Optional[int] = Field(None, description="Video file size in bytes")
    
    overall_pass: bool = Field(..., description="All checks passed")
    checked_at: str = Field(..., description="ISO timestamp when checks were run")
    
    class Config:
        json_schema_extra = {
            "example": {
                "duration_valid": True,
                "duration_actual": 8.1,
                "duration_expected": 8.0,
                "resolution_valid": True,
                "resolution_actual": "720x1280",
                "resolution_expected": "720x1280 minimum",
                "aspect_ratio_valid": True,
                "aspect_ratio_actual": "9:16",
                "aspect_ratio_expected": "9:16",
                "file_accessible": True,
                "file_size_bytes": 2457600,
                "overall_pass": True,
                "checked_at": "2025-11-07T12:00:00Z"
            }
        }


class QAApprovalRequest(BaseModel):
    """Request to approve or reject a post's QA."""
    approved: bool = Field(..., description="Whether post passes QA")
    notes: Optional[str] = Field(None, max_length=500, description="QA review notes")
    
    class Config:
        json_schema_extra = {
            "example": {
                "approved": True,
                "notes": "Video quality excellent, audio clear"
            }
        }


class QAApprovalResponse(BaseModel):
    """Response from QA approval."""
    post_id: str
    qa_pass: bool
    qa_notes: Optional[str]
    qa_auto_checks: Optional[AutoQAChecks]
    
    class Config:
        json_schema_extra = {
            "example": {
                "post_id": "123e4567-e89b-12d3-a456-426614174000",
                "qa_pass": True,
                "qa_notes": "Approved",
                "qa_auto_checks": AutoQAChecks.Config.json_schema_extra["example"]
            }
        }


class BatchQAStatusResponse(BaseModel):
    """Batch QA status summary."""
    batch_id: str
    total_posts: int
    posts_with_videos: int
    posts_qa_passed: int
    posts_qa_pending: int
    all_passed: bool
    can_advance_to_publish: bool
    
    class Config:
        json_schema_extra = {
            "example": {
                "batch_id": "123e4567-e89b-12d3-a456-426614174000",
                "total_posts": 10,
                "posts_with_videos": 10,
                "posts_qa_passed": 8,
                "posts_qa_pending": 2,
                "all_passed": False,
                "can_advance_to_publish": False
            }
        }
