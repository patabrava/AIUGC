"""
Provider-facing runtime orchestration for topic research and script generation.
"""

from __future__ import annotations

import json
import random
import re
import secrets
from typing import Any, Callable, Dict, List, Optional

from app.adapters.llm_client import get_llm_client
from app.core.errors import ThirdPartyError, ValidationError
from app.core.logging import get_logger
from app.core.video_profiles import get_duration_profile
from app.features.topics.prompts import (
    build_prompt1,
    build_prompt2,
    build_topic_normalization_prompt,
    build_topic_research_dossier_prompt,
    get_topic_pool_candidates,
)
from app.features.topics.response_parsers import (
    _coerce_prompt2_payload,
    _synthesize_research_dossier_from_text,
    _validate_dialog_scripts_payload,
    parse_prompt1_response,
    parse_prompt2_response,
    parse_topic_research_response,
)
from app.features.topics.schemas import DialogScripts, ResearchAgentBatch, ResearchAgentItem, ResearchDossier, SeedData, TopicData
from app.features.topics.topic_validation import estimate_script_duration_seconds

logger = get_logger(__name__)

PROMPT1_STAGE3_SYSTEM_PROMPT = """You are the Flow Forge PROMPT_1 stage-3 script agent.
Follow the user prompt exactly.
Return only valid JSON.
Do not invent facts beyond the provided dossier context.
Keep all output fully in German."""

PROMPT1_RESEARCH_SYSTEM_PROMPT = """You are the Flow Forge topic research dossier agent.
Return a dense, factual German research dossier as raw prose with short lists.
Do not return JSON, arrays, or fenced code blocks.
Keep all content fully in German."""

PROMPT1_RESEARCH_NORMALIZER_SYSTEM_PROMPT = """You are the Flow Forge research dossier normalization agent.
You receive a completed deep-research reply that may be prose or Markdown.
Convert it into exactly one valid JSON object for the research dossier schema.
Do not wrap the result in Markdown or commentary.
Keep all content fully in German.
Derive lane_candidates from clearly distinct sub-angles already present in the raw research reply.
Do not invent facts that are not supported by the raw reply."""

PROMPT1_NORMALIZER_SYSTEM_PROMPT = """You are the Flow Forge PROMPT_1 normalization agent.
You receive a raw assistant reply that failed validation because it was not valid JSON.
Rewrite it into a valid JSON array with exactly the requested number of items.
Never invent additional information beyond what is present in the raw reply.
Preserve German wording and keep all content fully in German.
Return JSON only."""

STRICT_EXTRACTOR_SYSTEM_PROMPT = """You are a strict fact extractor for a UGC video system.
Your ONLY job is to extract factual information from the provided topic.

Rules:
1. Extract ONLY facts that are explicitly stated or clearly implied
2. DO NOT add creative interpretations or embellishments
3. DO NOT hallucinate information
4. Keep facts concise and clear
5. If no clear facts are present, extract the core message or claim

Output ONLY valid JSON with a "facts" array of strings."""


def _should_attempt_json_normalization(error: ValidationError) -> bool:
    if "not JSON" in (error.message or ""):
        return True
    if "invalid" in (error.message or "").lower():
        return True
    if "empty" in (error.message or "").lower():
        return True
    return False


def _normalize_prompt1_response_to_json(llm, raw_response: str, desired_topics: int) -> str:
    normalizer_prompt = (
        f"Die folgende Antwort soll GENAU {desired_topics} JSON-Objekte enthalten.\n"
        "Repariere nur die Struktur. Erfinde keine neuen Fakten oder Formulierungen.\n"
        "Halte alles vollstaendig auf Deutsch.\n\n"
        f"ROHANTWORT:\n{raw_response}"
    )
    normalized = llm.generate_gemini_text(
        prompt=normalizer_prompt,
        system_prompt=PROMPT1_NORMALIZER_SYSTEM_PROMPT,
        max_tokens=3200,
    )
    return normalized.strip()


def _normalize_topic_research_response_to_json(
    llm,
    raw_response: str,
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
) -> str:
    prompt = build_topic_normalization_prompt(
        raw_response=raw_response,
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
    )
    return llm.generate_gemini_text(
        prompt=prompt,
        system_prompt=PROMPT1_RESEARCH_NORMALIZER_SYSTEM_PROMPT,
        max_tokens=4200,
    ).strip()


