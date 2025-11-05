"""
FLOW-FORGE Topic Discovery Agents
LLM agents for topic research and extraction.
Per Canon § 6: LLM Agents
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional

import yaml

from pydantic import ValidationError as PydanticValidationError

from app.adapters.llm_client import get_llm_client
from app.features.topics.schemas import (
    TopicData,
    SeedData,
    ResearchAgentBatch,
    ResearchAgentItem,
    DialogScripts,
)
from app.features.topics.prompts import build_prompt1, build_prompt2
from app.core.logging import get_logger
from app.core.errors import ValidationError

logger = get_logger(__name__)


def extract_soft_cta(script: str) -> str:
    words = script.strip().split()
    if not words:
        raise ValidationError(message="Script is empty", details={})
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
    # Auto-correct estimated_duration_s if LLM calculated it wrong
    if calculated != item.estimated_duration_s:
        item.estimated_duration_s = calculated


def validate_summary(item: ResearchAgentItem) -> None:
    overlap = compute_bigram_jaccard(item.script, item.source_summary)
    if overlap > 0.35:
        raise ValidationError(
            message="Source summary overlaps too much with script",
            details={"jaccard": overlap}
        )


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
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Remove trailing commas before closing braces/brackets
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def _parse_json_or_yaml(text: str) -> Any:
    text = _sanitize_json_text(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed_yaml = yaml.safe_load(text)
        except yaml.YAMLError as yaml_error:
            raise ValidationError(
                message="PROMPT_1 response not JSON",
                details={"error": str(yaml_error), "snippet": text[:200]}
            ) from yaml_error
        if parsed_yaml is None:
            raise ValidationError(
                message="PROMPT_1 response empty",
                details={"snippet": text[:200]}
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
                        1,
                        math.ceil(len(script_words) / 2.6)
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
    validate_round_robin(batch.items)
    validate_unique_ctas(batch.items)
    return batch


def parse_prompt2_response(raw: str) -> DialogScripts:
    def normalize_heading(line: str) -> str:
        cleaned = line.strip()
        cleaned = re.sub(r"^#+\s*", "", cleaned)
        cleaned = re.sub(r"^\*+\s*", "", cleaned)
        cleaned = cleaned.strip().strip("*").strip()
        return cleaned.lower()

    # Split by double newlines to get script blocks, then filter empty
    lines = raw.splitlines()
    headers = {
        "problem-agitieren-lösung ads": "problem_agitate_solution",
        "testimonial ads": "testimonial",
        "testimonial-stil ads": "testimonial",  # Legacy support
        "transformations-geschichten ads": "transformation",
    }
    buckets: Dict[str, List[str]] = {value: [] for value in headers.values()}
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
                script = " ".join(current_script_lines)
                buckets[current].append(script)
                current_script_lines = []
            current = key
            continue
        
        # Skip empty lines - they separate scripts
        if not stripped:
            if current and current_script_lines:
                script = " ".join(current_script_lines)
                buckets[current].append(script)
                current_script_lines = []
            continue
        
        # Accumulate script lines
        if current is None:
            raise ValidationError(
                message="PROMPT_2 output missing headings",
                details={"line": stripped}
            )
        current_script_lines.append(stripped)
    
    # Don't forget the last script
    if current and current_script_lines:
        script = " ".join(current_script_lines)
        buckets[current].append(script)

    try:
        return DialogScripts(**buckets)
    except PydanticValidationError as exc:
        raise ValidationError(
            message="PROMPT_2 response invalid",
            details=json.loads(exc.json())
        ) from exc


def convert_research_item_to_topic(item: ResearchAgentItem) -> TopicData:
    cta = extract_soft_cta(item.script)
    rotation = strip_cta_from_script(item.script, cta)
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
    return {
        "script": item.script,
        "framework": item.framework,
        "tone": item.tone,
        "estimated_duration_s": item.estimated_duration_s,
        "cta": extract_soft_cta(item.script),
        "sources": [
            {
                "title": source.title,
                "url": str(source.url)  # Convert HttpUrl to string
            }
            for source in item.sources
        ],
        "source_summary": item.source_summary,
        "dialog_scripts": {
            "problem": dialog_scripts.problem_agitate_solution,
            "testimonial": dialog_scripts.testimonial,
            "transformation": dialog_scripts.transformation,
        },
        "strict_seed": strict_seed.model_dump(),
        "disclaimer": item.disclaimer,
    }


def generate_topics_research_agent(
    brand: str,
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
            brand=brand,
            post_type=post_type,
            desired_topics=desired_topics,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
        )
        collected.extend(items)

        logger.info(
            "research_agent_chunk_complete",
            brand=brand,
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
    brand: str,
    post_type: str,
    desired_topics: int,
    chunk_index: int,
    total_chunks: int,
) -> List[ResearchAgentItem]:
    prompt = build_prompt1(
        brand=brand,
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
            brand=brand,
            post_type=post_type,
            desired_topics=desired_topics,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            attempt=attempt + 1,
            prompt_characters=len(prompt_with_feedback),
        )
        response = llm.generate_openai(
            prompt=prompt_with_feedback,
            system_prompt=None,
            tools=[{"type": "web_search", "external_web_access": True}],
            tool_choice="auto",
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
                brand=brand,
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
                brand=brand,
                post_type=post_type,
                attempt=attempt + 1,
                chunk_index=chunk_index,
                response_preview=response[:500],
                error=exc.message,
                details=exc.details
            )
            prompt_with_feedback = (
                f"{prompt}\n\nFEEDBACK: {exc.message}. Details: "
                f"{json.dumps(exc.details, default=str)[:500]}"
            )

    raise ValidationError(
        message="Unable to produce valid topics for chunk",
        details={
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "desired": desired_topics,
        },
    )


def generate_dialog_scripts(brand: str, topic: str) -> DialogScripts:
    """Execute PROMPT_2 and return structured dialog scripts."""
    llm = get_llm_client()
    prompt = build_prompt2(brand=brand, topic=topic)

    for attempt in range(3):
        response = llm.generate_chat(
            prompt=prompt,
            system_prompt=None,
            max_tokens=900,
        )
        try:
            scripts = parse_prompt2_response(response)
            logger.info(
                "dialog_scripts_success",
                brand=brand,
                topic=topic
            )
            return scripts
        except ValidationError as exc:
            logger.warning(
                "dialog_scripts_retry",
                brand=brand,
                topic=topic,
                attempt=attempt + 1,
                error=exc.message,
                details=exc.details,
                response_preview=response[:500]
            )
            prompt = f"{prompt}\n\nFEEDBACK: {exc.message}."
    
    logger.error(
        "dialog_scripts_failed_all_attempts",
        brand=brand,
        topic=topic,
        last_response=response[:1000] if 'response' in locals() else None
    )
    raise ValidationError(message="Unable to produce dialog scripts", details={})


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
