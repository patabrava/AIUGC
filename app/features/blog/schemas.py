"""
FLOW-FORGE Blog Schemas
Pydantic models and contract helpers for blog generation and Webflow publishing.
Per Constitution § II: Validated Boundaries
"""

from __future__ import annotations

from datetime import datetime
from html import escape
import math
import re
import unicodedata
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

DEFAULT_SUMMARY_TITLE = "Das Wichtigste auf einen Blick"
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class BlogSource(BaseModel):
    """A source reference from the research dossier."""

    title: str = Field(..., min_length=1, max_length=400, description="Source title")
    url: str = Field(..., min_length=1, description="Source URL")

    @field_validator("title", "url")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return _compact_text(value)


class BlogSection(BaseModel):
    """Structured section used to render Webflow rich text."""

    heading: str = Field(..., min_length=1, max_length=200)
    paragraphs: List[str] = Field(..., min_length=1, description="Paragraphs for this section")
    bullets: List[str] = Field(default_factory=list, description="Optional bullet list")

    @field_validator("heading")
    @classmethod
    def _strip_heading(cls, value: str) -> str:
        return _compact_text(value)

    @field_validator("paragraphs", "bullets")
    @classmethod
    def _strip_lists(cls, value: List[str]) -> List[str]:
        return [_compact_text(item) for item in value if _compact_text(item)]


class BlogLLMOutput(BaseModel):
    """Structured LLM output before local rendering and enrichment."""

    name: str = Field(..., min_length=1, max_length=300)
    slug: str = Field(..., min_length=1, max_length=200)
    merksatz: str = Field(..., min_length=1, max_length=280)
    tipp: str = Field(..., min_length=1, max_length=280)
    summary_bullets: List[str] = Field(..., min_length=3, max_length=6)
    intro_heading: str = Field(..., min_length=1, max_length=200)
    introduction_paragraphs: List[str] = Field(..., min_length=1, max_length=3)
    sections: List[BlogSection] = Field(..., min_length=3, max_length=6)
    conclusion_heading: str = Field(..., min_length=1, max_length=200)
    conclusion_paragraphs: List[str] = Field(..., min_length=1, max_length=3)
    preview_text: str = Field(..., min_length=1, max_length=320)
    meta_title: str = Field(..., min_length=1, max_length=160)
    meta_description: str = Field(..., min_length=1, max_length=320)
    image_prompt: Optional[str] = Field(default=None, max_length=500)

    @field_validator(
        "name",
        "slug",
        "merksatz",
        "tipp",
        "intro_heading",
        "conclusion_heading",
        "preview_text",
        "meta_title",
        "meta_description",
        "image_prompt",
    )
    @classmethod
    def _strip_strings(cls, value: str) -> str:
        return _compact_text(value)

    @field_validator("summary_bullets", "introduction_paragraphs", "conclusion_paragraphs")
    @classmethod
    def _strip_paragraph_lists(cls, value: List[str]) -> List[str]:
        return [_compact_text(item) for item in value if _compact_text(item)]


class BlogContent(BaseModel):
    """Generated blog article content stored in posts.blog_content."""

    schema_version: int = Field(default=2, ge=2)
    name: str = Field(..., min_length=1, max_length=300)
    title: str = Field(..., min_length=1, max_length=300, description="Legacy alias for name")
    slug: str = Field(..., min_length=1, max_length=200)
    merksatz: str = Field(..., min_length=1, max_length=280)
    tipp: str = Field(..., min_length=1, max_length=280)
    summary_title: str = Field(default=DEFAULT_SUMMARY_TITLE, min_length=1, max_length=200)
    summary_bullets: List[str] = Field(default_factory=list)
    summary_html: str = Field(..., min_length=1)
    intro_heading: Optional[str] = Field(default=None, max_length=200)
    introduction_paragraphs: List[str] = Field(default_factory=list)
    sections: List[BlogSection] = Field(default_factory=list)
    conclusion_heading: Optional[str] = Field(default=None, max_length=200)
    conclusion_paragraphs: List[str] = Field(default_factory=list)
    body_html: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1, description="Legacy alias for body_html")
    publication_date: Optional[str] = Field(default=None)
    preview_text: str = Field(..., min_length=1, max_length=320)
    reading_time: str = Field(..., min_length=1, max_length=64)
    image_prompt: Optional[str] = Field(default=None, max_length=500)
    image_model: Optional[str] = Field(default=None, max_length=100)
    image_generated_at: Optional[str] = Field(default=None)
    preview_image_url: Optional[str] = Field(default=None, max_length=500)
    author_name: Optional[str] = Field(default=None, max_length=160)
    meta_title: str = Field(..., min_length=1, max_length=160)
    meta_description: str = Field(..., min_length=1, max_length=320)
    sources: List[BlogSource] = Field(default_factory=list)
    word_count: int = Field(..., ge=0)
    generated_at: str = Field(...)
    dossier_id: str = Field(...)
    error: Optional[str] = Field(default=None)


