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
from app.core.config import get_settings
from app.core.logging import get_logger
from app.features.blog.queries import (
    _load_post_for_blog,
    get_due_scheduled_blog_posts,
    update_blog_status,
)
from app.features.blog.schemas import (
    blog_has_draft_content,
    build_blog_content_from_llm,
    merge_blog_content_updates,
)
from app.features.blog.webflow_client import WebflowClient
from app.features.topics.queries import get_topic_research_dossiers

logger = get_logger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "topics" / "prompt_data" / "blog_post.txt"

def _load_prompt_template() -> str:
    """Load the blog post prompt template from disk."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _isoformat_optional(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    return text or None


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


def _parse_labeled_blog_text(raw_text: str, dossier_payload: Dict[str, Any]) -> Dict[str, Any]:
    lines = [line.rstrip() for line in str(raw_text or "").splitlines()]
    sections: list[Dict[str, Any]] = []
    current_section: Optional[Dict[str, Any]] = None
    current_block: Optional[str] = None
    intro_heading = ""
    conclusion_heading = ""
    intro_paragraphs: list[str] = []
    conclusion_paragraphs: list[str] = []
    summary_bullets: list[str] = []
    name = slug = merksatz = tipp = preview_text = meta_title = meta_description = ""

    def add_paragraph(target: list[str], text: str) -> None:
        cleaned = text.strip()
        if cleaned:
            target.append(cleaned)

    def split_sentences(text: str) -> list[str]:
        parts = []
        for chunk in text.replace("\r", "\n").split("\n"):
            cleaned_chunk = chunk.strip()
            if not cleaned_chunk:
                continue
            pieces = [piece.strip() for piece in cleaned_chunk.split(". ") if piece.strip()]
            if len(pieces) == 1:
                parts.append(cleaned_chunk)
            else:
                for piece in pieces:
                    if piece and piece[-1] not in ".!?":
                        piece += "."
                    parts.append(piece)
        return [part for part in parts if part]

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Name:"):
            name = line.removeprefix("Name:").strip()
            current_block = None
            continue
        if line.startswith("Slug:"):
            slug = line.removeprefix("Slug:").strip()
            current_block = None
            continue
        if line.startswith("Merksatz:"):
            merksatz = line.removeprefix("Merksatz:").strip()
            current_block = None
            continue
        if line.startswith("Tipp:"):
            tipp = line.removeprefix("Tipp:").strip()
            current_block = None
            continue
        if line.startswith("Vorschautext:"):
            preview_text = line.removeprefix("Vorschautext:").strip()
            current_block = None
            continue
        if line.startswith("Meta-Titel:"):
            meta_title = line.removeprefix("Meta-Titel:").strip()
            current_block = None
            continue
        if line.startswith("Meta-Beschreibung:"):
            meta_description = line.removeprefix("Meta-Beschreibung:").strip()
            current_block = None
            continue
        if line == "Zusammenfassung:":
            current_block = "summary"
            continue
        if line == "Einleitung:":
            current_block = "intro"
            continue
        if line == "Schluss:":
            current_block = "conclusion"
            continue
        if line.startswith("Abschnitt "):
            current_section = {"heading": "", "paragraphs": [], "bullets": []}
            sections.append(current_section)
            current_block = "section"
            continue
        if line.startswith("### "):
            heading = line.removeprefix("### ").strip()
            if current_block == "intro":
                intro_heading = heading
            elif current_block == "conclusion":
                conclusion_heading = heading
            elif current_section is not None:
                current_section["heading"] = heading
            continue
        if line.startswith("- "):
            bullet = line[2:].strip()
            if current_block == "summary":
                summary_bullets.append(bullet)
            elif current_section is not None:
                current_section["bullets"].append(bullet)
            continue

        if current_block == "intro":
            add_paragraph(intro_paragraphs, line)
        elif current_block == "conclusion":
            add_paragraph(conclusion_paragraphs, line)
        elif current_section is not None:
            add_paragraph(current_section["paragraphs"], line)

    facts = list(dossier_payload.get("facts") or [])
    angles = list(dossier_payload.get("angle_options") or [])
    if len(sections) < 3:
        for index in range(3 - len(sections)):
            focus = angles[index] if index < len(angles) else (facts[index] if index < len(facts) else dossier_payload.get("topic", "Thema"))
            sections.append(
                {
                    "heading": f"Einordnung {index + 1}",
                    "paragraphs": [f"Dieser Abschnitt ordnet den Punkt '{focus}' ein und zeigt die praktische Konsequenz."],
                    "bullets": [],
                }
            )

    if len(summary_bullets) < 3:
        for fact in facts[: 3 - len(summary_bullets)]:
            summary_bullets.append(str(fact).strip())

    if not merksatz:
        merksatz = str(facts[0]).strip() if facts else str(dossier_payload.get("topic", "")).strip()
    if not tipp:
        tipp = str(facts[1]).strip() if len(facts) > 1 else merksatz
    if not preview_text:
        preview_text = str(dossier_payload.get("source_summary") or dossier_payload.get("cluster_summary") or merksatz)[:220]
    if not meta_title:
        meta_title = name or str(dossier_payload.get("topic", "")).strip()
    if not meta_description:
        meta_description = preview_text or str(dossier_payload.get("disclaimer", "")).strip()[:160]

    if not sections:
        body_sentences = split_sentences(str(dossier_payload.get("source_summary") or raw_text or ""))
        if not body_sentences:
            body_sentences = [str(dossier_payload.get("cluster_summary") or dossier_payload.get("topic") or "").strip()]
        body_sentences = [sentence for sentence in body_sentences if sentence]
        intro_heading = intro_heading or "Worum es geht"
        conclusion_heading = conclusion_heading or "Was daraus folgt"
        if body_sentences:
            intro_paragraphs = intro_paragraphs or body_sentences[:2]
        if len(body_sentences) > 2:
            remaining = body_sentences[2:]
            chunk_size = max(1, len(remaining) // 3)
            fallback_headings = [
                "Die rechtliche Einordnung",
                "Die praktische Realität",
                "Was Betroffene beachten sollten",
            ]
            for idx, heading in enumerate(fallback_headings):
                start = idx * chunk_size
                end = len(remaining) if idx == len(fallback_headings) - 1 else (idx + 1) * chunk_size
                section_sentences = remaining[start:end] or remaining[-1:]
                sections.append(
                    {
                        "heading": heading,
                        "paragraphs": section_sentences[:2],
                        "bullets": [],
                    }
                )
        if not conclusion_paragraphs:
            conclusion_paragraphs = [
                "Die zentrale Frage ist nicht nur, was auf dem Papier steht, sondern was im Alltag verlässlich funktioniert.",
                "Genau dort entsteht der Unterschied zwischen Anspruch und Praxis.",
            ]
        if not summary_bullets and body_sentences:
            summary_bullets = body_sentences[:4]
        preview_text = _truncate(body_sentences[0] if body_sentences else preview_text, 180)
        if not meta_description:
            meta_description = _truncate(preview_text, 160)

    return {
        "name": name or dossier_payload.get("topic", ""),
        "slug": slug or "",
        "merksatz": merksatz or "",
        "tipp": tipp or "",
        "summary_bullets": summary_bullets,
        "intro_heading": intro_heading or "Einleitung",
        "introduction_paragraphs": intro_paragraphs,
        "sections": sections,
        "conclusion_heading": conclusion_heading or "Schluss",
        "conclusion_paragraphs": conclusion_paragraphs,
        "preview_text": preview_text or "",
        "meta_title": meta_title or name or "",
        "meta_description": meta_description or "",
    }


def _build_error_content(post: Dict[str, Any], error: str) -> Dict[str, Any]:
    return {
        "title": post.get("topic_title") or post.get("seed_data", {}).get("canonical_topic", ""),
        "name": post.get("topic_title") or post.get("seed_data", {}).get("canonical_topic", ""),
        "body": "",
        "body_html": "",
        "slug": "",
        "meta_description": "",
        "meta_title": "",
        "summary_html": "",
        "summary_bullets": [],
        "sources": [],
        "word_count": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dossier_id": "",
        "error": error,
    }


def _lookup_dossier(post: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Find the research dossier for a post via topic_registry."""
    topic_title = post.get("topic_title") or post.get("seed_data", {}).get("canonical_topic", "")
    if not topic_title:
        return None

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
            error_content = _build_error_content(post, "No research dossier found for this topic.")
            update_blog_status(post_id, status="failed", blog_content=error_content)
            return error_content

        dossier_payload = dossier.get("normalized_payload") or {}
        dossier_id = dossier.get("id", "")

        prompt = _build_blog_prompt(dossier_payload)

        llm = get_llm_client()
        raw_text = llm.generate_gemini_text(
            prompt=prompt,
            temperature=0.7,
            max_tokens=8192,
        )
        parsed = _parse_labeled_blog_text(raw_text, dossier_payload)

        dossier_sources = dossier_payload.get("sources") or []
        blog_content = build_blog_content_from_llm(
            parsed,
            dossier_id=dossier_id,
            sources=[
                {"title": s.get("title", ""), "url": str(s.get("url", ""))}
                for s in dossier_sources
                if s.get("title") and s.get("url")
            ],
            scheduled_at=_isoformat_optional(post.get("blog_scheduled_at")),
        )

        next_status = "scheduled" if post.get("blog_scheduled_at") else "draft"
        update_blog_status(post_id, status=next_status, blog_content=blog_content)
        return blog_content

    except Exception as exc:
        logger.error("blog_generation_failed", post_id=post_id, error=str(exc))
        error_content = _build_error_content(post, str(exc))
        update_blog_status(post_id, status="failed", blog_content=error_content)
        return error_content


