"""Topic discovery prompt templates.
Per IMPLEMENTATION_GUIDE Phase 2 requirements.
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional

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


def build_prompt1(
    brand: str,
    post_type: str,
    desired_topics: int,
    chunk_index: Optional[int] = None,
    total_chunks: Optional[int] = None,
) -> str:
    """Render PROMPT_1 with dynamic context from YAML template."""
    data = _load_prompt("prompt1")
    post_type_context = {
        "value": "Educational Mehrwert-Clips",
        "lifestyle": "Lifestyle-Vibes mit Community-Touch",
        "product": "Produktnahe Alltagshilfen",
    }.get(post_type, post_type)

    format_kwargs = {
        "brand": brand,
        "post_type_context": post_type_context,
        "desired_topics": desired_topics,
        "chunk_index": chunk_index or 1,
        "total_chunks": total_chunks or 1,
    }

    return _join_sections(
        data.get("core", "").format(**format_kwargs),
        data.get("brand_context", "").format(**format_kwargs),
        data.get("topic_pool", "").format(**format_kwargs),
        data.get("output_schema", "").format(**format_kwargs),
        data.get("chunk_rules", "").format(**format_kwargs),
        data.get("example", "").format(**format_kwargs),
        data.get("closing", "").format(**format_kwargs),
    )


def build_prompt2(brand: str, topic: str) -> str:
    """Render PROMPT_2 with brand/topic context from YAML template."""
    data = _load_prompt("prompt2")
    format_kwargs = {"brand": brand, "topic": topic}

    return _join_sections(
        data.get("task", "").format(**format_kwargs),
        data.get("output", "").format(**format_kwargs),
        data.get("rules", "").format(**format_kwargs),
    )