class BlogToggleResponse(BaseModel):
    """Response after toggling blog_enabled."""

    post_id: str
    blog_enabled: bool
    blog_status: str


class BlogContentUpdateRequest(BaseModel):
    """Request to update editable blog fields."""

    name: Optional[str] = Field(None, min_length=1, max_length=300)
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    slug: Optional[str] = Field(None, min_length=1, max_length=200)
    merksatz: Optional[str] = Field(None, min_length=1, max_length=280)
    tipp: Optional[str] = Field(None, min_length=1, max_length=280)
    preview_text: Optional[str] = Field(None, min_length=1, max_length=320)
    image_prompt: Optional[str] = Field(None, min_length=1, max_length=500)
    preview_image_url: Optional[str] = Field(None, max_length=500)
    author_name: Optional[str] = Field(None, min_length=1, max_length=160)
    meta_title: Optional[str] = Field(None, min_length=1, max_length=160)
    meta_description: Optional[str] = Field(None, min_length=1, max_length=320)
    publication_date: Optional[str] = Field(None, min_length=1, max_length=80)

    @field_validator(
        "name",
        "title",
        "slug",
        "merksatz",
        "tipp",
        "preview_text",
        "image_prompt",
        "preview_image_url",
        "author_name",
        "meta_title",
        "meta_description",
        "publication_date",
    )
    @classmethod
    def _strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _compact_text(value)


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


def _compact_text(value: Any) -> str:
    text = str(value or "")
    return _WS_RE.sub(" ", text).strip()


def _strip_html(value: str) -> str:
    return _compact_text(_TAG_RE.sub(" ", value or ""))


def _looks_like_html(value: str) -> bool:
    return bool(value and "<" in value and ">" in value)


