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

from pydantic import ValidationError as PydanticValidationError

from app.adapters.llm_client import get_llm_client
from app.adapters.storage_client import get_storage_client
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


def _build_blog_image_prompt(blog_content: Dict[str, Any], dossier_payload: Dict[str, Any]) -> str:
    topic = _compact_line(blog_content.get("name") or dossier_payload.get("topic") or "Blogartikel")
    cluster_summary = _limit_text(
        dossier_payload.get("cluster_summary") or dossier_payload.get("source_summary") or "",
        120,
    )
    summary = _limit_text(blog_content.get("preview_text") or blog_content.get("merksatz") or "", 90)
    return _limit_text(
        (
            f"Quadratisches Coverbild fuer einen deutschen Blogartikel ueber {topic}. "
            f"Kontext: {cluster_summary}. "
            f"Teaser: {summary}. "
            "Freundliche realistische Editorial-Illustration, hell, professionell, ohne Text, Logo oder Wasserzeichen."
        ),
        300,
    )


def _compact_line(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _limit_text(value: Any, limit: int) -> str:
    text = _compact_line(value)
    if len(text) <= limit:
        return text
    clipped = text[: max(0, limit - 1)].rstrip(" ,.;:-")
    return f"{clipped}…"


def _collect_blog_contract_issues(parsed: Dict[str, Any]) -> list[str]:
    issues: list[str] = []

    if not _compact_line(parsed.get("name")):
        issues.append("Name fehlt.")
    if not _compact_line(parsed.get("slug")):
        issues.append("Slug fehlt.")
    if not _compact_line(parsed.get("merksatz")):
        issues.append("Merksatz fehlt.")
    if not _compact_line(parsed.get("tipp")):
        issues.append("Tipp fehlt.")

    summary_bullets = [_compact_line(item) for item in (parsed.get("summary_bullets") or []) if _compact_line(item)]
    if len(summary_bullets) < 3 or len(summary_bullets) > 6:
        issues.append("Zusammenfassung braucht 3 bis 6 Stichpunkte.")

    if not _compact_line(parsed.get("intro_heading")):
        issues.append("Einleitung braucht eine Zwischenueberschrift.")
    intro_paragraphs = [
        _compact_line(item)
        for item in (parsed.get("introduction_paragraphs") or [])
        if _compact_line(item)
    ]
    if not intro_paragraphs:
        issues.append("Einleitung braucht mindestens einen echten Absatz.")

    sections = parsed.get("sections") or []
    if len(sections) < 3 or len(sections) > 6:
        issues.append("Es braucht 3 bis 6 Hauptabschnitte.")
    for index, section in enumerate(sections, start=1):
        if not _compact_line((section or {}).get("heading")):
            issues.append(f"Abschnitt {index} braucht eine Zwischenueberschrift.")
        section_paragraphs = [
            _compact_line(item)
            for item in ((section or {}).get("paragraphs") or [])
            if _compact_line(item)
        ]
        if not section_paragraphs:
            issues.append(f"Abschnitt {index} braucht mindestens einen Absatz.")

    if not _compact_line(parsed.get("conclusion_heading")):
        issues.append("Schluss braucht eine Zwischenueberschrift.")
    conclusion_paragraphs = [
        _compact_line(item)
        for item in (parsed.get("conclusion_paragraphs") or [])
        if _compact_line(item)
    ]
    if not conclusion_paragraphs:
        issues.append("Schluss braucht mindestens einen echten Absatz.")

    if not _compact_line(parsed.get("preview_text")):
        issues.append("Vorschautext fehlt.")
    elif len(_compact_line(parsed.get("preview_text"))) > 220:
        issues.append("Vorschautext ist laenger als 220 Zeichen.")

    if not _compact_line(parsed.get("meta_title")):
        issues.append("Meta-Titel fehlt.")
    if not _compact_line(parsed.get("meta_description")):
        issues.append("Meta-Beschreibung fehlt.")

    return issues


def _build_blog_retry_prompt(base_prompt: str, issues: list[str], attempt: int) -> str:
    issue_lines = "\n".join(f"- {issue}" for issue in issues)
    return (
        f"{base_prompt}\n\n"
        f"KORREKTURRUNDE {attempt}:\n"
        "Deine vorherige Antwort war ungueltig.\n"
        "Schreibe den kompletten Blogbeitrag neu, nicht nur die fehlenden Teile.\n"
        "Behebe diese Punkte zwingend:\n"
        f"{issue_lines}\n"
        "Achte besonders darauf, dass Einleitung und Schluss echte inhaltliche Absaetze enthalten "
        "und dass alle Feldlabels exakt einmal in der vorgegebenen Reihenfolge vorkommen."
    )


def _format_blog_validation_issues(exc: PydanticValidationError) -> list[str]:
    issues: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "__root__")
        message = str(error.get("msg") or "ungueltig")
        issues.append(f"{location}: {message}" if location else message)
    return issues


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
    name = slug = merksatz = tipp = preview_text = meta_title = meta_description = image_prompt = ""

    def add_paragraph(target: list[str], text: str) -> None:
        cleaned = text.strip()
        if cleaned:
            target.append(cleaned)

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
        if line.startswith("Bildprompt:"):
            image_prompt = line.removeprefix("Bildprompt:").strip()
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

    return {
        "name": name or "",
        "slug": slug or "",
        "merksatz": merksatz or "",
        "tipp": tipp or "",
        "summary_bullets": summary_bullets,
        "intro_heading": intro_heading or "",
        "introduction_paragraphs": intro_paragraphs,
        "sections": sections,
        "conclusion_heading": conclusion_heading or "",
        "conclusion_paragraphs": conclusion_paragraphs,
        "preview_text": preview_text or "",
        "meta_title": meta_title or "",
        "meta_description": meta_description or "",
        "image_prompt": image_prompt or "",
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
        "image_prompt": "",
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

        llm = get_llm_client()
        dossier_sources = dossier_payload.get("sources") or []
        prompt = _build_blog_prompt(dossier_payload)
        sources = [
            {"title": s.get("title", ""), "url": str(s.get("url", ""))}
            for s in dossier_sources
            if s.get("title") and s.get("url")
        ]

        blog_content: Optional[Dict[str, Any]] = None
        current_prompt = prompt
        for attempt in range(1, 4):
            raw_text = llm.generate_gemini_text(
                prompt=current_prompt,
                temperature=0.7 if attempt == 1 else 0.35,
                max_tokens=8192,
            )
            parsed = _parse_labeled_blog_text(raw_text, dossier_payload)
            parsed["image_prompt"] = parsed.get("image_prompt") or _build_blog_image_prompt(parsed, dossier_payload)

            contract_issues = _collect_blog_contract_issues(parsed)
            if contract_issues:
                logger.warning(
                    "blog_generation_contract_retry",
                    post_id=post_id,
                    attempt=attempt,
                    issues=contract_issues,
                )
                if attempt < 3:
                    current_prompt = _build_blog_retry_prompt(prompt, contract_issues, attempt + 1)
                    continue
                raise ValueError("; ".join(contract_issues))

            try:
                blog_content = build_blog_content_from_llm(
                    parsed,
                    dossier_id=dossier_id,
                    sources=sources,
                    scheduled_at=_isoformat_optional(post.get("blog_scheduled_at")),
                )
                break
            except PydanticValidationError as exc:
                validation_issues = _format_blog_validation_issues(exc)
                logger.warning(
                    "blog_generation_validation_retry",
                    post_id=post_id,
                    attempt=attempt,
                    issues=validation_issues,
                )
                if attempt < 3:
                    current_prompt = _build_blog_retry_prompt(prompt, validation_issues, attempt + 1)
                    continue
                raise ValueError("; ".join(validation_issues)) from exc

        if blog_content is None:
            raise ValueError("Blog draft could not be generated with the required structure.")

        next_status = "scheduled" if post.get("blog_scheduled_at") else "draft"
        update_blog_status(post_id, status=next_status, blog_content=blog_content)
        return blog_content

    except Exception as exc:
        logger.error("blog_generation_failed", post_id=post_id, error=str(exc))
        error_content = _build_error_content(post, str(exc))
        update_blog_status(post_id, status="failed", blog_content=error_content)
        return error_content


def generate_blog_image(post_id: str, *, image_prompt: Optional[str] = None) -> Dict[str, Any]:
    """Generate a blog preview image, upload it to R2, and persist the preview URL."""
    post = _load_post_for_blog(post_id)
    blog_content = post.get("blog_content") or {}

    if not post.get("blog_enabled"):
        raise ValueError(f"Blog not enabled for post {post_id}")
    if not blog_has_draft_content(blog_content):
        raise ValueError(f"No blog content to generate an image for post {post_id}")

    dossier = _lookup_dossier(post)
    if not dossier:
        raise ValueError(f"No research dossier found for post {post_id}")

    dossier_payload = dossier.get("normalized_payload") or {}
    effective_prompt = (image_prompt or blog_content.get("image_prompt") or "").strip()
    if not effective_prompt:
        effective_prompt = _build_blog_image_prompt(blog_content, dossier_payload)

    llm = get_llm_client()
    image_result = llm.generate_gemini_image(
        prompt=effective_prompt,
        model=None,
        temperature=0.8,
        max_tokens=2048,
    )
    storage = get_storage_client()
    uploaded = storage.upload_image(
        image_bytes=image_result["image_bytes"],
        file_name=f"{blog_content.get('slug') or post_id}.png",
        correlation_id=post_id,
        content_type=image_result.get("mime_type") or "image/png",
    )
    updated_content = merge_blog_content_updates(
        blog_content,
        updates={
            "image_prompt": effective_prompt,
            "preview_image_url": uploaded["url"],
            "image_model": image_result.get("model"),
            "image_generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        },
    )
    update_blog_status(post_id, status=post.get("blog_status") or "draft", blog_content=updated_content)
    return {
        "post_id": post_id,
        "image_prompt": effective_prompt,
        "preview_image_url": uploaded["url"],
        "storage_key": uploaded.get("storage_key"),
        "image_model": image_result.get("model"),
    }


def publish_blog_post(post_id: str, *, publication_date: Optional[str] = None) -> Dict[str, Any]:
    """Publish a generated blog post to Webflow immediately."""
    settings = get_settings()
    post = _load_post_for_blog(post_id)
    blog_content = post.get("blog_content") or {}

    if not post.get("blog_enabled"):
        raise ValueError(f"Blog not enabled for post {post_id}")
    if not blog_has_draft_content(blog_content):
        raise ValueError(f"No blog content to publish for post {post_id}")
    if blog_content.get("image_prompt") and not blog_content.get("preview_image_url"):
        raise ValueError(f"Generate and accept a preview image before publishing post {post_id}")

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


def delete_blog_post(post_id: str, *, delete_webflow_item: bool = True) -> Dict[str, Any]:
    """Delete a published blog post from Webflow and clear local publish state."""
    settings = get_settings()
    post = _load_post_for_blog(post_id)
    blog_content = post.get("blog_content") or {}
    webflow_item_id = post.get("blog_webflow_item_id")

    if delete_webflow_item and webflow_item_id:
        client = WebflowClient(
            api_token=settings.webflow_api_token,
            collection_id=settings.webflow_collection_id,
            site_id=settings.webflow_site_id,
        )
        client.delete_item(str(webflow_item_id))

    updated_content = merge_blog_content_updates(
        blog_content,
        updates={"publication_date": None},
    )
    update_blog_status(
        post_id,
        status="disabled",
        blog_content=updated_content,
        clear_webflow_item_id=True,
        clear_published_at=True,
        clear_scheduled_at=True,
    )

    return {
        "post_id": post_id,
        "blog_status": "disabled",
        "webflow_item_id": webflow_item_id,
        "deleted_webflow_item": bool(delete_webflow_item and webflow_item_id),
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
