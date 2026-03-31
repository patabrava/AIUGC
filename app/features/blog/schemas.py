# app/features/blog/schemas.py
"""
FLOW-FORGE Blog Schemas
Pydantic models for blog post generation and Webflow publishing.
Per Constitution § II: Validated Boundaries
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class BlogSource(BaseModel):
    """A source reference from the research dossier."""
    title: str = Field(..., min_length=1, max_length=400, description="Source title")
    url: str = Field(..., min_length=1, description="Source URL")


class BlogContent(BaseModel):
    """Generated blog article content stored in posts.blog_content."""
    title: str = Field(..., min_length=1, max_length=300, description="Blog post title")
    body: str = Field(..., min_length=1, description="Blog post body text")
    slug: str = Field(..., min_length=1, max_length=200, description="URL slug for Webflow")
    meta_description: str = Field(..., min_length=1, max_length=500, description="SEO meta description")
    sources: List[BlogSource] = Field(default_factory=list, description="Source references")
    word_count: int = Field(..., ge=0, description="Word count of body")
    generated_at: str = Field(..., description="ISO timestamp of generation")
    dossier_id: str = Field(..., description="UUID of source research dossier")
    error: Optional[str] = Field(None, description="Error message if generation failed")


class BlogToggleResponse(BaseModel):
    """Response after toggling blog_enabled."""
    post_id: str
    blog_enabled: bool
    blog_status: str


class BlogContentUpdateRequest(BaseModel):
    """Request to update editable blog fields."""
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    body: Optional[str] = Field(None, min_length=1)
    slug: Optional[str] = Field(None, min_length=1, max_length=200)
    meta_description: Optional[str] = Field(None, min_length=1, max_length=500)


class BlogPublishResponse(BaseModel):
    """Response after publishing to Webflow."""
    post_id: str
    blog_status: str
    webflow_item_id: Optional[str] = None
    blog_published_at: Optional[str] = None


class BlogScheduleRequest(BaseModel):
    """Request to schedule a generated blog post for later publishing."""
    scheduled_at: datetime = Field(..., description="Scheduled publish time in UTC")


class BlogScheduleResponse(BaseModel):
    """Response after scheduling a blog post."""
    post_id: str
    blog_status: str
    blog_scheduled_at: str
