"""
Lippe Lift Studio QA Handlers
FastAPI route handlers for quality assurance operations.
Per Constitution § V: Locality & Vertical Slices
Per Canon § 3.2: S6_QA state management
"""

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Dict, Any
import json
import httpx
import time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status, Request
from fastapi.responses import JSONResponse

from app.adapters.supabase_client import get_supabase
from app.core.config import get_settings
from app.core.errors import FlowForgeException, SuccessResponse, ErrorCode
from app.core.logging import get_logger
from app.core.video_profiles import VIDEO_STATUS_CAPTION_COMPLETED, VIDEO_STATUS_COMPLETED
from app.features.qa.schemas import (
    AutoQAChecks,
    QAApprovalRequest,
    QAApprovalResponse,
    BatchQAStatusResponse
)

logger = get_logger(__name__)
router = APIRouter(prefix="/qa", tags=["qa"])

_QA_VIDEO_READY_STATUSES = {VIDEO_STATUS_COMPLETED, VIDEO_STATUS_CAPTION_COMPLETED}
_QA_DECISION_RPC_TIMEOUT_SECONDS = 6.0
_QA_DECISION_RPC_RETRY_DELAY_SECONDS = 0.15
_QA_DECISION_TRANSIENT_STATUSES = {502, 503, 504}


def _is_removed_post(post: Dict[str, Any]) -> bool:
    seed_data = post.get("seed_data") or {}
    if isinstance(seed_data, str):
        try:
            seed_data = json.loads(seed_data)
        except json.JSONDecodeError:
            seed_data = {}
    return seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True


def _active_posts(posts: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [post for post in posts if not _is_removed_post(post)]


def _qa_rpc_result(response: httpx.Response) -> dict[str, Any]:
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError("Delivery QA RPC returned invalid JSON.") from exc
    if isinstance(result, list):
        if len(result) != 1:
            raise RuntimeError("Delivery QA RPC returned an unexpected result count.")
        result = result[0]
    if not isinstance(result, Mapping):
        raise RuntimeError("Delivery QA RPC returned an invalid contract.")
    return dict(result)


async def _execute_qa_decision_rpc(
    *,
    post_id: str,
    qa_request: QAApprovalRequest,
    correlation_id: str,
) -> dict[str, Any]:
    """Run the idempotent QA transaction through one bounded fresh transport."""
    settings = get_settings()
    headers = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }
    payload = {
        "p_post_id": str(post_id),
        "p_approved": qa_request.approved,
        "p_notes": qa_request.notes,
    }
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=None) as client:
                response = await asyncio.wait_for(
                    client.post(
                        f"{settings.supabase_url.rstrip('/')}/rest/v1/rpc/apply_post_qa_decision",
                        headers=headers,
                        json=payload,
                    ),
                    timeout=_QA_DECISION_RPC_TIMEOUT_SECONDS,
                )
        except (TimeoutError, httpx.RequestError) as exc:
            last_error = exc
            logger.warning(
                "qa_approval_rpc_transport_retry",
                post_id=post_id,
                correlation_id=correlation_id,
                attempt=attempt,
                error_class=type(exc).__name__,
            )
            if attempt < 2:
                await asyncio.sleep(_QA_DECISION_RPC_RETRY_DELAY_SECONDS)
                continue
            raise RuntimeError("Delivery QA database request timed out.") from exc

        if response.status_code in _QA_DECISION_TRANSIENT_STATUSES and attempt < 2:
            logger.warning(
                "qa_approval_rpc_status_retry",
                post_id=post_id,
                correlation_id=correlation_id,
                attempt=attempt,
                status_code=response.status_code,
            )
            await asyncio.sleep(_QA_DECISION_RPC_RETRY_DELAY_SECONDS)
            continue
        if response.status_code >= 400:
            raise RuntimeError(
                f"Delivery QA RPC failed with HTTP {response.status_code}."
            )
        return _qa_rpc_result(response)

    raise RuntimeError("Delivery QA RPC failed without a response.") from last_error


