"""
FLOW-FORGE Batches Handlers
FastAPI route handlers for batch operations.
Per Constitution § V: Locality & Vertical Slices
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, StreamingResponse
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
from app.core.video_profiles import normalize_target_length_tier
from app.features.topics.handlers import discover_topics_for_batch
from app.features.topics.handlers import (
    get_seeding_events,
    get_seeding_progress,
    is_batch_discovery_active,
    schedule_batch_discovery,
    start_seeding_interaction,
    update_seeding_progress,
)
from app.features.publish.handlers import (
    _effective_meta_connection,
    _sanitize_meta_connection as _publish_sanitize_meta_connection,
)
from app.features.blog.schemas import normalize_blog_content

try:
    from app.features.publish.tiktok import get_tiktok_publish_state
except ModuleNotFoundError:
    async def get_tiktok_publish_state() -> Dict[str, Any]:
        """Keep batch detail rendering alive when TikTok code is not deployed yet."""
        return {"status": "unavailable"}
from app.core.errors import FlowForgeException, SuccessResponse, StateTransitionError
from app.core.logging import get_logger
from app.core.states import BatchState

logger = get_logger(__name__)

router = APIRouter(prefix="/batches", tags=["batches"])
templates = Jinja2Templates(directory="templates")
DETAIL_JS_VERSION = str(Path("static/js/batches/detail.js").stat().st_mtime_ns)


def _wants_html(request: Request) -> bool:
    """Determine if client expects HTML response."""
    hx_header = request.headers.get("HX-Request")
    if hx_header and hx_header.lower() == "true":
        return True
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "application/xhtml+xml" in accept


def _is_hx_history_restore_request(request: Request) -> bool:
    """Detect HTMX back/forward restoration requests that must receive a full document."""
    return request.headers.get("HX-History-Restore-Request", "").lower() == "true"


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
                    "post_type_counts": post_type_counts,
                    "target_length_tier": int(form.get("target_length_tier", 8) or 8)
                }
            )

        batch = create_batch(
            brand=payload.brand,
            post_type_counts=payload.post_type_counts.model_dump(),
            target_length_tier=normalize_target_length_tier(payload.target_length_tier)
        )
        start_seeding_interaction(
            batch_id=batch["id"],
            brand=batch["brand"],
            expected_posts=payload.post_type_counts.total,
        )

        schedule_batch_discovery(batch["id"], reason="batch_create")

        if _wants_html(request):
            batches, total = list_batches()
            batch_responses = [BatchResponse(**b).model_dump(mode="json") for b in batches]
            context = {
                "request": request,
                "batches": batch_responses,
                "total": total,
                "filters": {"archived": None, "limit": 50, "offset": 0}
            }
            response = templates.TemplateResponse("batches/list.html", context)
            response.headers["HX-Trigger"] = json.dumps({
                "batch_created": {
                    "batch_id": batch["id"],
                    "brand": batch["brand"],
                    "expected_posts": payload.post_type_counts.total,
                    "target_length_tier": batch.get("target_length_tier"),
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
        update_seeding_progress(
            batch_id,
            stage="failed",
            stage_label="Topic generation stopped",
            detail_message=exc.message,
            is_retrying=False,
            retry_message=None,
        )
        logger.error(
            "batch_autoseed_failed",
            batch_id=batch_id,
            error=exc.message,
            details=exc.details
        )
    except Exception as exc:
        update_seeding_progress(
            batch_id,
            stage="failed",
            stage_label="Topic generation stopped",
            detail_message="The seeding run failed before script review could start.",
            is_retrying=False,
            retry_message=None,
        )
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

    if not data.get("script_review_status"):
        data["script_review_status"] = "pending"

    return data


def _normalize_json_object(value: Any, *, field_name: str, post_id: Optional[str] = None) -> Dict[str, Any]:
    """Defensively parse JSON-like post fields into dictionaries."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            logger.warning(
                "post_json_decode_failed",
                field_name=field_name,
                post_id=post_id,
            )
            return {}

    return dict(value) if isinstance(value, dict) else {}


def _normalize_string_list(value: Any) -> list[str]:
    """Normalize list-like publish fields into plain string lists."""
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value:
        return [value]
    return []


