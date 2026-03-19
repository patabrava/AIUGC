"""Topic discovery prompt templates.
Per IMPLEMENTATION_GUIDE Phase 2 requirements.
"""

import random
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from app.core.video_profiles import DurationProfile, get_duration_profile

PROMPT_DATA_DIR = Path(__file__).resolve().parent / "prompt_data"


@lru_cache(maxsize=None)
def _load_prompt_text(name: str) -> str:
    """Load a text prompt definition from disk and cache the result."""
    prompt_path = PROMPT_DATA_DIR / f"{name}.txt"
    return prompt_path.read_text(encoding="utf-8")


def _extract_topic_candidates(topic_pool_text: str) -> List[str]:
    """Extract bullet-list topic candidates from the topic_pool section."""
    candidates: List[str] = []
    for line in topic_pool_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            candidate = stripped[2:].strip()
            if candidate:
                candidates.append(candidate)
    return candidates


@lru_cache(maxsize=None)
def get_topic_pool_candidates() -> List[str]:
    """Return cached list of topic pool focus areas."""
    prompt_text = _load_prompt_text("prompt1_8s")
    return _extract_topic_candidates(prompt_text)


def _format_assigned_topics_section(assigned_topics: List[str]) -> str:
    lines = [
        "ZUFALLS-THEMEN FÜR DIESEN DURCHLAUF:",
        "Nutze jede Zeile genau einmal. Benenne den Topic in höchstens 10 Wörtern, eng angelehnt an den Schwerpunkt.",
    ]
    lines.extend(f"{idx + 1}. {topic}" for idx, topic in enumerate(assigned_topics))
    return "\n".join(lines)


def build_prompt1(
    post_type: str,
    desired_topics: int,
    profile: Optional[DurationProfile] = None,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
    assigned_topics: Optional[List[str]] = None,
    seed: Optional[int] = None,
) -> str:
    """Render PROMPT_1 with dynamic context from text template."""
    active_profile = profile or get_duration_profile(None)
    prompt_text = _load_prompt_text(f"prompt1_{active_profile.target_length_tier}s")

    format_kwargs = {
        "desired_topics": desired_topics,
        "chunk_index": chunk_index or 1,
        "total_chunks": total_chunks or 1,
        "prompt1_min_words": active_profile.prompt1_min_words,
        "prompt1_max_words": active_profile.prompt1_max_words,
        "prompt1_min_seconds": active_profile.prompt1_min_seconds,
        "prompt1_max_seconds": active_profile.prompt1_max_seconds,
        "prompt1_max_chars_no_spaces": active_profile.prompt1_max_chars_no_spaces,
        "prompt1_sentence_guidance": active_profile.prompt1_sentence_guidance,
        "target_length_tier": active_profile.target_length_tier,
    }

    assigned_rotation_section: Optional[str] = None
    if assigned_topics:
        assigned_rotation_section = _format_assigned_topics_section(assigned_topics)
    elif seed is not None:
        candidates = get_topic_pool_candidates()
        if candidates:
            rng = random.Random(seed)
            shuffled = candidates[:]
            rng.shuffle(shuffled)
            subset = shuffled[: max(1, min(desired_topics, len(shuffled)))]
            assigned_rotation_section = _format_assigned_topics_section(subset)

    return prompt_text.format(
        **format_kwargs,
        assigned_rotation_section=assigned_rotation_section or "",
    ).strip()


def build_prompt2(
    topic: str,
    scripts_per_category: int = 5,
    profile: Optional[DurationProfile] = None,
) -> str:
    """Render PROMPT_2 with topic context from text template."""
    active_profile = profile or get_duration_profile(None)
    prompt_text = _load_prompt_text(f"prompt2_{active_profile.target_length_tier}s")
    total_scripts = scripts_per_category * 3
    format_kwargs = {
        "topic": topic,
        "scripts_per_category": scripts_per_category,
        "total_scripts": total_scripts,
        "target_length_tier": active_profile.target_length_tier,
        "prompt2_min_words": active_profile.prompt2_min_words,
        "prompt2_max_words": active_profile.prompt2_max_words,
        "prompt2_sentence_guidance": active_profile.prompt2_sentence_guidance,
    }

    return prompt_text.format(**format_kwargs).strip()
