"""
FLOW-FORGE Blog Handlers
FastAPI route handlers for blog post operations.
Per Constitution § V: Locality & Vertical Slices
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.errors import FlowForgeException, SuccessResponse
from app.core.logging import get_logger
from app.features.blog.queries import (
    _load_post_for_blog,
    get_blog_enabled_posts,
    toggle_blog_enabled,
    update_blog_status,
    update_blog_content_fields,
)
from app.features.blog.schemas import (
    BlogContentUpdateRequest,
    BlogPublishResponse,
    BlogScheduleRequest,
    BlogScheduleResponse,
    BlogToggleResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/blog", tags=["blog"])


class ToggleRequest(BaseModel):
    enabled: bool = Field(..., description="Whether to enable blog for this post")


@router.put("/posts/{post_id}/blog-toggle", response_model=SuccessResponse)
async def toggle_blog(post_id: str, request: Request):
    """Toggle blog_enabled for a post."""
    try:
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
            payload = ToggleRequest.model_validate(data)
            enabled = payload.enabled
        else:
            form = await request.form()
            enabled = str(form.get("enabled", "")).lower() in ("true", "1", "on")

        result = toggle_blog_enabled(post_id, enabled=enabled)
        return SuccessResponse(
            data=BlogToggleResponse(
                post_id=post_id,
                blog_enabled=result.get("blog_enabled", False),
                blog_status=result.get("blog_status", "disabled"),
            ).model_dump(),
        )
    except FlowForgeException:
        raise
    except Exception as exc:
        logger.error("blog_toggle_error", post_id=post_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/posts/{post_id}/blog/generate", response_model=SuccessResponse)
async def generate_blog_draft(post_id: str):
    """Generate a blog draft from the research dossier."""
    try:
        from app.features.blog.blog_runtime import generate_blog_draft as run_generate

        result = run_generate(post_id)
        if result.get("error"):
            return SuccessResponse(
                ok=False,
                data=result,
            )
        return SuccessResponse(
            data=result,
        )
    except FlowForgeException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.error("blog_generate_error", post_id=post_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/batches/{batch_id}/blog/generate-all", response_model=SuccessResponse)
async def generate_all_blog_drafts(batch_id: str):
    """Generate blog drafts for all blog-enabled posts in a batch."""
    try:
        from app.features.blog.blog_runtime import generate_blog_draft as run_generate

        posts = get_blog_enabled_posts(batch_id)
        results = []
        for post in posts:
            seed_data = post.get("seed_data") or {}
            if isinstance(seed_data, str):
                import json
                seed_data = json.loads(seed_data)
            if seed_data.get("script_review_status") != "approved":
                continue
            if post.get("blog_status") in ("generating", "draft", "scheduled", "publishing", "published"):
                continue
            result = run_generate(post["id"])
            results.append({"post_id": post["id"], "status": "draft" if not result.get("error") else "failed"})

        return SuccessResponse(
            data={"results": results},
        )
    except FlowForgeException:
        raise
    except Exception as exc:
        logger.error("blog_generate_all_error", batch_id=batch_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.put("/posts/{post_id}/blog/schedule", response_model=SuccessResponse)
async def schedule_blog_publish(post_id: str, request: BlogScheduleRequest):
    """Schedule a generated blog post for later publishing."""
    try:
        if request.scheduled_at.tzinfo is None:
            scheduled_at = request.scheduled_at.replace(tzinfo=timezone.utc)
        else:
            scheduled_at = request.scheduled_at.astimezone(timezone.utc)

        if scheduled_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Blog schedule must be in the future.")

        post = _load_post_for_blog(post_id)
        blog_content = post.get("blog_content") or {}
        if not post.get("blog_enabled"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enable blog generation before scheduling.")
        if not blog_content.get("body"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Generate the blog draft before scheduling.")

        updated = update_blog_status(
            post_id,
            status="scheduled",
            scheduled_at=scheduled_at.isoformat(),
        )
        return SuccessResponse(
            data=BlogScheduleResponse(
                post_id=post_id,
                blog_status=updated.get("blog_status", "scheduled"),
                blog_scheduled_at=str(updated.get("blog_scheduled_at") or scheduled_at.isoformat()),
            ).model_dump(),
        )
    except FlowForgeException:
        raise
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("blog_schedule_error", post_id=post_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.put("/posts/{post_id}/blog/content", response_model=SuccessResponse)
async def update_blog_content(post_id: str, request: Request):
    """Save edits to blog content fields (title, body, slug, meta_description)."""
    try:
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            data = await request.json()
            payload = BlogContentUpdateRequest.model_validate(data)
        else:
            form = await request.form()
            data = {k: v for k, v in form.items() if v}
            payload = BlogContentUpdateRequest.model_validate(data)

        updates = payload.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No fields to update")

        update_blog_content_fields(post_id, updates=updates)
        return SuccessResponse(data={"post_id": post_id, "updated_fields": list(updates.keys())})
    except FlowForgeException:
        raise
    except Exception as exc:
        logger.error("blog_content_update_error", post_id=post_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/posts/{post_id}/blog/publish", response_model=SuccessResponse)
async def publish_blog_to_webflow(post_id: str):
    """Push blog post to Webflow CMS."""
    try:
        from app.features.blog.blog_runtime import publish_blog_post

        result = publish_blog_post(post_id)
        return SuccessResponse(
            data=BlogPublishResponse(
                post_id=post_id,
                blog_status=result["blog_status"],
                webflow_item_id=result["webflow_item_id"],
                blog_published_at=result["blog_published_at"],
            ).model_dump(),
        )
    except FlowForgeException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.error("blog_publish_error", post_id=post_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


async def run_scheduled_blog_publish_job() -> dict:
    """Entry point used by the in-process scheduler in app lifespan."""
    from app.features.blog.blog_runtime import run_scheduled_blog_publish_job as run_job

    return await run_job()


@router.post("/cron/dispatch", response_model=SuccessResponse)
async def cron_dispatch_blog_publish(request: Request):
    """Cron-compatible endpoint for dispatching due Webflow blog posts."""
    settings = get_settings()
    authorization = request.headers.get("authorization")
    if not authorization or authorization != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    from app.features.blog.blog_runtime import dispatch_due_blog_posts

    result = await dispatch_due_blog_posts(trigger="cron")
    return SuccessResponse(data=result)
