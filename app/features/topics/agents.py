"""
FLOW-FORGE Topic Discovery Agents
LLM agents for topic research and extraction.
Per Canon § 6: LLM Agents
"""

from __future__ import annotations

import json
import math
import random
import re
import secrets
from typing import Any, Dict, List, Optional

import httpx
import yaml

from pydantic import ValidationError as PydanticValidationError

from app.adapters.llm_client import get_llm_client
from app.features.posts.prompt_builder import split_dialogue_sentences
from app.features.topics.schemas import (
    DiscoverTopicsRequest,
    TopicListResponse,
    TopicResponse,
    ResearchAgentItem,
    ResearchAgentBatch,
    TopicData,
    DialogScripts,
    SeedData,
    build_prompt1_json_schema,
)
from app.features.topics.prompts import build_prompt1, build_prompt2, get_topic_pool_candidates
from app.core.logging import get_logger
from app.core.errors import ValidationError
from app.core.video_profiles import (
    DurationProfile,
    build_seed_duration_metadata,
    get_duration_profile,
)

logger = get_logger(__name__)

DEFAULT_DURATION_PROFILE = get_duration_profile(None)
MIN_SCRIPT_WORDS = DEFAULT_DURATION_PROFILE.prompt1_min_words
MAX_SCRIPT_WORDS = DEFAULT_DURATION_PROFILE.prompt1_max_words
MIN_SCRIPT_SECONDS = DEFAULT_DURATION_PROFILE.prompt1_min_seconds
MAX_SCRIPT_SECONDS = DEFAULT_DURATION_PROFILE.prompt1_max_seconds
MAX_SCRIPT_CHARS_NO_SPACES = DEFAULT_DURATION_PROFILE.prompt1_max_chars_no_spaces
CHARS_PER_SECOND_ESTIMATE = 17.0


def _build_prompt1_system_prompt(profile: DurationProfile) -> str:
    multiline_guidance = ""
    if profile.target_length_tier > 8:
        multiline_guidance = (
            f'\n- For the {profile.target_length_tier}-second tier, the script may span {profile.prompt1_sentence_guidance}.'
            "\n- Keep the pacing natural, structured, and easy to speak. Avoid rushed wording and filler."
        )

    return f"""You are the Flow Forge PROMPT_1 execution agent.
You must strictly follow the user's message instructions.

CRITICAL SCRIPT REQUIREMENTS:
- script must be EXACTLY {profile.prompt1_min_words}-{profile.prompt1_max_words} words, {profile.prompt1_sentence_guidance} (≈{profile.prompt1_min_seconds}-{profile.prompt1_max_seconds} Sekunden Sprechzeit)
- COUNT YOUR WORDS BEFORE SUBMITTING. Scripts outside that word range will be REJECTED.
- Keep the script under {profile.prompt1_max_chars_no_spaces} non-space characters for natural Veo speech delivery.
- Prefer short, speakable wording. Avoid stacking multiple long institutional or compound nouns in one sentence.
- If you must mention a long institution name, simplify the rest of the sentence aggressively.
- Start with a VARIED, scroll-stopping opening. Rotate between multiple hook families:
  * Questions: "Kennst du...?", "Weißt du...?", "Hast du...?", "Brauchst du...?", "Suchst du...?"
  * Direct statements: "Check mal...", "Schau dir an...", "Hier kommt...", "Das musst du wissen..."
  * Empathetic hooks: "Stell dir vor...", "Ich zeig dir...", "Lass mich dir zeigen..."
  * Contrast/myth hooks: "Die größte Lüge über ... ist, dass ...", "Fast alle denken, ...", "Alle reden über ..., aber ..."
  * Consequence/friction hooks: "Wenn du ... ignorierst, ...", "Der unangenehme Grund, warum ...", "Dieser kleine Fehler macht ..."
  * Aha/action hooks: "Bevor du ...", "Alles verändert sich, wenn du ...", "Was dir bei ... niemand klar sagt: ..."
- Use du-Form (informal you), be direct, friendly, empowering
- NO passive declarations like "Ab 2025 gibt's..." and NO cheap clickbait like "Du wirst nicht glauben ..."
- If script is shorter than {profile.prompt1_min_words} words or estimated_duration_s unter {profile.prompt1_min_seconds}, ergänze konkrete, quellenbasierte Details.
- estimated_duration_s must be a conservative natural-speech estimate between {profile.prompt1_min_seconds} and {profile.prompt1_max_seconds}.{multiline_guidance}

CRITICAL SOURCE URL REQUIREMENTS:
- ALL source URLs MUST be currently accessible and valid (not 404, not archived, not removed)
- ONLY use URLs from authoritative, recent sources: government sites (.de domains), official organizations, established news outlets
- VERIFY URLs are active and current before including them
- DO NOT use outdated links, blog posts that may have been deleted, or URLs from unreliable sources
- If web search returns dead links, find alternative authoritative sources

Always respond with a valid JSON array whose length exactly matches the requested number of topics.
Each element must include all required keys: topic, framework, sources, script, source_summary, estimated_duration_s, tone, disclaimer.
Responses must be valid JSON only (no Markdown, no backticks, no commentary)."""


PROMPT1_SYSTEM_PROMPT = _build_prompt1_system_prompt(DEFAULT_DURATION_PROFILE)


PROMPT1_NORMALIZER_SYSTEM_PROMPT = """You are the Flow Forge PROMPT_1 normalization agent.
You receive a raw assistant reply that failed validation because it was not valid JSON.
Rewrite it into a valid JSON array with exactly the requested number of items.
Never invent additional information beyond what is present in the raw reply.
Preserve German wording and keep all content fully in German.
Return JSON only (no Markdown, no comments)."""


