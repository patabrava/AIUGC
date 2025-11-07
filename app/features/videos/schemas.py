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
    provider: Literal["veo_3_1", "sora_2", "sora_2_pro"] = Field(
        ..., 
        description="Video generation provider (veo_3_1, sora_2, sora_2_pro)"
    )
    aspect_ratio: Literal["9:16", "16:9"] = Field(
        default="9:16",
        description="Target video aspect ratio"
    )
    resolution: Literal["720p", "1080p"] = Field(
        default="720p",
        description="Output resolution (provider specific constraints apply)"
    )
    seconds: Literal[4, 8, 12] = Field(
        default=8,
        description="Target duration in seconds for the generated clip (Sora supports 4, 8, or 12)."
    )
    size: Optional[Literal["720x1280", "1080x1920", "1280x720", "1920x1080", "1024x1792", "1792x1024"]] = Field(
        default=None,
        description="Provider-specific pixel dimensions override."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "provider": "sora_2_pro",
                "aspect_ratio": "9:16",
                "resolution": "1080p",
                "seconds": 8,
                "size": "1080x1920"
            }
        }


class VideoGenerationResponse(BaseModel):
    """Response from video generation submission."""
    post_id: str = Field(..., description="Post UUID")
    operation_id: str = Field(..., description="Provider operation ID for polling")
    provider: str = Field(..., description="Video generation provider")
    provider_model: Optional[str] = Field(
        default=None,
        description="Underlying model identifier returned by the provider"
    )
    status: Literal["submitted", "processing", "queued"] = Field(
        ..., description="Current status"
    )
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
    status: Literal[
        "pending",
        "queued",
        "submitted",
        "processing",
        "completed",
        "failed"
    ] = Field(
        ..., 
        description="Current video generation status"
    )
    video_url: Optional[str] = Field(None, description="ImageKit URL of generated video")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    metadata: Optional[dict] = Field(None, description="Additional metadata")


class BatchVideoGenerationRequest(BaseModel):
    """Request to generate videos for all posts in a batch."""
    provider: Literal["veo_3_1", "sora_2", "sora_2_pro"] = Field(
        ..., 
        description="Video generation provider for all posts"
    )
    aspect_ratio: Literal["9:16", "16:9"] = Field(
        default="9:16",
        description="Target video aspect ratio"
    )
    resolution: Literal["720p", "1080p"] = Field(
        default="720p",
        description="Output resolution"
    )
    seconds: Literal[4, 8, 12] = Field(
        default=8,
        description="Target duration in seconds for generated clips (Sora supports 4, 8, or 12)"
    )
    size: Optional[Literal["720x1280", "1080x1920", "1280x720", "1920x1080", "1024x1792", "1792x1024"]] = Field(
        default=None,
        description="Provider-specific pixel dimensions override"
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
    provider_model: Optional[str] = Field(
        default=None,
        description="Underlying provider model identifier if uniform"
    )
    seconds: Optional[int] = Field(
        default=None,
        description="Target duration applied to submissions"
    )
    size: Optional[str] = Field(
        default=None,
        description="Pixel dimensions applied to submissions"
    )
