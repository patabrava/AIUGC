"""
FLOW-FORGE Batches Handlers
FastAPI route handlers for batch operations.
Per Constitution § V: Locality & Vertical Slices
"""

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from app.features.batches.schemas import (
    CreateBatchRequest,
    BatchResponse,
    BatchListResponse,
    BatchDetailResponse,
    AdvanceStateRequest,
    DuplicateBatchRequest,
    ArchiveBatchRequest,
    PostDetail,
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
from app.features.topics.handlers import discover_topics_for_batch
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

        asyncio.get_running_loop().create_task(_run_discover_topics(batch["id"]))

        if _wants_html(request):
            batches, total = list_batches()
            batch_responses = [BatchResponse(**b).model_dump(mode="json") for b in batches]
            context = {
                "request": request,
                "batches": batch_responses,
                "total": total,
                "filters": {"archived": None, "limit": 50, "offset": 0}
            }
            response = templates.TemplateResponse("batches/partials/list_content.html", context)
            response.headers["HX-Trigger"] = json.dumps({
                "batch_created": {
                    "batch_id": batch["id"],
                    "brand": batch["brand"],
                    "expected_posts": payload.post_type_counts.total,
                }
            })
            return response

        return SuccessResponse(data=BatchResponse(**batch))
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("create_batch_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create batch"
        )


async def _run_discover_topics(batch_id: str) -> None:
    try:
        result = await discover_topics_for_batch(batch_id)
        logger.info(
            "batch_autoseed_complete",
            batch_id=batch_id,
            posts_created=result["posts_created"],
            new_state=result["state"]
        )
    except FlowForgeException as exc:
        logger.error(
            "batch_autoseed_failed",
            batch_id=batch_id,
            error=exc.message,
            details=exc.details
        )
    except Exception as exc:
        logger.exception(
            "batch_autoseed_unexpected_error",
            batch_id=batch_id,
            error=str(exc)
        )


def _normalize_seed_data(seed_data: Any) -> Dict[str, Any]:
    if isinstance(seed_data, str):
        try:
            seed_data = json.loads(seed_data)
        except json.JSONDecodeError:
            logger.warning("seed_data_json_decode_failed", raw_value=seed_data)
            return {}

    if not isinstance(seed_data, dict):
        return {}

    data = dict(seed_data)

    framework = data.get("framework")
    framework_map = {
        "PAL": "problem",
        "Testimonial": "testimonial",
        "Transformation": "transformation",
    }
    script_category = data.get("script_category") or framework_map.get(framework, "problem")
    data["script_category"] = script_category

    dialog_script = data.get("dialog_script")
    if not dialog_script:
        scripts = data.get("dialog_scripts") or {}
        category_key_map = {
            "problem": "problem_agitate_solution",
            "testimonial": "testimonial",
            "transformation": "transformation",
        }
        bucket_key = category_key_map.get(script_category)
        if bucket_key and isinstance(scripts, dict):
            bucket = scripts.get(bucket_key)
            if isinstance(bucket, list) and bucket:
                dialog_script = bucket[0]
    if dialog_script:
        data["dialog_script"] = dialog_script

    source = data.get("source")
    sources = data.get("sources")
    if not source and isinstance(sources, list) and sources:
        first_source = sources[0]
        if isinstance(first_source, dict):
            source = {
                "title": first_source.get("title"),
                "url": first_source.get("url"),
            }
            summary = data.get("source_summary")
            if summary:
                source["summary"] = summary
            data["source"] = source

    if not data.get("description"):
        summary = data.get("source_summary")
        if summary:
            data["description"] = summary

    if data.get("source") and not data["source"].get("summary") and data.get("description"):
        data["source"]["summary"] = data["description"]

    strict_seed = data.get("strict_seed")
    if isinstance(strict_seed, str):
        try:
            strict_seed = json.loads(strict_seed)
        except json.JSONDecodeError:
            logger.warning("strict_seed_json_decode_failed")
            strict_seed = None
        data["strict_seed"] = strict_seed

    if isinstance(strict_seed, dict):
        facts = strict_seed.get("facts")
        if isinstance(facts, list) and facts:
            data.setdefault("strict_fact", facts[0])

    return data


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
        from app.features.topics.queries import get_posts_by_batch
        
        batch = get_batch_by_id(batch_id)
        posts_summary = get_batch_posts_summary(batch_id)
        posts_data = get_posts_by_batch(batch_id)
        
        posts_list = []
        for p in posts_data:
            normalized_seed = _normalize_seed_data(p.get("seed_data"))

            spoken_duration = p.get("spoken_duration")
            try:
                spoken_duration_value = float(spoken_duration) if spoken_duration is not None else 0.0
            except (TypeError, ValueError):
                logger.warning(
                    "post_spoken_duration_parse_failed",
                    post_id=p.get("id"),
                    value=spoken_duration
                )
                spoken_duration_value = 0.0

            posts_list.append(
                PostDetail(
                    id=p["id"],
                    post_type=p["post_type"],
                    topic_title=p["topic_title"],
                    topic_rotation=p["topic_rotation"],
                    topic_cta=p["topic_cta"],
                    spoken_duration=spoken_duration_value,
                    state=p.get("state"),
                    seed_data=normalized_seed,
                    created_at=p.get("created_at"),
                    updated_at=p.get("updated_at"),
                )
            )

        batch_detail = {**batch, **posts_summary, "posts": posts_list}

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


@router.get("/{batch_id}/status", response_model=SuccessResponse)
async def get_batch_status(batch_id: str):
    """Return lightweight status payload for polling newly created batches."""
    try:
        batch = get_batch_by_id(batch_id)
        posts_summary = get_batch_posts_summary(batch_id)

        payload = {
            "id": batch["id"],
            "state": batch["state"],
            "posts_count": posts_summary["posts_count"],
            "posts_by_state": posts_summary["posts_by_state"],
            "updated_at": batch["updated_at"],
        }

        return SuccessResponse(data=payload)

    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("get_batch_status_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch batch status"
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
