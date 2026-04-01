"""
FLOW-FORGE Video Generation Handlers
FastAPI route handlers for video generation operations.
Per Constitution § V: Locality & Vertical Slices
Per Canon § 3.2: S5_PROMPTS_BUILT → S6_QA transition
"""

import base64
import mimetypes
from fastapi import APIRouter, HTTPException, Request, status
from typing import Dict, Any, Optional
import json
import os
import random
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError
import httpx

from app.adapters.supabase_client import get_supabase
from app.adapters.storage_client import get_storage_client
from app.adapters.veo_client import get_veo_client
from app.adapters.sora_client import get_sora_client
from app.core.errors import FlowForgeException, SuccessResponse, ValidationError, ErrorCode
from app.core.logging import get_logger
from app.core.video_profiles import (
    VEO_EXTENDED_VIDEO_ROUTE,
    VEO_PROVIDER,
    get_duration_profile,
    get_submission_video_status,
    uses_duration_routing,
)
from app.features.batches.queries import get_batch_by_id
from app.features.batches.state_machine import reconcile_batch_video_pipeline_state
from app.features.posts.prompt_text import build_full_prompt_text
from app.features.posts.prompt_builder import build_veo_prompt_segment, split_dialogue_sentences
from app.features.videos.prompt_audit import record_prompt_audit
from app.features.videos.quota_guard import (
    build_reservation_key,
    chain_cost_units,
    consume_quota,
    ensure_immediate_submit_slot,
    maybe_freeze_after_provider_429,
    release_quota,
    reserve_quota,
)
from app.features.videos.schemas import (
    VideoGenerationRequest,
    VideoGenerationResponse,
    VideoStatusResponse,
    BatchVideoGenerationRequest,
    BatchVideoGenerationResponse
)

logger = get_logger(__name__)
router = APIRouter(prefix="/videos", tags=["videos"])


_GLOBAL_VEO_ANCHOR_RELATIVE_PATH = "static/images/sarah.jpg"
_GLOBAL_VEO_ANCHOR_PATH = Path(__file__).resolve().parents[3] / _GLOBAL_VEO_ANCHOR_RELATIVE_PATH
_GLOBAL_VEO_ANCHOR_OBJECT_KEY = "flow-forge/images/anchors/sarah.jpg"
# Keep the live preview path text-only until the API explicitly supports image.inlineData.
_GLOBAL_VEO_ANCHOR_ENABLED = False


def _resolve_global_veo_anchor_image(correlation_id: str) -> Dict[str, Any]:
    """Load the global Sarah portrait and mirror it to a fixed Cloudflare R2 key."""
    if not _GLOBAL_VEO_ANCHOR_PATH.exists():
        raise ValidationError(
            "Global Veo anchor image is missing.",
            {"anchor_image_path": _GLOBAL_VEO_ANCHOR_RELATIVE_PATH},
        )

    try:
        image_bytes = _GLOBAL_VEO_ANCHOR_PATH.read_bytes()
    except OSError as exc:
        raise ValidationError(
            "Global Veo anchor image could not be read.",
            {"anchor_image_path": _GLOBAL_VEO_ANCHOR_RELATIVE_PATH, "error": str(exc)},
        ) from exc

    if not image_bytes:
        raise ValidationError(
            "Global Veo anchor image is empty.",
            {"anchor_image_path": _GLOBAL_VEO_ANCHOR_RELATIVE_PATH},
        )

    mime_type = mimetypes.guess_type(_GLOBAL_VEO_ANCHOR_PATH.name)[0] or "image/jpeg"
    metadata = {
        "anchor_image_enabled": True,
        "anchor_image_source_path": _GLOBAL_VEO_ANCHOR_RELATIVE_PATH,
        "anchor_image_mime_type": mime_type,
        "anchor_image_size_bytes": len(image_bytes),
    }

    try:
        mirrored = get_storage_client().ensure_image(
            image_bytes=image_bytes,
            object_key=_GLOBAL_VEO_ANCHOR_OBJECT_KEY,
            correlation_id=correlation_id,
            content_type=mime_type,
        )
        metadata["anchor_image_storage_key"] = mirrored["storage_key"]
        metadata["anchor_image_url"] = mirrored["url"]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "veo_anchor_image_mirror_failed",
            correlation_id=correlation_id,
            anchor_image_path=_GLOBAL_VEO_ANCHOR_RELATIVE_PATH,
            error=str(exc),
        )

    return {
        "first_frame_image": {
            "mime_type": mime_type,
            "data_base64": base64.b64encode(image_bytes).decode("ascii"),
        },
        "metadata": metadata,
    }


