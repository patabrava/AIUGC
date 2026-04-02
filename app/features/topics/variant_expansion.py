"""
Multi-script variant expansion.

Generates multiple script variants per topic using a framework × hook_style
diversity matrix. Stateless — queries existing scripts to determine what's
missing, then picks the most diverse next combination.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from app.adapters.llm_client import get_llm_client
from app.core.errors import ThirdPartyError, ValidationError
from app.core.logging import get_logger
from app.core.video_profiles import get_duration_profile
from app.features.topics.prompts import build_prompt1_variant, build_prompt2, get_hook_bank
from app.features.topics.queries import (
    get_all_topics_from_registry,
    get_existing_variant_pairs,
    get_topic_research_dossiers,
    get_topic_scripts_for_registry,
    upsert_topic_script_variants,
)
from app.features.topics.response_parsers import parse_prompt1_response, parse_prompt2_response, _coerce_prompt2_payload, _validate_dialog_scripts_payload
from app.features.topics.schemas import DialogScripts, ResearchAgentItem
from app.features.topics.topic_validation import (
    estimate_script_duration_seconds,
    validate_pre_persistence_topic_payload,
)

logger = get_logger(__name__)

# Lifestyle-specific constants
LIFESTYLE_FRAMEWORKS = ["PAL", "Testimonial", "Transformation"]
LIFESTYLE_HOOK_STYLES = [
    "personal_story",
    "daily_tip",
    "community_moment",
    "challenge",
    "humor",
]

# Default config
DEFAULT_MAX_SCRIPTS_PER_TOPIC = 20
DEFAULT_MAX_SCRIPTS_PER_CRON_RUN = 30

ALL_TIERS = [8, 16, 32]


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", text or ""))


def _ensure_terminal_punctuation(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return stripped
    return stripped if stripped[-1] in ".!?" else f"{stripped}."


def _enforce_prompt1_word_envelope(prompt1_item, profile, lane_fact_texts: List[str]):
    min_words = int(getattr(profile, "prompt1_min_words", 12))
    max_words = int(getattr(profile, "prompt1_max_words", 15))
    script = _ensure_terminal_punctuation(getattr(prompt1_item, "script", ""))
    words = script.split()

    if _word_count(script) < min_words:
        for fact in lane_fact_texts:
            fragment = re.sub(r"\s+", " ", str(fact or "")).strip().rstrip(".!?")
            if not fragment:
                continue
            candidate = _ensure_terminal_punctuation(f"{' '.join(words)} {fragment}".strip())
            words = candidate.split()
            script = candidate
            if _word_count(script) >= min_words:
                break

    if _word_count(script) > max_words:
        script = _ensure_terminal_punctuation(" ".join(script.split()[:max_words]).strip())

    final_count = _word_count(script)
    if final_count < min_words or final_count > max_words:
        raise ValidationError(
            message="PROMPT_1 variant does not match target word envelope",
            details={
                "target_length_tier": getattr(profile, "target_length_tier", None),
                "word_count": final_count,
                "expected_range": [min_words, max_words],
            },
        )

    prompt1_item.script = script
    prompt1_item.estimated_duration_s = min(
        int(getattr(profile, "target_length_tier", 8) or 8),
        max(1, estimate_script_duration_seconds(script)),
    )
    return prompt1_item


def pick_next_variant(
    *,
    existing_pairs: List[Tuple[str, str]],
    available_frameworks: List[str],
    available_hook_styles: List[str],
    max_scripts: int = DEFAULT_MAX_SCRIPTS_PER_TOPIC,
) -> Optional[Tuple[str, str]]:
    """Pick the most diverse unused (framework, hook_style) combination.

    Returns None if all combinations are exhausted or max_scripts is reached.
    """
    if len(existing_pairs) >= max_scripts:
        return None

    used_set = set(existing_pairs)
    all_combos = [
        (fw, hs)
        for fw in available_frameworks
        for hs in available_hook_styles
        if (fw, hs) not in used_set
    ]
    if not all_combos:
        return None

    # Count how many scripts each framework and hook_style already have
    fw_counts = Counter(fw for fw, _ in existing_pairs)
    hs_counts = Counter(hs for _, hs in existing_pairs)

    # Sort by: least-used framework first, then least-used hook_style
    all_combos.sort(key=lambda pair: (fw_counts.get(pair[0], 0), hs_counts.get(pair[1], 0)))

    return all_combos[0]


def generate_dialog_scripts_variant(
    *,
    topic: str,
    forced_framework: str,
    forced_hook_style: str,
    target_length_tier: int = 8,
    dossier: dict | None = None,
):
    """Generate lifestyle dialog scripts constrained to a specific framework and hook style.

    Wraps PROMPT_2 with additional constraints. Does not modify the
    existing generate_dialog_scripts() function.
    """
    profile = get_duration_profile(target_length_tier)
    base_prompt = build_prompt2(
        topic=topic,
        scripts_per_category=1,
        profile=profile,
        dossier=dossier,
    )

    constraint_block = (
        f"\n\nPFLICHT-VORGABEN FÜR DIESES SKRIPT:\n"
        f"- Framework: {forced_framework}\n"
        f"- Hook-Stil: {forced_hook_style}\n"
        f"Halte dich strikt an dieses Framework und diesen Hook-Stil.\n"
    )
    constrained_prompt = base_prompt + constraint_block

    llm = get_llm_client()
    current_prompt = constrained_prompt

    for attempt in range(3):
        try:
            text_response = llm.generate_gemini_text(
                prompt=current_prompt,
                system_prompt=None,
                max_tokens=1600,
            )
            scripts = parse_prompt2_response(text_response, max_per_category=1)
            _validate_dialog_scripts_payload(scripts, profile, topic)
            return DialogScripts(
                problem_agitate_solution=scripts.problem_agitate_solution[:1],
                testimonial=scripts.testimonial[:1],
                transformation=scripts.transformation[:1],
                description=scripts.description,
            )
        except (ValidationError, ThirdPartyError) as exc:
            logger.warning(
                "dialog_scripts_variant_retry",
                topic=topic,
                forced_framework=forced_framework,
                forced_hook_style=forced_hook_style,
                attempt=attempt + 1,
                error=getattr(exc, "message", str(exc)),
            )
            current_prompt = f"{current_prompt}\n\nFEEDBACK: {getattr(exc, 'message', str(exc))}"

    raise ValidationError(
        message="Variant dialog script generation failed after text normalization",
        details={"topic": topic, "forced_framework": forced_framework, "forced_hook_style": forced_hook_style},
    )


def _get_hook_style_names() -> List[str]:
    """Extract hook family names from the hook bank YAML."""
    payload = get_hook_bank()
    families = list(payload.get("families") or [])
    return [str(f.get("name") or "").strip() for f in families if f.get("name")]


def _pick_lane_for_framework(
    lane_candidates: List[Dict[str, Any]],
    target_framework: str,
    existing_pairs: list,
) -> Dict[str, Any]:
    """Pick the lane whose framework_candidates contains the target framework."""
    matching = [
        lc for lc in lane_candidates
        if target_framework in (lc.get("framework_candidates") or [])
    ]
    if not matching:
        return lane_candidates[0] if lane_candidates else {}
    return matching[0]


def expand_topic_variants(
    *,
    topic_registry_id: str,
    title: str,
    post_type: str,
    target_length_tier: int,
    count: int = 1,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Generate up to *count* new script variants for a topic.

    Returns a summary dict with generated count and details.
    """
    existing_rows = get_existing_variant_pairs(
        topic_registry_id=topic_registry_id,
        target_length_tier=target_length_tier,
        post_type=post_type,
    )
    existing_pairs = [
        (row["framework"], row["hook_style"]) for row in existing_rows
    ]

    # Determine available frameworks and hook styles
    if post_type == "value":
        dossiers = get_topic_research_dossiers(topic_registry_id=topic_registry_id)
        dossier_payload = (dossiers[0].get("normalized_payload") or {}) if dossiers else {}
        available_frameworks = list(dossier_payload.get("framework_candidates") or ["PAL", "Testimonial", "Transformation"])
        available_hook_styles = _get_hook_style_names() or ["default"]
        lane_candidates = list(dossier_payload.get("lane_candidates") or [])
    else:
        dossiers = []
        dossier_payload = {}
        available_frameworks = LIFESTYLE_FRAMEWORKS
        available_hook_styles = LIFESTYLE_HOOK_STYLES
        lane_candidates = []

    generated = 0
    details: List[Dict[str, Any]] = []

    for _ in range(count):
        variant = pick_next_variant(
            existing_pairs=existing_pairs,
            available_frameworks=available_frameworks,
            available_hook_styles=available_hook_styles,
            max_scripts=DEFAULT_MAX_SCRIPTS_PER_TOPIC,
        )
        if variant is None:
            logger.info("variant_expansion_exhausted", topic_registry_id=topic_registry_id)
            break

        framework, hook_style = variant
        normalized_title = title

        if dry_run:
            details.append({"framework": framework, "hook_style": hook_style, "dry_run": True})
            existing_pairs.append(variant)
            generated += 1
            continue

        try:
            if post_type == "value":
                lane = _pick_lane_for_framework(lane_candidates, framework, existing_pairs)
                source_info = (dossier_payload.get("sources") or [{}])[0] if dossier_payload.get("sources") else {}
                source_title = str(source_info.get("title") or "").strip() or None
                source_url = str(source_info.get("url") or "").strip() or None
                lane_fact_texts = [
                    str(fact).strip()
                    for fact in list((lane or {}).get("facts") or []) + list(dossier_payload.get("facts") or [])
                    if str(fact).strip()
                ]
                variant_prompt = build_prompt1_variant(
                    post_type=post_type,
                    desired_topics=1,
                    dossier=dossier_payload,
                    lane_candidate=lane,
                    forced_framework=framework,
                    forced_hook_style=hook_style,
                    profile=get_duration_profile(target_length_tier),
                )
                llm = get_llm_client()
                raw = llm.generate_gemini_text(
                    prompt=variant_prompt,
                    system_prompt="You are the Flow Forge PROMPT_1 stage-3 script agent. Return only the final script text. Keep all output fully in German.",
                    max_tokens=3200,
                )
                script_text = re.sub(r"\s+", " ", str(raw or "").strip())
                if script_text and script_text[-1] not in ".!?":
                    script_text = script_text.rstrip(",;:") + "."
                if not script_text:
                    logger.warning("variant_expansion_parse_failed", framework=framework, hook_style=hook_style)
                    continue
                prompt1_item = ResearchAgentItem(
                    topic=str((lane or {}).get("title") or dossier_payload.get("topic") or title or "").strip() or "Thema",
                    script=script_text,
                    caption=str((lane or {}).get("source_summary") or dossier_payload.get("source_summary") or "").strip() or script_text,
                    framework=framework if framework in {"PAL", "Testimonial", "Transformation"} else "PAL",
                    sources=(
                        [{"title": source_title or str((lane or {}).get("title") or title or "Quelle"), "url": source_url}]
                        if source_url
                        else []
                    ),
                    source_summary=str((lane or {}).get("source_summary") or dossier_payload.get("source_summary") or "").strip() or script_text,
                    estimated_duration_s=max(1, min(get_duration_profile(target_length_tier).target_length_tier, estimate_script_duration_seconds(script_text))),
                    tone="direkt, freundlich, empowernd, du-Form",
                    disclaimer=str((lane or {}).get("disclaimer") or dossier_payload.get("disclaimer") or "Keine Rechts- oder medizinische Beratung.").strip(),
                )
                prompt1_item = _enforce_prompt1_word_envelope(
                    prompt1_item,
                    get_duration_profile(target_length_tier),
                    lane_fact_texts,
                )
                script_text = str(prompt1_item.script or "").strip()
                normalized_variant = validate_pre_persistence_topic_payload(
                    {
                        "topic": str(getattr(prompt1_item, "topic", title) or title).strip(),
                        "title": str(title or "").strip(),
                        "script": script_text,
                        "caption": str(getattr(prompt1_item, "caption", "") or "").strip(),
                        "source_summary": str(getattr(prompt1_item, "source_summary", "") or "").strip(),
                        "disclaimer": str(getattr(prompt1_item, "disclaimer", "") or "").strip(),
                    },
                    target_length_tier=target_length_tier,
                )
                script_text = str(normalized_variant.get("script") or script_text).strip()
                normalized_title = str(normalized_variant.get("title") or title or "").strip() or title
                variant_data: Dict[str, Any] = {
                    "script": script_text,
                    "framework": framework,
                    "hook_style": hook_style,
                    "bucket": framework.lower(),
                    "estimated_duration_s": getattr(prompt1_item, "estimated_duration_s", None),
                    "lane_key": getattr(prompt1_item, "lane_key", None) or lane.get("lane_key"),
                    "lane_family": getattr(prompt1_item, "lane_family", None) or lane.get("lane_family"),
                    "cluster_id": getattr(prompt1_item, "cluster_id", None),
                    "anchor_topic": getattr(prompt1_item, "anchor_topic", None),
                    "seed_payload": {},
                    "topic": str(normalized_variant.get("topic") or getattr(prompt1_item, "topic", title) or title or "").strip(),
                    "title": normalized_title,
                    "caption": str(normalized_variant.get("caption") or getattr(prompt1_item, "caption", "") or "").strip(),
                    "source_summary": str(normalized_variant.get("source_summary") or getattr(prompt1_item, "source_summary", "") or "").strip(),
                    "disclaimer": str(normalized_variant.get("disclaimer") or getattr(prompt1_item, "disclaimer", "") or "").strip(),
                }
            else:
                dialog_scripts = generate_dialog_scripts_variant(
                    topic=title,
                    forced_framework=framework,
                    forced_hook_style=hook_style,
                    target_length_tier=target_length_tier,
                )
                script_text = str(
                    (dialog_scripts.problem_agitate_solution or [""])[0]
                ).strip()
                normalized_variant = validate_pre_persistence_topic_payload(
                    {
                        "topic": str(title or "").strip(),
                        "title": str(title or "").strip(),
                        "script": script_text,
                        "caption": "",
                        "source_summary": "",
                        "disclaimer": "",
                    },
                    target_length_tier=target_length_tier,
                )
                script_text = str(normalized_variant.get("script") or script_text).strip()
                normalized_title = str(normalized_variant.get("title") or title or "").strip() or title
                variant_data = {
                    "script": script_text,
                    "framework": framework,
                    "hook_style": hook_style,
                    "bucket": framework.lower(),
                    "seed_payload": {},
                    "topic": str(normalized_variant.get("topic") or title or "").strip(),
                    "title": normalized_title,
                }

            if not script_text:
                logger.warning("variant_expansion_empty_script", framework=framework, hook_style=hook_style)
                continue

            dossier_id = dossiers[0].get("id") if (post_type == "value" and dossiers) else None
            upsert_topic_script_variants(
                topic_registry_id=topic_registry_id,
                title=normalized_title,
                post_type=post_type,
                target_length_tier=target_length_tier,
                topic_research_dossier_id=dossier_id,
                variants=[variant_data],
            )

            existing_pairs.append(variant)
            generated += 1
            details.append({"framework": framework, "hook_style": hook_style, "script": script_text[:80]})
            logger.info(
                "variant_expansion_generated",
                topic_registry_id=topic_registry_id,
                framework=framework,
                hook_style=hook_style,
            )

        except Exception as exc:
            logger.warning(
                "variant_expansion_failed",
                topic_registry_id=topic_registry_id,
                framework=framework,
                hook_style=hook_style,
                error=str(exc),
            )
            continue

    return {
        "topic_registry_id": topic_registry_id,
        "post_type": post_type,
        "target_length_tier": target_length_tier,
        "generated": generated,
        "total_existing": len(existing_rows) + generated,
        "details": details,
    }


