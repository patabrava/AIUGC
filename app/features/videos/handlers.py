"""
FLOW-FORGE Video Generation Handlers
FastAPI route handlers for video generation operations.
Per Constitution § V: Locality & Vertical Slices
Per Canon § 3.2: S5_PROMPTS_BUILT → S6_QA transition
"""

from fastapi import APIRouter, HTTPException, status, Request
from typing import Dict, Any

from app.adapters.supabase_client import get_supabase
from app.adapters.veo_client import get_veo_client
from app.core.errors import FlowForgeException, SuccessResponse
from app.core.logging import get_logger
from app.features.videos.schemas import (
    VideoGenerationRequest,
    VideoGenerationResponse,
    VideoStatusResponse,
    BatchVideoGenerationRequest,
    BatchVideoGenerationResponse
)

logger = get_logger(__name__)
router = APIRouter(prefix="/videos", tags=["videos"])


@router.post("/{post_id}/generate", response_model=SuccessResponse)
async def generate_video(post_id: str, request: VideoGenerationRequest):
    """
    Submit video generation request for a post.
    Transitions post from S5_PROMPTS_BUILT to S6_QA (when complete).
    
    Per Canon § 3.2: S5_PROMPTS_BUILT → S6_QA
    Per Constitution § II: Schema-validate at edges
    Per Constitution § IX: Boundary logging with correlation IDs
    
    Args:
        post_id: UUID of the post
        request: Video generation parameters (provider, aspect_ratio, resolution)
        
    Returns:
        SuccessResponse with VideoGenerationResponse data
        
    Raises:
        HTTPException: If post not found or video_prompt_json missing
    """
    correlation_id = f"gen_video_{post_id}"
    
    try:
        supabase = get_supabase().client
        
        # Fetch post with video_prompt_json
        response = supabase.table("posts").select("*").eq("id", post_id).execute()
        
        if not response.data:
            raise FlowForgeException(
                code="not_found",
                message=f"Post {post_id} not found",
                details={"post_id": post_id}
            )
        
        post = response.data[0]
        video_prompt = post.get("video_prompt_json")
        
        if not video_prompt:
            raise FlowForgeException(
                code="validation_error",
                message="Post missing video_prompt_json. Run build-prompt first.",
                details={"post_id": post_id}
            )
        
        # Build VEO-compatible prompt text from video_prompt_json
        prompt_text = _build_veo_prompt_text(video_prompt)
        
        # Validate resolution/aspect-ratio combination per VEO specs
        if request.resolution == "1080p" and request.aspect_ratio != "16:9":
            raise FlowForgeException(
                code="validation_error",
                message="1080p resolution requires 16:9 aspect ratio",
                details={
                    "post_id": post_id,
                    "aspect_ratio": request.aspect_ratio,
                    "resolution": request.resolution
                }
            )

        # Submit to provider
        if request.provider == "veo_3_1":
            veo_client = get_veo_client()
            result = veo_client.submit_video_generation(
                prompt=prompt_text,
                correlation_id=correlation_id,
                aspect_ratio=request.aspect_ratio,
                resolution=request.resolution
            )
            operation_id = result["operation_id"]
        else:
            # Future: Sora integration
            raise FlowForgeException(
                code="validation_error",
                message="Sora provider not yet implemented",
                details={"provider": request.provider}
            )
        
        # Update post with operation details
        existing_metadata = post.get("video_metadata") or {}
        submission_metadata = {
            **existing_metadata,
            "requested_aspect_ratio": request.aspect_ratio,
            "requested_resolution": request.resolution
        }

        supabase.table("posts").update({
            "video_provider": request.provider,
            "video_format": request.aspect_ratio,
            "video_operation_id": operation_id,
            "video_status": "submitted",
            "video_metadata": submission_metadata
        }).eq("id", post_id).execute()
        
        logger.info(
            "video_generation_submitted",
            post_id=post_id,
            correlation_id=correlation_id,
            provider=request.provider,
            aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            operation_id=operation_id
        )
        
        return SuccessResponse(
            data=VideoGenerationResponse(
                post_id=post_id,
                operation_id=operation_id,
                provider=request.provider,
                status="submitted",
                estimated_duration_seconds=180,  # VEO typically 2-3 minutes
                aspect_ratio=request.aspect_ratio,
                resolution=request.resolution
            ).model_dump()
        )
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception(
            "video_generation_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit video generation"
        )


@router.get("/{post_id}/status", response_model=SuccessResponse)
async def get_video_status(post_id: str):
    """
    Check video generation status for a post.
    Used by UI polling to track progress.
    
    Per Constitution § IX: Observable Implementation
    
    Args:
        post_id: UUID of the post
        
    Returns:
        SuccessResponse with VideoStatusResponse data
        
    Raises:
        HTTPException: If post not found
    """
    correlation_id = f"status_{post_id}"
    
    try:
        supabase = get_supabase().client
        response = supabase.table("posts").select("*").eq("id", post_id).execute()
        
        if not response.data:
            raise FlowForgeException(
                code="not_found",
                message=f"Post {post_id} not found",
                details={"post_id": post_id}
            )
        
        post = response.data[0]
        
        logger.info(
            "video_status_checked",
            post_id=post_id,
            correlation_id=correlation_id,
            status=post.get("video_status", "pending")
        )
        
        return SuccessResponse(
            data=VideoStatusResponse(
                post_id=post_id,
                operation_id=post.get("video_operation_id"),
                status=post.get("video_status", "pending"),
                video_url=post.get("video_url"),
                metadata=post.get("video_metadata")
            ).model_dump()
        )
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception(
            "video_status_check_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check video status"
        )


