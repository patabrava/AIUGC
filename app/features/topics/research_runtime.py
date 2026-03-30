"""
Provider-facing runtime orchestration for topic research and script generation.
"""

from __future__ import annotations

import json
import random
import re
import secrets
from itertools import combinations
from typing import Any, Callable, Dict, List, Optional

from pydantic import ValidationError as PydanticValidationError

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
from app.features.topics.topic_validation import (
    _clean_fact_pool,
    detect_spoken_copy_issues,
    estimate_script_duration_seconds,
    get_prompt1_sentence_bounds,
    get_prompt1_word_bounds,
    normalize_spoken_whitespace,
    sanitize_fact_fragments,
    sanitize_metadata_text,
    sanitize_spoken_fragment,
    validate_spoken_copy_cleanliness,
)

logger = get_logger(__name__)

PROMPT1_STAGE3_SYSTEM_PROMPT = """You are the Flow Forge PROMPT_1 stage-3 script agent.
Follow the user prompt exactly.
Return only the final script text.
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
    seen_topics = set()
    for seed_topic in shuffled_topics:
        normalized = re.sub(r"\s+", " ", str(seed_topic or "").strip()).lower()
        if not normalized or normalized in seen_topics:
            continue
        seen_topics.add(normalized)
        chosen_topics.append(str(seed_topic).strip())
        if len(chosen_topics) >= 3:
            break

    items: List[ResearchAgentItem] = []
    max_results = count if count and count > 0 else None
    for seed_topic in chosen_topics[:3]:
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
        for lane_candidate in lane_candidates:
            if max_results is not None and len(items) >= max_results:
                break
            candidate = generate_topic_script_candidate(
                post_type=post_type,
                target_length_tier=profile.target_length_tier,
                dossier=dossier,
                lane_candidate=lane_candidate,
                llm_factory=llm_factory,
            )
            items.append(candidate)

    if not items:
        raise ValidationError(
            message="Unable to produce sufficient topics",
            details={"requested": count, "produced": len(items), "seed_topics": chosen_topics[:3]},
        )
    logger.info(
        "research_agent_batch_complete",
        post_type=post_type,
        requested=count,
        produced=len(items),
        seed_topics=chosen_topics[:3],
    )
    return items


def normalize_topic_research_dossier(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
    raw_response: str,
    llm_factory: Callable = get_llm_client,
) -> ResearchDossier:
    try:
        return parse_topic_research_response(
            raw_response,
            seed_topic=seed_topic,
            post_type=post_type,
            target_length_tier=target_length_tier,
        )
    except ValidationError as exc:
        logger.warning(
            "research_dossier_parse_retry",
            seed_topic=seed_topic,
            post_type=post_type,
            error=exc.message,
            details=exc.details,
        )
        try:
            synthesized = _synthesize_research_dossier_from_text(
                raw_response,
                seed_topic=seed_topic,
                post_type=post_type,
                target_length_tier=target_length_tier,
            )
            return ResearchDossier(**synthesized)
        except PydanticValidationError as synthesized_exc:
            raise ValidationError(
                message="PROMPT_1 research dossier invalid",
                details=json.loads(synthesized_exc.json()),
            ) from synthesized_exc


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
    lane_title = normalize_spoken_whitespace(str(lane_payload.get("title") or "").strip())
    lane_caption = sanitize_metadata_text(lane_payload.get("source_summary") or lane_payload.get("caption") or "")
    dossier_source_summary = sanitize_metadata_text((dossier_payload or {}).get("source_summary") or "")
    cluster_summary = sanitize_metadata_text((dossier_payload or {}).get("cluster_summary") or "")
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
    lane_fact_texts = _clean_fact_pool(
        list(lane_payload.get("facts") or [])
        + list((dossier_payload or {}).get("facts") or [])
        + [
            lane_payload.get("angle"),
            *(lane_payload.get("risk_notes") or []),
            *((dossier_payload or {}).get("risk_notes") or []),
        ]
    )
    filler_sentence_pool = sanitize_fact_fragments(
        [
            f"Kurz gesagt: {lane_title}." if lane_title else "",
            f"Bei {lane_title} lohnt sich der genaue Blick auf die Details." if lane_title else "",
            "So kannst du das Thema im Alltag klarer einordnen.",
            "Genau das hilft dir bei sichereren Entscheidungen im Alltag.",
            "Damit erkennst du die Unterschiede früher und vermeidest unnötige Rückfragen.",
            "So planst du im Alltag ruhiger und mit deutlich mehr Klarheit.",
        ]
    )
    short_clause_pool = [
        "für mehr Klarheit im Alltag",
        "damit du sicherer entscheiden kannst",
        "ohne unnötigen Stress im Alltag",
    ]
    framework_choice = str(
        (lane_payload.get("framework_candidates") or dossier_payload.get("framework_candidates") or ["PAL"])[0] or "PAL"
    ).strip()
    framework_value = framework_choice if framework_choice in {"PAL", "Testimonial", "Transformation"} else "PAL"
    min_words, max_words = get_prompt1_word_bounds(profile.target_length_tier)
    min_sentences, max_sentences = get_prompt1_sentence_bounds(profile.target_length_tier)

    def _word_count(text: str) -> int:
        return len(re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", text or ""))

    def _ensure_terminal_punctuation(text: str) -> str:
        stripped = (text or "").strip()
        if not stripped:
            return stripped
        return stripped if stripped[-1] in ".!?" else f"{stripped}."

    def _split_sentences(text: Any) -> List[str]:
        cleaned = sanitize_spoken_fragment(text, ensure_terminal=True)
        if not cleaned:
            return []
        return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", cleaned) if sentence.strip()]

    def _dedupe_sentences(values: List[Any]) -> List[str]:
        sentences: List[str] = []
        seen = set()
        for value in values:
            for sentence in _split_sentences(value):
                signature = sentence.lower()
                if signature in seen:
                    continue
                seen.add(signature)
                sentences.append(sentence)
        return sentences

    clean_fact_sentences = _dedupe_sentences(lane_fact_texts)
    clean_filler_sentences = _dedupe_sentences(filler_sentence_pool)

    def _join_sentences(sentences: List[str]) -> str:
        return normalize_spoken_whitespace(" ".join(sentence.strip() for sentence in sentences if sentence.strip()))

    def _extend_selected_sentences(selected_sentences: List[str], clause_pool: List[str]) -> str:
        if not selected_sentences:
            return ""
        used = {sentence.rstrip(".!?").strip().lower() for sentence in selected_sentences}
        extended = list(selected_sentences)
        for clause in sorted(clause_pool, key=_word_count):
            signature = clause.lower()
            if signature in used:
                continue
            used.add(signature)
            extended[-1] = _ensure_terminal_punctuation(f"{extended[-1].rstrip('.!?')}, {clause}".strip(" ,"))
            candidate = _join_sentences(extended)
            candidate_words = _word_count(candidate)
            if min_words <= candidate_words <= max_words:
                return candidate
            if candidate_words > max_words:
                break
        return ""

    def _choose_multi_sentence_script(preferred_sentences: List[str]) -> str:
        sentence_pool = _dedupe_sentences(preferred_sentences + clean_fact_sentences + clean_filler_sentences)
        if not sentence_pool:
            return ""
        max_pool_size = min(len(sentence_pool), 8)
        capped_pool = sentence_pool[:max_pool_size]
        clause_pool = [sentence.rstrip(".!?").strip() for sentence in capped_pool if sentence.strip()]
        best_under: List[str] = []
        best_under_words = -1
        for sentence_count in range(min_sentences, max_sentences + 1):
            for indexes in combinations(range(len(capped_pool)), sentence_count):
                selected = [capped_pool[index] for index in indexes]
                candidate = _join_sentences(selected)
                word_count = _word_count(candidate)
                if min_words <= word_count <= max_words:
                    return candidate
                if word_count < min_words and word_count > best_under_words:
                    best_under = selected
                    best_under_words = word_count
        if best_under:
            return _extend_selected_sentences(best_under, clause_pool)
        return ""

    def _choose_single_sentence_script(preferred_sentences: List[str]) -> str:
        clause_pool = [
            sentence.rstrip(".!?").strip()
            for sentence in _dedupe_sentences(preferred_sentences + clean_fact_sentences + clean_filler_sentences)
            if sentence.strip()
        ]
        clause_pool.extend(short_clause_pool)
        clause_pool = [clause for clause in clause_pool if clause]
        if not clause_pool:
            return ""
        base_candidates = clause_pool[:]
        if lane_title:
            base_candidates.append(f"Kurz gesagt: {lane_title}")
        for base in base_candidates:
            candidate = _ensure_terminal_punctuation(base)
            if min_words <= _word_count(candidate) <= max_words:
                return candidate
            used = {base.lower()}
            current = base.rstrip(".!?")
            for clause in sorted(clause_pool, key=_word_count):
                signature = clause.lower()
                if signature in used:
                    continue
                used.add(signature)
                candidate = _ensure_terminal_punctuation(f"{current}, {clause}".strip(" ,"))
                word_count = _word_count(candidate)
                if min_words <= word_count <= max_words:
                    return candidate
                if word_count < max_words:
                    current = candidate.rstrip(".!?")
        return ""

    def _compile_prompt1_script(*, preferred_text: Any = "") -> str:
        preferred_sentences = _split_sentences(preferred_text)
        if not preferred_sentences and not clean_fact_sentences:
            return ""
        if min_sentences == max_sentences == 1:
            compiled = _choose_single_sentence_script(preferred_sentences)
        else:
            compiled = _choose_multi_sentence_script(preferred_sentences)
        return sanitize_spoken_fragment(compiled, ensure_terminal=True) if compiled else ""

    def _build_clean_item(*, script_text: str, metadata_summary: str) -> ResearchAgentItem:
        cleaned_script = sanitize_spoken_fragment(script_text, ensure_terminal=True)
        if not cleaned_script:
            raise ValidationError(
                message="PROMPT_1 script was empty after spoken-text sanitization",
                details={"lane_title": lane_payload.get("title"), "target_length_tier": target_length_tier},
            )
        cleaned_summary = sanitize_metadata_text(metadata_summary or lane_caption or dossier_source_summary)
        item = ResearchAgentItem(
            topic=lane_title or sanitize_metadata_text(dossier_payload.get("topic") or "", max_sentences=1) or "Thema",
            script=cleaned_script,
            caption=cleaned_summary,
            framework=framework_value,
            sources=(
                [{"title": source_title or lane_title or str(dossier_payload.get("topic") or "Quelle"), "url": source_url}]
                if source_url
                else []
            ),
            source_summary=cleaned_summary,
            estimated_duration_s=max(1, min(profile.target_length_tier, estimate_script_duration_seconds(cleaned_script))),
            tone="direkt, freundlich, empowernd, du-Form",
            disclaimer=sanitize_metadata_text(
                lane_payload.get("disclaimer")
                or dossier_payload.get("disclaimer")
                or "Keine Rechts- oder medizinische Beratung.",
                max_sentences=1,
            )
            or "Keine Rechts- oder medizinische Beratung.",
        )
        if not item.sources and source_title and source_url:
            item.sources = [{"title": source_title, "url": source_url}]
        item = _enforce_prompt1_word_envelope(item)
        validate_spoken_copy_cleanliness(item, profile=profile)
        if detect_spoken_copy_issues(item.source_summary):
            item.source_summary = ""
            item.caption = ""
        return item

    def _enforce_prompt1_word_envelope(item: ResearchAgentItem) -> ResearchAgentItem:
        script = _compile_prompt1_script(preferred_text=item.script) or sanitize_spoken_fragment(item.script, ensure_terminal=True)
        sentence_count = len(_split_sentences(script))
        final_count = _word_count(script)
        if (
            final_count < min_words
            or final_count > max_words
            or sentence_count < min_sentences
            or sentence_count > max_sentences
        ):
            raise ValidationError(
                message="PROMPT_1 script does not match target word/sentence envelope",
                details={
                    "target_length_tier": profile.target_length_tier,
                    "word_count": final_count,
                    "expected_range": [min_words, max_words],
                    "sentence_count": sentence_count,
                    "expected_sentences": [min_sentences, max_sentences],
                },
            )
        item.script = script
        item.estimated_duration_s = max(1, min(profile.target_length_tier, estimate_script_duration_seconds(script)))
        return item

    def _synthesize_prompt1_fallback_item() -> ResearchAgentItem:
        script = _compile_prompt1_script()
        if not script:
            raise ValidationError(
                message="PROMPT_1 script could not be reconstructed from clean facts",
                details={"lane_title": lane_payload.get("title"), "target_length_tier": target_length_tier},
            )
        return _build_clean_item(
            script_text=script,
            metadata_summary=lane_caption or dossier_source_summary or cluster_summary,
        )

    def _extract_script_text(raw_response: str) -> str:
        cleaned = (raw_response or "").strip()
        if not cleaned:
            return ""
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        parsed: Any = None
        if cleaned[:1] in {"{", "["}:
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, dict):
            for key in ("script", "text", "content", "body"):
                candidate = str(parsed.get(key) or "").strip()
                if candidate:
                    cleaned = candidate
                    break
            else:
                items = parsed.get("items")
                if isinstance(items, list) and items:
                    first = items[0]
                    if isinstance(first, dict):
                        cleaned = str(first.get("script") or first.get("text") or first.get("content") or "").strip()
                    else:
                        cleaned = str(first).strip()
        elif isinstance(parsed, list) and parsed:
            first = parsed[0]
            if isinstance(first, dict):
                cleaned = str(first.get("script") or first.get("text") or first.get("content") or "").strip()
            else:
                cleaned = str(first).strip()
        cleaned = re.sub(r"^(?:script|text|inhalt|caption|titel|title)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = sanitize_spoken_fragment(cleaned, ensure_terminal=True)
        if cleaned and cleaned[-1] not in ".!?":
            cleaned = cleaned.rstrip(",;:") + "."
        return cleaned

    def _build_item_from_text(raw_response: str) -> ResearchAgentItem:
        raw_issues = detect_spoken_copy_issues(raw_response or "")
        if raw_issues:
            raise ValidationError(
                message="PROMPT_1 raw draft contains research-note leakage",
                details={"lane_title": lane_payload.get("title"), "issues": raw_issues},
            )
        script_text = _extract_script_text(raw_response)
        if not script_text:
            raise ValidationError(
                message="PROMPT_1 lane output was empty",
                details={"lane_title": lane_payload.get("title"), "target_length_tier": target_length_tier},
            )
        return _build_clean_item(
            script_text=script_text,
            metadata_summary=lane_caption or dossier_source_summary or cluster_summary or script_text,
        )

    def _audit_gate(item: ResearchAgentItem) -> ResearchAgentItem:
        """Inline quality gate: audit the script before returning it for persistence."""
        from app.features.topics.audit import audit_single_script

        row = {
            "id": f"inline-{lane_title[:20]}",
            "script": item.script,
            "target_length_tier": target_length_tier,
            "title": item.topic,
        }
        try:
            result = audit_single_script(row, llm=llm)
        except Exception as exc:
            logger.warning(
                "topic_script_inline_audit_error",
                lane_title=lane_payload.get("title"),
                error=str(exc),
            )
            return item  # let it through if audit itself fails

        item.quality_score = result.total_score
        item.quality_notes = result.quality_notes

        if result.status == "pass":
            logger.info(
                "topic_script_inline_audit_pass",
                lane_title=lane_payload.get("title"),
                score=result.total_score,
            )
            return item

        if result.status == "needs_repair":
            logger.warning(
                "topic_script_inline_audit_needs_repair",
                lane_title=lane_payload.get("title"),
                score=result.total_score,
                notes=result.quality_notes[:200],
            )
            # Still return — the score travels with the item for downstream decisions
            return item

        # reject
        logger.warning(
            "topic_script_inline_audit_reject",
            lane_title=lane_payload.get("title"),
            score=result.total_score,
            notes=result.quality_notes[:200],
        )
        raise ValidationError(
            message="PROMPT_1 script rejected by inline audit",
            details={
                "lane_title": lane_payload.get("title"),
                "score": result.total_score,
                "status": result.status,
            },
        )

    for attempt in range(2):
        try:
            text_response = llm.generate_gemini_text(
                prompt=prompt,
                system_prompt=PROMPT1_STAGE3_SYSTEM_PROMPT,
                max_tokens=3200,
            )
            item = _build_item_from_text(text_response)
            return _audit_gate(item)
        except ValidationError as exc:
            logger.warning(
                "topic_script_candidate_text_invalid",
                lane_title=lane_payload.get("title"),
                error=getattr(exc, "message", str(exc)),
                details=getattr(exc, "details", {}),
            )
            if attempt == 0 and "rejected by inline audit" in getattr(exc, "message", ""):
                continue  # retry once if audit rejected
            return _synthesize_prompt1_fallback_item()
        except ThirdPartyError as exc:
            logger.warning(
                "topic_script_candidate_text_retry",
                lane_title=lane_payload.get("title"),
                attempt=attempt + 1,
                error=getattr(exc, "message", str(exc)),
                details=getattr(exc, "details", {}),
            )
            if attempt == 0:
                continue
            break

    logger.warning(
        "topic_script_candidate_fallback_synthesized",
        lane_title=lane_payload.get("title"),
        target_length_tier=target_length_tier,
    )
    return _synthesize_prompt1_fallback_item()


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
                "dialog_scripts_retry",
                topic=topic,
                attempt=attempt + 1,
                error=getattr(exc, "message", str(exc)),
                details=getattr(exc, "details", {}),
            )
            prompt = f"{prompt}\n\nFEEDBACK: {getattr(exc, 'message', str(exc))}. Details: {json.dumps(getattr(exc, 'details', {}), default=str)[:800]}"

    raise ValidationError(
        message="PROMPT_2 generation failed after text normalization",
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
