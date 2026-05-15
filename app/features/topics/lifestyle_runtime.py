"""
Lifestyle topic generation runtime.
"""

from __future__ import annotations

import math
import random
import re
import secrets
from typing import Callable, Dict, List, Optional

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.core.video_profiles import get_duration_profile, get_script_duration_bounds, script_word_count
from app.features.topics.content_utils import extract_soft_cta, strip_cta_from_script
from app.features.topics.schemas import DialogScripts
from app.features.topics.topic_validation import classify_script_overlap, normalize_similarity_text, trim_spoken_script_to_word_bounds
from app.features.topics.topic_validation import sanitize_spoken_fragment

logger = get_logger(__name__)


def _derive_lifestyle_title(main_script: str, rotation: str, fallback_title: str) -> str:
    source_text = (rotation or main_script or "").strip()
    if not source_text:
        return fallback_title
    normalized = re.sub(r"\s+", " ", source_text)
    words = re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", normalized)
    if not words:
        return fallback_title
    title = " ".join(words[:8]).strip(" -,:;.!?")
    return title[:90] if title else fallback_title


def _script_prefix_signature(script: str, *, words: int = 4) -> str:
    tokens = normalize_similarity_text(script).split()
    if len(tokens) < words:
        return ""
    return " ".join(tokens[:words])


def _script_suffix_signature(script: str, *, words: int = 6) -> str:
    tokens = normalize_similarity_text(script).split()
    if len(tokens) < words:
        return ""
    return " ".join(tokens[-words:])


def _request_level_overlap_reason(script: str, existing_scripts: List[str]) -> str:
    prefix = _script_prefix_signature(script)
    suffix = _script_suffix_signature(script)
    for existing in existing_scripts:
        overlap_reason = classify_script_overlap(script, existing)
        if overlap_reason:
            return overlap_reason
        if prefix and prefix == _script_prefix_signature(existing):
            return "duplicate_request_hook"
        if suffix and suffix == _script_suffix_signature(existing):
            return "duplicate_request_suffix"
    return ""


def _fit_lifestyle_script_to_tier(script: str, *, target_length_tier: Optional[int]) -> str:
    cleaned = sanitize_spoken_fragment(script, ensure_terminal=True)
    if not target_length_tier:
        return cleaned
    min_words, max_words = get_script_duration_bounds("lifestyle", target_length_tier)
    if script_word_count(cleaned) > max_words:
        cleaned = trim_spoken_script_to_word_bounds(cleaned, min_words=min_words, max_words=max_words)

    topic_hint = _topic_hint(cleaned)
    for sentence in _lifestyle_padding_sentences(topic_hint):
        if script_word_count(cleaned) >= min_words:
            break
        candidate = sanitize_spoken_fragment(f"{cleaned} {sentence}", ensure_terminal=True)
        if script_word_count(candidate) > max_words:
            candidate = trim_spoken_script_to_word_bounds(candidate, min_words=min_words, max_words=max_words)
        cleaned = candidate

    return cleaned