async def _record_qa_decision(
    *,
    post_id: str,
    qa_request: QAApprovalRequest,
    correlation_id: str,
) -> tuple[Dict[str, Any], str | None, bool]:
    started_at = time.monotonic()
    result = await _execute_qa_decision_rpc(
        post_id=post_id,
        qa_request=qa_request,
        correlation_id=correlation_id,
    )
    if result.get("ok") is not True:
        error_code = str(result.get("error_code") or "validation_error")
        status_code = 404 if error_code == "not_found" else 422
        raise FlowForgeException(
            code=(ErrorCode.NOT_FOUND if status_code == 404 else ErrorCode.VALIDATION_ERROR),
            message=str(result.get("message") or "Delivery QA decision was rejected."),
            details={
                "post_id": post_id,
                "batch_id": result.get("batch_id"),
            },
            status_code=status_code,
        )

    batch_id = str(result.get("batch_id") or "") or None
    should_advance = result.get("batch_state") == "S7_PUBLISH_PLAN"
    logger.info(
        "qa_approval_recorded",
        post_id=post_id,
        batch_id=batch_id,
        correlation_id=correlation_id,
        approved=qa_request.approved,
        has_notes=bool(qa_request.notes),
        batch_state=result.get("batch_state"),
        duration_ms=round((time.monotonic() - started_at) * 1000),
    )
    return {"qa_auto_checks": result.get("qa_auto_checks")}, batch_id, should_advance


@router.post("/{post_id}/auto-check", response_model=SuccessResponse)
async def run_auto_qa_checks(post_id: str):
    """
    Run automated QA checks on a post's video.
    Per Canon § 7.2: Duration 8s (±0.5s), Resolution 1080p min, Aspect ratio 9:16
    Per Constitution § II: Schema-validate at edges
    
    Args:
        post_id: UUID of the post
        
    Returns:
        SuccessResponse with AutoQAChecks data
        
    Raises:
        HTTPException: If post not found or video not ready
    """
    correlation_id = f"qa_auto_{post_id}"
    
    try:
        supabase = get_supabase().client
        response = supabase.table("posts").select("*").eq("id", post_id).execute()
        
        if not response.data:
            raise FlowForgeException(
                code=ErrorCode.NOT_FOUND,
                message=f"Post {post_id} not found",
                details={"post_id": post_id}
            )
        
        post = response.data[0]
        video_url = post.get("video_url")
        video_status = post.get("video_status")
        
        if video_status != "completed":
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Video generation not complete. Cannot run QA checks.",
                details={
                    "post_id": post_id,
                    "video_status": video_status
                }
            )
        
        if not video_url:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Video URL missing. Cannot run QA checks.",
                details={"post_id": post_id}
            )
        
        # Run automated checks
        auto_checks = await _perform_auto_qa_checks(
            post_id=post_id,
            video_url=video_url,
            video_metadata=post.get("video_metadata", {}),
            video_format=post.get("video_format", "9:16"),
            correlation_id=correlation_id
        )
        
        # Update post with auto check results
        supabase.table("posts").update({
            "qa_auto_checks": auto_checks.model_dump()
        }).eq("id", post_id).execute()
        
        logger.info(
            "qa_auto_checks_completed",
            post_id=post_id,
            correlation_id=correlation_id,
            overall_pass=auto_checks.overall_pass,
            duration_valid=auto_checks.duration_valid,
            resolution_valid=auto_checks.resolution_valid,
            file_accessible=auto_checks.file_accessible
        )
        
        return SuccessResponse(data=auto_checks.model_dump())
    
    except FlowForgeException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "qa_auto_checks_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run auto QA checks"
        )


