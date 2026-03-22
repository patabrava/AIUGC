"""
Multi-script variant expansion.

Generates multiple script variants per topic using a framework × hook_style
diversity matrix. Stateless — queries existing scripts to determine what's
missing, then picks the most diverse next combination.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional, Tuple

from app.core.logging import get_logger

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
