"""Topic discovery prompt templates.
Per IMPLEMENTATION_GUIDE Phase 2 requirements.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from app.core.video_profiles import DurationProfile, get_duration_profile
from app.features.topics.schemas import ProductKnowledgeEntry, ResearchDossier
from app.features.topics.topic_validation import sanitize_spoken_fragment, sanitize_metadata_text

PROMPT_DATA_DIR = Path(__file__).resolve().parent / "prompt_data"
TOPIC_BANK_PATH = PROMPT_DATA_DIR / "topic_bank.yaml"
HOOK_BANK_PATH = PROMPT_DATA_DIR / "hook_bank.yaml"


def _build_current_date_guardrail() -> str:
    # ASCII-only to avoid reintroducing long dash characters into prompt fixtures.
    return _join_sections(
        "ZEIT- UND FORMAT-GUARDRAILS:",
        "- Heute ist April 2026.",
        "- Wenn etwas 2025 in Kraft getreten ist, formuliere als bereits gültig (z.B. `Seit 2025 ...`), nicht als Ankündigung (`Ab 2025 ...`).",
        "- Verwende keine langen Dash-Zeichen: kein U+2014 (em dash), U+2013 (en dash), U+2015 (horizontal bar), U+2212 (minus).",
    )


def _build_current_date_context_for_research() -> str:
    return _join_sections(
        "ZEITKONTEXT:",
        "- Heute ist April 2026.",
        "- Ordne Fristen und Regelungen relativ zu 2026 ein.",
        "- Wenn eine Änderung bereits 2025 in Kraft getreten ist, beschreibe sie als bereits geltend (z.B. `Seit 2025 ...`).",
    )



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


def _coerce_prompt_payload(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, ResearchDossier):
        return value.model_dump(mode="json")
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    if hasattr(value, "__dict__"):
        return {
            key: payload
            for key, payload in vars(value).items()
            if not key.startswith("_")
        }
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


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


def _normalize_bank_topic_signature(value: Any) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", str(value or "").lower())
    tokens = [token for token in cleaned.split() if token]
    return " ".join(tokens)


def _parse_bank_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_or_zero(value: Any) -> float:
    parsed = _parse_bank_timestamp(value)
    return parsed.timestamp() if parsed is not None else 0.0


def _summarize_topic_usage(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        canonical_topic = str(row.get("canonical_topic") or row.get("title") or row.get("script") or "").strip()
        fingerprint = str(row.get("family_fingerprint") or _normalize_bank_topic_signature(canonical_topic)).strip()
        if not fingerprint:
            continue
        entry = summary.setdefault(
            fingerprint,
            {
                "use_count": 0,
                "last_used_at": None,
                "last_harvested_at": None,
                "created_at": None,
            },
        )
        entry["use_count"] += int(row.get("use_count") or 0)
        for field in ("last_used_at", "last_harvested_at", "created_at", "updated_at"):
            parsed = _parse_bank_timestamp(row.get(field))
            if parsed is None:
                continue
            current = entry.get(field)
            if current is None or parsed > current:
                entry[field] = parsed
    return summary


def pick_topic_bank_topics(
    count: int,
    *,
    seed: Optional[int] = None,
    post_type: Optional[str] = None,
    exclude_topics: Optional[List[str]] = None,
) -> List[str]:
    candidates = get_topic_seed_catalog()
    if not candidates:
        raise ValueError("No topic bank entries available")
    if count <= 0:
        return []

    exclude_normalized = {
        _normalize_bank_topic_signature(topic)
        for topic in list(exclude_topics or [])
        if _normalize_bank_topic_signature(topic)
    }

    try:
        from app.features.topics.queries import get_all_topics_from_registry

        registry_rows = get_all_topics_from_registry()
        if post_type:
            registry_rows = [row for row in registry_rows if str(row.get("post_type") or "").strip() == str(post_type).strip()]
    except Exception:
        registry_rows = []

    usage_by_fingerprint = _summarize_topic_usage(registry_rows)
    scored_topics: Dict[tuple, List[str]] = {}
    for topic in candidates:
        normalized = _normalize_bank_topic_signature(topic)
        if not normalized or normalized in exclude_normalized:
            continue
        usage = usage_by_fingerprint.get(normalized)
        score = (
            0 if usage is None else 1,
            int(usage.get("use_count") or 0) if usage else 0,
            _timestamp_or_zero(usage.get("last_used_at")) if usage else 0.0,
            _timestamp_or_zero(usage.get("last_harvested_at") or usage.get("created_at")) if usage else 0.0,
        )
        scored_topics.setdefault(score, []).append(topic)

    if not scored_topics:
        return []

    rng = random.Random(seed if seed is not None else count)
    chosen: List[str] = []
    for score in sorted(scored_topics):
        bucket = scored_topics[score][:]
        rng.shuffle(bucket)
        for topic in bucket:
            if topic in chosen:
                continue
            chosen.append(topic)
            if len(chosen) >= count:
                return chosen[:count]
    return chosen[:count]



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
        "- Antworte als lesbarer Rohtext auf Deutsch, ohne JSON-Schema.",
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
        current_date_context=_build_current_date_context_for_research(),
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
    negative_examples = list(payload.get("negative_examples") or [])
    if not families and not banned:
        return ""

    priority_order = {"high": 0, "medium": 1, "low": 2}
    sorted_families = sorted(
        families,
        key=lambda f: priority_order.get(str(f.get("priority", "medium")), 1),
    )

    lines = ["HOOK-BANK (verbindlich):"]
    current_priority = None
    for family in sorted_families:
        name = str(family.get("name") or "").strip()
        examples = [str(item).strip() for item in list(family.get("examples") or []) if str(item).strip()]
        priority = str(family.get("priority", "medium"))
        if not name or not examples:
            continue
        if priority != current_priority:
            label = {"high": "BEVORZUGT", "medium": "SOLIDE", "low": "SPARSAM EINSETZEN"}.get(priority, "")
            if label:
                lines.append(f"\n[{label}]")
            current_priority = priority
        lines.append(f"- {name}: " + ", ".join(f'"{example}"' for example in examples))

    if banned:
        lines.append("\nVerbotene Hook-Starter (NIEMALS verwenden):")
        lines.extend(f"- {item}" for item in banned)

    if negative_examples:
        lines.append("\nBeispiele (vorher/nachher):")
        for ex in negative_examples:
            bad = str(ex.get("bad", "")).strip()
            good = str(ex.get("good", "")).strip()
            why = str(ex.get("why", "")).strip()
            if bad and good:
                lines.append(f'SCHLECHT: "{bad}"')
                lines.append(f'GUT: "{good}"')
                if why:
                    lines.append(f"Warum: {why}")
                lines.append("")

    return "\n".join(lines).strip()


def _render_prompt1_template(
    *,
    template: str,
    desired_topics: int,
    research_context_section: str,
    hook_bank_section: str,
    current_date_guardrail: str,
) -> str:
    rendered = template.format(
        desired_topics=desired_topics,
        research_context_section=research_context_section,
        hook_bank_section=hook_bank_section,
        current_date_guardrail=current_date_guardrail,
    )
    if hook_bank_section and "{hook_bank_section}" not in template:
        rendered = rendered.rstrip() + "\n\n" + hook_bank_section
    return rendered


def _format_prompt1_research_context(
    dossier: ResearchDossier | Dict[str, Any] | None,
    lane_candidate: Dict[str, Any] | None,
) -> str:
    if dossier is None and lane_candidate is None:
        return ""

    payload = _coerce_prompt_payload(dossier)
    lane = dict(lane_candidate or {})
    lane_facts = [
        f"- {sanitize_spoken_fragment(fact, ensure_terminal=True)}"
        for fact in list(lane.get("facts") or [])[:4]
        if sanitize_spoken_fragment(fact, ensure_terminal=True)
    ]
    lane_risks = [
        f"- {sanitize_spoken_fragment(risk, ensure_terminal=True)}"
        for risk in list(lane.get("risk_notes") or [])[:3]
        if sanitize_spoken_fragment(risk, ensure_terminal=True)
    ]
    lane_frameworks = ", ".join(str(item) for item in list(lane.get("framework_candidates") or payload.get("framework_candidates") or [])[:4])

    sections = [
        "DOSSIER-KONTEXT FÜR DIESEN DURCHLAUF:",
        f"Cluster-Thema: {payload.get('topic', '').strip()}",
        f"Seed-Topic: {payload.get('seed_topic', '').strip()}",
        f"Lane-Titel: {str(lane.get('title') or '').strip()}",
        f"Lane-Familie: {str(lane.get('lane_family') or '').strip()}",
        f"Lane-Winkel: {str(lane.get('angle') or '').strip()}",
        f"Framework-Kandidaten: {lane_frameworks}",
        f"Lane Source Summary: {_clip_text(sanitize_metadata_text(lane.get('source_summary') or payload.get('source_summary') or ''), 450)}",
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
    """Render the single-lane PROMPT_1 stage-3 script prompt."""
    profile = profile or get_duration_profile(8)
    prompt_path = PROMPT_DATA_DIR / f"prompt1_{profile.target_length_tier}s.txt"
    with prompt_path.open("r", encoding="utf-8") as fp:
        template = fp.read().strip()
    research_context_section = _format_prompt1_research_context(dossier, lane_candidate)
    hook_bank_section = _format_hook_bank_section()
    return _render_prompt1_template(
        template=template,
        desired_topics=desired_topics,
        research_context_section=research_context_section,
        hook_bank_section=hook_bank_section,
        current_date_guardrail=_build_current_date_guardrail(),
    )


def build_prompt1_variant(
    post_type: str,
    desired_topics: int = 1,
    profile: Optional[DurationProfile] = None,
    dossier: ResearchDossier | Dict[str, Any] | None = None,
    lane_candidate: Optional[Dict[str, Any]] = None,
    *,
    forced_framework: str,
    forced_hook_style: str,
) -> str:
    """Render a variant PROMPT_1 stage-3 prompt with hook bank and forced constraints.

    Unlike build_prompt1(), this injects the hook bank and forces a specific
    framework + hook_style. Used only by the variant expansion system.
    """
    profile = profile or get_duration_profile(8)
    prompt_path = PROMPT_DATA_DIR / f"prompt1_{profile.target_length_tier}s.txt"
    with prompt_path.open("r", encoding="utf-8") as fp:
        template = fp.read().strip()
    research_context_section = _format_prompt1_research_context(dossier, lane_candidate)
    hook_bank_section = _format_hook_bank_section()

    # Append framework/hook constraints to the hook bank section
    constraint_block = (
        f"\n\nPFLICHT-VORGABEN FÜR DIESES SKRIPT:\n"
        f"- Framework: {forced_framework}\n"
        f"- Hook-Stil: {forced_hook_style}\n"
        f"Halte dich strikt an dieses Framework und diesen Hook-Stil."
    )
    hook_bank_section = (hook_bank_section + constraint_block).strip()

    return _render_prompt1_template(
        template=template,
        desired_topics=desired_topics,
        research_context_section=research_context_section,
        hook_bank_section=hook_bank_section,
        current_date_guardrail=_build_current_date_guardrail(),
    )


def build_prompt1_batch(
    post_type: str,
    desired_topics: int,
    profile: Optional[DurationProfile] = None,
    assigned_topics: Optional[List[str]] = None,
) -> str:
    """Render the legacy multi-topic batch-discovery prompt."""
    profile = profile or get_duration_profile(8)
    template = _load_prompt_text("prompt1_batch")
    assigned_rotation_section = _format_rotation_section(assigned_topics)
    hook_bank_section = _format_hook_bank_section()
    return template.format(
        desired_topics=desired_topics,
        post_type=post_type,
        assigned_rotation_section=assigned_rotation_section,
        hook_bank_section=hook_bank_section,
        prompt1_min_words=profile.prompt1_min_words,
        prompt1_max_words=profile.prompt1_max_words,
        prompt1_min_seconds=profile.prompt1_min_seconds,
        prompt1_max_seconds=profile.prompt1_max_seconds,
        prompt1_sentence_guidance=profile.prompt1_sentence_guidance,
    ).strip()


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
        current_date_context=_build_current_date_context_for_research(),
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

    payload = _coerce_prompt_payload(dossier)

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


def _format_prompt3_fact_lines(values: List[str]) -> str:
    cleaned = [sanitize_metadata_text(value, max_sentences=2) for value in values if str(value or "").strip()]
    if not cleaned:
        return "- Keine Zusatzfakten vorhanden."
    return "\n".join(f"- {item}" for item in cleaned[:8])


def build_prompt3(
    *,
    product: ProductKnowledgeEntry | Dict[str, Any],
    profile: Optional[DurationProfile] = None,
) -> str:
    profile = profile or get_duration_profile(8)
    payload = product.model_dump(mode="json") if isinstance(product, ProductKnowledgeEntry) else dict(product)
    template = _load_text_prompt("prompt3", profile.target_length_tier)
    return template.format(
        product_name=str(payload.get("product_name") or "").strip(),
        source_label=str(payload.get("source_label") or "").strip(),
        product_summary=_clip_text(payload.get("summary") or "", 320),
        product_facts=_format_prompt3_fact_lines(list(payload.get("facts") or [])),
        support_facts=_format_prompt3_fact_lines(list(payload.get("support_facts") or [])),
    ).strip()
