"""Topic discovery prompt templates.
Per IMPLEMENTATION_GUIDE Phase 2 requirements.
"""

from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from app.core.video_profiles import DurationProfile, get_duration_profile
from app.features.topics.schemas import ResearchDossier

PROMPT_DATA_DIR = Path(__file__).resolve().parent / "prompt_data"


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


def _extract_topic_candidates(topic_pool_text: str) -> List[str]:
    """Extract bullet-list topic candidates from the frozen canon section."""
    candidates: List[str] = []
    in_rotation = False
    for line in topic_pool_text.splitlines():
        stripped = line.strip()
        if not in_rotation:
            if stripped.startswith("Themenrotation"):
                in_rotation = True
            continue
        if stripped.startswith("OUTPUT FORMAT"):
            break
        if stripped.startswith("- "):
            candidate = stripped[2:].strip()
            if candidate:
                candidates.append(candidate)
    return candidates


@lru_cache(maxsize=None)
def get_topic_seed_catalog() -> List[str]:
    """Return cached list of seed topics from the frozen 8s canon."""
    return _extract_topic_candidates(_load_seed_canon_text())


@lru_cache(maxsize=None)
def get_topic_pool_candidates() -> List[str]:
    """Backward-compatible alias for the frozen seed catalog."""
    return get_topic_seed_catalog()


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


def build_prompt1(
    post_type: str,
    desired_topics: int,
    profile: Optional[DurationProfile] = None,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
    assigned_topics: Optional[List[str]] = None,
    seed: Optional[int] = None,
) -> str:
    """Render the dossier-only PROMPT_1 research prompt."""
    profile = profile or get_duration_profile(8)
    seed_topic = _choose_seed_topic(
        desired_topics,
        assigned_topics=assigned_topics,
        seed=seed,
    )
    return _format_research_dossier_prompt(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=profile.target_length_tier,
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
    }
    return template.format(**format_kwargs).strip()
