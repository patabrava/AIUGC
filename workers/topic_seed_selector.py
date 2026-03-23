"""
Topic Seed Selector
Determines which topics to research next: Phase 1 (YAML bank) then Phase 2 (LLM-generated).
"""
import os
import sys
from typing import List, Tuple

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.logging import get_logger
from app.features.topics.queries import get_all_topics_from_registry
from app.features.topics.deduplication import tokenize, jaccard_similarity

logger = get_logger(__name__)

TOPIC_BANK_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "features",
    "topics",
    "prompt_data",
    "topic_bank.yaml",
)

DEFAULT_NICHE = "Schwerbehinderung, Treppenlifte, Barrierefreiheit"


def load_seed_topics_from_yaml() -> List[str]:
    """Load and flatten all seed topics from topic_bank.yaml.

    Returns empty list if file is missing or malformed.
    """
    try:
        with open(TOPIC_BANK_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        logger.warning("topic_bank_yaml_missing", path=TOPIC_BANK_PATH)
        return []
    except yaml.YAMLError as exc:
        logger.warning("topic_bank_yaml_malformed", path=TOPIC_BANK_PATH, error=str(exc))
        return []

    if not isinstance(data, dict):
        return []

    # Prefer flat "topics" list if present
    if "topics" in data and isinstance(data["topics"], list):
        return [str(t) for t in data["topics"] if t]

    # Otherwise flatten categories
    topics = []
    for category in data.get("categories", []):
        for topic in category.get("topics", []):
            if topic:
                topics.append(str(topic))
    return topics


def filter_unresearched_seeds(seed_topics: List[str]) -> List[str]:
    """Filter out seeds that already exist in topic_registry (by title similarity)."""
    existing = get_all_topics_from_registry()
    existing_titles = {t["title"].lower().strip() for t in existing if t.get("title")}

    unresearched = []
    for seed in seed_topics:
        seed_lower = seed.lower().strip()
        # Exact match check
        if seed_lower in existing_titles:
            continue
        # Fuzzy match: Jaccard > 0.7 means too similar
        is_dup = False
        seed_tokens = tokenize(seed)
        for title in existing_titles:
            if jaccard_similarity(seed_tokens, tokenize(title)) > 0.7:
                is_dup = True
                break
        if not is_dup:
            unresearched.append(seed)
    return unresearched


def _generate_llm_seeds(
    existing_titles: List[str],
    count: int,
    niche: str,
) -> List[str]:
    """Generate new seed topic ideas via Gemini."""
    from app.adapters.llm_client import get_llm_client

    llm = get_llm_client()
    existing_str = "\n".join(f"- {t}" for t in existing_titles[:100])
    prompt = (
        f"Du bist ein Content-Stratege für den Bereich: {niche}.\n\n"
        f"Hier sind Themen, die wir bereits behandelt haben:\n{existing_str}\n\n"
        f"Generiere genau {count} NEUE, einzigartige Content-Themen für kurze Social-Media-Videos "
        f"(8-32 Sekunden). Jedes Thema soll ein konkretes Problem, einen Tipp oder eine "
        f"überraschende Tatsache für Menschen mit Behinderung / Rollstuhlfahrer behandeln.\n\n"
        f"Antworte NUR mit einer nummerierten Liste, ein Thema pro Zeile. "
        f"Keine Erklärungen, keine Einleitungen."
    )
    response = llm.generate_gemini_text(
        prompt=prompt,
    )
    lines = [
        line.strip().lstrip("0123456789.)- ").strip()
        for line in response.strip().split("\n")
        if line.strip()
    ]
    return [l for l in lines if len(l) > 10][:count]


def select_seeds(
    max_topics: int = 5,
    niche: str = DEFAULT_NICHE,
) -> Tuple[List[str], str]:
    """Select seed topics to research.

    Returns (seeds, source) where source is 'yaml_bank', 'llm_generated', or 'mixed'.
    """
    # Phase 1: YAML bank
    yaml_seeds = load_seed_topics_from_yaml()
    unresearched = filter_unresearched_seeds(yaml_seeds)

    if len(unresearched) >= max_topics:
        return unresearched[:max_topics], "yaml_bank"

    # Phase 2: Top up with LLM-generated seeds
    seeds = list(unresearched)
    remaining = max_topics - len(seeds)
    llm_added = 0

    if remaining > 0:
        existing = get_all_topics_from_registry()
        existing_titles = [t["title"] for t in existing if t.get("title")]
        llm_seeds = _generate_llm_seeds(existing_titles, remaining, niche)
        llm_filtered = filter_unresearched_seeds(llm_seeds)
        seeds.extend(llm_filtered[:remaining])
        llm_added = len(llm_filtered[:remaining])

    if llm_added == 0:
        source = "yaml_bank"
    elif len(unresearched) == 0:
        source = "llm_generated"
    else:
        source = "mixed"
    return seeds[:max_topics], source