def _parse_prompt1_with_normalization(
    llm,
    raw_response: str,
    desired_topics: int,
    *,
    profile: Optional[Any] = None,
) -> tuple[ResearchAgentBatch, str]:
    try:
        batch = parse_prompt1_response(raw_response, profile=profile)
        return batch, raw_response
    except ValidationError as exc:
        if not _should_attempt_json_normalization(exc):
            raise
        normalized = _normalize_prompt1_response_to_json(llm, raw_response, desired_topics)
        batch = parse_prompt1_response(normalized, profile=profile)
        return batch, normalized



def generate_topics_research_agent(
    *,
    post_type: str,
    count: int = 10,
    seed: Optional[int] = None,
    assigned_topics: Optional[List[str]] = None,
    profile: Optional[Any] = None,
    progress_callback: Optional[Any] = None,
    llm_factory: Callable = get_llm_client,
) -> List[ResearchAgentItem]:
    profile = profile or get_duration_profile(8)
    topic_candidates = assigned_topics or get_topic_pool_candidates()
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

    chosen_topics: List[str] = []
    if shuffled_topics:
        while len(chosen_topics) < count:
            remaining = count - len(chosen_topics)
            if remaining >= len(shuffled_topics):
                chosen_topics.extend(shuffled_topics)
                rng.shuffle(shuffled_topics)
            else:
                chosen_topics.extend(shuffled_topics[:remaining])

    items: List[ResearchAgentItem] = []
    for seed_topic in chosen_topics[:count]:
        dossier = generate_topic_research_dossier(
            seed_topic=seed_topic,
            post_type=post_type,
            target_length_tier=profile.target_length_tier,
            progress_callback=progress_callback,
            llm_factory=llm_factory,
        )
        lane_candidates = list(dossier.lane_candidates or [])
        if not lane_candidates:
            continue
        candidate = generate_topic_script_candidate(
            post_type=post_type,
            target_length_tier=profile.target_length_tier,
            dossier=dossier,
            lane_candidate=lane_candidates[0],
            llm_factory=llm_factory,
        )
        items.append(candidate)
        if len(items) >= count:
            break

    if len(items) < count:
        raise ValidationError(
            message="Unable to produce sufficient topics",
            details={"requested": count, "produced": len(items), "seed_topics": chosen_topics[:count]},
        )
    logger.info("research_agent_batch_complete", post_type=post_type, requested=count, produced=len(items))
    return items[:count]


def normalize_topic_research_dossier(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
    raw_response: str,
    llm_factory: Callable = get_llm_client,
) -> ResearchDossier:
    llm = llm_factory()
    try:
        return parse_topic_research_response(raw_response)
    except ValidationError as exc:
        logger.warning(
            "research_dossier_parse_retry",
            seed_topic=seed_topic,
            post_type=post_type,
            error=exc.message,
            details=exc.details,
        )
        normalized = _normalize_topic_research_response_to_json(
            llm,
            raw_response,
            seed_topic=seed_topic,
            post_type=post_type,
            target_length_tier=target_length_tier,
        )
        try:
            return parse_topic_research_response(normalized)
        except ValidationError:
            synthesized = _synthesize_research_dossier_from_text(
                raw_response,
                seed_topic=seed_topic,
                post_type=post_type,
                target_length_tier=target_length_tier,
            )
            return ResearchDossier(**synthesized)