def publish_blog_post(post_id: str, *, publication_date: Optional[str] = None) -> Dict[str, Any]:
    """Publish a generated blog post to Webflow immediately."""
    settings = get_settings()
    post = _load_post_for_blog(post_id)
    blog_content = post.get("blog_content") or {}

    if not post.get("blog_enabled"):
        raise ValueError(f"Blog not enabled for post {post_id}")
    if not blog_has_draft_content(blog_content):
        raise ValueError(f"No blog content to publish for post {post_id}")

    client = WebflowClient(
        api_token=settings.webflow_api_token,
        collection_id=settings.webflow_collection_id,
        site_id=settings.webflow_site_id,
    )

    effective_publication_date = publication_date or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    field_data = client.build_blog_field_data(blog_content, publication_date=effective_publication_date)

    update_blog_status(post_id, status="publishing")

    existing_item_id = post.get("blog_webflow_item_id")
    if existing_item_id:
        client.update_item(existing_item_id, field_data)
        item_id = existing_item_id
    else:
        item_id = client.create_item(field_data)

    client.publish_item(item_id)
    published_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    updated_content = merge_blog_content_updates(
        blog_content,
        updates={"publication_date": effective_publication_date},
    )

    update_blog_status(
        post_id,
        status="published",
        blog_content=updated_content,
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
            publish_blog_post(post_id, publication_date=_isoformat_optional(row.get("blog_scheduled_at")))
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