def _sanitize_meta_connection(meta_connection: Any) -> Dict[str, Any]:
    """Strip token material before Meta connection data reaches the browser."""
    normalized = _normalize_json_object(meta_connection, field_name="meta_connection")
    return _publish_sanitize_meta_connection(normalized)


def _resolve_review_caption(post: Dict[str, Any]) -> str:
    seed_data = post.get("seed_data") or {}
    caption_bundle = seed_data.get("caption_bundle") or {}
    return (
        str(post.get("publish_caption") or "").strip()
        or str(caption_bundle.get("selected_body") or "").strip()
        or str(seed_data.get("caption") or "").strip()
        or str(seed_data.get("description") or "").strip()
    )


def _build_batch_detail_view(batch_detail: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare template-only derived data for the batch detail page."""
    batch_state = batch_detail.get("state")
    posts = batch_detail.get("posts") or []
    polling_video_statuses = {
        "submitted",
        "processing",
        "extended_submitted",
        "extended_processing",
        "caption_pending",
        "caption_processing",
    }

    visible_posts = []
    active_posts_count = 0
    active_video_poll_count = 0
    prompt_ready_count = 0
    qa_passed_count = 0
    scheduled_count = 0
    approved_scripts_count = 0
    removed_scripts_count = 0
    pending_scripts_count = 0

    for post in posts:
        seed_data = post.get("seed_data") or {}
        review_status = seed_data.get("script_review_status") or "pending"
        is_removed = review_status == "removed" or seed_data.get("video_excluded") is True
        post_view = dict(post)
        post_view["review_caption"] = _resolve_review_caption(post)

        if batch_state == BatchState.S2_SEEDED.value or not is_removed:
            visible_posts.append(post_view)

        if review_status == "approved":
            approved_scripts_count += 1
        elif review_status == "removed":
            removed_scripts_count += 1
        else:
            pending_scripts_count += 1

        if is_removed:
            continue

        active_posts_count += 1
        if post.get("video_status") in polling_video_statuses or not post.get("video_url"):
            active_video_poll_count += 1
        if post.get("video_prompt_json"):
            prompt_ready_count += 1
        if post.get("qa_pass"):
            qa_passed_count += 1
        if post.get("scheduled_at"):
            scheduled_count += 1

    meta_publish_state = batch_detail.get("meta_connection") or {}
    tiktok_publish_state = batch_detail.get("tiktok_connection") or {}

    return {
        "should_poll_prompts": batch_state == BatchState.S5_PROMPTS_BUILT.value,
        "should_poll_videos": active_video_poll_count > 0,
        "progress_states": [
            {"code": BatchState.S1_SETUP.value, "label": "Setup"},
            {"code": BatchState.S2_SEEDED.value, "label": "Seeded"},
            {"code": BatchState.S4_SCRIPTED.value, "label": "Scripted"},
            {"code": BatchState.S5_PROMPTS_BUILT.value, "label": "Prompts"},
            {"code": BatchState.S6_QA.value, "label": "QA"},
            {"code": BatchState.S7_PUBLISH_PLAN.value, "label": "Plan"},
            {"code": BatchState.S8_COMPLETE.value, "label": "Complete"},
        ],
        "visible_posts": visible_posts,
        "active_posts_count": active_posts_count,
        "prompt_ready_count": prompt_ready_count,
        "qa_passed_count": qa_passed_count,
        "scheduled_count": scheduled_count,
        "review_summary": {
            "total_posts_count": len(posts),
            "approved_scripts_count": approved_scripts_count,
            "removed_scripts_count": removed_scripts_count,
            "pending_scripts_count": pending_scripts_count,
        },
        "publish_posts_json": [
            {
                "id": post.get("id"),
                "type": post.get("post_type"),
                "title": post.get("topic_title"),
                "canonicalTopic": (post.get("seed_data") or {}).get("canonical_topic") or "",
                "researchTitle": (post.get("seed_data") or {}).get("research_title") or "",
                "caption": post.get("publish_caption") or "",
                "captionOptions": [
                    {
                        "key": variant.get("key"),
                        "label": variant.get("key").replace("_", " ").title() if variant.get("key") else "",
                        "body": variant.get("body"),
                    }
                    for variant in (((post.get("seed_data") or {}).get("caption_bundle") or {}).get("variants") or [])
                    if isinstance(variant, dict) and variant.get("body")
                ],
                "selectedCaptionKey": ((post.get("seed_data") or {}).get("caption_bundle") or {}).get("selected_key") or "",
                "videoUrl": post.get("video_url"),
                "publishStatus": post.get("publish_status") or "pending",
                "scheduledAt": post.get("scheduled_at"),
                "socialNetworks": _normalize_string_list(post.get("social_networks")),
            }
            for post in posts
            if not (post.get("seed_data") or {}).get("video_excluded")
        ],
        "meta_publish_state": meta_publish_state,
        "tiktok_publish_state": tiktok_publish_state,
        "selected_meta_page": meta_publish_state.get("selected_page") or {},
        "selected_instagram_account": meta_publish_state.get("selected_instagram") or {},
        "available_meta_pages": meta_publish_state.get("available_pages") or [],
    }


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
            return templates.TemplateResponse("batches/list.html", context)

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

            video_prompt = p.get("video_prompt_json")
            if isinstance(video_prompt, str):
                try:
                    video_prompt = json.loads(video_prompt)
                except json.JSONDecodeError:
                    logger.warning(
                        "video_prompt_json_decode_failed",
                        post_id=p.get("id")
                    )
                    video_prompt = None

            video_metadata = p.get("video_metadata")
            if isinstance(video_metadata, str):
                try:
                    video_metadata = json.loads(video_metadata)
                except json.JSONDecodeError:
                    logger.warning(
                        "video_metadata_json_decode_failed",
                        post_id=p.get("id"),
                        value=video_metadata
                    )
                    video_metadata = None

            qa_auto_checks = p.get("qa_auto_checks")
            if isinstance(qa_auto_checks, str):
                try:
                    qa_auto_checks = json.loads(qa_auto_checks)
                except json.JSONDecodeError:
                    logger.warning(
                        "qa_auto_checks_json_decode_failed",
                        post_id=p.get("id")
                    )
                    qa_auto_checks = None

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

            platform_ids = _normalize_json_object(
                p.get("platform_ids"),
                field_name="platform_ids",
                post_id=p.get("id"),
            )
            publish_results = _normalize_json_object(
                p.get("publish_results"),
                field_name="publish_results",
                post_id=p.get("id"),
            )
            social_networks = _normalize_string_list(p.get("social_networks"))

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
                    video_prompt_json=video_prompt,
                    video_status=p.get("video_status"),
                    video_url=p.get("video_url"),
                    video_metadata=video_metadata,
                    video_operation_id=p.get("video_operation_id"),
                    video_provider=p.get("video_provider"),
                    qa_pass=p.get("qa_pass"),
                    qa_notes=p.get("qa_notes"),
                    qa_auto_checks=qa_auto_checks,
                    scheduled_at=p.get("scheduled_at"),
                    social_networks=social_networks,
                    publish_caption=p.get("publish_caption") or normalized_seed.get("caption") or normalized_seed.get("description"),
                    publish_status=p.get("publish_status"),
                    platform_ids=platform_ids,
                    publish_results=publish_results,
                    blog_enabled=p.get("blog_enabled", False),
                    blog_status=p.get("blog_status", "disabled"),
                    blog_content=normalize_blog_content(
                        p.get("blog_content") or {},
                        fallback_name=p.get("topic_title") or normalized_seed.get("canonical_topic", ""),
                        scheduled_at=str(p.get("blog_scheduled_at") or "") or None,
                        published_at=str(p.get("blog_published_at") or "") or None,
                    ),
                    blog_webflow_item_id=p.get("blog_webflow_item_id"),
                    blog_scheduled_at=p.get("blog_scheduled_at"),
                    blog_published_at=p.get("blog_published_at"),
                    created_at=p.get("created_at"),
                    updated_at=p.get("updated_at"),
                )
            )

        batch_detail = {
            **batch,
            **posts_summary,
            "meta_connection": _sanitize_meta_connection(
                _effective_meta_connection(batch_id, batch.get("meta_connection"))
            ),
            "tiktok_connection": await get_tiktok_publish_state(),
            "posts": posts_list,
        }

        if _wants_html(request):
            batch_model = BatchDetailResponse(**batch_detail)
            batch_payload = batch_model.model_dump(mode="json")
            context = {
                "request": request,
                "batch": batch_payload,
                "batch_view": _build_batch_detail_view(batch_payload),
                "static_version": DETAIL_JS_VERSION,
            }
            template_name = "batches/detail.html"
            if request.headers.get("HX-Request") == "true" and not _is_hx_history_restore_request(request):
                template_name = "batches/detail.html"
            return templates.TemplateResponse(template_name, context)

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
        progress = get_seeding_progress(batch_id)

        if (
            batch["state"] == BatchState.S1_SETUP.value
            and posts_summary["posts_count"] == 0
            and (progress is None or progress.get("stage") in {"failed", "completed"})
            and not is_batch_discovery_active(batch_id)
        ):
            start_seeding_interaction(
                batch_id=batch["id"],
                brand=batch["brand"],
                expected_posts=sum((batch.get("post_type_counts") or {}).values()),
            )
            schedule_batch_discovery(batch_id, reason="status_recovery")
            progress = get_seeding_progress(batch_id)

        payload = {
            "id": batch["id"],
            "state": batch["state"],
            "posts_count": posts_summary["posts_count"],
            "posts_by_state": posts_summary["posts_by_state"],
            "updated_at": batch["updated_at"],
            "progress": progress,
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


@router.get("/{batch_id}/progress/stream")
async def stream_batch_progress(request: Request, batch_id: str, last_event_id: Optional[str] = None):
    """Stream live seeding progress events with resumable replay."""

    async def event_stream():
        last_seen = last_event_id or request.headers.get("last-event-id")

        while True:
            if await request.is_disconnected():
                break

            events = get_seeding_events(batch_id, last_seen)
            if events:
                for event in events:
                    payload = json.dumps(event)
                    yield f"id: {event['event_id']}\ndata: {payload}\n\n"
                    last_seen = event["event_id"]

                terminal = events[-1]["event_type"]
                if terminal in {"interaction.complete", "interaction.failed"}:
                    break
            else:
                progress = get_seeding_progress(batch_id)
                if progress and progress.get("stage") in {"completed", "failed"}:
                    break

            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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


@router.put("/{batch_id}/approve-scripts", response_model=SuccessResponse)
async def approve_scripts_endpoint(batch_id: str, request: Request):
    """
    Approve scripts and advance batch from S2_SEEDED to S4_SCRIPTED.
    Per Canon § 3.2: Manual script approval (optional override).
    """
    try:
        batch = get_batch_by_id(batch_id)
        
        try:
            current_state = BatchState(batch["state"])
        except ValueError:
            raise FlowForgeException(
                code="state_transition_error",
                message=f"Unknown batch state {batch['state']}",
                details={"current_state": batch["state"], "required_state": BatchState.S2_SEEDED.value}
            )

        if current_state == BatchState.S4_SCRIPTED:
            logger.info(
                "scripts_already_approved",
                batch_id=batch_id,
                current_state=current_state.value
            )
            updated_batch = batch
        elif current_state != BatchState.S2_SEEDED:
            raise FlowForgeException(
                code="state_transition_error",
                message=f"Cannot approve scripts from state {batch['state']}",
                details={"current_state": batch["state"], "required_state": BatchState.S2_SEEDED.value}
            )
        else:
            from app.adapters.supabase_client import get_supabase

            supabase = get_supabase().client
            posts_response = supabase.table("posts").select("id", "seed_data").eq("batch_id", batch_id).execute()
            posts = posts_response.data or []
            if not posts:
                raise StateTransitionError("Cannot approve scripts without posts", {"batch_id": batch_id})

            approved_count = 0
            pending_post_ids = []
            for post in posts:
                seed_data = post.get("seed_data") or {}
                if isinstance(seed_data, str):
                    try:
                        seed_data = json.loads(seed_data)
                    except json.JSONDecodeError:
                        seed_data = {}
                review_status = seed_data.get("script_review_status") or "pending"
                if review_status == "approved":
                    approved_count += 1
                elif review_status != "removed":
                    pending_post_ids.append(post["id"])

            if pending_post_ids:
                raise StateTransitionError(
                    "Every post must be approved or removed before advancing.",
                    {"pending_post_ids": pending_post_ids}
                )

            if approved_count == 0:
                raise StateTransitionError(
                    "At least one approved script is required before advancing.",
                    {"batch_id": batch_id}
                )

            updated_batch = update_batch_state(batch_id, BatchState.S4_SCRIPTED)

        logger.info(
            "scripts_approved",
            batch_id=batch_id,
            previous_state=current_state.value,
            new_state=BatchState.S4_SCRIPTED.value
        )
        
        if _wants_html(request):
            response = PlainTextResponse("", status_code=status.HTTP_200_OK)
            response.headers["HX-Refresh"] = "true"
            return response
        
        return SuccessResponse(data=BatchResponse(**updated_batch))
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("approve_scripts_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to approve scripts"
        )


@router.put("/{batch_id}/advance-to-publish", response_model=SuccessResponse)
async def advance_to_publish_endpoint(batch_id: str, request: Request):
    """
    Advance batch from S6_QA to S7_PUBLISH_PLAN.
    Per Canon § 3.2: S6_QA → S7_PUBLISH_PLAN requires all posts qa_pass=true.
    Per Constitution § VII: State Machine Discipline - explicit guards.
    """
    try:
        from app.adapters.supabase_client import get_supabase
        
        batch = get_batch_by_id(batch_id)
        
        try:
            current_state = BatchState(batch["state"])
        except ValueError:
            raise FlowForgeException(
                code="state_transition_error",
                message=f"Unknown batch state {batch['state']}",
                details={"current_state": batch["state"], "required_state": BatchState.S6_QA.value}
            )
        
        if current_state != BatchState.S6_QA:
            raise FlowForgeException(
                code="state_transition_error",
                message=f"Cannot advance to publish from state {batch['state']}",
                details={"current_state": batch["state"], "required_state": BatchState.S6_QA.value}
            )
        
        # Guard: Verify all active posts have qa_pass=true
        supabase = get_supabase().client
        posts_response = supabase.table("posts").select("id, qa_pass, seed_data").eq("batch_id", batch_id).execute()
        posts = posts_response.data
        
        if not posts:
            raise FlowForgeException(
                code="state_transition_error",
                message="Cannot advance batch with no posts",
                details={"batch_id": batch_id}
            )

        active_posts = []
        for post in posts:
            seed_data = post.get("seed_data") or {}
            if isinstance(seed_data, str):
                try:
                    seed_data = json.loads(seed_data)
                except json.JSONDecodeError:
                    seed_data = {}
            if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
                continue
            active_posts.append(post)

        if not active_posts:
            raise FlowForgeException(
                code="state_transition_error",
                message="Cannot advance batch with no active posts",
                details={"batch_id": batch_id}
            )

        posts_not_approved = [p["id"] for p in active_posts if p.get("qa_pass") is not True]
        
        if posts_not_approved:
            raise FlowForgeException(
                code="state_transition_error",
                message=f"Cannot advance to publish. {len(posts_not_approved)} post(s) not approved.",
                details={
                    "batch_id": batch_id,
                    "total_posts": len(active_posts),
                    "approved_posts": len(active_posts) - len(posts_not_approved),
                    "pending_posts": posts_not_approved[:5]  # Show first 5
                }
            )
        
        # All guards passed - advance state
        updated_batch = update_batch_state(batch_id, BatchState.S7_PUBLISH_PLAN)
        
        logger.info(
            "batch_advanced_to_publish",
            batch_id=batch_id,
            previous_state=current_state.value,
            new_state=BatchState.S7_PUBLISH_PLAN.value,
            total_posts=len(active_posts)
        )
        
        if _wants_html(request):
            response = PlainTextResponse("", status_code=status.HTTP_200_OK)
            response.headers["HX-Refresh"] = "true"
            return response
        
        return SuccessResponse(data=BatchResponse(**updated_batch))
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception("advance_to_publish_failed", batch_id=batch_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to advance to publish"
        )
