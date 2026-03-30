# app/features/blog/queries.py
"""
FLOW-FORGE Blog Queries
Supabase queries for blog fields on the posts table.
Per Constitution § V: Locality & Vertical Slices
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.adapters.supabase_client import get_supabase
from app.core.errors import FlowForgeException, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)


def _load_post_for_blog(post_id: str) -> Dict[str, Any]:
    """Fetch a post with blog-relevant fields."""
    supabase = get_supabase()
    response = (
        supabase.client.table("posts")
        .select("id", "batch_id", "seed_data", "blog_enabled", "blog_status", "blog_content", "blog_webflow_item_id", "blog_published_at", "topic_title")
        .eq("id", post_id)
        .execute()
    )
    if not response.data:
        raise FlowForgeException(
            code=ErrorCode.NOT_FOUND,
            message=f"Post {post_id} not found",
            details={"post_id": post_id},
        )
    post = response.data[0]
    seed_data = post.get("seed_data") or {}
    if isinstance(seed_data, str):
        try:
            seed_data = json.loads(seed_data)
        except json.JSONDecodeError:
            seed_data = {}
    post["seed_data"] = seed_data
    blog_content = post.get("blog_content") or {}
    if isinstance(blog_content, str):
        try:
            blog_content = json.loads(blog_content)
        except json.JSONDecodeError:
            blog_content = {}
    post["blog_content"] = blog_content
    return post


def toggle_blog_enabled(post_id: str, *, enabled: bool) -> Dict[str, Any]:
    """Toggle blog_enabled and update blog_status accordingly."""
    supabase = get_supabase()
    post = _load_post_for_blog(post_id)
    seed_data = post["seed_data"]

    if enabled and seed_data.get("script_review_status") == "removed":
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Cannot enable blog for removed posts",
            details={"post_id": post_id},
        )

    if enabled:
        new_status = "pending"
        if post.get("blog_content", {}).get("body"):
            new_status = "draft"
    else:
        new_status = "disabled"

    update_payload = {
        "blog_enabled": enabled,
        "blog_status": new_status,
    }

    response = (
        supabase.client.table("posts")
        .update(update_payload)
        .eq("id", post_id)
        .execute()
    )
    if not response.data:
        raise FlowForgeException(
            code=ErrorCode.NOT_FOUND,
            message=f"Failed to update post {post_id}",
            details={"post_id": post_id},
        )

    return response.data[0]


def update_blog_status(post_id: str, *, status: str, blog_content: Optional[Dict[str, Any]] = None, webflow_item_id: Optional[str] = None, published_at: Optional[str] = None) -> Dict[str, Any]:
    """Update blog_status and optionally blog_content/webflow fields."""
    supabase = get_supabase()
    update_payload: Dict[str, Any] = {"blog_status": status}
    if blog_content is not None:
        update_payload["blog_content"] = blog_content
    if webflow_item_id is not None:
        update_payload["blog_webflow_item_id"] = webflow_item_id
    if published_at is not None:
        update_payload["blog_published_at"] = published_at

    response = (
        supabase.client.table("posts")
        .update(update_payload)
        .eq("id", post_id)
        .execute()
    )
    if not response.data:
        raise FlowForgeException(
            code=ErrorCode.NOT_FOUND,
            message=f"Failed to update blog status for post {post_id}",
            details={"post_id": post_id},
        )
    return response.data[0]


def update_blog_content_fields(post_id: str, *, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge partial updates into blog_content JSONB."""
    supabase = get_supabase()
    post = _load_post_for_blog(post_id)
    current_content = post.get("blog_content") or {}

    for key, value in updates.items():
        if key in ("title", "body", "slug", "meta_description"):
            current_content[key] = value
    if "body" in updates:
        current_content["word_count"] = len(updates["body"].split())

    response = (
        supabase.client.table("posts")
        .update({"blog_content": current_content})
        .eq("id", post_id)
        .execute()
    )
    if not response.data:
        raise FlowForgeException(
            code=ErrorCode.NOT_FOUND,
            message=f"Failed to update blog content for post {post_id}",
            details={"post_id": post_id},
        )
    return response.data[0]


def get_blog_enabled_posts(batch_id: str) -> List[Dict[str, Any]]:
    """Get all blog-enabled posts for a batch."""
    supabase = get_supabase()
    response = (
        supabase.client.table("posts")
        .select("id", "batch_id", "post_type", "topic_title", "seed_data", "blog_enabled", "blog_status", "blog_content", "blog_webflow_item_id", "blog_published_at")
        .eq("batch_id", batch_id)
        .eq("blog_enabled", True)
        .execute()
    )
    return response.data or []
