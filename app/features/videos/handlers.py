"""
FLOW-FORGE Video Generation Handlers
FastAPI route handlers for video generation operations.
Per Constitution § V: Locality & Vertical Slices
Per Canon § 3.2: S5_PROMPTS_BUILT → S6_QA transition
"""

import json

from fastapi import APIRouter, HTTPException, Request, status
from typing import Dict, Any, Optional
import json
import os
from datetime import datetime

from pydantic import ValidationError as PydanticValidationError
import httpx

from app.adapters.supabase_client import get_supabase
from app.adapters.veo_client import get_veo_client
from app.adapters.sora_client import get_sora_client
from app.core.errors import FlowForgeException, SuccessResponse, ValidationError, ErrorCode
from app.core.logging import get_logger
from app.features.posts.prompt_text import build_full_prompt_text
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
                code=ErrorCode.NOT_FOUND,
                message=f"Post {post_id} not found",
                details={"post_id": post_id}
            )
        
        post = response.data[0]
        video_prompt = post.get("video_prompt_json")
        
        if not video_prompt:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Post missing video_prompt_json. Run build-prompt first.",
                details={"post_id": post_id}
            )
        
        prompt_text = _build_provider_prompt_text(video_prompt, request.provider)

        submission_result = _submit_video_request(
            provider=request.provider,
            prompt_text=prompt_text,
            aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            seconds=request.seconds,
            size=request.size,
            correlation_id=correlation_id,
        )

        operation_id = submission_result["operation_id"]
        provider_model = submission_result.get("provider_model")
        requested_size = submission_result.get("requested_size")

        existing_metadata = post.get("video_metadata") or {}
        submission_metadata = {
            **existing_metadata,
            "requested_aspect_ratio": request.aspect_ratio,
            "requested_resolution": request.resolution,
            "requested_seconds": request.seconds,
            "requested_size": requested_size,
        }
        if provider_model:
            submission_metadata["provider_model"] = provider_model
        if submission_result.get("provider_metadata"):
            submission_metadata["provider_metadata"] = submission_result["provider_metadata"]

        # Normalize provider status to DB-compatible values
        provider_status = submission_result.get("status", "submitted")
        db_status = "submitted" if provider_status == "queued" else provider_status

        # CRITICAL: Log operation_id before DB update to enable recovery if update fails
        logger.warning(
            "video_operation_id_paid_request",
            post_id=post_id,
            operation_id=operation_id,
            provider=request.provider,
            correlation_id=correlation_id,
            message="PAID VIDEO SUBMITTED - Operation ID logged for recovery"
        )

        try:
            supabase.table("posts").update({
                "video_provider": request.provider,
                "video_format": request.aspect_ratio,
                "video_operation_id": operation_id,
                "video_status": db_status,
                "video_metadata": submission_metadata
            }).eq("id", post_id).execute()
        except Exception as db_error:
            logger.error(
                "video_db_update_failed_but_video_submitted",
                post_id=post_id,
                operation_id=operation_id,
                provider=request.provider,
                correlation_id=correlation_id,
                error=str(db_error),
                message="DATABASE UPDATE FAILED - Video is still processing at provider. Use operation_id to recover."
            )
            # Write to fallback recovery file
            _write_recovery_record(post_id, operation_id, request.provider, correlation_id)
            raise

        logger.info(
            "video_generation_submitted",
            post_id=post_id,
            correlation_id=correlation_id,
            provider=request.provider,
            provider_model=provider_model,
            aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            seconds=request.seconds,
            size=requested_size,
            operation_id=operation_id
        )

        return SuccessResponse(
            data=VideoGenerationResponse(
                post_id=post_id,
                operation_id=operation_id,
                provider=request.provider,
                provider_model=provider_model,
                status=submission_result.get("status", "submitted"),
                estimated_duration_seconds=submission_result.get("estimated_duration_seconds"),
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
                code=ErrorCode.NOT_FOUND,
                message=f"No posts found for batch {batch_id}",
                details={"batch_id": batch_id}
            )
        
        submitted_count = 0
        skipped_count = 0
        submitted_post_ids = []
        
        last_provider_model: Optional[str] = None

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
                prompt_text = _build_provider_prompt_text(video_prompt, request.provider)

                submission_result = _submit_video_request(
                    provider=request.provider,
                    prompt_text=prompt_text,
                    aspect_ratio=request.aspect_ratio,
                    resolution=request.resolution,
                    seconds=request.seconds,
                    size=request.size,
                    correlation_id=f"{correlation_id}_{post_id}",
                )
                operation_id = submission_result["operation_id"]
                provider_model = submission_result.get("provider_model")
                requested_size = submission_result.get("requested_size")
                
                # Update post
                existing_metadata = post.get("video_metadata") or {}
                submission_metadata = {
                    **existing_metadata,
                    "requested_aspect_ratio": request.aspect_ratio,
                    "requested_resolution": request.resolution,
                    "requested_seconds": request.seconds,
                    "requested_size": requested_size,
                }
                if provider_model:
                    submission_metadata["provider_model"] = provider_model
                if submission_result.get("provider_metadata"):
                    submission_metadata["provider_metadata"] = submission_result["provider_metadata"]

                # Normalize provider status to DB-compatible values
                provider_status = submission_result.get("status", "submitted")
                db_status = "submitted" if provider_status == "queued" else provider_status

                # CRITICAL: Log operation_id before DB update to enable recovery if update fails
                logger.warning(
                    "video_operation_id_paid_request",
                    post_id=post_id,
                    operation_id=operation_id,
                    provider=request.provider,
                    correlation_id=correlation_id,
                    message="PAID VIDEO SUBMITTED - Operation ID logged for recovery"
                )

                try:
                    supabase.table("posts").update({
                        "video_provider": request.provider,
                        "video_format": request.aspect_ratio,
                        "video_operation_id": operation_id,
                        "video_status": db_status,
                        "video_metadata": submission_metadata
                    }).eq("id", post_id).execute()
                except Exception as db_error:
                    logger.error(
                        "batch_video_db_update_failed_but_video_submitted",
                        post_id=post_id,
                        operation_id=operation_id,
                        provider=request.provider,
                        batch_id=batch_id,
                        correlation_id=correlation_id,
                        error=str(db_error),
                        message="DATABASE UPDATE FAILED - Video is still processing at provider. Use operation_id to recover."
                    )
                    # Write to fallback recovery file
                    _write_recovery_record(post_id, operation_id, request.provider, correlation_id)
                    # Don't raise in batch mode - continue with other posts
                    skipped_count += 1
                    continue
                
                submitted_count += 1
                submitted_post_ids.append(post_id)
                if provider_model:
                    last_provider_model = provider_model
                
                logger.info(
                    "batch_video_submitted",
                    post_id=post_id,
                    batch_id=batch_id,
                    provider=request.provider,
                    provider_model=provider_model,
                    seconds=request.seconds,
                    size=requested_size,
                    operation_id=operation_id
                )
                
            except FlowForgeException as exc:
                logger.warning(
                    "batch_video_submission_skipped",
                    post_id=post_id,
                    batch_id=batch_id,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details
                )
                skipped_count += 1
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
                post_ids=submitted_post_ids,
                provider_model=last_provider_model,
                seconds=request.seconds,
                size=request.size or _map_size_from_aspect_ratio(request.aspect_ratio, request.resolution)
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
    """Build VEO-compatible prompt text from video_prompt_json."""
    return build_full_prompt_text(video_prompt)


def _write_recovery_record(post_id: str, operation_id: str, provider: str, correlation_id: str) -> None:
    """Write paid video operation_id to recovery file when DB update fails."""
    recovery_dir = "recovery_logs"
    os.makedirs(recovery_dir, exist_ok=True)
    
    timestamp = datetime.utcnow().isoformat()
    recovery_file = os.path.join(recovery_dir, f"video_recovery_{datetime.utcnow().strftime('%Y%m%d')}.jsonl")
    
    record = {
        "timestamp": timestamp,
        "post_id": post_id,
        "operation_id": operation_id,
        "provider": provider,
        "correlation_id": correlation_id,
        "status": "db_update_failed",
        "message": "Video submitted to provider but database update failed. Video is processing and can be recovered."
    }
    
    try:
        with open(recovery_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        logger.info(
            "recovery_record_written",
            post_id=post_id,
            operation_id=operation_id,
            recovery_file=recovery_file
        )
    except Exception as e:
        logger.error(
            "recovery_record_write_failed",
            post_id=post_id,
            operation_id=operation_id,
            error=str(e)
        )


def _build_provider_prompt_text(video_prompt: Dict[str, Any], provider: str) -> str:
    """Build provider-specific prompt text."""
    if provider == "veo_3_1":
        return _build_veo_prompt_text(video_prompt)

    if provider in {"sora_2", "sora_2_pro"}:
        optimized_prompt = video_prompt.get("optimized_prompt")
        if optimized_prompt:
            logger.debug(
                "sora_optimized_prompt_selected",
                prompt_length=len(optimized_prompt)
            )
            return optimized_prompt

    # Fallback to canonical composition
    return build_full_prompt_text(video_prompt)


def _submit_video_request(
    *,
    provider: str,
    prompt_text: str,
    aspect_ratio: str,
    resolution: str,
    seconds: int,
    size: Optional[str],
    correlation_id: str,
) -> Dict[str, Any]:
    """Submit a video generation request to the selected provider."""

    if provider == "veo_3_1":
        if resolution == "1080p" and aspect_ratio != "16:9":
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="1080p resolution requires 16:9 aspect ratio",
                details={
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                },
            )

        veo_client = get_veo_client()
        result = veo_client.submit_video_generation(
            prompt=prompt_text,
            correlation_id=correlation_id,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        requested_size = _map_size_from_aspect_ratio(aspect_ratio, resolution)
        return {
            "operation_id": result["operation_id"],
            "status": result.get("status", "submitted"),
            "provider_model": "veo-3.1",
            "requested_size": requested_size,
            "estimated_duration_seconds": 180,
            "provider_metadata": result,
        }

    if provider in {"sora_2", "sora_2_pro"}:
        sora_client = get_sora_client()
        valid_seconds = {4, 8, 12}
        if seconds not in valid_seconds:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Sora requires seconds to be one of 4, 8, or 12",
                details={"seconds": seconds, "allowed_seconds": sorted(valid_seconds)},
            )

        target_size = size or _map_sora_size(aspect_ratio, resolution)
        if not target_size:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Unsupported size for Sora",
                details={
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                    "allowed_sizes": {
                        "9:16": {"720p": "720x1280", "1080p": "1024x1792"},
                        "16:9": {"720p": "1280x720", "1080p": "1792x1024"},
                    },
                },
            )

        model = "sora-2-pro" if provider == "sora_2_pro" else "sora-2"
        seconds_literal = str(seconds)

        try:
            submission = sora_client.submit_video_generation(
                prompt=prompt_text,
                correlation_id=correlation_id,
                model=model,
                seconds=seconds_literal,
                size=target_size,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            try:
                error_payload = exc.response.json()
            except ValueError:
                error_payload = {"body": exc.response.text[:500]}

            error_message = error_payload.get("error", {}).get("message") if isinstance(error_payload, dict) else None
            code = ErrorCode.VALIDATION_ERROR if status_code in {400, 422} else ErrorCode.THIRD_PARTY_FAIL
            message = error_message or "Sora video submission failed"

            raise FlowForgeException(
                code=code,
                message=message,
                details={
                    "provider": provider,
                    "status_code": status_code,
                    "response": error_payload,
                    "request": {
                        "seconds": seconds_literal,
                        "size": target_size,
                        "model": model,
                    },
                },
                status_code=422 if code == ErrorCode.VALIDATION_ERROR else 503,
            )

        return {
            "operation_id": submission["video_id"],
            "status": submission.get("status", "queued"),
            "provider_model": submission.get("model", model),
            "requested_size": submission.get("size", target_size),
            "estimated_duration_seconds": seconds * 60,  # conservative placeholder
            "provider_metadata": submission,
        }

    raise FlowForgeException(
        code=ErrorCode.VALIDATION_ERROR,
        message="Unsupported video provider",
        details={"provider": provider},
    )


def _map_size_from_aspect_ratio(aspect_ratio: str, resolution: str) -> Optional[str]:
    """Map canonical aspect ratio + resolution pairs to pixel sizes (VEO defaults)."""
    mapping = {
        ("9:16", "720p"): "720x1280",
        ("9:16", "1080p"): "1080x1920",
        ("16:9", "720p"): "1280x720",
        ("16:9", "1080p"): "1920x1080",
    }
    return mapping.get((aspect_ratio, resolution))


def _map_sora_size(aspect_ratio: str, resolution: str) -> Optional[str]:
    """Map aspect ratio + resolution pairs to Sora-supported pixel sizes."""
    mapping = {
        ("9:16", "720p"): "720x1280",
        ("9:16", "1080p"): "1024x1792",
        ("16:9", "720p"): "1280x720",
        ("16:9", "1080p"): "1792x1024",
    }
    return mapping.get((aspect_ratio, resolution))
