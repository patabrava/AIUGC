"""
Shared duration-tier profiles for batch routing, scripts, and Veo chaining.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


SHORT_VIDEO_ROUTE = "short"
VEO_EXTENDED_VIDEO_ROUTE = "veo_extended"
DEFAULT_TARGET_LENGTH_TIER = 8
SUPPORTED_TARGET_LENGTH_TIERS = (8, 16, 32)
VEO_PROVIDER = "veo_3_1"
VIDEO_STATUS_SUBMITTED = "submitted"
VIDEO_STATUS_PROCESSING = "processing"
VIDEO_STATUS_COMPLETED = "completed"
VIDEO_STATUS_FAILED = "failed"
TRIM_TAIL_MS = 500
VIDEO_STATUS_EXTENDED_SUBMITTED = "extended_submitted"
VIDEO_STATUS_EXTENDED_PROCESSING = "extended_processing"
VIDEO_STATUS_CAPTION_PENDING = "caption_pending"
VIDEO_STATUS_CAPTION_PROCESSING = "caption_processing"
VIDEO_STATUS_CAPTION_COMPLETED = "caption_completed"
VIDEO_STATUS_CAPTION_FAILED = "caption_failed"


@dataclass(frozen=True)
class DurationProfile:
    target_length_tier: int
    route: str
    requested_seconds: int
    provider_target_seconds: int
    veo_base_seconds: int
    veo_extension_seconds: int
    veo_extension_hops: int
    prompt1_min_words: int
    prompt1_max_words: int
    prompt1_min_seconds: int
    prompt1_max_seconds: int
    prompt1_max_chars_no_spaces: int
    prompt1_sentence_guidance: str
    prompt2_min_words: int
    prompt2_max_words: int
    prompt2_sentence_guidance: str


_PROFILES = {
    8: DurationProfile(
        target_length_tier=8,
        route=SHORT_VIDEO_ROUTE,
        requested_seconds=8,
        provider_target_seconds=8,
        veo_base_seconds=8,
        veo_extension_seconds=0,
        veo_extension_hops=0,
        prompt1_min_words=12,
        prompt1_max_words=15,
        prompt1_min_seconds=5,
        prompt1_max_seconds=6,
        prompt1_max_chars_no_spaces=90,
        prompt1_sentence_guidance="exactly one sentence",
        prompt2_min_words=16,
        prompt2_max_words=20,
        prompt2_sentence_guidance="exactly one sentence",
    ),
    16: DurationProfile(
        target_length_tier=16,
        route=VEO_EXTENDED_VIDEO_ROUTE,
        requested_seconds=16,
        provider_target_seconds=18,
        veo_base_seconds=4,
        veo_extension_seconds=7,
        veo_extension_hops=2,
        prompt1_min_words=26,
        prompt1_max_words=36,
        prompt1_min_seconds=12,
        prompt1_max_seconds=14,
        prompt1_max_chars_no_spaces=220,
        prompt1_sentence_guidance="2 concise sentences",
        prompt2_min_words=24,
        prompt2_max_words=34,
        prompt2_sentence_guidance="1-2 concise sentences",
    ),
    32: DurationProfile(
        target_length_tier=32,
        route=VEO_EXTENDED_VIDEO_ROUTE,
        requested_seconds=32,
        provider_target_seconds=32,
        veo_base_seconds=4,
        veo_extension_seconds=7,
        veo_extension_hops=4,
        prompt1_min_words=54,
        prompt1_max_words=74,
        prompt1_min_seconds=24,
        prompt1_max_seconds=28,
        prompt1_max_chars_no_spaces=430,
        prompt1_sentence_guidance="3-4 concise sentences",
        prompt2_min_words=40,
        prompt2_max_words=66,
        prompt2_sentence_guidance="3-4 Sätze",
    ),
}


def normalize_target_length_tier(value: Optional[int]) -> int:
    if value is None:
        return DEFAULT_TARGET_LENGTH_TIER
    if isinstance(value, str):
        value = int(value.strip())
    tier = int(value)
    if tier not in SUPPORTED_TARGET_LENGTH_TIERS:
        raise ValueError(f"Unsupported target length tier: {tier}")
    return tier


def get_duration_profile(value: Optional[int]) -> DurationProfile:
    tier = normalize_target_length_tier(value)
    return _PROFILES[tier]


def derive_pipeline_route(value: Optional[int]) -> str:
    return get_duration_profile(value).route


def uses_duration_routing(batch: dict) -> bool:
    return batch.get("target_length_tier") is not None


def build_seed_duration_metadata(profile: DurationProfile) -> dict:
    return {
        "target_length_tier": profile.target_length_tier,
        "video_pipeline_route": profile.route,
        "requested_seconds": profile.requested_seconds,
        "provider_target_seconds": profile.provider_target_seconds,
    }


def get_submission_video_status(route: Optional[str], provider_status: Optional[str]) -> str:
    normalized = VIDEO_STATUS_SUBMITTED if provider_status == "queued" else (provider_status or VIDEO_STATUS_SUBMITTED)
    if route == VEO_EXTENDED_VIDEO_ROUTE:
        if normalized == VIDEO_STATUS_PROCESSING:
            return VIDEO_STATUS_EXTENDED_PROCESSING
        if normalized == VIDEO_STATUS_SUBMITTED:
            return VIDEO_STATUS_EXTENDED_SUBMITTED
    return normalized


def get_processing_video_status(route: Optional[str]) -> str:
    if route == VEO_EXTENDED_VIDEO_ROUTE:
        return VIDEO_STATUS_EXTENDED_PROCESSING
    return VIDEO_STATUS_PROCESSING


def get_submitted_video_status(route: Optional[str]) -> str:
    if route == VEO_EXTENDED_VIDEO_ROUTE:
        return VIDEO_STATUS_EXTENDED_SUBMITTED
    return VIDEO_STATUS_SUBMITTED


def get_pollable_video_statuses() -> tuple[str, ...]:
    return (
        VIDEO_STATUS_SUBMITTED,
        VIDEO_STATUS_PROCESSING,
        VIDEO_STATUS_EXTENDED_SUBMITTED,
        VIDEO_STATUS_EXTENDED_PROCESSING,
    )


def get_caption_pollable_statuses() -> tuple[str, ...]:
    """Statuses the caption worker should poll for."""
    return (VIDEO_STATUS_CAPTION_PENDING,)
