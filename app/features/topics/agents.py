"""
Compatibility facade for the topic-generation slice.

This module intentionally keeps the public import surface stable while the
implementation lives in focused runtime, parser, validation, and mapper modules.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.adapters.llm_client import get_llm_client
from app.core.errors import ThirdPartyError, ValidationError
from app.core.video_profiles import get_duration_profile
from app.features.topics.content_utils import build_social_description, extract_soft_cta, strip_cta_from_script
from app.features.topics.lifestyle_runtime import generate_lifestyle_topics as _generate_lifestyle_topics
from app.features.topics.prompt3_runtime import generate_product_topics as _generate_product_topics
from app.features.topics.research_runtime import (
    extract_seed_strict_extractor as _extract_seed_strict_extractor,
    generate_dialog_scripts as _generate_dialog_scripts,
    generate_topic_research_dossier as _generate_topic_research_dossier,
    generate_topic_script_candidate as _generate_topic_script_candidate,
    generate_topics_research_agent as _generate_topics_research_agent,
    normalize_topic_research_dossier as _normalize_topic_research_dossier,
)
from app.features.topics.response_parsers import (
    _coerce_prompt2_payload,
    _validate_dialog_scripts_payload,
    parse_prompt1_response as _parse_prompt1_response,
    parse_prompt2_response,
    parse_prompt3_response as _parse_prompt3_response,
    parse_topic_research_response,
)
from app.features.topics.schemas import (
    DiscoverTopicsRequest,
    DialogScripts,
    ResearchAgentBatch,
    ResearchAgentItem,
    ResearchDossier,
    SeedData,
    TopicData,
    TopicListResponse,
    TopicResponse,
    TopicResearchDossierResponse,
    TopicResearchRunRequest,
    TopicResearchRunResponse,
    TopicScriptVariant,
)
from app.features.topics.seed_builders import (
    build_lifestyle_seed_payload,
    build_product_seed_payload,
    build_seed_payload,
    convert_research_item_to_topic,
)
from app.features.topics.topic_validation import (
    _dialog_word_bounds,
    _find_english_markers,
    _validate_dialog_script_semantics,
    _validate_dialog_script_tier,
    _validate_url_accessible,
    compute_bigram_jaccard,
    estimate_script_duration_seconds,
    normalize_framework,
    validate_duration,
    validate_german_content,
    validate_round_robin,
    validate_sources_accessible,
    validate_summary,
    validate_unique_ctas,
)


def parse_prompt1_response(raw: str, profile: Optional[Any] = None) -> ResearchAgentBatch:
    return _parse_prompt1_response(
        raw,
        profile=profile,
        validate_sources_accessible_fn=validate_sources_accessible,
        validate_duration_fn=validate_duration,
        validate_summary_fn=validate_summary,
        validate_german_content_fn=validate_german_content,
        validate_round_robin_fn=validate_round_robin,
        validate_unique_ctas_fn=validate_unique_ctas,
    )


def generate_topics_research_agent(
    post_type: str,
    count: int = 10,
    seed: Optional[int] = None,
    assigned_topics: Optional[List[str]] = None,
    profile: Optional[Any] = None,
    dossier: Optional[ResearchDossier | Dict[str, Any]] = None,
    lane_candidate: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Any] = None,
) -> List[ResearchAgentItem]:
    return _generate_topics_research_agent(
        post_type=post_type,
        count=count,
        seed=seed,
        assigned_topics=assigned_topics,
        profile=profile,
        progress_callback=progress_callback,
        llm_factory=get_llm_client,
    )


def normalize_topic_research_dossier(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
    raw_response: str,
    progress_callback: Optional[Any] = None,
) -> ResearchDossier:
    return _normalize_topic_research_dossier(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
        raw_response=raw_response,
        llm_factory=get_llm_client,
    )


def generate_topic_research_dossier(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
    progress_callback: Optional[Any] = None,
) -> ResearchDossier:
    return _generate_topic_research_dossier(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
        progress_callback=progress_callback,
        llm_factory=get_llm_client,
    )


def generate_topic_script_candidate(
    *,
    post_type: str,
    target_length_tier: int,
    dossier: ResearchDossier | Dict[str, Any],
    lane_candidate: Dict[str, Any],
    progress_callback: Optional[Any] = None,
) -> ResearchAgentItem:
    return _generate_topic_script_candidate(
        post_type=post_type,
        target_length_tier=target_length_tier,
        dossier=dossier,
        lane_candidate=lane_candidate,
        progress_callback=progress_callback,
        llm_factory=get_llm_client,
    )


def generate_dialog_scripts(
    topic: str,
    scripts_required: int = 1,
    previously_used_hooks: Optional[List[str]] = None,
    dossier: Optional[ResearchDossier | Dict[str, Any]] = None,
    profile: Optional[Any] = None,
) -> DialogScripts:
    return _generate_dialog_scripts(
        topic=topic,
        scripts_required=scripts_required,
        previously_used_hooks=previously_used_hooks,
        dossier=dossier,
        profile=profile,
        llm_factory=get_llm_client,
    )


def generate_lifestyle_topics(count: int = 1, seed: Optional[int] = None, target_length_tier: Optional[int] = None) -> List[Dict[str, object]]:
    return _generate_lifestyle_topics(
        count=count,
        seed=seed,
        target_length_tier=target_length_tier,
        generate_dialog_scripts_fn=generate_dialog_scripts,
    )


def generate_product_topics(count: int = 1, seed: Optional[int] = None, target_length_tier: Optional[int] = None) -> List[Dict[str, object]]:
    return _generate_product_topics(
        count=count,
        seed=seed,
        target_length_tier=target_length_tier,
        llm_factory=get_llm_client,
    )


def parse_prompt3_response(raw: str):
    return _parse_prompt3_response(raw)


def extract_seed_strict_extractor(topic: TopicData) -> SeedData:
    return _extract_seed_strict_extractor(topic, llm_factory=get_llm_client)
