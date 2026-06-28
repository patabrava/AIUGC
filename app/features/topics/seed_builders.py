"""
Pure mappers for turning validated topic outputs into seed payloads.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.features.topics.content_utils import build_social_description, extract_soft_cta, strip_cta_from_script
from app.features.topics.schemas import DialogScripts, ResearchAgentItem, SeedData, TopicData

logger = get_logger(__name__)

_SOURCE_RELEVANCE_STOPWORDS = {
    "http",
    "https",
    "www",
    "com",
    "de",
    "org",
    "net",
    "html",
    "php",
    "quelle",
    "source",
    "und",
    "oder",
    "der",
    "die",
    "das",
    "den",
    "dem",
    "ein",
    "eine",
    "einer",
    "eines",
    "mit",
    "für",
    "fuer",
    "zur",
    "zum",
    "bei",
    "von",
    "im",
    "am",
    "an",
    "auf",
    "barrierefrei",
    "barrierefreie",
    "barrierefreien",
    "barrierefreier",
    "barrierefreies",
    "barrierefreiheit",
    "behindert",
    "behinderung",
    "inklusion",
    "inklusiv",
    "alltag",
}


def clean_source_url(value: Any) -> str:
    """Normalize common LLM/source-list URL punctuation before persistence."""
    url = str(value or "").strip().strip("<>\"'`")
    while url.lower().endswith(("%60", "%27", "%22")):
        url = url[:-3].rstrip()
    return url.rstrip("`\"'.,;:!?)>]}")


def _normalize_source_relevance_text(value: Any) -> str:
    text = str(value or "").lower()
    return (
        text.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def _source_relevance_tokens(value: Any) -> set[str]:
    text = _normalize_source_relevance_text(value)
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]{3,}", text)
        if token not in _SOURCE_RELEVANCE_STOPWORDS and not token.isdigit()
    }
    return tokens


def _source_tokens(source: Dict[str, Any]) -> set[str]:
    tokens = _source_relevance_tokens(source.get("title") or source.get("label") or "")
    url = str(source.get("url") or "")
    try:
        parsed = urlsplit(url)
    except ValueError:
        parsed = None
    if parsed:
        tokens.update(_source_relevance_tokens(parsed.netloc.replace(".", " ")))
        tokens.update(_source_relevance_tokens(parsed.path.replace("-", " ").replace("_", " ")))
    else:
        tokens.update(_source_relevance_tokens(url))
    return tokens


def _source_relevance_score(context_tokens: set[str], source_tokens: set[str]) -> int:
    score = len(context_tokens & source_tokens) * 3
    for context_token in context_tokens:
        if len(context_token) < 5:
            continue
        for source_token in source_tokens:
            if len(source_token) < 5:
                continue
            if context_token == source_token:
                continue
            if context_token in source_token or source_token in context_token:
                score += 3
    return score


def select_relevant_sources(
    *,
    sources: list[Any],
    context_parts: list[Any],
    max_sources: int = 3,
    min_score: int = 1,
) -> list[Dict[str, str]]:
    """Rank source URLs by lexical evidence against the lane/script context."""
    context_tokens: set[str] = set()
    for part in context_parts:
        if isinstance(part, list):
            for item in part:
                context_tokens.update(_source_relevance_tokens(item))
        else:
            context_tokens.update(_source_relevance_tokens(part))

    ranked: list[tuple[int, int, Dict[str, str]]] = []
    seen_urls: set[str] = set()
    for index, item in enumerate(list(sources or [])):
        if isinstance(item, dict):
            url = clean_source_url(item.get("url"))
            title = str(item.get("title") or item.get("label") or "").strip()
        else:
            url = clean_source_url(item)
            title = ""
        seen_url_key = url.rstrip("/")
        if not url or seen_url_key in seen_urls:
            continue
        seen_urls.add(seen_url_key)
        source = {"title": title or url, "url": url}
        score = _source_relevance_score(context_tokens, _source_tokens(source))
        if score < min_score:
            continue
        ranked.append((score, -index, source))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [source for _, _, source in ranked[:max_sources]]


def convert_research_item_to_topic(item: ResearchAgentItem) -> TopicData:
    cta = extract_soft_cta(item.script)
    rotation = strip_cta_from_script(item.script, cta)
    if not rotation or not rotation.strip():
        rotation = item.script.strip()
        words = rotation.split()
        cta = " ".join(words[-4:]) if len(words) > 4 else rotation
    return TopicData(
        title=item.topic[:200].strip(),
        rotation=rotation,
        cta=cta,
        spoken_duration=item.estimated_duration_s,
    )


def build_research_seed_data(
    *,
    prompt1_item: ResearchAgentItem,
    research_dossier: Optional[Dict[str, Any]] = None,
    lane_dossier: Optional[Dict[str, Any]] = None,
    topic_title: Optional[str] = None,
    canonical_topic: Optional[str] = None,
) -> SeedData:
    """Derive factual seed data from the normalized research payload.

    This keeps the topic pipeline on the same raw-research -> normalization -> script
    flow used by the cron worker and avoids a second Gemini JSON extraction step.
    """

    research_payload = dict(research_dossier or {})
    lane_payload = dict(lane_dossier or {})
    facts: list[str] = []
    for payload in (lane_payload, research_payload):
        for fact in list(payload.get("facts") or []):
            text = str(fact or "").strip()
            if text and text not in facts:
                facts.append(text)

    source_context = str(
        lane_payload.get("source_summary")
        or research_payload.get("source_summary")
        or getattr(prompt1_item, "source_summary", "")
        or getattr(prompt1_item, "caption", "")
        or ""
    ).strip()
    if not facts:
        fallback_fact = (
            str(
                source_context
                or lane_payload.get("topic")
                or research_payload.get("topic")
                or getattr(prompt1_item, "topic", "")
                or topic_title
                or getattr(prompt1_item, "script", "")
                or getattr(prompt1_item, "caption", "")
                or ""
            ).strip()
        )
        if fallback_fact:
            facts = [fallback_fact]

    if not facts:
        raise ValueError("Unable to derive research seed facts")

    return SeedData(facts=facts[:10], source_context=source_context or None)


def build_seed_payload(
    item: ResearchAgentItem,
    strict_seed: SeedData,
    dialog_scripts: Optional[DialogScripts] = None,
    *,
    source_title: Optional[str] = None,
    source_url: Optional[str] = None,
    source_summary: Optional[str] = None,
    canonical_topic: Optional[str] = None,
    research_title: Optional[str] = None,
) -> Dict[str, Any]:
    framework_map = {
        "PAL": "problem",
        "Testimonial": "testimonial",
        "Transformation": "transformation",
    }
    if dialog_scripts is None:
        selected_script = item.script
        script_category = framework_map.get(item.framework, "problem")
    else:
        default_script = dialog_scripts.problem_agitate_solution[0] if dialog_scripts.problem_agitate_solution else item.script
        script_map = {
            "problem": dialog_scripts.problem_agitate_solution[0] if dialog_scripts.problem_agitate_solution else default_script,
            "testimonial": dialog_scripts.testimonial[0] if dialog_scripts.testimonial else default_script,
            "transformation": dialog_scripts.transformation[0] if dialog_scripts.transformation else default_script,
        }
        script_category = framework_map.get(item.framework, "problem")
        selected_script = script_map[script_category]

    seed_payload = strict_seed.model_dump()
    facts = seed_payload.get("facts", [])
    primary_fact = facts[0] if facts else None
    resolved_source_summary = (
        str(source_summary or item.source_summary or item.caption or "").strip() or None
    )
    payload: Dict[str, Any] = {
        "script": item.script,
        "caption": "",
        "research_caption": item.caption or "",
        "canonical_topic": (canonical_topic or item.topic or "").strip(),
        "research_title": (research_title or item.topic or "").strip(),
        "framework": item.framework,
        "tone": item.tone,
        "estimated_duration_s": item.estimated_duration_s,
        "cta": extract_soft_cta(item.script),
        "dialog_script": selected_script,
        "script_category": script_category,
        "strict_fact": primary_fact,
        "strict_seed": seed_payload,
        "description": build_social_description(item.script, resolved_source_summary),
        "disclaimer": item.disclaimer,
    }
    if source_url or source_title:
        payload["source"] = {
            "title": source_title,
            "url": source_url,
            "summary": resolved_source_summary,
        }
    else:
        logger.warning("research_source_missing", topic=item.topic)
    return payload


def build_lifestyle_seed_payload(topic_data: Dict[str, Any], dialog_scripts: DialogScripts) -> Dict[str, Any]:
    framework_map = {
        "PAL": "problem",
        "Testimonial": "testimonial",
        "Transformation": "transformation",
    }
    default_script = dialog_scripts.problem_agitate_solution[0] if dialog_scripts.problem_agitate_solution else topic_data["rotation"]
    script_map = {
        "problem": dialog_scripts.problem_agitate_solution[0] if dialog_scripts.problem_agitate_solution else default_script,
        "testimonial": dialog_scripts.testimonial[0] if dialog_scripts.testimonial else default_script,
        "transformation": dialog_scripts.transformation[0] if dialog_scripts.transformation else default_script,
    }
    script_category = framework_map.get(topic_data.get("framework", "PAL"), "problem")
    selected_script = script_map[script_category]
    strict_seed = {
        "facts": [f"Community-basiertes Thema: {topic_data['title']}"],
        "source_context": "Lifestyle content - community experiences",
    }
    payload: Dict[str, Any] = {
        "script": selected_script,
        "canonical_topic": str(topic_data.get("title") or "").strip(),
        "research_title": str(topic_data.get("title") or "").strip(),
        "framework": topic_data.get("framework", "PAL"),
        "tone": "direkt, freundlich, empowernd, du-Form",
        "estimated_duration_s": topic_data["spoken_duration"],
        "cta": topic_data["cta"],
        "dialog_script": selected_script,
        "script_category": script_category,
        "strict_fact": strict_seed["facts"][0],
        "strict_seed": strict_seed,
        "description": dialog_scripts.description or f"Lifestyle-Beitrag zu: {topic_data['title']}",
        "disclaimer": "Keine Rechts- oder medizinische Beratung.",
    }
    logger.info("lifestyle_seed_payload_built", title=topic_data["title"], has_sources=False)
    return payload


def build_product_seed_payload(topic_data: Dict[str, Any]) -> Dict[str, Any]:
    script = str(topic_data.get("script") or topic_data.get("rotation") or "").strip()
    facts = [str(item).strip() for item in list(topic_data.get("facts") or []) if str(item).strip()]
    support_facts = [str(item).strip() for item in list(topic_data.get("support_facts") or []) if str(item).strip()]
    source_summary = str(topic_data.get("source_summary") or "").strip()
    strict_seed = {
        "facts": facts[:5] or [str(topic_data.get("title") or "").strip()],
        "source_context": source_summary or None,
    }
    payload: Dict[str, Any] = {
        "script": script,
        "canonical_topic": str(topic_data.get("angle") or topic_data.get("title") or "").strip(),
        "research_title": str(topic_data.get("title") or "").strip(),
        "product_name": str(topic_data.get("product_name") or "").strip(),
        "product_angle": str(topic_data.get("angle") or "").strip(),
        "framework": str(topic_data.get("framework") or "PAL"),
        "tone": "direkt, freundlich, empowernd, du-Form",
        "estimated_duration_s": int(topic_data.get("spoken_duration") or 0),
        "cta": str(topic_data.get("cta") or "").strip(),
        "dialog_script": script,
        "script_category": "problem",
        "strict_fact": strict_seed["facts"][0],
        "strict_seed": strict_seed,
        "description": build_social_description(script, source_summary),
        "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        "support_facts": support_facts,
    }
    logger.info("product_seed_payload_built", title=topic_data.get("title"), product_name=payload["product_name"])
    return payload