def generate_topic_research_dossier(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
    progress_callback: Optional[Any] = None,
    llm_factory: Callable = get_llm_client,
) -> ResearchDossier:
    llm = llm_factory()
    prompt = build_topic_research_dossier_prompt(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
    )
    timeout_seconds = max(getattr(llm, "gemini_topic_timeout_seconds", 600), 300)
    raw_response = llm.generate_gemini_deep_research(
        prompt=prompt,
        system_prompt=PROMPT1_RESEARCH_SYSTEM_PROMPT,
        timeout_seconds=timeout_seconds,
        metadata={
            "feature": "topics.hub_research",
            "seed_topic": seed_topic,
            "post_type": post_type,
            "target_length_tier": str(target_length_tier),
        },
        progress_callback=progress_callback,
    )
    dossier = normalize_topic_research_dossier(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
        raw_response=raw_response,
        llm_factory=llm_factory,
    )
    try:
        from app.features.topics.queries import create_topic_research_dossier, create_topic_research_run, update_topic_research_run

        run_row = create_topic_research_run(
            trigger_source="hub_deep_research",
            requested_counts={"topics": 1, post_type: 1},
            target_length_tier=target_length_tier,
            topic_registry_id=None,
            seed_topic=seed_topic,
            post_type=post_type,
            raw_prompt=prompt,
            raw_response=raw_response,
            normalized_payload=dossier.model_dump(mode="json"),
        )
        dossier_row = create_topic_research_dossier(
            topic_research_run_id=run_row["id"],
            topic_registry_id=None,
            seed_topic=seed_topic,
            post_type=post_type,
            target_length_tier=target_length_tier,
            cluster_id=dossier.cluster_id,
            topic=dossier.topic,
            anchor_topic=dossier.anchor_topic,
            normalized_payload=dossier.model_dump(mode="json"),
        )
        update_topic_research_run(
            run_row["id"],
            status="completed",
            result_summary={
                "seed_topic": seed_topic,
                "post_type": post_type,
                "target_length_tier": target_length_tier,
                "dossier_id": dossier_row["id"],
            },
            error_message="",
            dossier_id=dossier_row["id"],
        )
    except Exception as exc:
        logger.warning(
            "research_dossier_persistence_failed",
            seed_topic=seed_topic,
            post_type=post_type,
            target_length_tier=target_length_tier,
            error=str(exc),
        )
    return dossier