def _resolve_extended_provider_aspect_ratio(route: Optional[str], requested_aspect_ratio: str) -> str:
    """Extended runs keep the requested aspect ratio when the REST path is used."""
    return requested_aspect_ratio


def _resolve_video_submission_plan(
    *,
    batch: Dict[str, Any],
    requested_provider: Optional[str],
    requested_seconds: Optional[int],
    aspect_ratio: str,
    resolution: str,
    size: Optional[str],
) -> Dict[str, Any]:
    if uses_duration_routing(batch):
        profile = get_duration_profile(batch.get("target_length_tier"))
        resolved_resolution = "720p" if profile.route == VEO_EXTENDED_VIDEO_ROUTE else resolution
        provider_aspect_ratio = _resolve_extended_provider_aspect_ratio(profile.route, aspect_ratio)
        requested_size = size or _map_size_from_aspect_ratio(aspect_ratio, resolved_resolution)
        return {
            "provider": VEO_PROVIDER,
            "seconds": profile.requested_seconds,
            "provider_target_seconds": profile.provider_target_seconds,
            "aspect_ratio": aspect_ratio,
            "requested_aspect_ratio": aspect_ratio,
            "provider_aspect_ratio": provider_aspect_ratio,
            "resolution": resolved_resolution,
            "size": requested_size,
            "requested_size": requested_size,
            "provider_requested_size": _map_size_from_aspect_ratio(provider_aspect_ratio, resolved_resolution),
            "postprocess_crop_aspect_ratio": aspect_ratio if provider_aspect_ratio != aspect_ratio else None,
            "postprocess_strategy": "center_crop_scale" if provider_aspect_ratio != aspect_ratio else None,
            "profile": profile,
            "duration_routed": True,
        }

    provider = requested_provider or VEO_PROVIDER
    seconds = requested_seconds or 8
    return {
        "provider": provider,
        "seconds": seconds,
        "provider_target_seconds": seconds,
        "aspect_ratio": aspect_ratio,
        "requested_aspect_ratio": aspect_ratio,
        "provider_aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "size": size,
        "requested_size": size,
        "provider_requested_size": size,
        "postprocess_crop_aspect_ratio": None,
        "postprocess_strategy": None,
        "profile": None,
        "duration_routed": False,
    }


