"""Topic discovery prompt templates.
Per IMPLEMENTATION_GUIDE Phase 2 requirements.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from app.core.video_profiles import DurationProfile, get_duration_profile
from app.features.topics.schemas import ResearchDossier

PROMPT_DATA_DIR = Path(__file__).resolve().parent / "prompt_data"
TOPIC_BANK_PATH = PROMPT_DATA_DIR / "topic_bank.yaml"
HOOK_BANK_PATH = PROMPT_DATA_DIR / "hook_bank.yaml"


@lru_cache(maxsize=None)
def _load_prompt(name: str) -> dict:
    """Load a YAML prompt definition from disk and cache the result."""
    prompt_path = PROMPT_DATA_DIR / f"{name}.yaml"
    with prompt_path.open("r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


def _join_sections(*sections: str) -> str:
    return "\n\n".join(section.strip() for section in sections if section).strip()


def _clip_text(value: Any, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_length:
        return text
    clipped = text[:max_length].rstrip()
    last_space = clipped.rfind(" ")
    if last_space >= max_length - 40:
        clipped = clipped[:last_space].rstrip()
    return clipped


@lru_cache(maxsize=None)
def _load_text_prompt(name: str, target_length_tier: int) -> str:
    prompt_path = PROMPT_DATA_DIR / f"{name}_{target_length_tier}s.txt"
    with prompt_path.open("r", encoding="utf-8") as fp:
        return fp.read().strip()


@lru_cache(maxsize=None)
def _load_prompt_text(name: str) -> str:
    prompt_path = PROMPT_DATA_DIR / f"{name}.txt"
    with prompt_path.open("r", encoding="utf-8") as fp:
        return fp.read().strip()


@lru_cache(maxsize=None)
def _load_seed_canon_text() -> str:
    prompt_path = PROMPT_DATA_DIR / "prompt1_8s.txt"
    with prompt_path.open("r", encoding="utf-8") as fp:
        return fp.read().strip()


def extract_topic_bank_from_prompt1_source(source_text: str) -> Dict[str, Any]:
    """Extract a structured topic bank from prompt1_8s.txt."""
    categories: List[Dict[str, Any]] = []
    current_category: Optional[Dict[str, Any]] = None
    in_rotation = False

    for line in source_text.splitlines():
        stripped = line.strip()
        if not in_rotation:
            if stripped.startswith("Themenrotation"):
                in_rotation = True
            continue
        if not stripped or stripped.startswith("{assigned_rotation_section}") or stripped.startswith("OUTPUT FORMAT"):
            if stripped.startswith("OUTPUT FORMAT"):
                break
            continue
        if stripped.endswith(":") and not stripped.startswith("- "):
            current_category = {"name": stripped[:-1].strip(), "topics": []}
            categories.append(current_category)
            continue
        if stripped.startswith("- "):
            if current_category is None:
                current_category = {"name": "Unkategorisiert", "topics": []}
                categories.append(current_category)
            current_category["topics"].append(stripped[2:].strip())

    return {
        "source_file": "prompt1_8s.txt",
        "categories": categories,
        "topics": [topic for category in categories for topic in category["topics"]],
    }


def extract_hook_bank_from_prompt1_source(source_text: str) -> Dict[str, Any]:
    """Extract hook families and banned starters from prompt1_8s.txt."""
    families: List[Dict[str, Any]] = []
    banned_patterns: List[str] = []
    in_hook_block = False

    for line in source_text.splitlines():
        stripped = line.strip()
        if "Rotate between mehreren Hook-Familien" in stripped:
            in_hook_block = True
            continue
        if in_hook_block:
            if stripped.startswith("• "):
                content = stripped[2:].strip()
                family_name, _, examples_text = content.partition(":")
                examples = re.findall(r'"([^"]+)"', examples_text)
                families.append(
                    {
                        "name": family_name.strip(),
                        "examples": examples,
                    }
                )
                continue
            if stripped.startswith("NIEMALS ") or stripped.startswith("Immer du-Form"):
                in_hook_block = False
        if "NIEMALS" in stripped or "NO passive declarations" in stripped:
            banned_patterns.extend(re.findall(r'"([^"]+)"', stripped))

    unique_banned = []
    for item in banned_patterns:
        candidate = item.strip()
        if candidate and candidate not in unique_banned:
            unique_banned.append(candidate)

    return {
        "source_file": "prompt1_8s.txt",
        "families": families,
        "banned_patterns": unique_banned,
    }


@lru_cache(maxsize=None)
def _load_topic_bank_payload() -> Dict[str, Any]:
    if TOPIC_BANK_PATH.exists():
        with TOPIC_BANK_PATH.open("r", encoding="utf-8") as fp:
            return yaml.safe_load(fp) or {}
    return extract_topic_bank_from_prompt1_source(_load_seed_canon_text())


@lru_cache(maxsize=None)
def _load_hook_bank_payload() -> Dict[str, Any]:
    if HOOK_BANK_PATH.exists():
        with HOOK_BANK_PATH.open("r", encoding="utf-8") as fp:
            return yaml.safe_load(fp) or {}
    return extract_hook_bank_from_prompt1_source(_load_seed_canon_text())


@lru_cache(maxsize=None)
def get_topic_seed_catalog() -> List[str]:
    """Return cached list of seed topics from the topic bank."""
    payload = _load_topic_bank_payload()
    topics = [str(topic).strip() for topic in list(payload.get("topics") or []) if str(topic).strip()]
    return topics


@lru_cache(maxsize=None)
def get_topic_pool_candidates() -> List[str]:
    """Backward-compatible alias for the frozen seed catalog."""
    return get_topic_seed_catalog()


@lru_cache(maxsize=None)
def get_topic_bank() -> Dict[str, Any]:
    return _load_topic_bank_payload()


@lru_cache(maxsize=None)
def get_hook_bank() -> Dict[str, Any]:
    return _load_hook_bank_payload()


def pick_topic_bank_topics(count: int, *, seed: Optional[int] = None) -> List[str]:
    candidates = get_topic_seed_catalog()
    if not candidates:
        raise ValueError("No topic bank entries available")
    if count <= 0:
        return []
    rng = random.Random(seed if seed is not None else count)
    shuffled = candidates[:]
    rng.shuffle(shuffled)
    chosen: List[str] = []
    while len(chosen) < count:
        remaining = count - len(chosen)
        if remaining >= len(shuffled):
            chosen.extend(shuffled)
            rng.shuffle(shuffled)
        else:
            chosen.extend(shuffled[:remaining])
    return chosen


def _choose_seed_topic(
    desired_topics: int,
    *,
    assigned_topics: Optional[List[str]] = None,
    seed: Optional[int] = None,
) -> str:
    candidates = assigned_topics or get_topic_seed_catalog()
    if not candidates:
        raise ValueError("No topic seed candidates available")
    if len(candidates) == 1:
        return candidates[0]
    rng = random.Random(seed if seed is not None else desired_topics)
    return rng.choice(candidates)


def _format_research_prompt_context(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
) -> str:
    return _join_sections(
        "DU SCHREIBST NUR EINE EINZIGE RESEARCH-DOSSIER-AUSGABE.",
        "Ziel: Sammle so viel verwertbaren Kontext wie möglich für spätere Skripte.",
        f"Seed-Topic: {seed_topic}",
        f"Post-Typ: {post_type}",
        f"Ziel-Länge später: {target_length_tier}s",
        "Wichtig:",
        "- Liefere kein finales Skript.",
        "- Liefere keine Hook-Liste für die finale Ausgabe.",
        "- Konzentriere dich auf Quellen, Fakten, Winkel, Risiken und hilfreiche Einordnung.",
        "- Die Ausgabe soll als dauerhafte Dossier-Datenbank gespeichert werden.",
        "- Antworte als gültiges JSON-Objekt mit genau einem Dossier.",
    )


def _format_research_dossier_prompt(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
) -> str:
    template = _load_prompt_text("prompt1_research")
    return template.format(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
        research_context=_format_research_prompt_context(
            seed_topic=seed_topic,
            post_type=post_type,
            target_length_tier=target_length_tier,
        ),
    ).strip()


def build_topic_normalization_prompt(
    *,
    raw_response: str,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
) -> str:
    """Render the stage-2 normalization prompt for a completed Deep Research response."""
    template = _load_prompt_text("prompt1_normalization")
    return template.format(
        raw_response=raw_response.strip(),
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
    ).strip()


def _format_hook_bank_section() -> str:
    payload = get_hook_bank()
    families = list(payload.get("families") or [])
    banned = [str(item).strip() for item in list(payload.get("banned_patterns") or []) if str(item).strip()]
    if not families and not banned:
        return ""

    lines = ["HOOK-BANK (verbindlich):"]
    for family in families:
        name = str(family.get("name") or "").strip()
        examples = [str(item).strip() for item in list(family.get("examples") or []) if str(item).strip()]
        if not name or not examples:
            continue
        lines.append(f"- {name}: " + ", ".join(f'"{example}"' for example in examples))
    if banned:
        lines.append("Verbotene oder zu vermeidende Hook-Starter:")
        lines.extend(f"- {item}" for item in banned)
    return "\n".join(lines).strip()


def _format_prompt1_research_context(
    dossier: ResearchDossier | Dict[str, Any] | None,
    lane_candidate: Dict[str, Any] | None,
) -> str:
    if dossier is None and lane_candidate is None:
        return ""

    payload = dossier.model_dump(mode="json") if isinstance(dossier, ResearchDossier) else dict(dossier or {})
    lane = dict(lane_candidate or {})
    lane_facts = [f"- {fact}" for fact in list(lane.get("facts") or [])[:4]]
    lane_risks = [f"- {risk}" for risk in list(lane.get("risk_notes") or [])[:3]]
    lane_frameworks = ", ".join(str(item) for item in list(lane.get("framework_candidates") or payload.get("framework_candidates") or [])[:4])

    sections = [
        "DOSSIER-KONTEXT FÜR DIESEN DURCHLAUF:",
        f"Cluster-Thema: {payload.get('topic', '').strip()}",
        f"Seed-Topic: {payload.get('seed_topic', '').strip()}",
        f"Lane-Titel: {str(lane.get('title') or '').strip()}",
        f"Lane-Familie: {str(lane.get('lane_family') or '').strip()}",
        f"Lane-Winkel: {str(lane.get('angle') or '').strip()}",
        f"Framework-Kandidaten: {lane_frameworks}",
        f"Lane Source Summary: {_clip_text(lane.get('source_summary') or payload.get('source_summary') or '', 450)}",
    ]
    if lane_facts:
        sections.extend(["Lane-Fakten:", *lane_facts])
    if lane_risks:
        sections.extend(["Lane-Risiken:", *lane_risks])
    sections.append("Bleibe strikt bei diesem Lane-Winkel und erfinde kein neues Thema.")
    return _join_sections(*sections)


def _format_prompt2_research_context(dossier: ResearchDossier | Dict[str, Any] | None) -> str:
    context = _format_research_context(dossier)
    if not context:
        return ""
    return _join_sections("RESEARCH-KONTEXT FÜR DIE SKRIPTE:", context)


def _format_rotation_section(assigned_topics: Optional[List[str]]) -> str:
    topics = [topic.strip() for topic in (assigned_topics or []) if str(topic or "").strip()]
    if not topics:
        topics = get_topic_seed_catalog()[:5]
    lines = "\n".join(f"- {topic}" for topic in topics)
    return f"ZUFALLS-THEMEN FÜR DIESEN DURCHLAUF:\n{lines}".strip()


def build_prompt1(
    post_type: str,
    desired_topics: int,
    profile: Optional[DurationProfile] = None,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
    assigned_topics: Optional[List[str]] = None,
    seed: Optional[int] = None,
    seed_topic: Optional[str] = None,
    dossier: ResearchDossier | Dict[str, Any] | None = None,
    lane_candidate: Optional[Dict[str, Any]] = None,
) -> str:
    """Render the legacy tiered PROMPT_1 research prompt."""
    profile = profile or get_duration_profile(8)
    prompt_path = PROMPT_DATA_DIR / f"prompt1_{profile.target_length_tier}s.txt"
    with prompt_path.open("r", encoding="utf-8") as fp:
        template = fp.read().strip()
    assigned_rotation_section = _format_rotation_section(assigned_topics)
    research_context_section = _format_prompt1_research_context(dossier, lane_candidate)
    hook_bank_section = _format_hook_bank_section()
    return template.format(
        desired_topics=desired_topics,
        assigned_rotation_section=assigned_rotation_section,
        research_context_section=research_context_section,
        hook_bank_section=hook_bank_section,
    )


def build_topic_research_prompt(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
) -> str:
    """Render the stage-1 raw research prompt."""
    template = _load_prompt_text("prompt1_research")
    return template.format(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
        research_context=_format_research_prompt_context(
            seed_topic=seed_topic,
            post_type=post_type,
            target_length_tier=target_length_tier,
        ),
    ).strip()


def build_topic_research_dossier_prompt(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
) -> str:
    """Render the stage-2 dossier normalization prompt."""
    return _format_research_dossier_prompt(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
    )


def _format_research_context(dossier: ResearchDossier | Dict[str, Any] | None) -> str:
    if dossier is None:
        return ""

    if isinstance(dossier, ResearchDossier):
        payload = dossier.model_dump(mode="json")
    else:
        payload = dict(dossier)

    sources = payload.get("sources") or []
    source_lines = []
    for source in list(sources)[:3]:
        title = str(source.get("title") or "").strip()
        if title:
            source_lines.append(f"- {title}")

    fact_lines = [f"- {fact}" for fact in list(payload.get("facts") or [])[:3]]
    angle_lines = [f"- {angle}" for angle in list(payload.get("angle_options") or [])[:2]]
    risk_lines = [f"- {risk}" for risk in list(payload.get("risk_notes") or [])[:2]]
    framework_candidates = ", ".join(str(item) for item in list(payload.get("framework_candidates") or [])[:3])

    sections = [
        f"RESEARCH-DOSSIER FÜR: {payload.get('topic', '').strip()}",
        f"Seed-Topic: {payload.get('seed_topic', '').strip()}",
        f"Framework-Kandidaten: {framework_candidates}",
        "Quellen:",
        *source_lines,
        "Fakten:",
        *fact_lines,
        "Mögliche Winkel:",
        *angle_lines,
        "Risiken / Hinweise:",
        *risk_lines,
        f"Zusammenfassung: {_clip_text(payload.get('source_summary', ''), 450)}",
        f"Disclaimer: {payload.get('disclaimer', '').strip()}",
    ]
    return _join_sections(*sections)


def build_prompt2(
    topic: Optional[str] = None,
    scripts_per_category: int = 5,
    profile: Optional[DurationProfile] = None,
    dossier: ResearchDossier | Dict[str, Any] | None = None,
) -> str:
    """Render the dossier-driven PROMPT_2 script-generation prompt."""
    profile = profile or get_duration_profile(8)
    template = _load_text_prompt("prompt2", profile.target_length_tier)
    research_context = _format_research_context(dossier)
    resolved_topic = topic or (dossier.topic if isinstance(dossier, ResearchDossier) else None)  # type: ignore[attr-defined]
    if resolved_topic is None and isinstance(dossier, dict):
        resolved_topic = str(dossier.get("topic") or "")
    resolved_topic = resolved_topic or "Unbekanntes Thema"
    total_scripts = scripts_per_category * 3
    format_kwargs = {
        "topic": resolved_topic,
        "scripts_per_category": scripts_per_category,
        "total_scripts": total_scripts,
        "target_length_tier": profile.target_length_tier,
        "research_context": research_context,
        "research_context_section": _format_prompt2_research_context(dossier),
        "hook_bank_section": _format_hook_bank_section(),
    }
    return template.format(**format_kwargs).strip()