def generate_topic_script_candidate(
    *,
    post_type: str,
    target_length_tier: int,
    dossier: ResearchDossier | Dict[str, Any],
    lane_candidate: Dict[str, Any],
    progress_callback: Optional[Any] = None,
    llm_factory: Callable = get_llm_client,
) -> ResearchAgentItem:
    llm = llm_factory()
    profile = get_duration_profile(target_length_tier)
    dossier_payload = dossier.model_dump(mode="json") if hasattr(dossier, "model_dump") else (dossier or {})
    lane_payload = lane_candidate.model_dump(mode="json") if hasattr(lane_candidate, "model_dump") else dict(lane_candidate or {})
    lane_title = str(lane_payload.get("title") or "").strip()
    lane_caption = str(lane_payload.get("source_summary") or lane_payload.get("caption") or "").strip()
    lane_sources = list((dossier_payload or {}).get("sources") or [])
    source_title = None
    source_url = None
    if lane_sources:
        first_source = lane_sources[0]
        if isinstance(first_source, dict):
            source_title = str(first_source.get("title") or "").strip() or None
            source_url = str(first_source.get("url") or "").strip() or None
    base_prompt = build_prompt1(
        post_type=post_type,
        desired_topics=1,
        profile=profile,
        assigned_topics=[lane_title],
        dossier=dossier_payload,
        lane_candidate=lane_payload,
    )
    prompt = base_prompt

    item_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "script": {"type": "string"},
            "caption": {"type": "string"},
        },
        "required": ["title", "script", "caption"],
    }

    lane_fact_texts = [
        str(fact).strip()
        for fact in list(lane_payload.get("facts") or []) + list((dossier_payload or {}).get("facts") or [])
        if str(fact).strip()
    ]

    def _word_count(text: str) -> int:
        return len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", text or ""))

    def _ensure_terminal_punctuation(text: str) -> str:
        stripped = (text or "").strip()
        if not stripped:
            return stripped
        return stripped if stripped[-1] in ".!?" else f"{stripped}."

    def _enforce_prompt1_word_envelope(item: ResearchAgentItem) -> ResearchAgentItem:
        min_words = int(getattr(profile, "prompt1_min_words", 12))
        max_words = int(getattr(profile, "prompt1_max_words", 15))
        script = _ensure_terminal_punctuation(item.script)
        words = script.split()
        word_count = _word_count(script)

        # Deterministic lane-grounded expansion for short outputs.
        if word_count < min_words:
            for fact in lane_fact_texts:
                fragment = re.sub(r"\s+", " ", fact).strip().rstrip(".!?")
                if not fragment:
                    continue
                candidate = _ensure_terminal_punctuation(f"{' '.join(words)} {fragment}".strip())
                words = candidate.split()
                word_count = _word_count(candidate)
                script = candidate
                if word_count >= min_words:
                    break

        # Hard cap to keep outputs in the requested tier envelope.
        if _word_count(script) > max_words:
            tokens = script.split()[:max_words]
            script = _ensure_terminal_punctuation(" ".join(tokens).strip())

        final_count = _word_count(script)
        if final_count < min_words or final_count > max_words:
            raise ValidationError(
                message="PROMPT_1 script does not match target word envelope",
                details={
                    "target_length_tier": profile.target_length_tier,
                    "word_count": final_count,
                    "expected_range": [min_words, max_words],
                },
            )
        item.script = script
        return item

    for attempt in range(3):
        try:
            response = llm.generate_gemini_json(
                prompt=prompt,
                system_prompt=PROMPT1_STAGE3_SYSTEM_PROMPT,
                json_schema={
                    "type": "array",
                    "items": item_schema,
                    "minItems": 1,
                    "maxItems": 1,
                },
                max_tokens=2200,
            )
            batch = parse_prompt1_response(json.dumps(response, ensure_ascii=False), profile=profile)
            if batch.items:
                item = batch.items[0]
                if not item.topic.strip():
                    item.topic = lane_title or item.topic
                if not item.caption.strip():
                    item.caption = lane_caption or item.source_summary or item.script
                if not item.source_summary.strip():
                    item.source_summary = lane_caption or item.caption
                if not item.sources and source_url:
                    item.sources = [{"title": source_title or lane_title or item.topic, "url": source_url}]
                if not item.sources and source_title:
                    item.sources = [{"title": source_title, "url": source_url or ""}]
                item.estimated_duration_s = max(
                    1,
                    min(profile.target_length_tier, estimate_script_duration_seconds(item.script)),
                )
                if not item.tone.strip():
                    item.tone = "direkt, freundlich, empowernd, du-Form"
                if not item.disclaimer.strip():
                    item.disclaimer = "Keine Rechts- oder medizinische Beratung."
                item = _enforce_prompt1_word_envelope(item)
                return item
            raise ValidationError(
                message="PROMPT_1 lane response was empty",
                details={"lane_title": lane_payload.get("title")},
            )
        except ValidationError as exc:
            logger.warning(
                "topic_script_candidate_retry",
                lane_title=lane_payload.get("title"),
                attempt=attempt + 1,
                error=exc.message,
                details=exc.details,
            )
            prompt = f"{base_prompt}\n\nFEEDBACK: {exc.message}. Details: {json.dumps(exc.details, default=str)[:600]}"

    for attempt in range(2):
        try:
            text_response = llm.generate_gemini_text(
                prompt=prompt,
                system_prompt=PROMPT1_STAGE3_SYSTEM_PROMPT,
                max_tokens=3200,
            )
            batch, _ = _parse_prompt1_with_normalization(
                llm,
                text_response,
                1,
                profile=profile,
            )
            if batch.items:
                item = batch.items[0]
                if not item.topic.strip():
                    item.topic = lane_title or item.topic
                if not item.caption.strip():
                    item.caption = lane_caption or item.source_summary or item.script
                if not item.source_summary.strip():
                    item.source_summary = lane_caption or item.caption
                if not item.sources and source_url:
                    item.sources = [{"title": source_title or lane_title or item.topic, "url": source_url}]
                if not item.sources and source_title:
                    item.sources = [{"title": source_title, "url": source_url or ""}]
                item.estimated_duration_s = max(
                    1,
                    min(profile.target_length_tier, estimate_script_duration_seconds(item.script)),
                )
                if not item.tone.strip():
                    item.tone = "direkt, freundlich, empowernd, du-Form"
                if not item.disclaimer.strip():
                    item.disclaimer = "Keine Rechts- oder medizinische Beratung."
                item = _enforce_prompt1_word_envelope(item)
                return item
        except ValidationError as exc:
            logger.warning(
                "topic_script_candidate_text_retry",
                lane_title=lane_payload.get("title"),
                attempt=attempt + 1,
                error=exc.message,
                details=exc.details,
            )
            prompt = f"{base_prompt}\n\nFEEDBACK: {exc.message}. Details: {json.dumps(exc.details, default=str)[:600]}"

    raise ValidationError(
        message="PROMPT_1 lane generation failed after structured and text normalization",
        details={"lane_title": lane_payload.get("title"), "target_length_tier": target_length_tier},
    )