def _build_submission_metadata(
    *,
    existing_metadata: Dict[str, Any],
    submission_plan: Dict[str, Any],
    submission_result: Dict[str, Any],
    segment_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    requested_aspect_ratio = submission_plan.get("requested_aspect_ratio") or submission_plan["aspect_ratio"]
    requested_size = submission_result.get("requested_size") or submission_plan.get("requested_size")
    provider_aspect_ratio = submission_plan.get("provider_aspect_ratio") or requested_aspect_ratio
    metadata = {
        **existing_metadata,
        "requested_aspect_ratio": requested_aspect_ratio,
        "requested_resolution": submission_plan["resolution"],
        "requested_seconds": submission_plan["seconds"],
        "requested_size": requested_size,
        "provider_aspect_ratio": provider_aspect_ratio,
    }

    provider_requested_size = (
        submission_result.get("provider_requested_size")
        or submission_plan.get("provider_requested_size")
    )
    if provider_requested_size:
        metadata["provider_requested_size"] = provider_requested_size
    if submission_plan.get("postprocess_crop_aspect_ratio"):
        metadata["postprocess_crop_aspect_ratio"] = submission_plan["postprocess_crop_aspect_ratio"]
    if submission_plan.get("postprocess_strategy"):
        metadata["postprocess_strategy"] = submission_plan["postprocess_strategy"]

    profile = submission_plan.get("profile")
    if profile is not None:
        metadata.update(
            {
                "target_length_tier": profile.target_length_tier,
                "video_pipeline_route": profile.route,
                "provider_target_seconds": profile.provider_target_seconds,
                "generated_seconds": 0,
                "actual_seconds": None,
                "chain_status": "submitted",
                "operation_ids": [submission_result["operation_id"]],
                "veo_base_seconds": profile.veo_base_seconds,
                "veo_extension_seconds": profile.veo_extension_seconds,
                "veo_extension_hops_target": profile.veo_extension_hops,
                "veo_extension_hops_completed": 0,
            }
        )
    if profile is not None and profile.route != VEO_EXTENDED_VIDEO_ROUTE:
        metadata["duration_seconds"] = profile.provider_target_seconds

    provider_model = submission_result.get("provider_model")
    if provider_model:
        metadata["provider_model"] = provider_model
    if submission_result.get("provider_metadata"):
        metadata["provider_metadata"] = submission_result["provider_metadata"]
    if segment_metadata:
        metadata.update(segment_metadata)

    return metadata


def _required_veo_segments_for_profile_hops(hops_target: int) -> int:
    return max(int(hops_target or 0), 0) + 1


def _resolve_veo_extension_hops_target(*, segment_count: int, planned_hops: int) -> int:
    if segment_count <= 0:
        return 0
    return max(int(planned_hops or 0), 0)


def _validate_veo_extension_segment_budget(
    *,
    segment_count: int,
    planned_extension_hops: int,
    target_length_tier: Optional[int],
) -> None:
    required_segments = _required_veo_segments_for_profile_hops(planned_extension_hops)
    if segment_count >= required_segments:
        return
    raise ValidationError(
        "Veo extended chains need one complete dialogue segment per hop plus a base segment.",
        {
            "target_length_tier": target_length_tier,
            "planned_extension_hops": planned_extension_hops,
            "segments_available": segment_count,
            "segments_required": required_segments,
        },
    )


def _build_veo_extended_base_prompt(
    seed_data: Dict[str, Any],
    *,
    planned_extension_hops: Optional[int] = None,
    target_length_tier: Optional[int] = None,
) -> tuple[str, Dict[str, Any]]:
    script = str(seed_data.get("script") or seed_data.get("dialog_script") or "").strip()
    segments = split_dialogue_sentences(script) if script else []
    if not segments and script:
        segments = [script]

    effective_hops: Optional[int] = None
    if planned_extension_hops is not None:
        _validate_veo_extension_segment_budget(
            segment_count=len(segments),
            planned_extension_hops=planned_extension_hops,
            target_length_tier=target_length_tier,
        )
        effective_hops = _resolve_veo_extension_hops_target(
            segment_count=len(segments),
            planned_hops=planned_extension_hops,
        )

    base_segment = segments[0] if segments else ""
    segment_metadata = {
        "veo_segments": segments,
        "veo_segments_total": len(segments),
        "veo_current_segment_index": 0,
    }
    if planned_extension_hops is not None:
        segment_metadata.update(
            {
                "veo_required_segments": _required_veo_segments_for_profile_hops(planned_extension_hops),
                "veo_planned_extension_hops_target": planned_extension_hops,
                "veo_extension_hops_target": effective_hops,
                "veo_chain_shortened_to_available_segments": False,
            }
        )
    return build_veo_prompt_segment(base_segment, include_quotes=False, include_ending=False), segment_metadata


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
    quota_reservation_key: Optional[str] = None
    quota_reserved = False
    quota_consumed = False
    
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
        seed_data = post.get("seed_data") or {}
        if isinstance(seed_data, str):
            try:
                seed_data = json.loads(seed_data)
            except json.JSONDecodeError:
                seed_data = {}

        if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
            raise ValidationError(
                "Removed posts cannot be submitted for video generation.",
                {"post_id": post_id}
            )

        if not video_prompt:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Post missing video_prompt_json. Run build-prompt first.",
                details={"post_id": post_id}
            )
        
        prompt_request = _build_provider_prompt_request(video_prompt, request.provider)

        requested_units = 0
        if request.provider == VEO_PROVIDER:
            requested_units = chain_cost_units(None, provider=request.provider)
            quota_reservation_key = build_reservation_key(
                provider=request.provider,
                post_id=post_id,
                correlation_id=correlation_id,
            )
            reserve_quota(
                provider=request.provider,
                post_id=post_id,
                batch_id=post.get("batch_id"),
                reservation_key=quota_reservation_key,
                requested_units=requested_units,
                require_immediate_slot=True,
            )
            quota_reserved = True

        anchor_image_bundle = (
            _resolve_global_veo_anchor_image(correlation_id)
            if _GLOBAL_VEO_ANCHOR_ENABLED and request.provider == VEO_PROVIDER
            else None
        )
        veo_seed = random.randint(0, 2**32 - 1) if request.provider == "veo_3_1" else None
        submission_result = _submit_video_request(
            provider=request.provider,
            prompt_text=prompt_request["prompt_text"] or "",
            negative_prompt=prompt_request.get("negative_prompt"),
            aspect_ratio=request.aspect_ratio,
            provider_aspect_ratio=request.aspect_ratio,
            requested_aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            seconds=request.seconds,
            size=request.size,
            correlation_id=correlation_id,
            provider_duration_seconds=request.seconds if request.provider == "veo_3_1" else None,
            first_frame_image=anchor_image_bundle["first_frame_image"] if anchor_image_bundle else None,
            seed=veo_seed,
        )

        operation_id = submission_result["operation_id"]
        provider_model = submission_result.get("provider_model")
        requested_size = submission_result.get("requested_size")

        if quota_reservation_key:
            consume_quota(reservation_key=quota_reservation_key, operation_id=operation_id, units=1)
            quota_consumed = True

        record_prompt_audit(
            post_id=post_id,
            operation_id=operation_id,
            provider=request.provider,
            prompt_text=prompt_request["prompt_text"] or "",
            negative_prompt=prompt_request.get("negative_prompt"),
            prompt_path=prompt_request["prompt_path"],
            aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            requested_seconds=request.seconds,
            correlation_id=correlation_id,
            seed=veo_seed,
        )

        existing_metadata = post.get("video_metadata") or {}
        submission_metadata = {
            **existing_metadata,
            "requested_aspect_ratio": request.aspect_ratio,
            "requested_resolution": request.resolution,
            "requested_seconds": request.seconds,
            "requested_size": requested_size,
        }
        if veo_seed is not None:
            submission_metadata["veo_seed"] = veo_seed
        if quota_reservation_key:
            submission_metadata["quota_reservation_key"] = quota_reservation_key
            submission_metadata["quota_reserved_units"] = requested_units
        if provider_model:
            submission_metadata["provider_model"] = provider_model
        if submission_result.get("provider_metadata"):
            submission_metadata["provider_metadata"] = submission_result["provider_metadata"]
        if anchor_image_bundle:
            submission_metadata.update(anchor_image_bundle["metadata"])

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
    
    except FlowForgeException as exc:
        if quota_reservation_key and quota_reserved and not quota_consumed:
            release_quota(
                reservation_key=quota_reservation_key,
                reason=exc.message,
                final_status="released",
                error_code=str(exc.code),
            )
        if (
            quota_reservation_key
            and request.provider == VEO_PROVIDER
            and exc.status_code == 429
            and not exc.details.get("blocked_before_submit")
        ):
            maybe_freeze_after_provider_429(provider=VEO_PROVIDER, reason=exc.message)
        raise
    except HTTPException:
        raise
    except Exception as e:
        if quota_reservation_key and quota_reserved and not quota_consumed:
            release_quota(
                reservation_key=quota_reservation_key,
                reason=str(e),
                final_status="released",
                error_code="unexpected_error",
            )
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
    except HTTPException:
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
        batch = get_batch_by_id(batch_id)

        if not posts:
            raise FlowForgeException(
                code=ErrorCode.NOT_FOUND,
                message=f"No posts found for batch {batch_id}",
                details={"batch_id": batch_id}
            )
        
        submitted_count = 0
        skipped_count = 0
        submitted_post_ids = []
        prepared_submissions = []
        last_provider_model: Optional[str] = None
        batch_veo_seed = random.randint(0, 2**32 - 1) if request.provider == "veo_3_1" else None

        for post in posts:
            post_id = post["id"]
            video_prompt = post.get("video_prompt_json")
            seed_data = post.get("seed_data") or {}
            if isinstance(seed_data, str):
                try:
                    seed_data = json.loads(seed_data)
                except json.JSONDecodeError:
                    seed_data = {}
            
            # Skip posts without prompts or already submitted
            if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
                logger.info(
                    "post_skipped_removed_from_batch",
                    post_id=post_id,
                    batch_id=batch_id
                )
                skipped_count += 1
                continue

            if not video_prompt:
                logger.warning(
                    "post_skipped_no_prompt",
                    post_id=post_id,
                    batch_id=batch_id
                )
                skipped_count += 1
                continue
            
            if post.get("video_status") in ["submitted", "processing", "completed", "extended_submitted", "extended_processing"]:
                logger.info(
                    "post_skipped_already_submitted",
                    post_id=post_id,
                    batch_id=batch_id,
                    status=post.get("video_status")
                )
                skipped_count += 1
                continue

            submission_plan = _resolve_video_submission_plan(
                batch=batch,
                requested_provider=request.provider,
                requested_seconds=request.seconds,
                aspect_ratio=request.aspect_ratio,
                resolution=request.resolution,
                size=request.size,
            )

            profile = submission_plan.get("profile")
            is_extended = profile is not None and profile.route == VEO_EXTENDED_VIDEO_ROUTE

            if is_extended:
                prompt_text, segment_metadata = _build_veo_extended_base_prompt(
                    seed_data,
                    planned_extension_hops=profile.veo_extension_hops,
                    target_length_tier=profile.target_length_tier,
                )
                negative_prompt = None
            else:
                prompt_request = _build_provider_prompt_request(video_prompt, submission_plan["provider"])
                prompt_text = prompt_request["prompt_text"] or ""
                negative_prompt = prompt_request.get("negative_prompt")
                segment_metadata = None

            prepared_submissions.append(
                {
                    "post": post,
                    "post_id": post_id,
                    "seed_data": seed_data,
                    "submission_plan": submission_plan,
                    "profile": profile,
                    "is_extended": is_extended,
                    "prompt_text": prompt_text,
                    "negative_prompt": negative_prompt,
                    "segment_metadata": segment_metadata,
                    "quota_requested_units": chain_cost_units(profile, provider=submission_plan["provider"]),
                }
            )

        veo_submissions = [item for item in prepared_submissions if item["submission_plan"]["provider"] == VEO_PROVIDER]
        reserved_keys = []
        if veo_submissions:
            ensure_immediate_submit_slot(requested_units=len(veo_submissions), provider=VEO_PROVIDER)
            try:
                for item in veo_submissions:
                    reservation_key = build_reservation_key(
                        provider=VEO_PROVIDER,
                        post_id=item["post_id"],
                        correlation_id=correlation_id,
                    )
                    reserve_quota(
                        provider=VEO_PROVIDER,
                        post_id=item["post_id"],
                        batch_id=batch_id,
                        reservation_key=reservation_key,
                        requested_units=item["quota_requested_units"],
                        require_immediate_slot=False,
                    )
                    item["quota_reservation_key"] = reservation_key
                    reserved_keys.append(reservation_key)
            except FlowForgeException:
                for reservation_key in reserved_keys:
                    release_quota(
                        reservation_key=reservation_key,
                        reason="Batch preflight failed before any Veo submission.",
                        final_status="released",
                        error_code="batch_preflight_aborted",
                    )
                raise

        anchor_image_bundle = _resolve_global_veo_anchor_image(correlation_id) if _GLOBAL_VEO_ANCHOR_ENABLED and veo_submissions else None
        for index, item in enumerate(prepared_submissions):
            post = item["post"]
            post_id = item["post_id"]
            submission_plan = item["submission_plan"]
            profile = item["profile"]
            is_extended = item["is_extended"]
            prompt_text = item["prompt_text"]
            negative_prompt = item["negative_prompt"]
            segment_metadata = item["segment_metadata"]
            quota_reservation_key = item.get("quota_reservation_key")
            quota_consumed = False

            try:
                submission_result = _submit_video_request(
                    provider=submission_plan["provider"],
                    prompt_text=prompt_text,
                    negative_prompt=negative_prompt,
                    aspect_ratio=submission_plan["aspect_ratio"],
                    provider_aspect_ratio=submission_plan.get("provider_aspect_ratio"),
                    requested_aspect_ratio=submission_plan.get("requested_aspect_ratio"),
                    resolution=submission_plan["resolution"],
                    seconds=submission_plan["seconds"],
                    size=submission_plan["size"],
                    correlation_id=f"{correlation_id}_{post_id}",
                    provider_duration_seconds=(
                        profile.veo_base_seconds
                        if is_extended and profile is not None
                        else submission_plan["provider_target_seconds"]
                        if submission_plan["provider"] == VEO_PROVIDER
                        else None
                    ),
                    first_frame_image=(
                        anchor_image_bundle["first_frame_image"]
                        if submission_plan["provider"] == VEO_PROVIDER and anchor_image_bundle
                        else None
                    ),
                    seed=batch_veo_seed,
                )
                operation_id = submission_result["operation_id"]
                provider_model = submission_result.get("provider_model")

                if quota_reservation_key:
                    consume_quota(reservation_key=quota_reservation_key, operation_id=operation_id, units=1)
                    quota_consumed = True

                record_prompt_audit(
                    post_id=post_id,
                    operation_id=operation_id,
                    provider=submission_plan["provider"],
                    prompt_text=prompt_text,
                    negative_prompt=negative_prompt,
                    prompt_path="veo_extended_segment" if is_extended else "batch_standard",
                    aspect_ratio=submission_plan["aspect_ratio"],
                    resolution=submission_plan["resolution"],
                    requested_seconds=submission_plan["seconds"],
                    correlation_id=f"{correlation_id}_{post_id}",
                    batch_id=batch_id,
                    seed=batch_veo_seed,
                )

                existing_metadata = post.get("video_metadata") or {}
                submission_metadata = _build_submission_metadata(
                    existing_metadata=existing_metadata,
                    submission_plan=submission_plan,
                    submission_result=submission_result,
                    segment_metadata=segment_metadata,
                )
                if batch_veo_seed is not None:
                    submission_metadata["veo_seed"] = batch_veo_seed
                if quota_reservation_key:
                    submission_metadata["quota_reservation_key"] = quota_reservation_key
                    submission_metadata["quota_reserved_units"] = item["quota_requested_units"]
                if submission_plan["provider"] == VEO_PROVIDER and anchor_image_bundle:
                    submission_metadata.update(anchor_image_bundle["metadata"])

                route = profile.route if profile else None
                provider_status = submission_result.get("status", "submitted")
                db_status = get_submission_video_status(route, provider_status)

                logger.warning(
                    "video_operation_id_paid_request",
                    post_id=post_id,
                    operation_id=operation_id,
                    provider=submission_plan["provider"],
                    correlation_id=correlation_id,
                    message="PAID VIDEO SUBMITTED - Operation ID logged for recovery"
                )

                try:
                    supabase.table("posts").update({
                        "video_provider": submission_plan["provider"],
                        "video_format": submission_plan["aspect_ratio"],
                        "video_operation_id": operation_id,
                        "video_status": db_status,
                        "video_metadata": submission_metadata
                    }).eq("id", post_id).execute()
                except Exception as db_error:
                    logger.error(
                        "batch_video_db_update_failed_but_video_submitted",
                        post_id=post_id,
                        operation_id=operation_id,
                        provider=submission_plan["provider"],
                        batch_id=batch_id,
                        correlation_id=correlation_id,
                        error=str(db_error),
                        message="DATABASE UPDATE FAILED - Video is still processing at provider."
                    )
                    _write_recovery_record(post_id, operation_id, submission_plan["provider"], correlation_id)
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
                    provider=submission_plan["provider"],
                    provider_model=provider_model,
                    seconds=submission_plan["seconds"],
                    size=submission_plan["size"],
                    operation_id=operation_id,
                    duration_routed=submission_plan["duration_routed"],
                )

            except FlowForgeException as exc:
                if quota_reservation_key and not quota_consumed:
                    release_quota(
                        reservation_key=quota_reservation_key,
                        reason=exc.message,
                        final_status="released",
                        error_code=str(exc.code),
                    )
                logger.warning(
                    "batch_video_submission_skipped",
                    post_id=post_id,
                    batch_id=batch_id,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details
                )
                skipped_count += 1
                if (
                    submission_plan["provider"] == VEO_PROVIDER
                    and exc.status_code == 429
                    and not exc.details.get("blocked_before_submit")
                ):
                    maybe_freeze_after_provider_429(provider=VEO_PROVIDER, reason=exc.message)
                    for pending in prepared_submissions[index + 1:]:
                        pending_key = pending.get("quota_reservation_key")
                        if pending_key:
                            release_quota(
                                reservation_key=pending_key,
                                reason="Batch stopped after provider quota drift.",
                                final_status="released",
                                error_code="batch_provider_quota_drift",
                            )
                    break
            except Exception as e:
                if quota_reservation_key and not quota_consumed:
                    release_quota(
                        reservation_key=quota_reservation_key,
                        reason=str(e),
                        final_status="released",
                        error_code="unexpected_error",
                    )
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

        reconcile_batch_video_pipeline_state(
            batch_id=batch_id,
            correlation_id=correlation_id,
            supabase_client=supabase,
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


def _build_veo_prompt_text(video_prompt: Dict[str, Any]) -> tuple[str, str]:
    """Build VEO-compatible prompt text from video_prompt_json. Returns (text, path)."""
    veo_prompt = video_prompt.get("veo_prompt")
    if veo_prompt:
        logger.debug(
            "veo_prompt_selected",
            prompt_length=len(veo_prompt)
        )
        return veo_prompt, "veo_prompt"

    optimized_prompt = video_prompt.get("optimized_prompt")
    if optimized_prompt:
        logger.debug(
            "veo_optimized_prompt_fallback_selected",
            prompt_length=len(optimized_prompt)
        )
        return optimized_prompt, "optimized_prompt"
    return build_full_prompt_text(video_prompt), "full_prompt_text_fallback"


def _build_veo_negative_prompt(video_prompt: Dict[str, Any]) -> Optional[str]:
    """Build VEO negativePrompt text from stored prompt metadata."""
    negative_prompt = video_prompt.get("veo_negative_prompt")
    if negative_prompt:
        logger.debug(
            "veo_negative_prompt_selected",
            prompt_length=len(negative_prompt)
        )
        return negative_prompt
    return None


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


def _build_provider_prompt_text(video_prompt: Dict[str, Any], provider: str) -> tuple[str, str]:
    """Build provider-specific prompt text. Returns (text, path)."""
    if provider == "veo_3_1":
        return _build_veo_prompt_text(video_prompt)

    if provider in {"sora_2", "sora_2_pro"}:
        optimized_prompt = video_prompt.get("optimized_prompt")
        if optimized_prompt:
            logger.debug(
                "sora_optimized_prompt_selected",
                prompt_length=len(optimized_prompt)
            )
            return optimized_prompt, "sora_optimized_prompt"

    # Fallback to canonical composition
    return build_full_prompt_text(video_prompt), "full_prompt_text_fallback"


def _build_provider_prompt_request(video_prompt: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """Build provider-specific prompt payload pieces."""
    prompt_text, prompt_path = _build_provider_prompt_text(video_prompt, provider)
    negative_prompt = _build_veo_negative_prompt(video_prompt) if provider == "veo_3_1" else None
    return {
        "prompt_text": prompt_text,
        "negative_prompt": negative_prompt,
        "prompt_path": prompt_path,
    }


def _submit_video_request(
    *,
    provider: str,
    prompt_text: str,
    negative_prompt: Optional[str],
    aspect_ratio: str,
    provider_aspect_ratio: Optional[str],
    requested_aspect_ratio: Optional[str],
    resolution: str,
    seconds: int,
    size: Optional[str],
    correlation_id: str,
    provider_duration_seconds: Optional[int] = None,
    first_frame_image: Optional[Dict[str, str]] = None,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Submit a video generation request to the selected provider."""

    if provider == "veo_3_1":
        veo_client = get_veo_client()
        provider_aspect = provider_aspect_ratio or aspect_ratio
        requested_aspect = requested_aspect_ratio or aspect_ratio
        veo_duration_seconds = provider_duration_seconds or seconds
        if veo_duration_seconds not in {4, 6, 8}:
            veo_duration_seconds = 8
        try:
            result = veo_client.submit_video_generation(
                prompt=prompt_text,
                negative_prompt=negative_prompt,
                correlation_id=correlation_id,
                aspect_ratio=provider_aspect,
                resolution=resolution,
                duration_seconds=veo_duration_seconds,
                first_frame_image=first_frame_image,
                seed=seed,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            try:
                error_payload = exc.response.json()
            except ValueError:
                error_payload = {"body": exc.response.text[:500]}

            error_message = error_payload.get("error", {}).get("message") if isinstance(error_payload, dict) else None
            if status_code == 429:
                raise FlowForgeException(
                    code=ErrorCode.RATE_LIMIT,
                    message=error_message or "VEO quota exhausted",
                    details={
                        "provider": provider,
                        "status_code": status_code,
                        "response": error_payload,
                    },
                    status_code=429,
                ) from exc

            raise FlowForgeException(
                code=ErrorCode.THIRD_PARTY_FAIL,
                message=error_message or "VEO video submission failed",
                details={
                    "provider": provider,
                    "status_code": status_code,
                    "response": error_payload,
                },
                status_code=503,
            ) from exc
        requested_size = _map_size_from_aspect_ratio(requested_aspect, resolution)
        provider_requested_size = _map_size_from_aspect_ratio(provider_aspect, resolution)
        return {
            "operation_id": result["operation_id"],
            "status": result.get("status", "submitted"),
            "provider_model": "veo-3.1",
            "requested_size": requested_size,
            "provider_requested_size": provider_requested_size,
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