def expand_script_bank(
    *,
    max_scripts_per_cron_run: int = DEFAULT_MAX_SCRIPTS_PER_CRON_RUN,
    target_length_tiers: List[int] | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Cron entry point: fill the script bank across all topics and tiers."""
    tiers = target_length_tiers or ALL_TIERS
    topics = get_all_topics_from_registry()

    scored = []
    for topic in topics:
        scripts = get_topic_scripts_for_registry(topic["id"])
        scored.append((len(scripts), topic))
    scored.sort(key=lambda pair: pair[0])

    total_generated = 0
    topic_results = []

    for script_count, topic in scored:
        if total_generated >= max_scripts_per_cron_run:
            break

        post_type = topic.get("post_type") or "value"

        for tier in tiers:
            if total_generated >= max_scripts_per_cron_run:
                break
            remaining_budget = max_scripts_per_cron_run - total_generated

            result = expand_topic_variants(
                topic_registry_id=topic["id"],
                title=topic.get("title") or "",
                post_type=post_type,
                target_length_tier=tier,
                count=min(remaining_budget, 3),
                dry_run=dry_run,
            )

            total_generated += result["generated"]
            if result["generated"] > 0:
                topic_results.append({
                    "topic_id": topic["id"],
                    "title": topic.get("title"),
                    "tier": tier,
                    "generated": result["generated"],
                    "total": result["total_existing"],
                })

            logger.info(
                "expand_script_bank_topic",
                topic_id=topic["id"],
                title=topic.get("title"),
                tier=tier,
                generated=result["generated"],
                total=result["total_existing"],
                dry_run=dry_run,
            )

    summary = {
        "total_generated": total_generated,
        "topics_processed": len(topic_results),
        "topic_results": topic_results,
        "dry_run": dry_run,
    }
    logger.info("expand_script_bank_complete", **summary)
    return summary
