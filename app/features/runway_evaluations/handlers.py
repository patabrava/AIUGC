"""Seedance evaluations through the selected direct ModelArk or legacy Runway host.

Preview is free; submission echoes the host-scoped budget estimate. Host-specific
feature flags, operator allowlists, quotas and immutable source checks apply.
"""

from __future__ import annotations

import asyncio
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.core.errors import SuccessResponse
from app.features.runway_evaluations import service
from app.features.runway_evaluations.schemas import RunwayEvaluationSubmitRequest

router = APIRouter(prefix="/runway-evaluations", tags=["seedance-evaluations"])
seedance_router = APIRouter(prefix="/seedance-evaluations", tags=["seedance-evaluations"])


def _operator_email(request: Request) -> Optional[str]:
    value = getattr(request.state, "user_email", None)
    return str(value).strip().lower() if value else None


def _correlation_id(request: Request, post_id: UUID) -> str:
    return str(getattr(request.state, "correlation_id", None) or f"runway_eval_{post_id}")


@seedance_router.get("/posts/{post_id}/preview", response_model=SuccessResponse)
@router.get("/posts/{post_id}/preview", response_model=SuccessResponse)
async def preview_runway_evaluation(
    post_id: UUID,
    request: Request,
    take_index: int = Query(default=0, ge=0, le=63),
    resolution: Optional[Literal["480p", "720p", "1080p"]] = Query(default=None),
) -> SuccessResponse:
    preview = await asyncio.to_thread(
        service.preview_evaluation,
        post_id=str(post_id),
        take_index=take_index,
        resolution=resolution,
        operator_email=_operator_email(request),
    )
    return SuccessResponse(data=preview)


@seedance_router.post("/posts/{post_id}", response_model=SuccessResponse)
@router.post("/posts/{post_id}", response_model=SuccessResponse)
async def submit_runway_evaluation(
    post_id: UUID,
    payload: RunwayEvaluationSubmitRequest,
    request: Request,
) -> SuccessResponse:
    evaluation = await asyncio.to_thread(
        service.submit_evaluation,
        post_id=str(post_id),
        take_index=payload.take_index,
        resolution=payload.resolution,
        confirm_estimated_credits=payload.confirm_estimated_credits,
        operator_email=_operator_email(request) or "",
        correlation_id=_correlation_id(request, post_id),
    )
    return SuccessResponse(data=evaluation)


@seedance_router.get("/posts/{post_id}", response_model=SuccessResponse)
@router.get("/posts/{post_id}", response_model=SuccessResponse)
async def list_runway_evaluations(post_id: UUID) -> SuccessResponse:
    evaluations = await asyncio.to_thread(service.list_evaluations, post_id=str(post_id))
    return SuccessResponse(data={"post_id": str(post_id), "evaluations": evaluations})

# Preserve previously bookmarked evaluation URLs during the provider refactor.