def _should_attempt_json_normalization(error: ValidationError) -> bool:
    if "not JSON" in (error.message or ""):
        return True
    details = error.details
    if isinstance(details, list):
        for item in details:
            if isinstance(item, dict) and item.get("type") == "list_type":
                return True
    return False


def _normalize_prompt1_response_to_json(
    llm,
    raw_response: str,
    desired_topics: int,
    profile: DurationProfile,
) -> str:
    prompt = (
        "Konvertiere die folgende Antwort in ein valides JSON-Array mit genau "
        f"{desired_topics} Objekten. Jedes Objekt muss alle geforderten Felder beinhalten."
        "Nutze nur Informationen aus der Rohantwort. Keine Kommentare oder Markdown."
        "\n<<<ROHANTWORT>>>\n"
        f"{raw_response.strip()}"
        "\n<<<ENDE>>>"
    )

    logger.info("research_agent_normalizing_response", desired_topics=desired_topics)

    max_tokens_attempts = [
        max(4000, desired_topics * 900),
        max(6000, desired_topics * 1200),
    ]
    last_error: Optional[Exception] = None
    for max_tokens in max_tokens_attempts:
        try:
            parsed = llm.generate_gemini_json(
                prompt=prompt,
                system_prompt=PROMPT1_NORMALIZER_SYSTEM_PROMPT,
                json_schema=build_prompt1_json_schema(profile),
                max_tokens=max_tokens,
            )
            return json.dumps(parsed, ensure_ascii=False)
        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "research_agent_normalizing_retry",
                desired_topics=desired_topics,
                max_tokens=max_tokens,
                error=exc.message,
                details=exc.details,
            )

    for max_tokens in max_tokens_attempts:
        try:
            fallback_text = llm.generate_gemini_text(
                prompt=prompt,
                system_prompt=PROMPT1_NORMALIZER_SYSTEM_PROMPT,
                max_tokens=max_tokens,
            )
            fallback_batch = parse_prompt1_response(fallback_text, profile=profile)
            return json.dumps([item.model_dump(mode="json") for item in fallback_batch.items], ensure_ascii=False)
        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "research_agent_normalizing_text_retry",
                desired_topics=desired_topics,
                max_tokens=max_tokens,
                error=exc.message,
                details=exc.details,
            )

    if last_error is not None:
        raise last_error
    raise ValidationError(
        message="PROMPT_1 normalization failed without a detailed error",
        details={"desired_topics": desired_topics},
    )


def _parse_prompt1_with_normalization(
    llm,
    raw_response: str,
    desired_topics: int,
    profile: DurationProfile,
) -> tuple[ResearchAgentBatch, str]:
    try:
        batch = parse_prompt1_response(raw_response, profile=profile)
        return batch, raw_response
    except ValidationError as exc:
        if not _should_attempt_json_normalization(exc):
            raise

        normalized = _normalize_prompt1_response_to_json(
            llm,
            raw_response,
            desired_topics,
            profile=profile,
        )
        batch = parse_prompt1_response(normalized, profile=profile)
        return batch, normalized


