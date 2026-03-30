"""
FLOW-FORGE Blog Handlers
FastAPI route handlers for blog post operations.
Per Constitution § V: Locality & Vertical Slices
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.errors import FlowForgeException, SuccessResponse
from app.core.logging import get_logger
from app.features.blog.queries import (
    get_blog_enabled_posts,
    toggle_blog_enabled,
    update_blog_content_fields,
)
from app.features.blog.schemas import BlogContentUpdateRequest, BlogPublishResponse, BlogToggleResponse

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
            if post.get("blog_status") in ("generating", "draft", "published"):
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
        from datetime import datetime, timezone
        from app.features.blog.queries import _load_post_for_blog, update_blog_status
        from app.features.blog.webflow_client import WebflowClient

        settings = get_settings()
        post = _load_post_for_blog(post_id)
        blog_content = post.get("blog_content") or {}

        if not blog_content.get("body"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No blog content to publish")

        client = WebflowClient(
            api_token=settings.webflow_api_token,
            collection_id=settings.webflow_collection_id,
            site_id=settings.webflow_site_id,
        )

        field_data = {
            "name": blog_content.get("title", ""),
            "slug": blog_content.get("slug", ""),
            "post-body": blog_content.get("body", ""),
            "meta-description": blog_content.get("meta_description", ""),
        }

        existing_item_id = post.get("blog_webflow_item_id")
        if existing_item_id:
            client.update_item(existing_item_id, field_data)
            item_id = existing_item_id
        else:
            item_id = client.create_item(field_data)

        client.publish_site()
        published_at = datetime.now(timezone.utc).isoformat()

        update_blog_status(
            post_id,
            status="published",
            webflow_item_id=item_id,
            published_at=published_at,
        )

        return SuccessResponse(
            data=BlogPublishResponse(
                post_id=post_id,
                blog_status="published",
                webflow_item_id=item_id,
                blog_published_at=published_at,
            ).model_dump(),
        )
    except FlowForgeException:
        raise
    except Exception as exc:
        logger.error("blog_publish_error", post_id=post_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
