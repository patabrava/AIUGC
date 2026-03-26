"""
Validation helpers for topic generation contracts.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List

import httpx

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.features.topics.content_utils import extract_soft_cta
from app.features.topics.schemas import ResearchAgentItem

logger = get_logger(__name__)

MIN_SCRIPT_WORDS = 12
MAX_SCRIPT_WORDS = 15
MIN_SCRIPT_SECONDS = 5
MAX_SCRIPT_SECONDS = 6
MAX_SCRIPT_CHARS_NO_SPACES = 90
CHARS_PER_SECOND_ESTIMATE = 17.0

PROMPT2_DIALOG_WORD_BOUNDS = {
    8: (16, 20),
    16: (24, 34),
    32: (40, 66),
}

GERMAN_SIGNAL_WORDS = {
    "und", "oder", "mit", "ohne", "für", "deutschland", "deutsche", "deutschen",
    "rollstuhl", "rollstuhlnutzer", "rollstuhlnutzerinnen", "alltag", "pflege",
    "recht", "leistungen", "anspruch", "barrierefrei", "barrierefreiheit",
    "hilfsmittel", "krankenkasse", "begleitperson", "beratung", "quelle",
    "quellen", "freundlich", "direkt", "keine", "nicht", "dein", "deine", "du",
}

ENGLISH_SIGNAL_WORDS = {
    "advanced", "and", "awareness", "care", "challenge", "challenges", "community",
    "days", "events", "following", "for", "forums", "groups", "in", "local",
    "media", "mentoring", "movement", "patient", "peer", "rehabilitation",
    "sports", "staying", "support", "systems", "training", "transfer", "users",
    "visible", "vocational", "wheelchair", "with",
}

ACCEPTED_GERMAN_LOAN_PHRASES = {
    "peer-support",
    "peer support",
}


def _validate_url_accessible(url: str, timeout: float = 5.0) -> bool:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            try:
                response = client.head(url)
                if response.status_code < 400:
                    return True
                if response.status_code == 405:
                    response = client.get(url)
                    return response.status_code < 400
                return False
            except httpx.HTTPStatusError:
                response = client.get(url)
                return response.status_code < 400
    except Exception as exc:
        logger.debug("url_validation_failed", url=url, error=str(exc))
        return False


def compute_bigram_jaccard(a: str, b: str) -> float:
    def bigrams(text: str) -> set[str]:
        tokens = text.lower().split()
        return {" ".join(tokens[i:i + 2]) for i in range(len(tokens) - 1)} if len(tokens) > 1 else set()

    bigrams_a = bigrams(a)
    bigrams_b = bigrams(b)
    if not bigrams_a or not bigrams_b:
        return 0.0
    return len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)


def validate_round_robin(items: List[ResearchAgentItem]) -> None:
    topics = [item.topic for item in items]
    unique_topics = list(dict.fromkeys(topics))
    min_expected = min(2, len(items))
    if len(unique_topics) < min_expected:
        raise ValidationError(
            message="PROMPT_1 batch lacks enough topic variety",
            details={"topics": topics, "unique_topics": unique_topics, "min_expected": min_expected},
        )


def validate_unique_ctas(items: List[ResearchAgentItem]) -> None:
    seen: Dict[str, int] = {}
    for idx, item in enumerate(items):
        cta = extract_soft_cta(item.script)
        if cta in seen:
            raise ValidationError(
                message="CTA reuse detected",
                details={"cta": cta, "first_index": seen[cta], "duplicate_index": idx},
            )
        seen[cta] = idx


def _script_non_space_char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text.strip()))


def estimate_script_duration_seconds(text: str) -> int:
    words = text.strip().split()
    if not words:
        return 0
    word_estimate = math.ceil(len(words) / 2.6)
    char_estimate = math.ceil(_script_non_space_char_count(text) / CHARS_PER_SECOND_ESTIMATE)
    return max(word_estimate, char_estimate)


def validate_duration(item: ResearchAgentItem) -> None:
    calculated = estimate_script_duration_seconds(item.script)
    char_count = _script_non_space_char_count(item.script)
    if char_count > MAX_SCRIPT_CHARS_NO_SPACES:
        raise ValidationError(
            message="Script too dense for natural Veo speech delivery",
            details={
                "word_count": item.word_count(),
                "char_count_no_spaces": char_count,
                "max_char_count_no_spaces": MAX_SCRIPT_CHARS_NO_SPACES,
                "calculated": calculated,
            },
        )
    if calculated > MAX_SCRIPT_SECONDS:
        raise ValidationError(
            message="Script exceeds 6 seconds",
            details={
                "word_count": item.word_count(),
                "char_count_no_spaces": char_count,
                "calculated": calculated,
            },
        )
    if calculated < MIN_SCRIPT_SECONDS:
        raise ValidationError(
            message="Script under 5 seconds",
            details={
                "word_count": item.word_count(),
                "char_count_no_spaces": char_count,
                "calculated": calculated,
            },
        )
    if calculated != item.estimated_duration_s:
        item.estimated_duration_s = calculated


def validate_summary(item: ResearchAgentItem) -> None:
    overlap = compute_bigram_jaccard(item.script, item.source_summary)
    if overlap > 0.25:
        raise ValidationError(
            message="Source summary overlaps too much with script (must provide additional facts, not repeat script)",
            details={"jaccard": overlap, "threshold": 0.25},
        )


def _validate_dialog_script_tier(script: str, profile: Any, context: str = "") -> None:
    tier = getattr(profile, "target_length_tier", None)
    min_words, max_words = PROMPT2_DIALOG_WORD_BOUNDS.get(int(tier or 8), (16, 20))
    words = script.split()
    if not words:
        raise ValidationError(message="Dialog script is empty", details={"context": context})
    if len(words) < min_words or len(words) > max_words:
        raise ValidationError(
            message="Dialog script does not match the requested tier",
            details={
                "context": context,
                "target_length_tier": tier,
                "word_count": len(words),
                "expected_range": [min_words, max_words],
            },
        )


def _validate_dialog_script_semantics(script: str, context: str = "") -> None:
    text = script.strip()
    if not text:
        raise ValidationError(message="Dialog script is empty", details={"context": context})
    if "\n" in text:
        raise ValidationError(message="Dialog script must be a single line", details={"context": context})
    if text[-1] not in ".!?":
        raise ValidationError(
            message="Dialog script must end with terminal punctuation",
            details={"context": context, "script": text[:120]},
        )


def _dialog_word_bounds(profile: Any) -> tuple[int, int]:
    tier = getattr(profile, "target_length_tier", None)
    return PROMPT2_DIALOG_WORD_BOUNDS.get(int(tier or 8), (16, 20))


def validate_sources_accessible(item: ResearchAgentItem) -> None:
    inaccessible_sources = []
    for source in item.sources:
        url = str(source.url)
        if not _validate_url_accessible(url, timeout=8.0):
            inaccessible_sources.append({"title": source.title, "url": url})
    if inaccessible_sources:
        logger.warning(
            "research_source_urls_not_accessible",
            topic=item.topic,
            inaccessible_count=len(inaccessible_sources),
            inaccessible_sources=inaccessible_sources,
            guidance="URLs may be outdated or temporarily unavailable",
        )


def _tokenize_language_words(text: str) -> List[str]:
    return re.findall(r"[a-zA-ZäöüÄÖÜß]+", text.lower())


def _find_english_markers(text: str) -> List[str]:
    tokens = _tokenize_language_words(text)
    lowered = text.lower()
    filtered_signals = set(ENGLISH_SIGNAL_WORDS)
    if any(phrase in lowered for phrase in ACCEPTED_GERMAN_LOAN_PHRASES):
        filtered_signals -= {"peer", "support"}
    return sorted({token for token in tokens if token in filtered_signals})


def _count_german_markers(text: str) -> int:
    tokens = _tokenize_language_words(text)
    return sum(1 for token in tokens if token in GERMAN_SIGNAL_WORDS)


def validate_german_content(item: ResearchAgentItem) -> None:
    fields_to_check = {
        "topic": item.topic,
        "script": item.script,
        "source_summary": item.source_summary,
        "tone": item.tone,
        "disclaimer": item.disclaimer,
    }
    violations: List[Dict[str, Any]] = []
    for field_name, value in fields_to_check.items():
        english_markers = _find_english_markers(value)
        german_markers = _count_german_markers(value)
        if not english_markers:
            continue
        is_short_field = field_name in {"topic", "tone", "disclaimer"}
        if is_short_field or len(english_markers) >= 2 or german_markers == 0:
            violations.append(
                {
                    "field": field_name,
                    "english_markers": english_markers,
                    "value": value[:200],
                }
            )
    if violations:
        raise ValidationError(
            message="PROMPT_1 output must be fully in German",
            details={"violations": violations},
        )


def normalize_framework(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return "PAL"
    value_lower = value.lower().strip()
    if "pal" in value_lower or "problem" in value_lower or "agit" in value_lower or "lösung" in value_lower:
        return "PAL"
    if "testimonial" in value_lower or "zeugnis" in value_lower:
        return "Testimonial"
    if "transformation" in value_lower or "wandel" in value_lower:
        return "Transformation"
    return value
