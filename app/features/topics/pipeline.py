"""Three-stage topic pipeline orchestration.

This module makes the research -> normalization -> script generation boundary explicit
without forcing callers to change the existing handlers immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.adapters.llm_client import get_llm_client
from app.features.topics.agents import (
    generate_dialog_scripts,
    normalize_topic_research_dossier,
)
from app.features.topics.prompts import build_topic_research_prompt


@dataclass(frozen=True)
class RawResearchArtifact:
    seed_topic: str
    post_type: str
    target_length_tier: int
    prompt: str
    raw_response: str
    metadata: Dict[str, Any]


def run_stage1_raw_research(
    *,
    seed_topic: str,
    post_type: str,
    target_length_tier: int,
    progress_callback: Optional[Any] = None,
) -> RawResearchArtifact:
    llm = get_llm_client()
    prompt = build_topic_research_prompt(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
    )
    raw_response = llm.generate_gemini_deep_research(
        prompt=prompt,
        system_prompt=None,
        progress_callback=progress_callback,
        metadata={
            "feature": "topics.stage1_raw_research",
            "seed_topic": seed_topic,
            "post_type": post_type,
            "target_length_tier": str(target_length_tier),
        },
    )
    return RawResearchArtifact(
        seed_topic=seed_topic,
        post_type=post_type,
        target_length_tier=target_length_tier,
        prompt=prompt,
        raw_response=raw_response,
        metadata={
            "feature": "topics.stage1_raw_research",
            "seed_topic": seed_topic,
            "post_type": post_type,
            "target_length_tier": target_length_tier,
        },
    )


def run_stage2_normalization(artifact: RawResearchArtifact):
    return normalize_topic_research_dossier(
        seed_topic=artifact.seed_topic,
        post_type=artifact.post_type,
        target_length_tier=artifact.target_length_tier,
        raw_response=artifact.raw_response,
    )


def run_stage3_script_generation(
    *,
    topic: str,
    scripts_required: int,
    dossier: Any,
    profile: Optional[Any] = None,
    previously_used_hooks: Optional[list[str]] = None,
):
    return generate_dialog_scripts(
        topic=topic,
        scripts_required=scripts_required,
        dossier=dossier,
        profile=profile,
        previously_used_hooks=previously_used_hooks,
    )