def _slugify(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug[:200] or "blog-post"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    clipped = value[: max(0, limit - 1)].rstrip(" ,.;:-")
    return f"{clipped}…"


def _compact_limited_text(value: Any, limit: int) -> str:
    return _truncate(_compact_text(value), limit)


def _split_plain_paragraphs(value: str) -> List[str]:
    chunks = re.split(r"\n\s*\n", value or "")
    return [_compact_text(chunk) for chunk in chunks if _compact_text(chunk)]


def _extract_sentences(value: str) -> List[str]:
    text = _strip_html(value)
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [_compact_text(part) for part in parts if _compact_text(part)]


def _sanitize_source_list(raw_sources: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_sources, list):
        return []
    cleaned: List[Dict[str, str]] = []
    for source in raw_sources:
        if not isinstance(source, dict):
            continue
        title = _compact_text(source.get("title"))
        url = _compact_text(source.get("url"))
        if title and url:
            cleaned.append({"title": title, "url": url})
    return cleaned


def _render_bullet_list(items: List[str]) -> str:
    safe_items = [item for item in (_compact_text(value) for value in items) if item]
    if not safe_items:
        return ""
    rendered = "".join(f"<li>{escape(item)}</li>" for item in safe_items)
    return f"<ul>{rendered}</ul>"


def render_summary_html(summary_title: str, summary_bullets: List[str]) -> str:
    title = escape(_compact_text(summary_title) or DEFAULT_SUMMARY_TITLE)
    bullets_html = _render_bullet_list(summary_bullets)
    return f"<h2>{title}</h2>{bullets_html}" if bullets_html else f"<h2>{title}</h2>"


def _render_plain_text_html(value: str) -> str:
    paragraphs = _split_plain_paragraphs(value)
    if not paragraphs:
        return ""
    return "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)


def render_body_html(
    *,
    intro_heading: Optional[str],
    introduction_paragraphs: List[str],
    sections: List[Dict[str, Any]],
    conclusion_heading: Optional[str],
    conclusion_paragraphs: List[str],
) -> str:
    parts: List[str] = []
    heading = _compact_text(intro_heading)
    if heading:
        parts.append(f"<h2>{escape(heading)}</h2>")
    for paragraph in introduction_paragraphs or []:
        text = _compact_text(paragraph)
        if text:
            parts.append(f"<p>{escape(text)}</p>")
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        section_heading = _compact_text(section.get("heading"))
        if section_heading:
            parts.append(f"<h2>{escape(section_heading)}</h2>")
        for paragraph in section.get("paragraphs") or []:
            text = _compact_text(paragraph)
            if text:
                parts.append(f"<p>{escape(text)}</p>")
        bullets_html = _render_bullet_list(section.get("bullets") or [])
        if bullets_html:
            parts.append(bullets_html)
    ending = _compact_text(conclusion_heading)
    if ending:
        parts.append(f"<h2>{escape(ending)}</h2>")
    for paragraph in conclusion_paragraphs or []:
        text = _compact_text(paragraph)
        if text:
            parts.append(f"<p>{escape(text)}</p>")
    return "".join(parts)


def _count_words(*values: str) -> int:
    total = 0
    for value in values:
        text = _strip_html(value)
        if text:
            total += len([token for token in text.split(" ") if token])
    return total


def format_reading_time(word_count: int) -> str:
    if word_count <= 0:
        return "1 Minute"
    lower = max(1, math.ceil(word_count / 190))
    upper = max(lower, math.ceil(word_count / 150))
    if lower == upper:
        return f"{lower} Minute" if lower == 1 else f"{lower} Minuten"
    return f"{lower}-{upper} Minuten"


def blog_has_draft_content(raw_content: Any) -> bool:
    content = normalize_blog_content(raw_content)
    return bool(content.get("name") and content.get("body_html"))


def build_blog_content_from_llm(
    data: Dict[str, Any],
    *,
    dossier_id: str,
    sources: Optional[List[Dict[str, str]]] = None,
    scheduled_at: Optional[str] = None,
) -> Dict[str, Any]:
    prepared = dict(data or {})
    prepared["image_prompt"] = _compact_limited_text(prepared.get("image_prompt"), 500) or None
    parsed = BlogLLMOutput.model_validate(prepared)
    sections = [section.model_dump() for section in parsed.sections]
    body_html = render_body_html(
        intro_heading=parsed.intro_heading,
        introduction_paragraphs=parsed.introduction_paragraphs,
        sections=sections,
        conclusion_heading=parsed.conclusion_heading,
        conclusion_paragraphs=parsed.conclusion_paragraphs,
    )
    summary_title = DEFAULT_SUMMARY_TITLE
    summary_html = render_summary_html(summary_title, parsed.summary_bullets)
    word_count = _count_words(
        parsed.merksatz,
        parsed.tipp,
        " ".join(parsed.summary_bullets),
        body_html,
    )
    generated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    return normalize_blog_content(
        {
            "schema_version": 2,
            "name": parsed.name,
            "title": parsed.name,
            "slug": _slugify(parsed.slug or parsed.name),
            "merksatz": parsed.merksatz,
            "tipp": parsed.tipp,
            "summary_title": summary_title,
            "summary_bullets": parsed.summary_bullets,
            "summary_html": summary_html,
            "intro_heading": parsed.intro_heading,
            "introduction_paragraphs": parsed.introduction_paragraphs,
            "sections": sections,
            "conclusion_heading": parsed.conclusion_heading,
            "conclusion_paragraphs": parsed.conclusion_paragraphs,
            "body_html": body_html,
            "body": body_html,
            "publication_date": scheduled_at,
            "preview_text": parsed.preview_text,
            "reading_time": format_reading_time(word_count),
            "meta_title": parsed.meta_title,
            "meta_description": parsed.meta_description,
            "image_prompt": _compact_limited_text(parsed.image_prompt, 500) if parsed.image_prompt else None,
            "sources": sources or [],
            "word_count": word_count,
            "generated_at": generated_at,
            "dossier_id": dossier_id,
        },
        scheduled_at=scheduled_at,
    )


def normalize_blog_content(
    raw_content: Any,
    *,
    fallback_name: str = "",
    scheduled_at: Optional[str] = None,
    published_at: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(raw_content, dict):
        return {}

    content = dict(raw_content)
    name = _compact_text(content.get("name") or content.get("title") or fallback_name)
    if not name:
        return content

    summary_title = _compact_text(content.get("summary_title") or DEFAULT_SUMMARY_TITLE)

    intro_heading = _compact_text(content.get("intro_heading")) or None
    introduction_paragraphs = [
        _compact_text(item)
        for item in (content.get("introduction_paragraphs") or [])
        if _compact_text(item)
    ]
    sections = []
    for section in content.get("sections") or []:
        if not isinstance(section, dict):
            continue
        heading = _compact_text(section.get("heading"))
        paragraphs = [_compact_text(item) for item in (section.get("paragraphs") or []) if _compact_text(item)]
        bullets = [_compact_text(item) for item in (section.get("bullets") or []) if _compact_text(item)]
        if heading and paragraphs:
            sections.append({"heading": heading, "paragraphs": paragraphs, "bullets": bullets})

    conclusion_heading = _compact_text(content.get("conclusion_heading")) or None
    conclusion_paragraphs = [
        _compact_text(item)
        for item in (content.get("conclusion_paragraphs") or [])
        if _compact_text(item)
    ]

    summary_bullets = [
        _compact_text(item)
        for item in (content.get("summary_bullets") or [])
        if _compact_text(item)
    ]

    body_html = content.get("body_html") or ""
    if not body_html and (intro_heading or sections or conclusion_heading or conclusion_paragraphs):
        body_html = render_body_html(
            intro_heading=intro_heading,
            introduction_paragraphs=introduction_paragraphs,
            sections=sections,
            conclusion_heading=conclusion_heading,
            conclusion_paragraphs=conclusion_paragraphs,
        )
    if not body_html:
        legacy_body = str(content.get("body") or "")
        body_html = legacy_body if _looks_like_html(legacy_body) else _render_plain_text_html(legacy_body)

    plain_body = _strip_html(body_html)
    if not summary_bullets:
        summary_bullets = _extract_sentences(plain_body)[:4]
    if not summary_bullets and content.get("preview_text"):
        summary_bullets = [_compact_text(content.get("preview_text"))]

    summary_html = content.get("summary_html") or render_summary_html(summary_title, summary_bullets)

    sentences = _extract_sentences(plain_body)
    merksatz = _compact_text(content.get("merksatz")) or (sentences[0] if sentences else name)
    tipp = _compact_text(content.get("tipp")) or (sentences[1] if len(sentences) > 1 else merksatz)
    preview_text = _compact_text(content.get("preview_text"))
    if not preview_text:
        paragraphs = _split_plain_paragraphs(plain_body)
        preview_text = _truncate(paragraphs[0] if paragraphs else plain_body, 220)

    word_count = int(content.get("word_count") or _count_words(summary_html, body_html, merksatz, tipp))
    publication_date = (
        _compact_text(content.get("publication_date"))
        or _compact_text(published_at)
        or _compact_text(scheduled_at)
        or None
    )

    normalized = {
        "schema_version": 2,
        "name": name,
        "title": name,
        "slug": _compact_text(content.get("slug")) or _slugify(name),
        "merksatz": merksatz,
        "tipp": tipp,
        "summary_title": summary_title,
        "summary_bullets": summary_bullets,
        "summary_html": summary_html,
        "intro_heading": intro_heading,
        "introduction_paragraphs": introduction_paragraphs,
        "sections": sections,
        "conclusion_heading": conclusion_heading,
        "conclusion_paragraphs": conclusion_paragraphs,
        "body_html": body_html,
        "body": body_html,
        "publication_date": publication_date,
        "preview_text": preview_text,
        "reading_time": _compact_text(content.get("reading_time")) or format_reading_time(word_count),
        "image_prompt": _compact_limited_text(content.get("image_prompt"), 500) or None,
        "image_model": _compact_text(content.get("image_model")) or None,
        "image_generated_at": _compact_text(content.get("image_generated_at")) or None,
        "preview_image_url": _compact_text(content.get("preview_image_url")) or None,
        "author_name": _compact_text(content.get("author_name")) or None,
        "meta_title": _compact_text(content.get("meta_title")) or name,
        "meta_description": _compact_text(content.get("meta_description")) or _truncate(preview_text or plain_body, 160),
        "sources": _sanitize_source_list(content.get("sources")),
        "word_count": word_count,
        "generated_at": _compact_text(content.get("generated_at")) or datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "dossier_id": _compact_text(content.get("dossier_id")),
        "error": _compact_text(content.get("error")) or None,
    }
    return normalized


def merge_blog_content_updates(current_content: Any, *, updates: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_blog_content(current_content)
    merged = dict(normalized)

    for key, value in updates.items():
        if value is None:
            continue
        if key == "title":
            merged["name"] = _compact_text(value)
            merged["title"] = _compact_text(value)
            continue
        if key == "name":
            merged["name"] = _compact_text(value)
            merged["title"] = _compact_text(value)
            continue
        if key in {
            "slug",
            "merksatz",
            "tipp",
            "preview_text",
            "image_prompt",
            "preview_image_url",
            "author_name",
            "meta_title",
            "meta_description",
            "publication_date",
        }:
            merged[key] = _compact_text(value)

    return normalize_blog_content(merged)
