"""
FLOW-FORGE Batches Handlers
FastAPI route handlers for batch operations.
Per Constitution § V: Locality & Vertical Slices
"""

import json
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from app.features.batches.schemas import (
    CreateBatchRequest,
    BatchResponse,
    BatchListResponse,
    BatchDetailResponse,
    AdvanceStateRequest,
    DuplicateBatchRequest,
    ArchiveBatchRequest
)
from app.features.batches.queries import (
    create_batch,
    get_batch_by_id,
    list_batches,
    update_batch_state,
    archive_batch,
    duplicate_batch,
    get_batch_posts_summary
)
from app.core.errors import FlowForgeException, SuccessResponse
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/batches", tags=["batches"])
templates = Jinja2Templates(directory="templates")


def _wants_html(request: Request) -> bool:
    """Determine if client expects HTML response."""
    hx_header = request.headers.get("HX-Request")
    if hx_header and hx_header.lower() == "true":
        return True
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "application/xhtml+xml" in accept


@router.post("", response_model=SuccessResponse, status_code=status.HTTP_201_CREATED)
async def create_batch_endpoint(request: Request):
    """
    Create a new batch.
    Per Canon § 3.2: Initializes batch in S1_SETUP state.
    """
    try:
        payload: Optional[CreateBatchRequest] = None

        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                data = await request.json()
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": "Invalid JSON payload", "error": str(exc)}
                ) from exc

            payload = CreateBatchRequest.model_validate(data)
        else:
            form = await request.form()
            post_type_counts: Dict[str, int] = {
                "value": int(form.get("post_type_counts.value", 0) or 0),
                "lifestyle": int(form.get("post_type_counts.lifestyle", 0) or 0),
                "product": int(form.get("post_type_counts.product", 0) or 0)
            }
            payload = CreateBatchRequest.model_validate(
                {
                    "brand": str(form.get("brand", "")).strip(),
                    "post_type_counts": post_type_counts
                }
            )

        batch = create_batch(
            brand=payload.brand,
            post_type_counts=payload.post_type_counts.model_dump()
        )
        
        if _wants_html(request):
            batches, total = list_batches()
            batch_responses = [BatchResponse(**b).model_dump(mode="json") for b in batches]
            context = {
                "request": request,
                "batches": batch_responses,
                "total": total,
                "filters": {"archived": None, "limit": 50, "offset": 0}
            }
            return templates.TemplateResponse("batches/partials/list_content.html", context)

        return SuccessResponse(data=BatchResponse(**batch))
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("create_batch_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create batch"
        )


@router.get("", response_model=SuccessResponse)
async def list_batches_endpoint(
    request: Request,
    archived: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0
):
    """
    List batches with optional filtering.
    """
    try:
        batches, total = list_batches(archived=archived, limit=limit, offset=offset)

        batch_responses = [BatchResponse(**batch) for batch in batches]

        if _wants_html(request):
            batch_dicts = [batch.model_dump(mode="json") for batch in batch_responses]
            context = {
                "request": request,
                "batches": batch_dicts,
                "total": total,
                "filters": {
                    "archived": archived,
                    "limit": limit,
                    "offset": offset
                }
            }
            template_name = (
                "batches/partials/list_content.html"
                if request.headers.get("HX-Request") == "true"
                else "batches/list.html"
            )
            return templates.TemplateResponse(template_name, context)

        return SuccessResponse(
            data=BatchListResponse(batches=batch_responses, total=total)
        )

    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("list_batches_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list batches"
        )


@router.get("/{batch_id}", response_model=SuccessResponse)
async def get_batch_endpoint(request: Request, batch_id: str):
    """
    Get batch by ID with posts summary.
    """
    try:
        batch = get_batch_by_id(batch_id)
        posts_summary = get_batch_posts_summary(batch_id)

        batch_detail = {**batch, **posts_summary}

        if _wants_html(request):
            batch_model = BatchDetailResponse(**batch_detail)
            context = {
                "request": request,
                "batch": batch_model.model_dump(mode="json")
            }
            return templates.TemplateResponse("batches/detail.html", context)

        return SuccessResponse(data=BatchDetailResponse(**batch_detail))
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("get_batch_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get batch"
        )


@router.put("/{batch_id}/state", response_model=SuccessResponse)
async def advance_batch_state_endpoint(batch_id: str, request: AdvanceStateRequest):
    """
    Advance batch to target state.
    Per Constitution § VII: Validates state transitions.
    """
    try:
        batch = update_batch_state(batch_id, request.target_state)
        
        return SuccessResponse(data=BatchResponse(**batch))
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("advance_state_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to advance batch state"
        )


@router.post("/{batch_id}/duplicate", response_model=SuccessResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_batch_endpoint(batch_id: str, request: DuplicateBatchRequest):
    """
    Duplicate a batch.
    """
    try:
        new_batch = duplicate_batch(batch_id, request.new_brand)
        
        return SuccessResponse(data=BatchResponse(**new_batch))
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("duplicate_batch_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to duplicate batch"
        )


@router.put("/{batch_id}/archive", response_model=SuccessResponse)
async def archive_batch_endpoint(batch_id: str, request: ArchiveBatchRequest):
    """
    Archive or unarchive a batch.
    """
    try:
        batch = archive_batch(batch_id, request.archived)
        
        return SuccessResponse(data=BatchResponse(**batch))
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("archive_batch_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to archive batch"
        )
