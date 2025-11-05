"""
FLOW-FORGE Topic Discovery Agents
LLM agents for topic research and extraction.
Per Canon § 6: LLM Agents
"""

from __future__ import annotations

import json
import math
from typing import List, Dict, Any, Optional

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
    if len(unique_topics) != 4:
        raise ValidationError(
            message="PROMPT_1 output must contain four distinct topics",
            details={"unique_topics": unique_topics}
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
    if calculated != item.estimated_duration_s:
        raise ValidationError(
            message="Estimated duration mismatch",
            details={
                "script": item.script,
                "calculated": calculated,
                "reported": item.estimated_duration_s,
            }
        )


def validate_summary(item: ResearchAgentItem) -> None:
    overlap = compute_bigram_jaccard(item.script, item.source_summary)
    if overlap > 0.35:
        raise ValidationError(
            message="Source summary overlaps too much with script",
            details={"jaccard": overlap}
        )


def parse_prompt1_response(raw: str) -> ResearchAgentBatch:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            message="PROMPT_1 response not JSON",
            details={"error": str(exc), "snippet": raw[:200]}
        ) from exc

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
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    headers = {
        "problem-agitieren-lösung ads": "problem_agitate_solution",
        "testimonial-stil ads": "testimonial",
        "transformations-geschichten ads": "transformation",
    }
    buckets: Dict[str, List[str]] = {value: [] for value in headers.values()}
    current: Optional[str] = None

    for line in lines:
        key = headers.get(line.lower())
        if key:
            current = key
            continue
        if current is None:
            raise ValidationError(
                message="PROMPT_2 output missing headings",
                details={"line": line}
            )
        buckets[current].append(line)

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
        "sources": [source.model_dump() for source in item.sources],
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
    prompt = build_prompt1(brand=brand, post_type=post_type, desired_topics=count)

    for attempt in range(3):
        response = llm.generate_openai(
            prompt=prompt,
            system_prompt=None,
            model="gpt-4",
            temperature=0.2,
            max_tokens=3200,
            tools=[{"type": "web_search"}],
            store=False
        )
        try:
            batch = parse_prompt1_response(response)
            logger.info(
                "research_agent_success",
                brand=brand,
                post_type=post_type,
                items=len(batch.items)
            )
            return batch.items
        except ValidationError as exc:
            logger.warning(
                "research_agent_retry",
                brand=brand,
                post_type=post_type,
                attempt=attempt + 1,
                error=exc.message,
                details=exc.details
            )
            prompt = f"{prompt}\n\nFEEDBACK: {exc.message}. Details: {json.dumps(exc.details, default=str)[:500]}"
    raise ValidationError(message="Unable to produce valid topics", details={})


def generate_dialog_scripts(brand: str, topic: str) -> DialogScripts:
    """Execute PROMPT_2 and return structured dialog scripts."""
    llm = get_llm_client()
    prompt = build_prompt2(brand=brand, topic=topic)

    for attempt in range(3):
        response = llm.generate_openai(
            prompt=prompt,
            system_prompt=None,
            model="gpt-4",
            temperature=0.5,
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
                details=exc.details
            )
            prompt = f"{prompt}\n\nFEEDBACK: {exc.message}."
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
            provider="openai",
            model="gpt-4"
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
