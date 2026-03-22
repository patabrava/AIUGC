"""
Lifestyle topic generation runtime.
"""

from __future__ import annotations

import math
import random
import re
import secrets
from typing import Callable, Dict, List, Optional

from app.core.logging import get_logger
from app.features.topics.content_utils import extract_soft_cta, strip_cta_from_script

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


def generate_lifestyle_topics(
    *,
    count: int = 1,
    seed: Optional[int] = None,
    generate_dialog_scripts_fn: Callable,
) -> List[Dict[str, object]]:
    lifestyle_topic_templates = [
        "Rollstuhl-Alltag – Tipps & Tricks",
        "Barrierefreiheit im Alltag erleben",
        "Community-Erfahrungen teilen",
        "Freizeit mit Rollstuhl genießen",
        "Alltägliche Herausforderungen meistern",
    ]
    rng = random.Random(seed if seed is not None else secrets.randbits(64))
    shuffled_templates = lifestyle_topic_templates[:]
    rng.shuffle(shuffled_templates)

    results: List[Dict[str, object]] = []
    used_hooks: List[str] = []
    for index in range(count):
        topic_template = shuffled_templates[index % len(shuffled_templates)]
        dialog_scripts = generate_dialog_scripts_fn(
            topic=topic_template,
            scripts_required=1,
            previously_used_hooks=used_hooks if used_hooks else None,
        )
        main_script = dialog_scripts.problem_agitate_solution[0]
        cta = extract_soft_cta(main_script)
        rotation = strip_cta_from_script(main_script, cta)
        if not rotation or not rotation.strip():
            rotation = main_script.strip()
            words = rotation.split()
            cta = " ".join(words[-4:]) if len(words) > 4 else rotation

        derived_title = _derive_lifestyle_title(main_script, rotation, topic_template)
        used_hooks.append(" ".join(main_script.split()[:4]))
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
        results.append(topic_data)
        logger.info(
            "lifestyle_topic_generated",
            title=derived_title,
            template_title=topic_template,
            scripts_count=1,
            seed=seed,
        )
    return results