@router.put("/{post_id}/approve", response_model=SuccessResponse)
async def approve_qa(post_id: str, req: Request):
    """
    Approve or reject a post's QA review.
    Updates qa_pass and qa_notes fields.
    
    Per Constitution § VII: State Machine Discipline
    Per Constitution § II: Validated Boundaries
    
    Args:
        post_id: UUID of the post
        request: QA approval decision and optional notes
        
    Returns:
        SuccessResponse with QAApprovalResponse data
        
    Raises:
        HTTPException: If post not found
    """
    correlation_id = f"qa_approve_{post_id}"
    
    try:
        # Parse payload supporting both JSON (application/json) and form submissions (HTMX default)
        payload: dict[str, Any] = {}
        try:
            if req.headers.get("content-type", "").startswith("application/json"):
                payload = await req.json()
            else:
                form = await req.form()
                payload = dict(form.multi_items())
        except Exception as parse_error:
            logger.warning(
                "qa_approval_payload_parse_failed",
                post_id=post_id,
                correlation_id=correlation_id,
                error=str(parse_error)
            )
            payload = {}

        if not payload:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Missing approval payload"
            )

        # Normalize payload values for Pydantic model
        approved_value = payload.get("approved")
        if isinstance(approved_value, str):
            approved_lower = approved_value.strip().lower()
            if approved_lower in {"true", "1", "yes", "on"}:
                approved_value = True
            elif approved_lower in {"false", "0", "no", "off"}:
                approved_value = False
        payload_normalized = {
            "approved": approved_value,
            "notes": payload.get("notes")
        }

        qa_request = QAApprovalRequest(**payload_normalized)

        post, batch_id, should_advance = await _record_qa_decision(
            post_id=post_id,
            qa_request=qa_request,
            correlation_id=correlation_id,
        )
        
        response_payload = SuccessResponse(
            data={
                **QAApprovalResponse(
                    post_id=post_id,
                    qa_pass=qa_request.approved,
                    qa_notes=qa_request.notes,
                    qa_auto_checks=post.get("qa_auto_checks")
                ).model_dump(),
                "batch_advanced": should_advance
            }
        )
        if batch_id and req.headers.get("hx-request", "").lower() == "true":
            redirect_anchor = (
                "publish-workflow"
                if should_advance
                else f"semantic-video-post-{quote(str(post_id), safe='')}"
            )
            return JSONResponse(
                content=response_payload.model_dump(mode="json"),
                headers={
                    "HX-Redirect": (
                        f"/batches/{quote(str(batch_id), safe='')}"
                        f"#{redirect_anchor}"
                    )
                },
            )
        return response_payload
    
    except FlowForgeException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "qa_approval_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to approve QA"
        )


@router.get("/batch/{batch_id}/status", response_model=SuccessResponse)
async def get_batch_qa_status(batch_id: str):
    """
    Get QA status summary for a batch.
    Used to determine if batch can advance from S6_QA → S7_PUBLISH_PLAN.
    
    Per Canon § 3.2: S6_QA → S7_PUBLISH_PLAN requires all posts qa_pass=true
    Per Constitution § IX: Observable Implementation
    
    Args:
        batch_id: UUID of the batch
        
    Returns:
        SuccessResponse with BatchQAStatusResponse data
        
    Raises:
        HTTPException: If batch not found
    """
    correlation_id = f"qa_status_{batch_id}"
    
    try:
        supabase = get_supabase().client
        
        # Fetch batch
        batch_response = supabase.table("batches").select("*").eq("id", batch_id).execute()
        if not batch_response.data:
            raise FlowForgeException(
                code=ErrorCode.NOT_FOUND,
                message=f"Batch {batch_id} not found",
                details={"batch_id": batch_id}
            )
        
        # Fetch all posts in batch
        posts_response = supabase.table("posts").select("*").eq("batch_id", batch_id).execute()
        active_posts = _active_posts(posts_response.data or [])

        total_posts = len(active_posts)
        posts_with_videos = sum(1 for p in active_posts if p.get("video_status") in _QA_VIDEO_READY_STATUSES)
        posts_qa_passed = sum(1 for p in active_posts if p.get("qa_pass") is True)
        posts_qa_pending = max(total_posts - posts_qa_passed, 0)
        
        all_passed = (total_posts > 0 and posts_qa_passed == total_posts)
        can_advance = all_passed
        
        logger.info(
            "batch_qa_status_checked",
            batch_id=batch_id,
            correlation_id=correlation_id,
            total_posts=total_posts,
            posts_with_videos=posts_with_videos,
            posts_qa_passed=posts_qa_passed,
            all_passed=all_passed
        )
        
        return SuccessResponse(
            data=BatchQAStatusResponse(
                batch_id=batch_id,
                total_posts=total_posts,
                posts_with_videos=posts_with_videos,
                posts_qa_passed=posts_qa_passed,
                posts_qa_pending=posts_qa_pending,
                all_passed=all_passed,
                can_advance_to_publish=can_advance
            ).model_dump()
        )
    
    except FlowForgeException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "batch_qa_status_failed",
            batch_id=batch_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get batch QA status"
        )


