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
from app.features.topics.schemas import (
    DiscoverTopicsRequest,
    TopicListResponse,
    TopicResponse,
    ResearchAgentItem,
    ResearchAgentBatch,
    TopicData,
    DialogScripts,
    SeedData,
)
from app.features.topics.prompts import build_prompt1, build_prompt2
from app.core.logging import get_logger
from app.core.errors import ValidationError

logger = get_logger(__name__)


PROMPT1_SYSTEM_PROMPT = """You are the Flow Forge PROMPT_1 execution agent.
You must strictly follow the user's message instructions.

CRITICAL SCRIPT REQUIREMENTS:
- script must be 16-20 words, one sentence (≈7-8 Sekunden Sprechzeit)
- Start with an engaging question using "Kennst du...?", "Weißt du...?", "Hast du...?" OR make a bold direct statement
- Use du-Form (informal you), be direct, friendly, empowering
- NO passive declarations like "Ab 2025 gibt's..."
- If script is shorter than 16 words or estimated_duration_s unter 7, ergänze konkrete, quellenbasierte Details.
- estimated_duration_s must equal CEIL(word_count(script)/2.6) and be 7 or 8.
- Example good scripts:
  * "Kennst du das Hilfsmittelverzeichnis? Es zeigt dir genau, welche aktiven und elektrischen Rollstühle die Kasse aktuell übernehmen muss."
  * "Weißt du, dass deine Begleitperson im ÖPNV oft gratis mitfährt, wenn du im Ausweis die B-Marke aktiviert hast?"
  * "Hast du schon die B-Marke geprüft? Sie spart dir auf Reisen Geld, Sitzplatzreservierungen und erleichtert richtig spontane Ausflüge."

CRITICAL SOURCE URL REQUIREMENTS:
- ALL source URLs MUST be currently accessible and valid (not 404, not archived, not removed)
- ONLY use URLs from authoritative, recent sources: government sites (.de domains), official organizations, established news outlets
- VERIFY URLs are active and current before including them
- DO NOT use outdated links, blog posts that may have been deleted, or URLs from unreliable sources
- If web search returns dead links, find alternative authoritative sources

Always respond with a valid JSON array whose length exactly matches the requested number of topics.
Each element must include all required keys: topic, framework, sources, script, source_summary, estimated_duration_s, tone, disclaimer.
Responses must be valid JSON only (no Markdown, no backticks, no commentary)."""


