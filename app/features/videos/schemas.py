"""
FLOW-FORGE Video Generation Schemas
Pydantic models for video generation requests and responses.
Per Constitution § II: Validated Boundaries
Per Canon § 5: API Contracts
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal


class VideoGenerationRequest(BaseModel):
    """
    Request to generate video for a post.
    Per Canon § 3.2: S5_PROMPTS_BUILT → S6_QA transition
    """
    provider: Literal["veo_3_1", "sora_2"] = Field(
        ..., 
        description="Video generation provider (veo_3_1 or sora_2)"
    )
    aspect_ratio: Literal["9:16", "16:9"] = Field(
        default="9:16",
        description="Target video aspect ratio supported by VEO"
    )
    resolution: Literal["720p", "1080p"] = Field(
        default="720p",
        description="Output resolution supported by VEO (1080p requires 16:9)"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "provider": "veo_3_1",
                "aspect_ratio": "9:16",
                "resolution": "720p"
            }
        }


class VideoGenerationResponse(BaseModel):
    """Response from video generation submission."""
    post_id: str = Field(..., description="Post UUID")
    operation_id: str = Field(..., description="Provider operation ID for polling")
    provider: str = Field(..., description="Video generation provider")
    status: Literal["submitted", "processing"] = Field(..., description="Current status")
    estimated_duration_seconds: Optional[int] = Field(
        None, 
        description="Estimated time to completion in seconds"
    )
    aspect_ratio: Literal["9:16", "16:9"] = Field(
        ..., description="Target aspect ratio for generated video"
    )
    resolution: Literal["720p", "1080p"] = Field(
        ..., description="Requested output resolution"
    )


class VideoStatusResponse(BaseModel):
    """Response for video generation status check."""
    post_id: str = Field(..., description="Post UUID")
    operation_id: Optional[str] = Field(None, description="Provider operation ID")
    status: Literal["pending", "submitted", "processing", "completed", "failed"] = Field(
        ..., 
        description="Current video generation status"
    )
    video_url: Optional[str] = Field(None, description="ImageKit URL of generated video")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    metadata: Optional[dict] = Field(None, description="Additional metadata")


class BatchVideoGenerationRequest(BaseModel):
    """Request to generate videos for all posts in a batch."""
    provider: Literal["veo_3_1", "sora_2"] = Field(
        ..., 
        description="Video generation provider for all posts"
    )
    aspect_ratio: Literal["9:16", "16:9"] = Field(
        default="9:16",
        description="Target video aspect ratio supported by VEO"
    )
    resolution: Literal["720p", "1080p"] = Field(
        default="720p",
        description="Output resolution supported by VEO"
    )


class BatchVideoGenerationResponse(BaseModel):
    """Response from batch video generation submission."""
    batch_id: str = Field(..., description="Batch UUID")
    submitted_count: int = Field(..., description="Number of videos submitted")
    skipped_count: int = Field(..., description="Number of posts skipped")
    provider: str = Field(..., description="Video generation provider")
    aspect_ratio: str = Field(..., description="Video aspect ratio")
    resolution: str = Field(..., description="Output resolution")
    post_ids: list[str] = Field(..., description="List of post IDs submitted")