def _topic_hint(topic_template: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", str(topic_template or ""))
    if not words:
        return "dein Alltag"
    return " ".join(words[:3])


def _lifestyle_padding_sentences(topic_hint: str) -> List[str]:
    hint = topic_hint or "dein Alltag"
    return [
        f"Gerade bei {hint} summieren sich kleine Umwege schneller, als viele von aussen erwarten.",
        "Wenn du den Weg vorher kurz pruefst, sparst du dir spaeter hektische Stopps und unnoetiges Zuruecksetzen.",
        "So bleibt der naechste Schritt klar, auch wenn unterwegs wieder etwas nicht sofort passt.",
        "Diese paar Minuten Planung klingen klein, machen im Rollstuhl-Alltag aber oft den groessten Unterschied.",
        "Am Ende bleibt mehr Energie fuer den eigentlichen Termin, statt schon auf dem Hinweg verloren zu gehen.",
    ]


def _synthesize_lifestyle_dialog_scripts(
    topic_template: str,
    *,
    target_length_tier: Optional[int],
) -> DialogScripts:
    hint = _topic_hint(topic_template)
    script = _fit_lifestyle_script_to_tier(
        " ".join(
            [
                f"Wenn {hint} im Alltag mehr Kraft kostet, merkst du das oft erst nach mehreren kleinen Umwegen.",
                "Pruefe den Weg vorher kurz und plane einen einfachen Ausweichschritt ein.",
                "Damit musst du unterwegs weniger improvisieren und bleibst im Kopf deutlich ruhiger.",
                "Genau solche Routinen nehmen Druck raus, wenn der Alltag sowieso schon genug Energie kostet.",
                "So bleibt mehr Kraft fuer das, was du eigentlich vorhast, statt fuer zusaetzliche Barrieren draufzugehen.",
            ]
        ),
        target_length_tier=target_length_tier,
    )
    description = (
        "Dieser Lifestyle Beitrag zeigt einen konkreten Alltagsschritt, "
        "der Rollstuhlnutzerinnen und Rollstuhlnutzern Planung, Kraft und "
        "Orientierung im Tagesablauf erleichtert."
    )
    return DialogScripts(
        problem_agitate_solution=[script],
        testimonial=[script],
        transformation=[script],
        description=description,
    )


def generate_lifestyle_topics(
    *,
    count: int = 1,
    seed: Optional[int] = None,
    target_length_tier: Optional[int] = None,
    generate_dialog_scripts_fn: Callable,
) -> List[Dict[str, object]]:
    lifestyle_topic_templates = [
        "Rollstuhl-Alltag – Tipps & Tricks",
        "Barrierefreiheit im Alltag erleben",
        "Community-Erfahrungen teilen",
        "Freizeit mit Rollstuhl genießen",
        "Alltägliche Herausforderungen meistern",
        "Spontane Wege ohne Extra-Stress planen",
        "ÖPNV, Umwege und clevere Alltagsroutinen",
        "Wohnung, Türen und enge Übergänge entspannter meistern",
        "Selbstbestimmt unterwegs trotz kleiner Barrieren",
        "Was im Rollstuhl-Alltag wirklich Energie spart",
    ]
    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    shuffled_templates = lifestyle_topic_templates[:]
    rng.shuffle(shuffled_templates)

    results: List[Dict[str, object]] = []
    used_hooks: List[str] = []
    accepted_scripts: List[str] = []
    for index in range(count):
        resolved_profile = get_duration_profile(target_length_tier) if target_length_tier else None
        retry_hooks = list(used_hooks)
        topic_data: Optional[Dict[str, object]] = None
        for attempt in range(3):
            topic_template = shuffled_templates[(index + attempt) % len(shuffled_templates)]
            try:
                dialog_scripts = generate_dialog_scripts_fn(
                    topic=topic_template,
                    scripts_required=1,
                    previously_used_hooks=retry_hooks if retry_hooks else None,
                    profile=resolved_profile,
                )
            except ValidationError as exc:
                logger.warning(
                    "lifestyle_topic_generation_retry",
                    template_title=topic_template,
                    attempt=attempt + 1,
                    error=getattr(exc, "message", str(exc)),
                    details=getattr(exc, "details", {}),
                )
                retry_hooks.append(topic_template[:24])
                topic_data = None
                continue
            main_script = _fit_lifestyle_script_to_tier(
                dialog_scripts.problem_agitate_solution[0],
                target_length_tier=target_length_tier,
            )
            dialog_scripts.problem_agitate_solution[0] = main_script
            overlap_reason = _request_level_overlap_reason(main_script, accepted_scripts)
            if overlap_reason:
                retry_hooks.append(" ".join(main_script.split()[:4]))
                logger.info(
                    "lifestyle_topic_retrying_for_diversity",
                    template_title=topic_template,
                    attempt=attempt + 1,
                    reason=overlap_reason,
                )
                topic_data = None
                continue

            cta = extract_soft_cta(main_script)
            rotation = strip_cta_from_script(main_script, cta)
            if not rotation or not rotation.strip():
                rotation = main_script.strip()
                words = rotation.split()
                cta = " ".join(words[-4:]) if len(words) > 4 else rotation

            derived_title = _derive_lifestyle_title(main_script, rotation, topic_template)
            duration = max(1, math.ceil(len(main_script.split()) / 2.6))
            topic_data = {
                "title": derived_title,
                "template_title": topic_template,
                "rotation": rotation,
                "cta": cta,
                "spoken_duration": duration,
                "dialog_scripts": dialog_scripts,
                "framework": "PAL",
            }
            accepted_scripts.append(main_script)
            used_hooks.append(" ".join(main_script.split()[:4]))
            break

        if topic_data is None:
            topic_template = shuffled_templates[index % len(shuffled_templates)]
            try:
                dialog_scripts = generate_dialog_scripts_fn(
                    topic=topic_template,
                    scripts_required=1,
                    previously_used_hooks=used_hooks if used_hooks else None,
                    profile=resolved_profile,
                )
            except ValidationError as exc:
                logger.warning(
                    "lifestyle_topic_provider_exhausted_synthesizing_fallback",
                    template_title=topic_template,
                    error=getattr(exc, "message", str(exc)),
                    details=getattr(exc, "details", {}),
                )
                dialog_scripts = _synthesize_lifestyle_dialog_scripts(
                    topic_template,
                    target_length_tier=target_length_tier,
                )
            main_script = _fit_lifestyle_script_to_tier(
                dialog_scripts.problem_agitate_solution[0],
                target_length_tier=target_length_tier,
            )
            dialog_scripts.problem_agitate_solution[0] = main_script
            cta = extract_soft_cta(main_script)
            rotation = strip_cta_from_script(main_script, cta) or main_script.strip()
            if not cta:
                words = rotation.split()
                cta = " ".join(words[-4:]) if len(words) > 4 else rotation
            topic_data = {
                "title": _derive_lifestyle_title(main_script, rotation, topic_template),
                "template_title": topic_template,
                "rotation": rotation,
                "cta": cta,
                "spoken_duration": max(1, math.ceil(len(main_script.split()) / 2.6)),
                "dialog_scripts": dialog_scripts,
                "framework": "PAL",
            }
            accepted_scripts.append(main_script)
            used_hooks.append(" ".join(main_script.split()[:4]))
            logger.warning(
                "lifestyle_topic_diversity_fallback_accept",
                template_title=topic_template,
            )

        results.append(topic_data)
        logger.info(
            "lifestyle_topic_generated",
            title=topic_data["title"],
            template_title=topic_data["template_title"],
            scripts_count=1,
            seed=seed,
        )
    return results