@router.post("/batch/{batch_id}/generate-all", response_model=SuccessResponse)
async def generate_all_videos(batch_id: str, request: BatchVideoGenerationRequest):
    """
    Generate videos for all posts in a batch.
    Submits all posts with video_prompt_json to video generation provider.
    
    Per Constitution § XIII: Idempotent operations
    Per Constitution § IX: Structured logging
    
    Args:
        batch_id: UUID of the batch
        request: Batch video generation parameters (provider, aspect_ratio, resolution)
        
    Returns:
        SuccessResponse with BatchVideoGenerationResponse data
        
    Raises:
        HTTPException: If batch not found or submission fails
    """
    correlation_id = f"gen_all_{batch_id}"
    
    try:
        supabase = get_supabase().client
        
        # Fetch all posts in batch with video_prompt_json
        response = supabase.table("posts").select("*").eq("batch_id", batch_id).execute()
        posts = response.data
        
        if not posts:
            raise FlowForgeException(
                code="not_found",
                message=f"No posts found for batch {batch_id}",
                details={"batch_id": batch_id}
            )
        
        submitted_count = 0
        skipped_count = 0
        submitted_post_ids = []
        
        for post in posts:
            post_id = post["id"]
            video_prompt = post.get("video_prompt_json")
            
            # Skip posts without prompts or already submitted
            if not video_prompt:
                logger.warning(
                    "post_skipped_no_prompt",
                    post_id=post_id,
                    batch_id=batch_id
                )
                skipped_count += 1
                continue
            
            if post.get("video_status") in ["submitted", "processing", "completed"]:
                logger.info(
                    "post_skipped_already_submitted",
                    post_id=post_id,
                    batch_id=batch_id,
                    status=post.get("video_status")
                )
                skipped_count += 1
                continue
            
            # Build prompt and submit
            try:
                prompt_text = _build_veo_prompt_text(video_prompt)
                
                if request.provider == "veo_3_1":
                    if request.resolution == "1080p" and request.aspect_ratio != "16:9":
                        logger.error(
                            "batch_invalid_resolution_configuration",
                            post_id=post_id,
                            aspect_ratio=request.aspect_ratio,
                            resolution=request.resolution
                        )
                        skipped_count += 1
                        continue

                    veo_client = get_veo_client()
                    result = veo_client.submit_video_generation(
                        prompt=prompt_text,
                        correlation_id=f"{correlation_id}_{post_id}",
                        aspect_ratio=request.aspect_ratio,
                        resolution=request.resolution
                    )
                    operation_id = result["operation_id"]
                else:
                    logger.warning(
                        "unsupported_provider",
                        post_id=post_id,
                        provider=request.provider
                    )
                    skipped_count += 1
                    continue
                
                # Update post
                existing_metadata = post.get("video_metadata") or {}
                submission_metadata = {
                    **existing_metadata,
                    "requested_aspect_ratio": request.aspect_ratio,
                    "requested_resolution": request.resolution
                }

                supabase.table("posts").update({
                    "video_provider": request.provider,
                    "video_format": request.aspect_ratio,
                    "video_operation_id": operation_id,
                    "video_status": "submitted",
                    "video_metadata": submission_metadata
                }).eq("id", post_id).execute()
                
                submitted_count += 1
                submitted_post_ids.append(post_id)
                
                logger.info(
                    "batch_video_submitted",
                    post_id=post_id,
                    batch_id=batch_id,
                    operation_id=operation_id
                )
                
            except Exception as e:
                logger.exception(
                    "batch_video_submission_failed",
                    post_id=post_id,
                    batch_id=batch_id,
                    error=str(e)
                )
                skipped_count += 1
        
        logger.info(
            "batch_videos_submitted",
            batch_id=batch_id,
            correlation_id=correlation_id,
            submitted_count=submitted_count,
            skipped_count=skipped_count,
            provider=request.provider
        )
        
        return SuccessResponse(
            data=BatchVideoGenerationResponse(
                batch_id=batch_id,
                submitted_count=submitted_count,
                skipped_count=skipped_count,
                provider=request.provider,
                aspect_ratio=request.aspect_ratio,
                resolution=request.resolution,
                post_ids=submitted_post_ids
            ).model_dump()
        )
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception(
            "batch_video_generation_failed",
            batch_id=batch_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate batch videos"
        )


def _build_veo_prompt_text(video_prompt: Dict[str, Any]) -> str:
    """
    Build VEO-compatible prompt text from video_prompt_json.
    Extracts and concatenates key fields from Phase 3 prompt structure.
    
    Per Constitution § V: Co-locate feature logic
    
    Args:
        video_prompt: Video prompt JSON from Phase 3
        
    Returns:
        Formatted prompt text for VEO API
    """
    parts = []
    
    # Extract character description
    if "character" in video_prompt:
        parts.append(video_prompt["character"])
    
    # Extract action/scene description
    if "action" in video_prompt:
        parts.append(video_prompt["action"])
    
    # Extract style modifiers
    if "style" in video_prompt:
        parts.append(video_prompt["style"])
    
    # Extract camera positioning
    if "camera" in video_prompt:
        parts.append(video_prompt["camera"])
    
    # Extract ambiance
    if "ambiance" in video_prompt:
        parts.append(video_prompt["ambiance"])
    
    # Join all parts with proper spacing
    prompt_text = " ".join(filter(None, parts))
    
    logger.debug(
        "veo_prompt_built",
        prompt_length=len(prompt_text),
        parts_count=len(parts)
    )
    
    return prompt_text