PROMPT1_NORMALIZER_SYSTEM_PROMPT = """You are the Flow Forge PROMPT_1 normalization agent.
You receive a raw assistant reply that failed validation because it was not valid JSON.
Rewrite it into a valid JSON array with exactly the requested number of items.
Never invent additional information beyond what is present in the raw reply.
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


def _normalize_prompt1_response_to_json(llm, raw_response: str, desired_topics: int) -> str:
    prompt = (
        "Konvertiere die folgende Antwort in ein valides JSON-Array mit genau "
        f"{desired_topics} Objekten. Jedes Objekt muss alle geforderten Felder beinhalten."
        "Nutze nur Informationen aus der Rohantwort. Keine Kommentare oder Markdown."
        "\n<<<ROHANTWORT>>>\n"
        f"{raw_response.strip()}"
        "\n<<<ENDE>>>"
    )

    logger.info("research_agent_normalizing_response", desired_topics=desired_topics)

    return llm.generate_openai(
        prompt=prompt,
        system_prompt=PROMPT1_NORMALIZER_SYSTEM_PROMPT,
        text_format={"type": "json_object"},
        max_tokens=3500,
    )


def _parse_prompt1_with_normalization(llm, raw_response: str, desired_topics: int) -> tuple[ResearchAgentBatch, str]:
    try:
        batch = parse_prompt1_response(raw_response)
        return batch, raw_response
    except ValidationError as exc:
        if not _should_attempt_json_normalization(exc):
            raise

        normalized = _normalize_prompt1_response_to_json(llm, raw_response, desired_topics)
        batch = parse_prompt1_response(normalized)
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


def validate_duration(item: ResearchAgentItem) -> None:
    calculated = math.ceil(item.word_count() / 2.6)
    if calculated > 8:
        raise ValidationError(
            message="Script exceeds 8 seconds",
            details={"word_count": item.word_count(), "calculated": calculated}
        )
    if calculated < 7:
        raise ValidationError(
            message="Script under 7 seconds",
            details={"word_count": item.word_count(), "calculated": calculated}
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


def normalize_framework(value: str) -> str:
    """Normalize framework value to match schema literals."""
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


def parse_prompt1_response(raw: str) -> ResearchAgentBatch:
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
                
                # Add missing required fields with defaults
                if "estimated_duration_s" not in item and "script" in item:
                    word_count = len(item["script"].split())
                    item["estimated_duration_s"] = math.ceil(word_count / 2.6)

                if "tone" not in item:
                    item["tone"] = "direkt, freundlich, empowernd, du-Form"
                
                if "disclaimer" not in item:
                    item["disclaimer"] = "Keine Rechts- oder medizinische Beratung."

                # Ensure scripts respect 8-second / 20-word ceiling
                script_words = item.get("script", "").split()
                if script_words:
                    while script_words and math.ceil(len(script_words) / 2.6) > 8:
                        script_words.pop()
                    trimmed_script = " ".join(script_words).strip()
                    if not trimmed_script:
                        raise ValidationError(
                            message="PROMPT_1 script empty after trimming",
                            details={"original": item.get("script", "")}
                        )
                    item["script"] = trimmed_script
                    item["estimated_duration_s"] = max(
                        7,
                        math.ceil(len(script_words) / 2.6)
                    )
                    # Validate minimum aligns with 7-second floor: CEIL(16/2.6) = 7
                    if len(script_words) < 16:
                        raise ValidationError(
                            message="PROMPT_1 script too short (requires ≥16 words for 7-second minimum)",
                            details={"word_count": len(script_words)}
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
        validate_duration(item)
        validate_summary(item)
        validate_sources_accessible(item)
    validate_round_robin(batch.items)
    validate_unique_ctas(batch.items)
    return batch


def parse_prompt2_response(raw: str, max_per_category: int = 5) -> DialogScripts:
    max_per_category = max(1, min(5, max_per_category))
    def normalize_heading(line: str) -> str:
        cleaned = line.strip()
        cleaned = re.sub(r"^#+\s*", "", cleaned)
        cleaned = re.sub(r"^\*+\s*", "", cleaned)
        cleaned = cleaned.strip().strip("*").strip()
        return cleaned.lower()

    def looks_like_script_start(line: str) -> bool:
        """Heuristic to detect the beginning of a new script sentence."""
        patterns = [
            r"^Kennst du",
            r"^Schon erlebt",
            r"^Ich dachte",
            r"^Früher",
            r"^Ich hab",
            r"^Mein",
        ]
        return any(re.match(pattern, line, re.IGNORECASE) for pattern in patterns)

    lines = raw.splitlines()
    headers = {
        "problem-agitieren-lösung ads": "problem_agitate_solution",
        "testimonial ads": "testimonial",
        "testimonial-stil ads": "testimonial",  # Legacy support
        "transformations-geschichten ads": "transformation",
        "beschreibung": "description",
    }
    buckets: Dict[str, List[str]] = {value: [] for value in headers.values() if value != "description"}
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
            and len(current_script_lines) >= 2
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
) -> Dict[str, Any]:
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
    count: int = 10
) -> List[ResearchAgentItem]:
    """Execute PROMPT_1 and return validated items."""
    llm = get_llm_client()
    if count <= 2:
        chunk_size = count
    else:
        chunk_size = 2
    total_chunks = math.ceil(count / chunk_size)
    collected: List[ResearchAgentItem] = []

    for chunk_index in range(1, total_chunks + 1):
        remaining = count - len(collected)
        desired_topics = min(chunk_size, remaining)
        items = _generate_prompt1_chunk(
            llm=llm,
            post_type=post_type,
            desired_topics=desired_topics,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        )
        collected.extend(items)

        logger.info(
            "research_agent_chunk_complete",
            post_type=post_type,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            chunk_items=len(items),
            collected=len(collected)
        )

        if len(collected) >= count:
            break

    if len(collected) < count:
        raise ValidationError(
            message="Unable to produce sufficient topics",
            details={"requested": count, "produced": len(collected)}
        )

    return collected[:count]


def _generate_prompt1_chunk(
    llm,
    post_type: str,
    desired_topics: int,
    chunk_index: int,
    total_chunks: int,
) -> List[ResearchAgentItem]:
    prompt = build_prompt1(
        post_type=post_type,
        desired_topics=desired_topics,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
    )
    prompt_with_feedback = prompt

    max_attempts = 4
    for attempt in range(max_attempts):
        logger.debug(
            "research_agent_chunk_attempt",
            post_type=post_type,
            desired_topics=desired_topics,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            attempt=attempt + 1,
            prompt_characters=len(prompt_with_feedback),
        )
        response = llm.generate_openai(
            prompt=prompt_with_feedback,
            system_prompt=PROMPT1_SYSTEM_PROMPT,
            tools=[{"type": "web_search", "external_web_access": True}],
            tool_choice="auto",
            text_format={"type": "json_object"},
            include=[
                "web_search_call.results",
                "web_search_call.action.sources",
            ],
            metadata={
                "feature": "topics.prompts_1",
                "attempt": str(attempt + 1),
                "chunk_index": str(chunk_index),
                "total_chunks": str(total_chunks),
                "desired_outputs": str(desired_topics),
            },
            max_tokens=3500,
        )
        try:
            batch = parse_prompt1_response(response)
            items = batch.items
            if len(items) != desired_topics:
                raise ValidationError(
                    message="PROMPT_1 chunk produced unexpected count",
                    details={"expected": desired_topics, "actual": len(items)}
                )
            logger.info(
                "research_agent_chunk_success",
                post_type=post_type,
                desired_topics=desired_topics,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                attempt=attempt + 1,
                response_length=len(response),
            )
            return items
        except ValidationError as exc:
            logger.warning(
                "research_agent_chunk_retry",
                post_type=post_type,
                attempt=attempt + 1,
                chunk_index=chunk_index,
                response_preview=response[:500],
                error=exc.message,
                details=exc.details
            )
            
            # Build detailed feedback for URL validation failures
            feedback_parts = [f"FEEDBACK: {exc.message}"]
            
            if "not accessible" in exc.message.lower():
                # Provide specific guidance for URL failures
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
                feedback_parts.append(
                    "\nRemember: Output must be a valid JSON array only, with double-quoted keys and no prose."
                )
            
            prompt_with_feedback = prompt + "\n\n" + "".join(feedback_parts)

    raise ValidationError(
        message="Unable to produce valid topics for chunk",
        details={
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "desired": desired_topics,
        },
    )


def generate_dialog_scripts(topic: str, scripts_required: int = 5) -> DialogScripts:
    """Execute PROMPT_2 and return structured dialog scripts."""
    scripts_required = max(1, min(5, scripts_required))
    llm = get_llm_client()
    prompt = build_prompt2(topic=topic, scripts_per_category=scripts_required)

    for attempt in range(3):
        response = llm.generate_chat(
            prompt=prompt,
            system_prompt=None,
            max_tokens=900,
        )
        try:
            scripts = parse_prompt2_response(response, max_per_category=scripts_required)
            if (
                len(scripts.problem_agitate_solution) < scripts_required
                or len(scripts.testimonial) < scripts_required
                or len(scripts.transformation) < scripts_required
            ):
                raise ValidationError(
                    message="PROMPT_2 returned fewer scripts than required",
                    details={"required": scripts_required}
                )
            trimmed = DialogScripts(
                problem_agitate_solution=scripts.problem_agitate_solution[:scripts_required],
                testimonial=scripts.testimonial[:scripts_required],
                transformation=scripts.transformation[:scripts_required],
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


def generate_lifestyle_topics(count: int = 1, seed: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Generate lifestyle topics using PROMPT_2 directly (no web research).
    Returns list of topic dicts with dialog scripts and metadata.
    """
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
    for i in range(count):
        topic_template = shuffled_templates[i % len(shuffled_templates)]

        # Generate dialog scripts for this lifestyle topic
        dialog_scripts = generate_dialog_scripts(
            topic=topic_template,
            scripts_required=1
        )
        
        # Use first script from problem_agitate_solution as the main content
        main_script = dialog_scripts.problem_agitate_solution[0]
        cta = extract_soft_cta(main_script)
        rotation = strip_cta_from_script(main_script, cta)
        
        # Calculate duration
        word_count = len(main_script.split())
        duration = max(1, math.ceil(word_count / 2.6))
        
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


def build_lifestyle_seed_payload(topic_data: Dict[str, Any], dialog_scripts: DialogScripts) -> Dict[str, Any]:
    """
    Build seed payload for lifestyle posts (no sources required).
    """
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
        "description": f"Lifestyle-Beitrag zu: {topic_data['title']}",
        "disclaimer": "Keine Rechts- oder medizinische Beratung.",
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
        response = llm.generate_json(
            prompt=prompt,
            system_prompt=STRICT_EXTRACTOR_SYSTEM_PROMPT,
            provider="openai"
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