def _validate_url_accessible(url: str, timeout: float = 5.0) -> bool:
    """Check if URL is accessible via HEAD request, fallback to GET if HEAD fails."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            # Try HEAD first (faster)
            try:
                response = client.head(url)
                if response.status_code < 400:
                    return True
                # Some servers don't support HEAD, try GET
                if response.status_code == 405:
                    response = client.get(url)
                    return response.status_code < 400
                return False
            except httpx.HTTPStatusError:
                # Try GET as fallback
                response = client.get(url)
                return response.status_code < 400
    except Exception as e:
        logger.debug("url_validation_failed", url=url, error=str(e))
        return False


def extract_soft_cta(script: str) -> str:
    script = script.strip()
    if not script:
        raise ValidationError(message="Script is empty", details={})

    # Prefer the trailing sentence (captures full question for lifestyle scripts)
    sentences = re.findall(r"[^.!?]*[.!?]", script)
    for sentence in reversed(sentences):
        candidate = sentence.strip()
        if candidate:
            return candidate

    # Fallback: use up to the last four words
    words = script.split()
    slice_length = min(4, len(words))
    return " ".join(words[-slice_length:])


def strip_cta_from_script(script: str, cta: str) -> str:
    script = script.strip()
    if not cta:
        return script
    if script.endswith(cta):
        trimmed = script[: -len(cta)].rstrip()
        return trimmed.rstrip("-–—,:;")
    return script


def build_social_description(script: str, source_summary: Optional[str]) -> str:
    """
    Compose a social caption-style description for social media.
    Uses ONLY the source_summary (additional facts), NOT the script.
    The script is the video voiceover; description provides context for social posts.
    """
    if source_summary:
        stripped_summary = source_summary.strip()
        if stripped_summary:
            # Collapse extra whitespace to keep output tidy
            return re.sub(r"\s+", " ", stripped_summary).strip()
    
    # Fallback if no source_summary (shouldn't happen with validation)
    return script.strip()


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
    # For chunks of 2-4 items, we expect at least 2 distinct topics
    min_expected = min(2, len(items))
    if len(unique_topics) < min_expected:
        raise ValidationError(
            message=f"PROMPT_1 output must contain at least {min_expected} distinct topics",
            details={"unique_topics": unique_topics, "expected_min": min_expected}
        )
    for idx in range(1, len(topics)):
        if topics[idx] == topics[idx - 1]:
            raise ValidationError(
                message="PROMPT_1 topics must not repeat consecutively",
                details={"index": idx, "topic": topics[idx]}
            )
    counts = {topic: topics.count(topic) for topic in unique_topics}
    if max(counts.values()) - min(counts.values()) > 1:
        raise ValidationError(
            message="PROMPT_1 topic distribution must be balanced",
            details={"counts": counts}
        )


def validate_unique_ctas(items: List[ResearchAgentItem]) -> None:
    seen: Dict[str, int] = {}
    for idx, item in enumerate(items):
        cta = extract_soft_cta(item.script)
        if cta in seen:
            raise ValidationError(
                message="CTA reuse detected",
                details={"cta": cta, "first_index": seen[cta], "duplicate_index": idx}
            )
        seen[cta] = idx


def _script_non_space_char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text.strip()))


def _validate_prompt1_script(text: str, profile: DurationProfile) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        raise ValidationError(
            message="PROMPT_1 script empty",
            details={},
        )

    sentence_chunks = split_dialogue_sentences(cleaned)
    if not sentence_chunks:
        raise ValidationError(
            message="PROMPT_1 script contains no complete sentences",
            details={"script": cleaned[:200]},
        )

    normalized_sentences = " ".join(sentence_chunks).strip()
    if normalized_sentences != cleaned:
        raise ValidationError(
            message="PROMPT_1 script ends with an incomplete fragment",
            details={
                "script": cleaned[:200],
                "normalized_sentences": normalized_sentences[:200],
            },
        )

    char_count = _script_non_space_char_count(cleaned)
    estimated_seconds = estimate_script_duration_seconds(cleaned)
    if char_count > profile.prompt1_max_chars_no_spaces or estimated_seconds > profile.prompt1_max_seconds:
        raise ValidationError(
            message="PROMPT_1 script exceeds tier without cutting a sentence",
            details={
                "char_count_no_spaces": char_count,
                "estimated_duration_s": estimated_seconds,
                "max_seconds": profile.prompt1_max_seconds,
                "max_char_count_no_spaces": profile.prompt1_max_chars_no_spaces,
            },
        )

    return cleaned


def estimate_script_duration_seconds(text: str) -> int:
    words = text.strip().split()
    if not words:
        return 0

    word_estimate = math.ceil(len(words) / 2.6)
    char_estimate = math.ceil(_script_non_space_char_count(text) / CHARS_PER_SECOND_ESTIMATE)
    return max(word_estimate, char_estimate)


def validate_duration(item: ResearchAgentItem, profile: Optional[DurationProfile] = None) -> None:
    active_profile = profile or DEFAULT_DURATION_PROFILE
    calculated = estimate_script_duration_seconds(item.script)
    char_count = _script_non_space_char_count(item.script)
    if char_count > active_profile.prompt1_max_chars_no_spaces:
        raise ValidationError(
            message="Script too dense for natural Veo speech delivery",
            details={
                "word_count": item.word_count(),
                "char_count_no_spaces": char_count,
                "max_char_count_no_spaces": active_profile.prompt1_max_chars_no_spaces,
                "calculated": calculated,
            }
        )
    if calculated > active_profile.prompt1_max_seconds:
        raise ValidationError(
            message=f"Script exceeds {active_profile.prompt1_max_seconds} seconds",
            details={
                "word_count": item.word_count(),
                "char_count_no_spaces": char_count,
                "calculated": calculated,
            }
        )
    if calculated < active_profile.prompt1_min_seconds:
        raise ValidationError(
            message=f"Script under {active_profile.prompt1_min_seconds} seconds",
            details={
                "word_count": item.word_count(),
                "char_count_no_spaces": char_count,
                "calculated": calculated,
            }
        )
    # Auto-correct estimated_duration_s if LLM calculated it wrong
    if calculated != item.estimated_duration_s:
        item.estimated_duration_s = calculated


def validate_summary(item: ResearchAgentItem) -> None:
    overlap = compute_bigram_jaccard(item.script, item.source_summary)
    if overlap > 0.25:
        raise ValidationError(
            message="Source summary overlaps too much with script (must provide additional facts, not repeat script)",
            details={"jaccard": overlap, "threshold": 0.25}
        )


def validate_sources_accessible(item: ResearchAgentItem) -> None:
    """Validate that all source URLs are accessible. Logs warnings but does not fail."""
    inaccessible_sources = []
    for source in item.sources:
        url = str(source.url)
        if not _validate_url_accessible(url, timeout=8.0):
            inaccessible_sources.append({
                "title": source.title,
                "url": url
            })
    
    if inaccessible_sources:
        logger.warning(
            "research_source_urls_not_accessible",
            topic=item.topic,
            inaccessible_count=len(inaccessible_sources),
            inaccessible_sources=inaccessible_sources,
            guidance="URLs may be outdated or temporarily unavailable"
        )
        # Do not raise - allow processing to continue with warning logged


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

        # Short fields like topic/tone/disclaimer should not contain English markers at all.
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
    """Normalize framework value to match schema literals."""
    if not isinstance(value, str) or not value.strip():
        return "PAL"
    value_lower = value.lower().strip()
    if "pal" in value_lower or "problem" in value_lower or "agit" in value_lower or "lösung" in value_lower:
        return "PAL"
    elif "testimonial" in value_lower or "zeugnis" in value_lower:
        return "Testimonial"
    elif "transformation" in value_lower or "wandel" in value_lower:
        return "Transformation"
    return value  # Return as-is if no match, will fail validation

def _sanitize_json_text(text: str) -> str:
    replacements = {
        # English curly quotes
        "\u201c": '"',  # Left double quotation mark
        "\u201d": '"',  # Right double quotation mark
        "\u2018": "'",  # Left single quotation mark
        "\u2019": "'",  # Right single quotation mark
        # German curly quotes
        "\u201e": '"',  # Double low-9 quotation mark „
        "\u201f": '"',  # Double high-reversed-9 quotation mark ‟
        "\u201a": "'",  # Single low-9 quotation mark ‚
        "\u201b": "'",  # Single high-reversed-9 quotation mark ‛
        # Other problematic quotes
        "\u00ab": '"',  # Left-pointing double angle quotation mark «
        "\u00bb": '"',  # Right-pointing double angle quotation mark »
        "\u2039": "'",  # Single left-pointing angle quotation mark ‹
        "\u203a": "'",  # Single right-pointing angle quotation mark ›
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Remove trailing commas before closing braces/brackets
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def _parse_json_or_yaml(text: str) -> Any:
    # Sanitize first - this handles curly quotes and other problematic characters
    sanitized = _sanitize_json_text(text)
    
    # Try direct JSON parse first
    try:
        return json.loads(sanitized)
    except json.JSONDecodeError:
        pass
    
    # Try to extract JSON from text (handle cases where LLM adds preamble)
    # Look for array or object start
    json_start = -1
    for i, char in enumerate(sanitized):
        if char in ['{', '[']:
            json_start = i
            break
    
    if json_start >= 0:
        # Try parsing from the first JSON character (use sanitized text)
        try:
            return json.loads(sanitized[json_start:])
        except json.JSONDecodeError:
            pass
    
    # Fall back to YAML parsing (use sanitized text)
    try:
        parsed_yaml = yaml.safe_load(sanitized)
    except yaml.YAMLError as yaml_error:
        raise ValidationError(
            message="PROMPT_1 response not JSON",
            details={"error": str(yaml_error), "snippet": sanitized[:200]}
        ) from yaml_error
    
    if parsed_yaml is None:
        raise ValidationError(
            message="PROMPT_1 response empty",
            details={"snippet": sanitized[:200]}
        )
    return parsed_yaml


def parse_prompt1_response(raw: str, profile: Optional[DurationProfile] = None) -> ResearchAgentBatch:
    active_profile = profile or DEFAULT_DURATION_PROFILE
    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]  # Remove ```json
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]  # Remove ```
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]  # Remove trailing ```
    cleaned = cleaned.strip()
    
    parsed = _parse_json_or_yaml(cleaned)

    payload = parsed if isinstance(parsed, dict) else {"items": parsed}
    
    # Normalize items before validation
    if "items" in payload and isinstance(payload["items"], list):
        for item in payload["items"]:
            if isinstance(item, dict):
                # Normalize framework
                if "framework" in item:
                    item["framework"] = normalize_framework(item["framework"])
                else:
                    item["framework"] = "PAL"
                
                # Add missing required fields with defaults
                if "estimated_duration_s" not in item and "script" in item:
                    item["estimated_duration_s"] = estimate_script_duration_seconds(item["script"])

                if "tone" not in item:
                    item["tone"] = "direkt, freundlich, empowernd, du-Form"
                
                if "disclaimer" not in item:
                    item["disclaimer"] = "Keine Rechts- oder medizinische Beratung."

                # Ensure scripts respect the conservative Veo speech-density ceiling without chopping tails.
                validated_script = _validate_prompt1_script(item.get("script", ""), active_profile)
                script_words = validated_script.split()
                if len(script_words) < active_profile.prompt1_min_words:
                    raise ValidationError(
                        message=(
                            "PROMPT_1 script too short "
                            f"(requires ≥{active_profile.prompt1_min_words} words for the selected tier)"
                        ),
                        details={
                            "word_count": len(script_words),
                            "char_count_no_spaces": _script_non_space_char_count(validated_script),
                        }
                    )
                item["script"] = validated_script
                item["estimated_duration_s"] = max(
                    active_profile.prompt1_min_seconds,
                    estimate_script_duration_seconds(validated_script)
                )

    payload = parsed if isinstance(parsed, dict) else {"items": parsed}
    try:
        batch = ResearchAgentBatch(**payload)
    except PydanticValidationError as exc:
        raise ValidationError(
            message="PROMPT_1 response invalid",
            details=json.loads(exc.json())
        ) from exc

    for item in batch.items:
        validate_duration(item, active_profile)
        validate_summary(item)
        validate_german_content(item)
        validate_sources_accessible(item)
    validate_round_robin(batch.items)
    validate_unique_ctas(batch.items)
    return batch


def parse_prompt2_response(raw: str, max_per_category: int = 5) -> DialogScripts:
    max_per_category = max(1, min(5, max_per_category))
    hook_prefixes = (
        "kennst du",
        "weißt du",
        "hast du",
        "brauchst du",
        "suchst du",
        "check mal",
        "schau dir",
        "hier kommt",
        "das musst",
        "stell dir",
        "ich zeig",
        "lass mich",
        "die größte",
        "wenn du",
        "fast alle",
        "der unangenehme",
        "die meisten",
        "was dir",
        "bevor du",
        "alles verändert",
        "dieser kleine",
        "viele verlassen",
        "alle reden",
        "die harte",
        "schon mal erlebt",
        "ich dachte früher",
        "ich dachte lange",
        "neulich ist mir",
        "wusstest du",
        "manchmal frage ich",
        "ehrlich gesagt",
        "von außen",
        "was viele",
        "kaum jemand",
        "dieser eine",
        "niemand sagt",
        "alle denken",
        "was meinen alltag",
        "seit ich",
        "viele meinen",
        "der moment",
        "erst wenn",
        "das frustigste",
        "eine sache",
    )

    def normalize_heading(line: str) -> str:
        cleaned = line.strip()
        cleaned = re.sub(r"^#+\s*", "", cleaned)
        cleaned = re.sub(r"^\*+\s*", "", cleaned)
        cleaned = cleaned.strip().strip("*").strip()
        return cleaned.lower()

    def looks_like_script_start(line: str) -> bool:
        """Heuristic to detect the beginning of a new script sentence."""
        normalized = line.strip().lower()
        return normalized.startswith(hook_prefixes)

    lines = raw.splitlines()
    headers = {
        "problem-agitieren-lösung ads": "problem_agitate_solution",
        "testimonial ads": "testimonial",
        "testimonial-stil ads": "testimonial",  # Legacy support
        "transformations-geschichten ads": "transformation",
        "beschreibung": "description",
    }
    # Initialize all buckets (may remain empty for new single-category format)
    buckets: Dict[str, List[str]] = {
        "problem_agitate_solution": [],
        "testimonial": [],
        "transformation": [],
    }
    description_text: Optional[str] = None
    current: Optional[str] = None
    current_script_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        
        # Check if this is a heading
        normalized = normalize_heading(stripped)
        key = headers.get(normalized)
        if key:
            # Save any accumulated script before switching sections
            if current and current_script_lines:
                if current == "description":
                    description_text = " ".join(current_script_lines)
                else:
                    script = " ".join(current_script_lines)
                    buckets[current].append(script)
                current_script_lines = []
            current = key
            continue
        
        # Skip empty lines - they separate scripts
        if not stripped:
            if current and current_script_lines:
                if current == "description":
                    description_text = " ".join(current_script_lines)
                else:
                    script = " ".join(current_script_lines)
                    buckets[current].append(script)
                current_script_lines = []
            continue
        
        # Check if this is a new script starting without a blank line separator
        # (only for script sections, not description)
        if (
            current
            and current != "description"
            and current_script_lines
            and looks_like_script_start(stripped)
        ):
            # Save the previous script
            script = " ".join(current_script_lines)
            buckets[current].append(script)
            current_script_lines = [stripped]
            continue
        
        # Accumulate script lines or description lines
        if current is None:
            raise ValidationError(
                message="PROMPT_2 output missing headings",
                details={"line": stripped}
            )
        current_script_lines.append(stripped)
    
    # Don't forget the last script or description
    if current and current_script_lines:
        if current == "description":
            description_text = " ".join(current_script_lines)
        else:
            script = " ".join(current_script_lines)
            buckets[current].append(script)

    # Enforce maximum scripts per category before validation
    for category, scripts in buckets.items():
        if len(scripts) > max_per_category:
            logger.warning(
                "dialog_scripts_truncated",
                category=category,
                original_count=len(scripts),
                truncated_to=max_per_category,
            )
            buckets[category] = scripts[:max_per_category]
    
    # For new single-category format, fill empty categories with first script as fallback
    if buckets["problem_agitate_solution"] and not buckets["testimonial"] and not buckets["transformation"]:
        fallback_script = buckets["problem_agitate_solution"][0]
        buckets["testimonial"] = [fallback_script]
        buckets["transformation"] = [fallback_script]
        logger.info(
            "single_category_format_detected",
            message="Using Problem-Agitieren-Lösung script as fallback for other categories"
        )

    # Add description to payload
    payload = {**buckets, "description": description_text}

    try:
        return DialogScripts(**payload)
    except PydanticValidationError as exc:
        raise ValidationError(
            message="PROMPT_2 response invalid",
            details=json.loads(exc.json())
        ) from exc


def convert_research_item_to_topic(item: ResearchAgentItem) -> TopicData:
    cta = extract_soft_cta(item.script)
    rotation = strip_cta_from_script(item.script, cta)
    
    # Guard: if stripping CTA leaves empty rotation, use full script as rotation
    # and extract a shorter CTA from the last few words
    if not rotation or not rotation.strip():
        rotation = item.script.strip()
        words = rotation.split()
        if len(words) > 4:
            cta = " ".join(words[-4:])
        else:
            cta = rotation
    
    return TopicData(
        title=item.topic,
        rotation=rotation,
        cta=cta,
        spoken_duration=item.estimated_duration_s,
    )


def build_seed_payload(
    item: ResearchAgentItem,
    strict_seed: SeedData,
    dialog_scripts: DialogScripts,
    profile: Optional[DurationProfile] = None,
) -> Dict[str, Any]:
    active_profile = profile or DEFAULT_DURATION_PROFILE
    # Normalize sources to a single entry
    primary_source = item.sources[0] if item.sources else None

    # Map framework to script category and select single script
    framework_map = {
        "PAL": "problem",
        "Testimonial": "testimonial",
        "Transformation": "transformation",
    }
    default_script = dialog_scripts.problem_agitate_solution[0] if dialog_scripts.problem_agitate_solution else item.script
    script_map = {
        "problem": dialog_scripts.problem_agitate_solution[0] if dialog_scripts.problem_agitate_solution else default_script,
        "testimonial": dialog_scripts.testimonial[0] if dialog_scripts.testimonial else default_script,
        "transformation": dialog_scripts.transformation[0] if dialog_scripts.transformation else default_script,
    }

    script_category = framework_map.get(item.framework, "problem")
    selected_script = script_map[script_category]

    # Strict seed facts: take first fact as primary summary
    seed_payload = strict_seed.model_dump()
    facts = seed_payload.get("facts", [])
    primary_fact = facts[0] if facts else None

    source_summary = item.source_summary.strip() if item.source_summary else None
    description_text = build_social_description(item.script, source_summary)

    payload: Dict[str, Any] = {
        "script": item.script,
        "framework": item.framework,
        "tone": item.tone,
        "estimated_duration_s": item.estimated_duration_s,
        "cta": extract_soft_cta(item.script),
        "dialog_script": selected_script,
        "script_category": script_category,
        "strict_fact": primary_fact,
        "strict_seed": seed_payload,
        "description": description_text,
        "disclaimer": item.disclaimer,
        **build_seed_duration_metadata(active_profile),
    }

    if primary_source:
        source_url = str(primary_source.url)
        
        # Validate URL format
        if not source_url.startswith(("http://", "https://")):
            logger.warning(
                "research_source_invalid_url_format",
                title=primary_source.title,
                url=source_url,
                topic=item.topic,
            )
            # Still store it but log warning
            payload["source"] = {
                "title": primary_source.title,
                "url": source_url,
                "summary": source_summary,
                "accessible": False,
            }
        else:
            # Validate URL is accessible
            is_accessible = _validate_url_accessible(source_url)
            if not is_accessible:
                logger.warning(
                    "research_source_url_not_accessible",
                    title=primary_source.title,
                    url=source_url,
                    topic=item.topic,
                )
            
            payload["source"] = {
                "title": primary_source.title,
                "url": source_url,
                "summary": source_summary,
                "accessible": is_accessible,
            }
    else:
        logger.warning(
            "research_source_missing",
            topic=item.topic,
        )

    return payload


def generate_topics_research_agent(
    post_type: str,
    count: int = 10,
    seed: Optional[int] = None,
    progress_callback: Optional[Any] = None,
    profile: Optional[DurationProfile] = None,
) -> List[ResearchAgentItem]:
    """Execute one PROMPT_1 batch request and return validated items."""
    active_profile = profile or DEFAULT_DURATION_PROFILE
    llm = get_llm_client()

    topic_candidates = get_topic_pool_candidates()
    assigned_seed = seed if seed is not None else secrets.randbits(64)
    rng = random.Random(assigned_seed)
    shuffled_topics: List[str] = []
    if topic_candidates:
        shuffled_topics = topic_candidates[:]
        rng.shuffle(shuffled_topics)

    logger.info(
        "research_agent_topic_pool_seed",
        post_type=post_type,
        seed=assigned_seed,
        candidate_count=len(shuffled_topics),
    )

    assigned_topics: List[str] = []
    if shuffled_topics:
        while len(assigned_topics) < count:
            remaining = count - len(assigned_topics)
            if remaining >= len(shuffled_topics):
                assigned_topics.extend(shuffled_topics)
                rng.shuffle(shuffled_topics)
            else:
                assigned_topics.extend(shuffled_topics[:remaining])

    items = _generate_prompt1_batch(
        llm=llm,
        post_type=post_type,
        desired_topics=count,
        assigned_topics=assigned_topics or None,
        progress_callback=progress_callback,
        profile=active_profile,
    )

    if len(items) < count:
        raise ValidationError(
            message="Unable to produce sufficient topics",
            details={"requested": count, "produced": len(items)}
        )

    logger.info(
        "research_agent_batch_complete",
        post_type=post_type,
        requested=count,
        produced=len(items)
    )

    return items[:count]


def _generate_prompt1_batch(
    llm,
    post_type: str,
    desired_topics: int,
    assigned_topics: Optional[List[str]] = None,
    progress_callback: Optional[Any] = None,
    profile: Optional[DurationProfile] = None,
) -> List[ResearchAgentItem]:
    active_profile = profile or DEFAULT_DURATION_PROFILE
    prompt = build_prompt1(
        post_type=post_type,
        desired_topics=desired_topics,
        assigned_topics=assigned_topics,
        profile=active_profile,
    )
    prompt_with_feedback = prompt

    max_attempts = 4
    timeout_seconds = max(getattr(llm, "gemini_topic_timeout_seconds", 600), min(1800, desired_topics * 90))
    for attempt in range(max_attempts):
        logger.debug(
            "research_agent_batch_attempt",
            post_type=post_type,
            desired_topics=desired_topics,
            attempt=attempt + 1,
            prompt_characters=len(prompt_with_feedback),
            timeout_seconds=timeout_seconds,
        )
        
        raw_response = llm.generate_gemini_deep_research(
            prompt=prompt_with_feedback,
            system_prompt=_build_prompt1_system_prompt(active_profile),
            timeout_seconds=timeout_seconds,
            metadata={
                "feature": "topics.prompts_1",
                "attempt": str(attempt + 1),
                "desired_outputs": str(desired_topics),
                "assigned_topics": json.dumps(assigned_topics or []),
                "target_length_tier": str(active_profile.target_length_tier),
            },
            progress_callback=progress_callback,
        )
        
        try:
            batch, normalized_response = _parse_prompt1_with_normalization(
                llm=llm,
                raw_response=raw_response,
                desired_topics=desired_topics,
                profile=active_profile,
            )
            items = batch.items
            
            if len(items) != desired_topics:
                raise ValidationError(
                    message="PROMPT_1 batch produced unexpected count",
                    details={"expected": desired_topics, "actual": len(items)}
                )
            
            # Validate each item
            for item in items:
                validate_duration(item, active_profile)
                validate_summary(item)
                validate_german_content(item)
                validate_sources_accessible(item)
            
            # Validate batch constraints
            validate_round_robin(items)
            validate_unique_ctas(items)
            
            logger.info(
                "research_agent_batch_success",
                post_type=post_type,
                desired_topics=desired_topics,
                attempt=attempt + 1,
                response_length=len(normalized_response),
            )
            return items
            
        except ValidationError as exc:
            logger.warning(
                "research_agent_batch_retry",
                post_type=post_type,
                attempt=attempt + 1,
                response_preview=raw_response[:500],
                error=exc.message,
                details=exc.details
            )
            
            # Build detailed feedback
            feedback_parts = [f"FEEDBACK: {exc.message}"]
            
            if "too short" in exc.message.lower() or f"under {active_profile.prompt1_min_seconds} seconds" in exc.message.lower():
                feedback_parts.append(
                    f"\nDetails: {json.dumps(exc.details, default=str)}"
                    f"\n\nIMPORTANT: Scripts MUST be EXACTLY {active_profile.prompt1_min_words}-{active_profile.prompt1_max_words} words."
                    "\nCOUNT YOUR WORDS CAREFULLY before submitting."
                    f"\nIf a script is too short, ADD concrete source-based details until it reaches {active_profile.prompt1_min_words}-{active_profile.prompt1_max_words} words."
                )
            elif "too dense for natural veo speech delivery" in exc.message.lower() or f"exceeds {active_profile.prompt1_max_seconds} seconds" in exc.message.lower():
                feedback_parts.append(
                    f"\nDetails: {json.dumps(exc.details, default=str)}"
                    f"\n\nIMPORTANT: Keep the script under {active_profile.prompt1_max_chars_no_spaces} non-space characters."
                    "\nUse shorter, more speakable wording and avoid stacking long compound or institutional nouns."
                    "\nIf you mention a long institution name, simplify the rest of the sentence."
                    "\nBad density example: 'Weißt du eigentlich, dass das Integrationsamt deine kompletten technischen Arbeitshilfen im Job vollständig bezahlt?'"
                    "\nBetter density example: 'Weißt du, dass das Integrationsamt deine Hilfen im Job oft komplett bezahlt?'"
                )
            elif "fully in german" in exc.message.lower():
                details_str = json.dumps(exc.details, default=str, ensure_ascii=False, indent=2)
                feedback_parts.append(f"\nDetails: {details_str}")
                feedback_parts.append(
                    "\nWICHTIG: Die komplette Ausgabe MUSS auf Deutsch sein."
                    "\nTopic, Script, Source Summary, Tone und Disclaimer dürfen keine englischen oder gemischtsprachigen Formulierungen enthalten."
                    "\nWenn die Quelle englische Begriffe nutzt, übersetze das Thema und die Beschreibung idiomatisch ins Deutsche."
                )
            elif "not accessible" in exc.message.lower():
                details_str = json.dumps(exc.details, default=str, indent=2)
                feedback_parts.append(f"\nDetails: {details_str}")
                feedback_parts.append(
                    "\nIMPORTANT: The URLs you provided are not accessible (404, timeout, or connection error)."
                    "\nPlease use ONLY currently accessible URLs from authoritative sources:"
                    "\n- German government sites (.de domains)"
                    "\n- Official organizations (e.g., GKV-Spitzenverband, Deutsche Bahn)"
                    "\n- Established news outlets"
                    "\nVerify URLs are active and current before including them."
                )
            else:
                feedback_parts.append(f"\nDetails: {json.dumps(exc.details, default=str)[:500]}")
            
            prompt_with_feedback = prompt + "\n\n" + "".join(feedback_parts)

    raise ValidationError(
        message="Unable to produce valid topics for batch",
        details={
            "desired": desired_topics,
        },
    )


def generate_dialog_scripts(
    topic: str,
    scripts_required: int = 1,
    previously_used_hooks: Optional[List[str]] = None,
    profile: Optional[DurationProfile] = None,
) -> DialogScripts:
    """Execute PROMPT_2 and return structured dialog scripts."""
    active_profile = profile or DEFAULT_DURATION_PROFILE
    scripts_required = max(1, min(5, scripts_required))
    llm = get_llm_client()
    prompt = build_prompt2(topic=topic, scripts_per_category=scripts_required, profile=active_profile)
    
    # Add constraint to avoid repeating hooks if we have previous ones
    if previously_used_hooks:
        hooks_list = ", ".join([f'"{hook}"' for hook in previously_used_hooks])
        prompt += f"\n\nWICHTIG: Die folgenden Hooks wurden bereits verwendet: {hooks_list}\nNutze einen ANDEREN Hook-Start für dieses Skript."

    for attempt in range(3):
        response = llm.generate_gemini_text(
            prompt=prompt,
            system_prompt=None,
            max_tokens=900,
        )
        try:
            scripts = parse_prompt2_response(response, max_per_category=scripts_required)
            
            # Check if single-category fallback was applied
            # (testimonial and transformation have same script as problem_agitate_solution[0])
            single_category_fallback_applied = (
                scripts.problem_agitate_solution
                and scripts.testimonial
                and scripts.transformation
                and scripts.testimonial[0] == scripts.problem_agitate_solution[0]
                and scripts.transformation[0] == scripts.problem_agitate_solution[0]
            )
            
            # If fallback was applied, accept 1 script per category (the duplicated one)
            # Otherwise, validate that we have enough scripts
            if single_category_fallback_applied:
                # Fallback provides 1 script per category - accept it
                min_required = 1
            else:
                min_required = scripts_required
            
            if (
                len(scripts.problem_agitate_solution) < min_required
                or len(scripts.testimonial) < min_required
                or len(scripts.transformation) < min_required
            ):
                raise ValidationError(
                    message="PROMPT_2 returned fewer scripts than required",
                    details={"required": scripts_required, "min_required": min_required}
                )
            trimmed = DialogScripts(
                problem_agitate_solution=scripts.problem_agitate_solution[:scripts_required],
                testimonial=scripts.testimonial[:scripts_required],
                transformation=scripts.transformation[:scripts_required],
                description=scripts.description,
            )
            logger.info(
                "dialog_scripts_success",
                topic=topic
            )
            return trimmed
        except ValidationError as exc:
            logger.warning(
                "dialog_scripts_retry",
                topic=topic,
                attempt=attempt + 1,
                error=exc.message,
                details=exc.details,
                response_preview=response[:500]
            )
            prompt = f"{prompt}\n\nFEEDBACK: {exc.message}."
    
    logger.error(
        "dialog_scripts_failed_all_attempts",
        topic=topic,
        last_response=response[:1000] if 'response' in locals() else None
    )
    raise ValidationError(message="Unable to produce dialog scripts", details={})


def generate_lifestyle_topics(
    count: int = 1,
    seed: Optional[int] = None,
    profile: Optional[DurationProfile] = None,
) -> List[Dict[str, Any]]:
    """
    Generate lifestyle topics using PROMPT_2 directly (no web research).
    Returns list of topic dicts with dialog scripts and metadata.
    """
    active_profile = profile or DEFAULT_DURATION_PROFILE
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

    results = []
    used_hooks = []  # Track hooks to ensure variety
    
    for i in range(count):
        topic_template = shuffled_templates[i % len(shuffled_templates)]

        # Generate dialog scripts for this lifestyle topic, avoiding previous hooks
        dialog_scripts = generate_dialog_scripts(
            topic=topic_template,
            scripts_required=1,
            previously_used_hooks=used_hooks if used_hooks else None,
            profile=active_profile,
        )
        
        # Use first script from problem_agitate_solution as the main content
        main_script = dialog_scripts.problem_agitate_solution[0]
        cta = extract_soft_cta(main_script)
        rotation = strip_cta_from_script(main_script, cta)

        # Guard: single-sentence lifestyle scripts can be mistaken for a full CTA.
        # Keep the full script as rotation instead of collapsing it to empty text.
        if not rotation or not rotation.strip():
            rotation = main_script.strip()
            words = rotation.split()
            if len(words) > 4:
                cta = " ".join(words[-4:])
            else:
                cta = rotation
        
        # Extract the hook (first 3-5 words) to track for next iteration
        hook = " ".join(main_script.split()[:4])  # First 4 words typically capture the hook
        used_hooks.append(hook)
        
        # Calculate duration
        duration = estimate_script_duration_seconds(main_script)
        
        topic_data = {
            "title": topic_template,
            "rotation": rotation,
            "cta": cta,
            "spoken_duration": duration,
            "dialog_scripts": dialog_scripts,
            "framework": "PAL",  # Default framework for lifestyle
        }
        
        results.append(topic_data)
        
        logger.info(
            "lifestyle_topic_generated",
            title=topic_template,
            scripts_count=1,
            seed=seed
        )
    
    return results


def build_lifestyle_seed_payload(
    topic_data: Dict[str, Any],
    dialog_scripts: DialogScripts,
    profile: Optional[DurationProfile] = None,
) -> Dict[str, Any]:
    """
    Build seed payload for lifestyle posts (no sources required).
    """
    active_profile = profile or DEFAULT_DURATION_PROFILE
    # Select script based on framework
    framework_map = {
        "PAL": "problem",
        "Testimonial": "testimonial",
        "Transformation": "transformation",
    }
    
    default_script = dialog_scripts.problem_agitate_solution[0] if dialog_scripts.problem_agitate_solution else topic_data["rotation"]
    script_map = {
        "problem": dialog_scripts.problem_agitate_solution[0] if dialog_scripts.problem_agitate_solution else default_script,
        "testimonial": dialog_scripts.testimonial[0] if dialog_scripts.testimonial else default_script,
        "transformation": dialog_scripts.transformation[0] if dialog_scripts.transformation else default_script,
    }
    
    script_category = framework_map.get(topic_data.get("framework", "PAL"), "problem")
    selected_script = script_map[script_category]
    
    # Create minimal seed with community-focused facts
    seed_payload = {
        "facts": [f"Community-basiertes Thema: {topic_data['title']}"],
        "source_context": "Lifestyle content - community experiences"
    }
    
    # Use description from dialog_scripts if available, otherwise fallback to template
    description_text = dialog_scripts.description if dialog_scripts.description else f"Lifestyle-Beitrag zu: {topic_data['title']}"
    
    payload: Dict[str, Any] = {
        "script": selected_script,
        "framework": topic_data.get("framework", "PAL"),
        "tone": "direkt, freundlich, empowernd, du-Form",
        "estimated_duration_s": topic_data["spoken_duration"],
        "cta": topic_data["cta"],
        "dialog_script": selected_script,
        "script_category": script_category,
        "strict_fact": seed_payload["facts"][0],
        "strict_seed": seed_payload,
        "description": description_text,
        "disclaimer": "Keine Rechts- oder medizinische Beratung.",
        **build_seed_duration_metadata(active_profile),
    }
    
    # No sources for lifestyle posts
    logger.info(
        "lifestyle_seed_payload_built",
        title=topic_data["title"],
        has_sources=False
    )
    
    return payload


STRICT_EXTRACTOR_SYSTEM_PROMPT = """You are a strict fact extractor for a UGC video system.
Your ONLY job is to extract factual information from the provided topic.

