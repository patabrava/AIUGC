"""Topic discovery prompt templates.
Per IMPLEMENTATION_GUIDE Phase 2 requirements.
"""

import random
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import yaml

PROMPT_DATA_DIR = Path(__file__).resolve().parent / "prompt_data"


@lru_cache(maxsize=None)
def _load_prompt(name: str) -> dict:
    """Load a YAML prompt definition from disk and cache the result."""
    prompt_path = PROMPT_DATA_DIR / f"{name}.yaml"
    with prompt_path.open("r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


def _join_sections(*sections: str) -> str:
    return "\n\n".join(section.strip() for section in sections if section).strip()


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
    data = _load_prompt("prompt1")
    topic_pool_text = data.get("topic_pool", "")
    return _extract_topic_candidates(topic_pool_text)


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
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
    assigned_topics: Optional[List[str]] = None,
    seed: Optional[int] = None,
) -> str:
    """Render PROMPT_1 with dynamic context from YAML template."""
    data = _load_prompt("prompt1")

    format_kwargs = {
        "desired_topics": desired_topics,
        "chunk_index": chunk_index or 1,
        "total_chunks": total_chunks or 1,
    }

    topic_pool_section = data.get("topic_pool", "").format(**format_kwargs)

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

    return _join_sections(
        data.get("core", "").format(**format_kwargs),
        data.get("audience_context", "").format(**format_kwargs),
        topic_pool_section,
        assigned_rotation_section,
        data.get("output_schema", "").format(**format_kwargs),
        data.get("chunk_rules", "").format(**format_kwargs),
        data.get("example", "").format(**format_kwargs),
        data.get("closing", "").format(**format_kwargs),
    )


def build_prompt2(topic: str, scripts_per_category: int = 5) -> str:
    """Render PROMPT_2 with topic context from YAML template."""
    data = _load_prompt("prompt2")
    total_scripts = scripts_per_category * 3
    format_kwargs = {
        "topic": topic,
        "scripts_per_category": scripts_per_category,
        "total_scripts": total_scripts,
    }

    return _join_sections(
        data.get("core", "").format(**format_kwargs),
        data.get("audience_context", "").format(**format_kwargs),
        data.get("voice", "").format(**format_kwargs),
        data.get("structure", "").format(**format_kwargs),
        data.get("length_rules", "").format(**format_kwargs),
        data.get("headings", "").format(**format_kwargs),
        data.get("description_section", "").format(**format_kwargs),
        data.get("closing", "").format(**format_kwargs),
    )
