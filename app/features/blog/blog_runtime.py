# app/features/blog/blog_runtime.py
"""
FLOW-FORGE Blog Runtime
LLM generation logic: reads dossier, builds prompt, calls LLM, validates response.
Per Constitution § V: Locality & Vertical Slices
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.adapters.llm_client import get_llm_client
from app.adapters.supabase_client import get_supabase
from app.core.logging import get_logger
from app.features.blog.queries import (
    _load_post_for_blog,
    get_due_scheduled_blog_posts,
    update_blog_status,
)
from app.features.topics.queries import get_topic_research_dossiers
from app.features.blog.webflow_client import WebflowClient
from app.core.config import get_settings

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "topics" / "prompt_data" / "blog_post.txt"


def _load_prompt_template() -> str:
    """Load the blog post prompt template from disk."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _build_blog_prompt(dossier_payload: Dict[str, Any]) -> str:
    """Inject dossier fields into the prompt template."""
    template = _load_prompt_template()

    facts = dossier_payload.get("facts") or []
    facts_text = "\n".join(f"- {f}" for f in facts)

    angle_options = dossier_payload.get("angle_options") or []
    angles_text = "\n".join(f"- {a}" for a in angle_options)

    sources = dossier_payload.get("sources") or []
    sources_text = "\n".join(f"- {s.get('title', '')}: {s.get('url', '')}" for s in sources)

    risk_notes = dossier_payload.get("risk_notes") or []
    risks_text = "\n".join(f"- {r}" for r in risk_notes)

    prompt = template.replace("{topic}", dossier_payload.get("topic", ""))
    prompt = prompt.replace("{cluster_summary}", dossier_payload.get("cluster_summary", ""))
    prompt = prompt.replace("{facts}", facts_text)
    prompt = prompt.replace("{angle_options}", angles_text)
    prompt = prompt.replace("{sources}", sources_text)
    prompt = prompt.replace("{source_summary}", dossier_payload.get("source_summary", ""))
    prompt = prompt.replace("{risk_notes}", risks_text)
    prompt = prompt.replace("{disclaimer}", dossier_payload.get("disclaimer", ""))

    return prompt


BLOG_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Blog-Titel"},
        "body": {"type": "string", "description": "Vollständiger Artikeltext mit Absätzen"},
        "slug": {"type": "string", "description": "SEO-URL-Slug mit Bindestrichen"},
        "meta_description": {"type": "string", "description": "SEO Meta-Description, max 160 Zeichen"},
    },
    "required": ["title", "body", "slug", "meta_description"],
}


