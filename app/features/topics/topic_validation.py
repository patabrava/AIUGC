"""
Validation helpers for topic generation contracts.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

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

PROMPT1_WORD_BOUNDS = {
    8: (12, 15),
    16: (26, 36),
    32: (54, 74),
}

PROMPT1_SENTENCE_BOUNDS = {
    8: (1, 1),
    16: (3, 4),
    32: (5, 6),
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

SPOKEN_COPY_LABEL_MARKERS = (
    "demografische dringlichkeit",
    "zentrale erkenntnisse",
    "leitende zusammenfassung",
    "einordnung der faktenlage",
    "faktenlage",
    "quellenlage",
    "studienlage",
)

INCOMPLETE_TRAILING_TOKENS = {
    "aber",
    "als",
    "am",
    "auch",
    "auf",
    "aus",
    "bei",
    "bist",
    "damit",
    "dann",
    "darauf",
    "dass",
    "dem",
    "den",
    "der",
    "des",
    "die",
    "durch",
    "ein",
    "eine",
    "einem",
    "einen",
    "einer",
    "eines",
    "fuer",
    "für",
    "haeufig",
    "häufig",
    "im",
    "in",
    "massiv",
    "nach",
    "oder",
    "ohne",
    "pro",
    "seit",
    "somit",
    "ueber",
    "über",
    "um",
    "und",
    "vom",
    "von",
    "vor",
    "waehrend",
    "während",
    "weil",
    "wenn",
    "zunehmender",
    "zunehmende",
    "zunehmendem",
    "zunehmenden",
    "chronischer",
    "chronische",
    "chronischem",
    "chronischen",
    "zu",
    "zum",
    "zur",
}

_ALLOWED_PARTICLE_ENDINGS = (
    re.compile(r"(?i)\b(?:ruf(?:e|st|t|en)?|meld(?:e|est|et|en)?|frag(?:e|st|t|en)?|sprich|sprecht|sprechen)\b.*\ban[.!?]$"),
    re.compile(r"(?i)\b(?:fahr(?:e|st|t|en)?|fährt|fahren|komm(?:e|st|t|en)?|kommen|nimm(?:st|t)?|nehmt|nehmen)\b.*\bmit[.!?]$"),
)
_DEFINITION_RESIDUE_PATTERNS = (
    re.compile(r"(?i)\bmaßnahmen zur feststellung und beurteilung\b"),
    re.compile(r"(?i)\büberlassung einer sache auf zeit\b"),
    re.compile(r"(?i)\bist-zustands\b"),
    re.compile(r"(?i)\beines kopierger[aä]tes\b"),
)

_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CITATION_PATTERN = re.compile(r"\[cite:\s*\d+(?:\s*,\s*\d+)*\]", flags=re.IGNORECASE)
_URL_PATTERN = re.compile(r"https?://\S+")
_BULLET_PREFIX_PATTERN = re.compile(r"(?m)^\s*[-*•]+\s*")
_MULTISPACE_PATTERN = re.compile(r"\s+")
_SCRIPT_ARTIFACT_PATTERN = re.compile(r"[\u200d\uFE0E\uFE0F]")
_RESEARCH_LABEL_PATTERN = re.compile(
    r"(?i)(?:^|[\s(\\[\"'])"
    r"(demografische dringlichkeit|zentrale erkenntnisse|leitende zusammenfassung|"
    r"einordnung(?: der faktenlage)?|faktenlage|quellenlage|quelle|quellen|"
    r"studie|studienlage|fazit|kontext)"
    r"(?:\s*:\s*|(?=[\s.?!,;:]|$))"
)
_ABBREVIATION_REPLACEMENTS = (
    (re.compile(r"\bz\.\s*b\.", flags=re.IGNORECASE), "__ABB_ZB__"),
    (re.compile(r"\bu\.\s*a\.", flags=re.IGNORECASE), "__ABB_UA__"),
    (re.compile(r"\bd\.\s*h\.", flags=re.IGNORECASE), "__ABB_DH__"),
    (re.compile(r"\bbzw\.", flags=re.IGNORECASE), "__ABB_BZW__"),
)


def _protect_abbreviations(text: str) -> str:
    protected = str(text or "")
    for pattern, placeholder in _ABBREVIATION_REPLACEMENTS:
        protected = pattern.sub(placeholder, protected)
    return protected


def _restore_abbreviations(text: str) -> str:
    restored = str(text or "")
    restored = restored.replace("__ABB_ZB__", "z.B.")
    restored = restored.replace("__ABB_UA__", "u.a.")
    restored = restored.replace("__ABB_DH__", "d.h.")
    restored = restored.replace("__ABB_BZW__", "bzw.")
    return restored


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


def get_prompt1_word_bounds(tier: int | None) -> tuple[int, int]:
    return PROMPT1_WORD_BOUNDS.get(int(tier or 8), (12, 15))


def get_prompt1_sentence_bounds(tier: int | None) -> tuple[int, int]:
    return PROMPT1_SENTENCE_BOUNDS.get(int(tier or 8), (1, 1))


def normalize_spoken_whitespace(text: Any) -> str:
    return _MULTISPACE_PATTERN.sub(" ", str(text or "")).strip()


def _strip_research_labels(text: Any) -> str:
    cleaned = str(text or "")
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = _RESEARCH_LABEL_PATTERN.sub(" ", cleaned)
    return cleaned


def sanitize_spoken_fragment(text: Any, *, ensure_terminal: bool = True) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    cleaned = _MARKDOWN_LINK_PATTERN.sub(r"\1", cleaned)
    cleaned = _CITATION_PATTERN.sub(" ", cleaned)
    cleaned = _URL_PATTERN.sub(" ", cleaned)
    cleaned = cleaned.replace("**", " ").replace("__", " ").replace("`", " ")
    cleaned = _BULLET_PREFIX_PATTERN.sub("", cleaned)
    cleaned = _SCRIPT_ARTIFACT_PATTERN.sub("", cleaned)
    cleaned = _strip_research_labels(cleaned)
    cleaned = re.sub(r"[|]+", " ", cleaned)
    cleaned = re.sub(r"(\w):\s+in(nen)?\b", r"\1:in\2", cleaned)
    cleaned = normalize_spoken_whitespace(cleaned)
    if not cleaned:
        return ""

    sentence_candidates = re.split(r"(?<=[.!?])\s+|\n+", _protect_abbreviations(cleaned))
    sentences: List[str] = []
    seen = set()
    for candidate in sentence_candidates:
        fragment = normalize_spoken_whitespace(_restore_abbreviations(candidate).strip(" -*•"))
        if not fragment:
            continue
        fragment = _strip_research_labels(fragment).strip(" ,;:-")
        fragment = normalize_spoken_whitespace(fragment)
        if not fragment or not re.search(r"[A-Za-zÄÖÜäöüß]", fragment):
            continue
        if ":" in fragment:
            prefix, suffix = fragment.split(":", 1)
            if len(prefix.split()) <= 4:
                fragment = suffix.strip()
        fragment = normalize_spoken_whitespace(fragment)
        if not fragment:
            continue
        bare_fragment = fragment.rstrip(".!?")
        trailing_tokens = re.findall(r"[A-Za-zÄÖÜäöüß]+", bare_fragment.lower())
        if trailing_tokens and trailing_tokens[-1] in INCOMPLETE_TRAILING_TOKENS:
            continue
        if ensure_terminal and fragment[-1] not in ".!?":
            fragment = fragment.rstrip(",;:") + "."
        if detect_spoken_copy_issues(fragment):
            continue
        signature = fragment.lower()
        if signature in seen:
            continue
        seen.add(signature)
        sentences.append(fragment)

    return normalize_spoken_whitespace(" ".join(sentences))


def _has_unbalanced_parenthetical(text: str) -> bool:
    round_balance = 0
    square_balance = 0
    stray_close = False
    for char in str(text or ""):
        if char == "(":
            round_balance += 1
        elif char == ")":
            if round_balance == 0:
                stray_close = True
            round_balance = max(0, round_balance - 1)
        elif char == "[":
            square_balance += 1
        elif char == "]":
            if square_balance == 0:
                stray_close = True
            square_balance = max(0, square_balance - 1)
    return stray_close or round_balance > 0 or square_balance > 0


def _detect_core_copy_issues(value: str, *, allow_structured_markers: bool) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    lowered = value.lower()
    for marker in SPOKEN_COPY_LABEL_MARKERS:
        if re.search(rf"(^|[\s(\\[\"']){re.escape(marker)}(?:\s*:|(?=[\s.?!,;:]|$))", lowered):
            issues.append({"kind": "label_fragment", "marker": marker})
            break

    if allow_structured_markers:
        if "**" in value or "__" in value or "`" in value:
            issues.append({"kind": "markdown_residue"})
    elif "**" in value or "__" in value or "`" in value or re.search(r"(?m)^\s*[-*•]\s+", value):
        issues.append({"kind": "markdown_residue"})

    if re.search(r"\[cite:\s*\d+(?:\s*,\s*\d+)*\]", value, flags=re.IGNORECASE):
        issues.append({"kind": "citation_residue"})

    if re.search(r"[\u200d\uFE0E\uFE0F]", value):
        issues.append({"kind": "artifact_tail"})

    if re.search(r"\b\w+:\s+in(?:nen)?\b", value):
        issues.append({"kind": "broken_inclusive_form"})

    if _has_unbalanced_parenthetical(value):
        issues.append({"kind": "dangling_parenthetical"})

    for pattern in _DEFINITION_RESIDUE_PATTERNS:
        if pattern.search(value):
            issues.append({"kind": "definition_residue"})
            break

    return issues


def _detect_incomplete_clause_issue(value: str) -> Dict[str, Any] | None:
    stripped = str(value or "").rstrip()
    if not stripped:
        return None
    if any(pattern.search(stripped) for pattern in _ALLOWED_PARTICLE_ENDINGS):
        return None
    compact = normalize_spoken_whitespace(stripped)
    compact_without_punct = compact.rstrip(".!?")
    short_clause_tokens = _tokenize_language_words(compact_without_punct)
    if (
        len(short_clause_tokens) <= 4
        and short_clause_tokens
        and short_clause_tokens[-1] in {"ist", "sind", "war", "waren", "bleibt", "bleiben"}
    ):
        return {"kind": "incomplete_clause", "tail_token": short_clause_tokens[-1]}
    if stripped.endswith((",", ";", ":")):
        return {"kind": "incomplete_clause", "tail": stripped[-24:]}
    trailing_tokens = _tokenize_language_words(compact_without_punct)
    if trailing_tokens and trailing_tokens[-1] in INCOMPLETE_TRAILING_TOKENS:
        return {"kind": "incomplete_clause", "tail_token": trailing_tokens[-1]}
    return None


def sanitize_metadata_text(text: Any, *, max_sentences: int = 2) -> str:
    cleaned = sanitize_spoken_fragment(text, ensure_terminal=True)
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    return normalize_spoken_whitespace(" ".join(sentences[:max_sentences]))


def sanitize_fact_fragments(values: List[Any]) -> List[str]:
    fragments: List[str] = []
    seen = set()
    for value in values:
        cleaned = sanitize_spoken_fragment(value, ensure_terminal=True)
        if not cleaned:
            continue
        signature = cleaned.lower()
        if signature in seen:
            continue
        seen.add(signature)
        fragments.append(cleaned)
    return fragments


def _clean_fact_pool(raw_values: List[Any]) -> List[str]:
    """Clean and validate individual fact sentences before they enter the script pool.

    Each fact is split into sentences, sanitized independently, and rejected
    if it triggers spoken-copy issues or is too short to be meaningful.
    """
    clean: List[str] = []
    seen: set = set()
    for value in raw_values:
        text = str(value or "").strip()
        if not text:
            continue
        if detect_spoken_copy_issues(text):
            continue
        sanitized = sanitize_spoken_fragment(text, ensure_terminal=True)
        if not sanitized:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", sanitized):
            sentence = sentence.strip()
            if not sentence:
                continue
            word_count = len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", sentence))
            if word_count < 4:
                continue
            if detect_spoken_copy_issues(sentence):
                continue
            sig = sentence.lower()
            if sig in seen:
                continue
            seen.add(sig)
            clean.append(sentence)
    return clean


def detect_metadata_bleed(
    script: str,
    *,
    source_summary: str = "",
    cluster_summary: str = "",
    min_consecutive_words: int = 6,
) -> Optional[Dict[str, Any]]:
    """Detect if a script contains long verbatim runs from metadata fields.

    Returns a dict with kind='metadata_bleed' if any metadata field shares
    min_consecutive_words or more consecutive words with the script.
    Returns None if clean.
    """
    script_text = str(script or "").strip().lower()
    if not script_text:
        return None

    script_words = re.findall(r"[a-zäöüß0-9-]+", script_text)
    if len(script_words) < min_consecutive_words:
        return None

    for field_name, field_value in [("source_summary", source_summary), ("cluster_summary", cluster_summary)]:
        value = str(field_value or "").strip().lower()
        if not value:
            continue
        meta_words = re.findall(r"[a-zäöüß0-9-]+", value)
        if len(meta_words) < min_consecutive_words:
            continue
        for i in range(len(meta_words) - min_consecutive_words + 1):
            window = " ".join(meta_words[i : i + min_consecutive_words])
            if window in " ".join(script_words):
                return {
                    "kind": "metadata_bleed",
                    "field": field_name,
                    "matched_words": min_consecutive_words,
                    "window": window,
                }
    return None


def compute_bigram_jaccard(a: str, b: str) -> float:
    def bigrams(text: str) -> set[str]:
        tokens = text.lower().split()
        return {" ".join(tokens[i:i + 2]) for i in range(len(tokens) - 1)} if len(tokens) > 1 else set()

    bigrams_a = bigrams(a)
    bigrams_b = bigrams(b)
    if not bigrams_a or not bigrams_b:
        return 0.0
    return len(bigrams_a & bigrams_b) / len(bigrams_a | bigrams_b)


def normalize_similarity_text(text: Any) -> str:
    cleaned = sanitize_spoken_fragment(text, ensure_terminal=True).lower()
    cleaned = re.sub(r"[^\w\säöüß-]", " ", cleaned)
    return normalize_spoken_whitespace(cleaned)


def _script_window_signature(text: str, *, size: int, from_end: bool = False) -> str:
    tokens = re.findall(r"[a-z0-9äöüß-]+", text.lower())
    if len(tokens) < size:
        return ""
    window = tokens[-size:] if from_end else tokens[:size]
    return " ".join(window)


def classify_script_overlap(candidate: str, existing: str) -> Optional[str]:
    candidate_norm = normalize_similarity_text(candidate)
    existing_norm = normalize_similarity_text(existing)
    if not candidate_norm or not existing_norm:
        return None
    if candidate_norm == existing_norm:
        return "duplicate_exact"

    bigram_overlap = compute_bigram_jaccard(candidate_norm, existing_norm)
    prefix_match = _script_window_signature(candidate_norm, size=5) == _script_window_signature(existing_norm, size=5)
    suffix_match = _script_window_signature(candidate_norm, size=7, from_end=True) == _script_window_signature(existing_norm, size=7, from_end=True)

    if suffix_match and bigram_overlap >= 0.45:
        return "duplicate_suffix_pattern"
    if (prefix_match and bigram_overlap >= 0.55) or bigram_overlap >= 0.78:
        return "duplicate_semantic_signature"
    return None


def _lane_similarity_signature(candidate: Dict[str, Any]) -> str:
    fragments: List[str] = []
    for value in (
        candidate.get("title"),
        candidate.get("angle"),
        candidate.get("source_summary"),
        " ".join(str(item).strip() for item in list(candidate.get("facts") or [])[:2] if str(item).strip()),
    ):
        cleaned = normalize_similarity_text(value)
        if cleaned:
            fragments.append(" ".join(cleaned.split()[:14]))
    return " | ".join(fragments)


def select_distinct_lane_candidates(
    candidates: List[Dict[str, Any]],
    *,
    max_candidates: Optional[int] = None,
    threshold: float = 0.58,
) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    signatures: List[str] = []

    for candidate in list(candidates or []):
        signature = _lane_similarity_signature(candidate)
        if signature:
            is_duplicate = any(
                signature == existing or compute_bigram_jaccard(signature, existing) >= threshold
                for existing in signatures
            )
            if is_duplicate:
                continue
            signatures.append(signature)
        selected.append(candidate)
        if max_candidates is not None and len(selected) >= max_candidates:
            break

    if not selected and candidates:
        return [candidates[0]]
    return selected


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


def detect_spoken_copy_issues(text: str) -> List[Dict[str, Any]]:
    value = str(text or "").strip()
    if not value:
        return []
    issues = _detect_core_copy_issues(value, allow_structured_markers=False)
    incomplete_issue = _detect_incomplete_clause_issue(value)
    if incomplete_issue:
        issues.append(incomplete_issue)
    return issues


def detect_metadata_copy_issues(text: str) -> List[Dict[str, Any]]:
    value = str(text or "").strip()
    if not value:
        return []

    issues = _detect_core_copy_issues(value, allow_structured_markers=True)
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    for line in lines:
        plain_line = re.sub(r"^\s*(?:[-*•]|\d+\.)\s*", "", line).strip()
        if not plain_line or plain_line.startswith("#"):
            continue
        incomplete_issue = _detect_incomplete_clause_issue(plain_line)
        if incomplete_issue:
            issues.append(incomplete_issue)
            break
    return issues


def validate_spoken_copy_cleanliness(item: ResearchAgentItem, profile: Any | None = None) -> None:
    issues = detect_spoken_copy_issues(item.script)
    if issues:
        raise ValidationError(
            message="PROMPT_1 script contains research-note leakage or malformed spoken copy",
            details={
                "target_length_tier": getattr(profile, "target_length_tier", None),
                "issues": issues,
                "script": item.script[:240],
            },
        )


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