Rules:
1. Extract ONLY facts that are explicitly stated or clearly implied
2. DO NOT add creative interpretations or embellishments
3. DO NOT hallucinate information
4. Keep facts concise and clear
5. If no clear facts are present, extract the core message/claim

Output ONLY valid JSON with a "facts" array of strings."""


def extract_seed_strict_extractor(topic: TopicData) -> SeedData:
    """
    Strict Extractor Agent
    Extract factual seed data from topic (no hallucination).
    Per Canon § 6.2: Strict Extractor Agent
    """
    llm = get_llm_client()
    
    prompt = f"""Extract factual seed information from this topic:

Title: {topic.title}
Rotation: {topic.rotation}
CTA: {topic.cta}

Extract ONLY the factual claims, core messages, or key points. Do not add any creative interpretation.

Return JSON format:
{{
  "facts": ["fact 1", "fact 2", ...],
  "source_context": "brief context if needed"
}}

Extract facts now:"""
    
    try:
        response = llm.generate_gemini_json(
            prompt=prompt,
            system_prompt=STRICT_EXTRACTOR_SYSTEM_PROMPT,
            json_schema={
                "type": "object",
                "properties": {
                    "facts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "source_context": {"type": "string"},
                },
                "required": ["facts"],
                "additionalProperties": False,
            },
        )
        
        seed = SeedData(**response)
        
        logger.info(
            "strict_extractor_success",
            topic_title=topic.title[:50],
            facts_count=len(seed.facts)
        )
        
        return seed
    
    except Exception as e:
        logger.error(
            "strict_extractor_failed",
            topic_title=topic.title[:50],
            error=str(e)
        )
        raise