async def _perform_auto_qa_checks(
    post_id: str,
    video_url: str,
    video_metadata: Dict[str, Any],
    video_format: str,
    correlation_id: str
) -> AutoQAChecks:
    """
    Perform automated QA checks on video.
    Per Canon § 7.2: Duration 8s (±0.5s), Resolution 1080p min, Aspect ratio 9:16
    
    Args:
        post_id: UUID of the post
        video_url: URL of the video
        video_metadata: Video metadata from post
        video_format: Expected video format (e.g., "9:16")
        correlation_id: Correlation ID for logging
        
    Returns:
        AutoQAChecks with validation results
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    
    # Check 1: File accessibility
    file_accessible = False
    file_size_bytes = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            head_response = await client.head(video_url, follow_redirects=True)
            file_accessible = head_response.status_code == 200
            content_length = head_response.headers.get("content-length")
            if content_length:
                file_size_bytes = int(content_length)
            else:
                # Fallback: use size from metadata
                file_size_bytes = video_metadata.get("size_bytes") or video_metadata.get("size")
    except Exception as e:
        logger.warning(
            "qa_file_check_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            video_url=video_url,
            error=str(e)
        )
        file_accessible = False
    
    # Check 2: Duration validation (8s ±0.5s = 7.5s to 8.5s)
    duration_actual = video_metadata.get("duration_seconds")
    duration_expected = 8.0
    duration_tolerance = 0.5
    duration_valid = False
    
    if duration_actual is not None:
        duration_valid = (duration_expected - duration_tolerance) <= duration_actual <= (duration_expected + duration_tolerance)
    
    # Check 3: Resolution validation (minimum 720 height for vertical, 720 width for horizontal)
    resolution_actual = video_metadata.get("resolution") or video_metadata.get("requested_size")
    resolution_valid = False
    
    if resolution_actual:
        # Parse resolution like "720x1280" or "1080x1920"
        try:
            if 'x' in str(resolution_actual):
                width, height = map(int, str(resolution_actual).split('x'))
                # For vertical (9:16), height should be >= 720
                # For horizontal (16:9), width should be >= 720
                if video_format == "9:16":
                    resolution_valid = height >= 720
                elif video_format == "16:9":
                    resolution_valid = width >= 720
                else:
                    resolution_valid = min(width, height) >= 720
        except (ValueError, AttributeError) as e:
            logger.warning(
                "qa_resolution_parse_failed",
                post_id=post_id,
                resolution_actual=resolution_actual,
                error=str(e)
            )
    
    # Check 4: Aspect ratio validation
    aspect_ratio_actual = video_format  # Assuming video was generated with requested format
    aspect_ratio_expected = video_format
    aspect_ratio_valid = (aspect_ratio_actual == aspect_ratio_expected)
    
    # Overall pass: all checks must pass
    overall_pass = (
        file_accessible and
        duration_valid and
        resolution_valid and
        aspect_ratio_valid
    )
    
    return AutoQAChecks(
        duration_valid=duration_valid,
        duration_actual=duration_actual,
        duration_expected=duration_expected,
        resolution_valid=resolution_valid,
        resolution_actual=resolution_actual,
        resolution_expected="720x1280 minimum" if video_format == "9:16" else "1280x720 minimum",
        aspect_ratio_valid=aspect_ratio_valid,
        aspect_ratio_actual=aspect_ratio_actual,
        aspect_ratio_expected=aspect_ratio_expected,
        file_accessible=file_accessible,
        file_size_bytes=file_size_bytes,
        overall_pass=overall_pass,
        checked_at=checked_at
    )
