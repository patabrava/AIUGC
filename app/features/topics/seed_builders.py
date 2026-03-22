"""
Pure mappers for turning validated topic outputs into seed payloads.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.logging import get_logger
from app.features.topics.content_utils import build_social_description, extract_soft_cta, strip_cta_from_script
from app.features.topics.schemas import DialogScripts, ResearchAgentItem, SeedData, TopicData

logger = get_logger(__name__)


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


def build_seed_payload(
    item: ResearchAgentItem,
    strict_seed: SeedData,
    dialog_scripts: Optional[DialogScripts] = None,
    *,
    source_title: Optional[str] = None,
    source_url: Optional[str] = None,
    source_summary: Optional[str] = None,
) -> Dict[str, Any]:
    primary_source = item.sources[0] if item.sources else None
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
        "caption": item.caption or build_social_description(item.script, resolved_source_summary),
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
    if primary_source or source_url or source_title:
        payload["source"] = {
            "title": source_title or (primary_source.title if primary_source else None),
            "url": source_url or (str(primary_source.url) if primary_source else None),
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