def generate_dialog_scripts(
    *,
    topic: str,
    scripts_required: int = 1,
    previously_used_hooks: Optional[List[str]] = None,
    dossier: Optional[ResearchDossier | Dict[str, Any]] = None,
    profile: Optional[Any] = None,
    llm_factory: Callable = get_llm_client,
) -> DialogScripts:
    scripts_required = max(1, min(5, scripts_required))
    llm = llm_factory()
    resolved_profile = profile or get_duration_profile(8)
    prompt = build_prompt2(
        topic=topic,
        scripts_per_category=scripts_required,
        profile=resolved_profile,
        dossier=dossier,
    )
    if previously_used_hooks:
        hooks_list = ", ".join([f'"{hook}"' for hook in previously_used_hooks])
        prompt += f"\n\nWICHTIG: Die folgenden Hooks wurden bereits verwendet: {hooks_list}\nNutze einen anderen Hook-Start fuer dieses Skript."

    for attempt in range(3):
        try:
            response = llm.generate_gemini_json(
                prompt=prompt,
                system_prompt=None,
                json_schema={
                    "type": "object",
                    "properties": {
                        "problem_agitate_solution": {"type": "array", "items": {"type": "string"}},
                        "testimonial": {"type": "array", "items": {"type": "string"}},
                        "transformation": {"type": "array", "items": {"type": "string"}},
                        "description": {"type": "string"},
                    },
                    "required": ["problem_agitate_solution", "description"],
                },
                max_tokens=1600,
            )
            scripts = _coerce_prompt2_payload(response, scripts_required=scripts_required)
            _validate_dialog_scripts_payload(scripts, resolved_profile, topic)
            return DialogScripts(
                problem_agitate_solution=scripts.problem_agitate_solution[:scripts_required],
                testimonial=scripts.testimonial[:scripts_required],
                transformation=scripts.transformation[:scripts_required],
                description=scripts.description,
            )
        except (ValidationError, ThirdPartyError) as exc:
            logger.warning(
                "dialog_scripts_retry",
                topic=topic,
                attempt=attempt + 1,
                error=getattr(exc, "message", str(exc)),
                details=getattr(exc, "details", {}),
                response_preview=(json.dumps(response, ensure_ascii=False)[:500] if "response" in locals() else None),
            )
            prompt = f"{prompt}\n\nFEEDBACK: {getattr(exc, 'message', str(exc))}. Details: {json.dumps(getattr(exc, 'details', {}), default=str)[:800]}"

    for attempt in range(2):
        try:
            text_response = llm.generate_gemini_text(
                prompt=prompt,
                system_prompt=None,
                max_tokens=1600,
            )
            scripts = parse_prompt2_response(text_response, max_per_category=scripts_required)
            _validate_dialog_scripts_payload(scripts, resolved_profile, topic)
            return DialogScripts(
                problem_agitate_solution=scripts.problem_agitate_solution[:scripts_required],
                testimonial=scripts.testimonial[:scripts_required],
                transformation=scripts.transformation[:scripts_required],
                description=scripts.description,
            )
        except (ValidationError, ThirdPartyError) as exc:
            logger.warning(
                "dialog_scripts_text_retry",
                topic=topic,
                attempt=attempt + 1,
                error=getattr(exc, "message", str(exc)),
                details=getattr(exc, "details", {}),
            )

    raise ValidationError(
        message="PROMPT_2 generation failed after structured and text normalization",
        details={"topic": topic, "scripts_required": scripts_required, "target_length_tier": resolved_profile.target_length_tier},
    )


def extract_seed_strict_extractor(
    topic: TopicData,
    *,
    llm_factory: Callable = get_llm_client,
) -> SeedData:
    llm = llm_factory()
    prompt = f"""Extract factual seed information from this topic:

Title: {topic.title}
Rotation: {topic.rotation}
CTA: {topic.cta}

Extract ONLY the factual claims, core messages, or key points. Do not add any creative interpretation.

Return JSON format:
{{
  "facts": ["fact 1", "fact 2"],
  "source_context": "brief context if needed"
}}

Extract facts now:"""
    response = llm.generate_gemini_json(
        prompt=prompt,
        system_prompt=STRICT_EXTRACTOR_SYSTEM_PROMPT,
        json_schema={
            "type": "object",
            "properties": {
                "facts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "source_context": {"type": "string"},
            },
            "required": ["facts"],
            "additionalProperties": False,
        },
    )
    seed = SeedData(**response)
    logger.info("strict_extractor_success", topic_title=topic.title[:50], facts_count=len(seed.facts))
    return seed