def _parse_blog_json(data: Dict[str, Any], *, dossier_id: str) -> Dict[str, Any]:
    """Convert structured LLM JSON output into BlogContent dict."""
    body = data.get("body", "")
    return {
        "title": data.get("title", ""),
        "body": body,
        "slug": data.get("slug", ""),
        "meta_description": data.get("meta_description", ""),
        "sources": [],
        "word_count": len(body.split()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dossier_id": dossier_id,
    }


def _lookup_dossier(post: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Find the research dossier for a post via topic_registry."""
    topic_title = post.get("topic_title") or post.get("seed_data", {}).get("canonical_topic", "")
    if not topic_title:
        return None

    from app.adapters.supabase_client import get_supabase
    supabase = get_supabase()

    response = (
        supabase.client.table("topic_registry")
        .select("id")
        .eq("title", topic_title)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None

    registry_id = response.data[0]["id"]
    dossiers = get_topic_research_dossiers(topic_registry_id=registry_id, limit=1)
    if not dossiers:
        return None

    return dossiers[0]


def generate_blog_draft(post_id: str) -> Dict[str, Any]:
    """Generate a blog draft for a post from its research dossier."""
    post = _load_post_for_blog(post_id)
    seed_data = post.get("seed_data") or {}

    if not post.get("blog_enabled"):
        raise ValueError(f"Blog not enabled for post {post_id}")

    if seed_data.get("script_review_status") != "approved":
        raise ValueError(f"Script not approved for post {post_id}")

    update_blog_status(post_id, status="generating")

    try:
        dossier = _lookup_dossier(post)
        if not dossier:
            error_content = {
                "title": "",
                "body": "",
                "slug": "",
                "meta_description": "",
                "sources": [],
                "word_count": 0,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "dossier_id": "",
                "error": "No research dossier found for this topic.",
            }
            update_blog_status(post_id, status="failed", blog_content=error_content)
            return error_content

        dossier_payload = dossier.get("normalized_payload") or {}
        dossier_id = dossier.get("id", "")

        prompt = _build_blog_prompt(dossier_payload)

        llm = get_llm_client()
        parsed = llm.generate_gemini_json(
            prompt=prompt,
            json_schema=BLOG_JSON_SCHEMA,
            temperature=0.7,
            max_tokens=8192,
        )

        blog_content = _parse_blog_json(parsed, dossier_id=dossier_id)

        dossier_sources = dossier_payload.get("sources") or []
        blog_content["sources"] = [
            {"title": s.get("title", ""), "url": str(s.get("url", ""))}
            for s in dossier_sources
        ]

        if blog_content.get("error"):
            update_blog_status(post_id, status="failed", blog_content=blog_content)
        else:
            next_status = "scheduled" if post.get("blog_scheduled_at") else "draft"
            update_blog_status(post_id, status=next_status, blog_content=blog_content)

        return blog_content

    except Exception as exc:
        logger.error("blog_generation_failed", post_id=post_id, error=str(exc))
        error_content = {
            "title": "",
            "body": "",
            "slug": "",
            "meta_description": "",
            "sources": [],
            "word_count": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dossier_id": "",
            "error": str(exc),
        }
        update_blog_status(post_id, status="failed", blog_content=error_content)
        return error_content


def publish_blog_post(post_id: str) -> Dict[str, Any]:
    """Publish a generated blog post to Webflow immediately."""
    settings = get_settings()
    post = _load_post_for_blog(post_id)
    blog_content = post.get("blog_content") or {}

    if not post.get("blog_enabled"):
        raise ValueError(f"Blog not enabled for post {post_id}")
    if not blog_content.get("body"):
        raise ValueError(f"No blog content to publish for post {post_id}")

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

    update_blog_status(post_id, status="publishing")

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
        clear_scheduled_at=True,
    )

    return {
        "post_id": post_id,
        "blog_status": "published",
        "webflow_item_id": item_id,
        "blog_published_at": published_at,
    }


async def dispatch_due_blog_posts(limit: int = 10, *, trigger: str = "scheduler") -> Dict[str, Any]:
    """Publish due scheduled blog posts to Webflow."""
    due_posts = get_due_scheduled_blog_posts(limit=limit)
    supabase = get_supabase().client
    processed = 0
    published = 0
    failed = 0

    for row in due_posts:
        post_id = str(row.get("id") or "")
        if not post_id:
            continue

        try:
            claim = supabase.table("posts").update({"blog_status": "publishing"}).eq(
                "id", post_id
            ).eq("blog_status", "scheduled").execute()
            if not claim.data:
                continue
            publish_blog_post(post_id)
            processed += 1
            published += 1
            logger.info("blog_due_post_published", trigger=trigger, post_id=post_id)
        except Exception as exc:
            processed += 1
            failed += 1
            logger.error("blog_due_post_publish_failed", trigger=trigger, post_id=post_id, error=str(exc))
            update_blog_status(post_id, status="failed")

    return {
        "processed": processed,
        "published": published,
        "failed": failed,
        "trigger": trigger,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def run_scheduled_blog_publish_job() -> Dict[str, Any]:
    """Scheduler entry point for due blog publishing."""
    try:
        result = await dispatch_due_blog_posts(trigger="apscheduler")
        logger.info("blog_publish_scheduler_tick", **result)
        return result
    except Exception as exc:
        logger.exception("blog_publish_scheduler_failed", error=str(exc))
        return {"processed": 0, "published": 0, "failed": 0, "error": str(exc)}
