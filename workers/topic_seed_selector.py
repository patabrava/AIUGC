"""
Topic Seed Selector
Determines which topics to research next: Phase 1 (YAML bank) then Phase 2 (LLM-generated).
"""
import os
import sys
import time
from typing import List, Tuple

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.logging import get_logger
from app.features.topics.queries import get_all_topics_from_registry, get_researched_topic_texts
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
MAX_LLM_SEED_ATTEMPTS = int(os.environ.get("TOPIC_LLM_SEED_ATTEMPTS", "3"))
LLM_SEED_BACKOFF_SECONDS = float(os.environ.get("TOPIC_LLM_SEED_BACKOFF_SECONDS", "2"))


def _get_existing_research_titles() -> List[str]:
    """Collect historical seed/topic texts so new runs stay in new territory."""
    existing = get_all_topics_from_registry()
    historical = get_researched_topic_texts()
    titles: List[str] = []
    seen = set()
    for raw in [t.get("title") for t in existing if t.get("title")] + historical:
        title = str(raw or "").strip()
        if not title:
            continue
        signature = " ".join(sorted(tokenize(title))) or title.lower()
        if signature in seen:
            continue
        seen.add(signature)
        titles.append(title)
    return titles


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
    """Filter out seeds that overlap with previous seed/topic families."""
    existing_titles = [title.lower().strip() for title in _get_existing_research_titles()]
    exact_titles = set(existing_titles)

    unresearched = []
    for seed in seed_topics:
        seed_lower = seed.lower().strip()
        # Exact match check
        if seed_lower in exact_titles:
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


def _build_fallback_seeds(existing_titles: List[str], count: int, niche: str) -> List[str]:
    """Generate deterministic non-empty fallback seeds when Gemini produces nothing."""
    base_topics = [
        f"Die 3 häufigsten Missverständnisse zu {niche}",
        f"So erkennst du versteckte Barrieren bei {niche}",
        f"Ein einfacher Weg, um {niche} im Alltag besser zu machen",
        f"Was viele bei {niche} zuerst falsch machen",
        f"Ein schneller Praxis-Tipp zu {niche}",
        f"Warum {niche} oft an kleinen Details scheitert",
        f"Der unterschätzte Hebel für bessere {niche}",
        f"Das solltest du bei {niche} sofort prüfen",
    ]

    existing = {title.lower().strip() for title in existing_titles}
    fallback: List[str] = []
    for topic in base_topics:
        candidate = topic.strip()
        if not candidate or candidate.lower() in existing:
            continue
        fallback.append(candidate)
        if len(fallback) >= count:
            break

    if len(fallback) < count:
        suffix = 1
        while len(fallback) < count:
            candidate = f"{niche} - neuer Blickwinkel {suffix}"
            if candidate.lower() not in existing and candidate not in fallback:
                fallback.append(candidate)
            suffix += 1
    return fallback[:count]


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
        existing_titles = _get_existing_research_titles()
        llm_seeds: List[str] = []
        for attempt in range(MAX_LLM_SEED_ATTEMPTS):
            try:
                llm_seeds = _generate_llm_seeds(existing_titles, remaining, niche)
                if llm_seeds:
                    break
            except Exception as exc:
                logger.warning(
                    "topic_seed_llm_generation_failed",
                    attempt=attempt + 1,
                    max_attempts=MAX_LLM_SEED_ATTEMPTS,
                    error=str(exc),
                )
            if attempt + 1 < MAX_LLM_SEED_ATTEMPTS:
                time.sleep(LLM_SEED_BACKOFF_SECONDS * (attempt + 1))

        llm_filtered = filter_unresearched_seeds(llm_seeds)
        seeds.extend(llm_filtered[:remaining])
        llm_added = len(llm_filtered[:remaining])

        if len(seeds) < max_topics:
            fallback = _build_fallback_seeds(existing_titles + seeds, max_topics - len(seeds), niche)
            fallback = filter_unresearched_seeds(fallback)
            seeds.extend(fallback[: max_topics - len(seeds)])

    if llm_added == 0:
        source = "yaml_bank" if len(seeds) else "fallback_bank"
    elif len(unresearched) == 0:
        source = "llm_generated"
    else:
        source = "mixed"
    if len(seeds) and len(unresearched) == 0 and llm_added == 0:
        source = "fallback_bank"
    return seeds[:max_topics], source
