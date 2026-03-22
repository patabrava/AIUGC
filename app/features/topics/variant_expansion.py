"""
Multi-script variant expansion.

Generates multiple script variants per topic using a framework × hook_style
diversity matrix. Stateless — queries existing scripts to determine what's
missing, then picks the most diverse next combination.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional, Tuple

from app.adapters.llm_client import get_llm_client
from app.core.logging import get_logger
from app.core.video_profiles import get_duration_profile
from app.features.topics.prompts import build_prompt2
from app.features.topics.response_parsers import parse_prompt2_response

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
    raw_response = llm.generate_gemini_json(
        prompt=constrained_prompt,
        system_prompt="You are a German UGC script writer. Return valid JSON only.",
    )

    return parse_prompt2_response(raw_response)
