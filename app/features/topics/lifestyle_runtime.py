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
from app.core.video_profiles import get_duration_profile
from app.features.topics.content_utils import extract_soft_cta, strip_cta_from_script
from app.features.topics.topic_validation import classify_script_overlap, normalize_similarity_text

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
            main_script = dialog_scripts.problem_agitate_solution[0]
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
            dialog_scripts = generate_dialog_scripts_fn(
                topic=topic_template,
                scripts_required=1,
                previously_used_hooks=used_hooks if used_hooks else None,
                profile=resolved_profile,
            )
            main_script = dialog_scripts.problem_agitate_solution[0]
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
