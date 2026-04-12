"""
Lippe Lift Studio Video Generation Handlers
FastAPI route handlers for video generation operations.
Per Constitution § V: Locality & Vertical Slices
Per Canon § 3.2: S5_PROMPTS_BUILT → S6_QA transition
"""

import base64
import mimetypes
import re
from fastapi import APIRouter, HTTPException, Request, status
from typing import Dict, Any, Optional
import json
import os
import random
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError
import httpx

from app.adapters.supabase_client import get_supabase
from app.adapters.storage_client import get_storage_client
from app.adapters.veo_client import get_veo_client
from app.adapters.vertex_ai_client import get_vertex_ai_client
from app.core.config import get_settings
from app.core.errors import FlowForgeException, SuccessResponse, ValidationError, ErrorCode
from app.core.logging import get_logger
from app.core.video_profiles import (
    VEO_EXTENDED_VIDEO_ROUTE,
    VEO_PROVIDER,
    get_duration_profile,
    get_profile_route_config,
    get_submission_video_status,
    uses_duration_routing,
)
from app.features.batches.queries import get_batch_by_id
from app.features.batches.state_machine import reconcile_batch_video_pipeline_state
from app.features.posts.prompt_text import build_full_prompt_text
from app.features.posts.prompt_builder import (
    build_video_prompt_from_seed,
    build_veo_prompt_segment,
    split_dialogue_sentences,
    validate_video_prompt,
)
from app.features.videos.prompt_audit import record_prompt_audit
from app.features.videos.quota_guard import (
    build_reservation_key,
    chain_cost_units,
    consume_quota,
    ensure_immediate_submit_slot,
    maybe_freeze_after_provider_429,
    quota_controls_bypassed,
    release_quota,
    reserve_quota,
)
from app.features.videos.schemas import (
    VideoGenerationRequest,
    VideoGenerationResponse,
    VideoStatusResponse,
    BatchVideoGenerationRequest,
    BatchVideoGenerationResponse,
    VertexVideoGenerationRequest,
    VertexVideoGenerationResponse,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/videos", tags=["videos"])

_WORDS_PER_SECOND = 2.5


_GLOBAL_VEO_ANCHOR_RELATIVE_PATH = "static/images/sarah.jpg"
_GLOBAL_VEO_ANCHOR_PATH = Path(__file__).resolve().parents[3] / _GLOBAL_VEO_ANCHOR_RELATIVE_PATH
_GLOBAL_VEO_ANCHOR_OBJECT_KEY = "Lippe Lift Studio/images/anchors/sarah.jpg"
# Keep the live preview path text-only until the API explicitly supports image.inlineData.
_GLOBAL_VEO_ANCHOR_ENABLED = False


def _resolve_global_veo_anchor_image(correlation_id: str) -> Dict[str, Any]:
    """Load the global Sarah portrait and mirror it to a fixed Cloudflare R2 key."""
    if not _GLOBAL_VEO_ANCHOR_PATH.exists():
        raise ValidationError(
            "Global Veo anchor image is missing.",
            {"anchor_image_path": _GLOBAL_VEO_ANCHOR_RELATIVE_PATH},
        )

    try:
        image_bytes = _GLOBAL_VEO_ANCHOR_PATH.read_bytes()
    except OSError as exc:
        raise ValidationError(
            "Global Veo anchor image could not be read.",
            {"anchor_image_path": _GLOBAL_VEO_ANCHOR_RELATIVE_PATH, "error": str(exc)},
        ) from exc

    if not image_bytes:
        raise ValidationError(
            "Global Veo anchor image is empty.",
            {"anchor_image_path": _GLOBAL_VEO_ANCHOR_RELATIVE_PATH},
        )

    mime_type = mimetypes.guess_type(_GLOBAL_VEO_ANCHOR_PATH.name)[0] or "image/jpeg"
    metadata = {
        "anchor_image_enabled": True,
        "anchor_image_source_path": _GLOBAL_VEO_ANCHOR_RELATIVE_PATH,
        "anchor_image_mime_type": mime_type,
        "anchor_image_size_bytes": len(image_bytes),
    }

    try:
        mirrored = get_storage_client().ensure_image(
            image_bytes=image_bytes,
            object_key=_GLOBAL_VEO_ANCHOR_OBJECT_KEY,
            correlation_id=correlation_id,
            content_type=mime_type,
        )
        metadata["anchor_image_storage_key"] = mirrored["storage_key"]
        metadata["anchor_image_url"] = mirrored["url"]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "veo_anchor_image_mirror_failed",
            correlation_id=correlation_id,
            anchor_image_path=_GLOBAL_VEO_ANCHOR_RELATIVE_PATH,
            error=str(exc),
        )

    return {
        "first_frame_image": {
            "mime_type": mime_type,
            "data_base64": base64.b64encode(image_bytes).decode("ascii"),
        },
        "metadata": metadata,
    }


def _resolve_extended_provider_aspect_ratio(route: Optional[str], requested_aspect_ratio: str) -> str:
    """Extended runs keep the requested aspect ratio when the REST path is used."""
    return requested_aspect_ratio


def _resolve_video_submission_plan(
    *,
    batch: Dict[str, Any],
    requested_provider: Optional[str],
    requested_seconds: Optional[int],
    aspect_ratio: str,
    resolution: str,
    size: Optional[str],
) -> Dict[str, Any]:
    if uses_duration_routing(batch):
        profile = get_duration_profile(batch.get("target_length_tier"))
        profile_config = get_profile_route_config(profile)
        resolved_resolution = "720p" if profile.route == VEO_EXTENDED_VIDEO_ROUTE else resolution
        provider_aspect_ratio = _resolve_extended_provider_aspect_ratio(profile.route, aspect_ratio)
        requested_size = size or _map_size_from_aspect_ratio(aspect_ratio, resolved_resolution)
        provider = "vertex_ai" if requested_provider == "vertex_ai" else VEO_PROVIDER
        return {
            "provider": provider,
            "seconds": profile.requested_seconds,
            "provider_target_seconds": profile.provider_target_seconds,
            "aspect_ratio": aspect_ratio,
            "requested_aspect_ratio": aspect_ratio,
            "provider_aspect_ratio": provider_aspect_ratio,
            "resolution": resolved_resolution,
            "size": requested_size,
            "requested_size": requested_size,
            "provider_requested_size": _map_size_from_aspect_ratio(provider_aspect_ratio, resolved_resolution),
            "postprocess_crop_aspect_ratio": aspect_ratio if provider_aspect_ratio != aspect_ratio else None,
            "postprocess_strategy": "center_crop_scale" if provider_aspect_ratio != aspect_ratio else None,
            "profile": profile,
            "profile_config": profile_config,
            "duration_routed": True,
        }

    provider = requested_provider or VEO_PROVIDER
    seconds = requested_seconds or 8
    return {
        "provider": provider,
        "seconds": seconds,
        "provider_target_seconds": seconds,
        "aspect_ratio": aspect_ratio,
        "requested_aspect_ratio": aspect_ratio,
        "provider_aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "size": size,
        "requested_size": size,
        "provider_requested_size": size,
        "postprocess_crop_aspect_ratio": None,
        "postprocess_strategy": None,
        "profile": None,
        "profile_config": None,
        "duration_routed": False,
    }


def _normalize_seed_data(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _load_or_build_video_prompt(
    *,
    post: Dict[str, Any],
    supabase_client,
    correlation_id: str,
) -> Dict[str, Any]:
    video_prompt = post.get("video_prompt_json")
    if isinstance(video_prompt, str):
        try:
            video_prompt = json.loads(video_prompt)
        except json.JSONDecodeError:
            video_prompt = None

    if isinstance(video_prompt, dict) and video_prompt:
        return video_prompt

    seed_data = _normalize_seed_data(post.get("seed_data"))
    if not seed_data:
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Post missing video_prompt_json and seed_data. Run build-prompt first.",
            details={"post_id": post.get("id")},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        built_prompt = build_video_prompt_from_seed(seed_data, legacy_32_visuals=False)
        validate_video_prompt(built_prompt)
    except ValidationError as exc:
        raise FlowForgeException(
            code=exc.code,
            message=exc.message,
            details={**exc.details, "post_id": post.get("id")},
            status_code=exc.status_code,
        ) from exc
    except PydanticValidationError as exc:
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Generated video prompt failed validation.",
            details={"post_id": post.get("id"), "error": str(exc)},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        ) from exc

    supabase_client.table("posts").update({
        "video_prompt_json": built_prompt,
    }).eq("id", post["id"]).execute()
    post["video_prompt_json"] = built_prompt
    logger.info(
        "video_prompt_backfilled_for_submission",
        post_id=post.get("id"),
        batch_id=post.get("batch_id"),
        correlation_id=correlation_id,
    )
    try:
        reconcile_batch_video_pipeline_state(
            batch_id=post.get("batch_id"),
            correlation_id=correlation_id,
            supabase_client=supabase_client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "video_prompt_backfill_reconcile_failed",
            post_id=post.get("id"),
            batch_id=post.get("batch_id"),
            correlation_id=correlation_id,
            error=str(exc),
        )
    return built_prompt


def _build_submission_metadata(
    *,
    existing_metadata: Dict[str, Any],
    submission_plan: Dict[str, Any],
    submission_result: Dict[str, Any],
    segment_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    requested_aspect_ratio = submission_plan.get("requested_aspect_ratio") or submission_plan["aspect_ratio"]
    requested_size = submission_result.get("requested_size") or submission_plan.get("requested_size")
    provider_aspect_ratio = submission_plan.get("provider_aspect_ratio") or requested_aspect_ratio
    metadata = {
        **existing_metadata,
        "requested_aspect_ratio": requested_aspect_ratio,
        "requested_resolution": submission_plan["resolution"],
        "requested_seconds": submission_plan["seconds"],
        "requested_size": requested_size,
        "provider_aspect_ratio": provider_aspect_ratio,
    }

    provider_requested_size = (
        submission_result.get("provider_requested_size")
        or submission_plan.get("provider_requested_size")
    )
    if provider_requested_size:
        metadata["provider_requested_size"] = provider_requested_size
    if submission_plan.get("postprocess_crop_aspect_ratio"):
        metadata["postprocess_crop_aspect_ratio"] = submission_plan["postprocess_crop_aspect_ratio"]
    if submission_plan.get("postprocess_strategy"):
        metadata["postprocess_strategy"] = submission_plan["postprocess_strategy"]

    profile = submission_plan.get("profile")
    if profile is not None:
        metadata.update(
            {
                "target_length_tier": profile.target_length_tier,
                "video_pipeline_route": profile.route,
                "provider_target_seconds": profile.provider_target_seconds,
                "generated_seconds": 0,
                "actual_seconds": None,
                "chain_status": "submitted",
                "operation_ids": [submission_result["operation_id"]],
                "veo_base_seconds": profile.veo_base_seconds,
                "veo_extension_seconds": profile.veo_extension_seconds,
                "veo_extension_hops_target": profile.veo_extension_hops,
                "veo_extension_hops_completed": 0,
            }
        )
    if profile is not None and profile.route != VEO_EXTENDED_VIDEO_ROUTE:
        metadata["duration_seconds"] = profile.provider_target_seconds

    provider_model = submission_result.get("provider_model")
    if provider_model:
        metadata["provider_model"] = provider_model
    if submission_result.get("provider_metadata"):
        metadata["provider_metadata"] = submission_result["provider_metadata"]
    if segment_metadata:
        metadata.update(segment_metadata)

    return metadata


def _uses_actual_efficient_long_route(profile: Optional[Any]) -> bool:
    """Efficient long-route profiles use an 8s base with 1 or 3 extension hops."""
    return bool(
        profile is not None
        and profile.route == VEO_EXTENDED_VIDEO_ROUTE
        and profile.veo_base_seconds == 8
        and profile.veo_extension_hops in {1, 3}
    )


def _should_assign_veo_seed(*, provider: str, profile: Optional[Any]) -> bool:
    """Legacy 32s chains must stay unseeded to mirror the last stable contract."""
    return provider == VEO_PROVIDER and _uses_actual_efficient_long_route(profile)


def _required_veo_segments_for_profile_hops(hops_target: int) -> int:
    return max(int(hops_target or 0), 0) + 1


def _estimate_spoken_seconds(text: str) -> float:
    words = [word for word in str(text).split() if word]
    if not words:
        return 0.0
    return len(words) / _WORDS_PER_SECOND


def _split_dialogue_units_for_time_balance(segments: list[str], *, target_base_words: float) -> list[str]:
    units: list[str] = []
    for index, segment in enumerate(segments):
        words = [word for word in segment.split() if word]
        if index == 0 and len(words) >= 8:
            candidate_matches = list(re.finditer(r"[,;:]", segment))
            best_split: Optional[tuple[float, int, str, str]] = None
            for match in candidate_matches:
                split_index = match.start()
                head = segment[: split_index + 1].strip()
                tail = segment[split_index + 1 :].strip()
                if not head or not tail:
                    continue
                head_words = len(head.split())
                tail_words = len(tail.split())
                if head_words < 3 or tail_words < 3:
                    continue
                score = abs(head_words - target_base_words) + abs(tail_words - target_base_words * 0.75)
                candidate = (score, -head_words, head, tail)
                if best_split is None or candidate < best_split:
                    best_split = candidate
            if best_split is not None:
                _, _, head, tail = best_split
                units.append(head)
                units.append(tail)
                continue
        units.append(segment)
    return units


def _partition_dialogue_units_for_profile(
    units: list[str],
    *,
    profile: Any,
    required_segments: int,
) -> list[str]:
    if required_segments <= 0 or not units:
        return units
    if len(units) < required_segments:
        return units

    target_budgets = [
        _segment_time_budget_seconds(profile=profile, segment_index=index)
        for index in range(required_segments)
    ]
    target_words = [budget * _WORDS_PER_SECOND for budget in target_budgets]
    packed_segments: list[str] = []
    cursor = 0

    for segment_index in range(required_segments):
        if segment_index == required_segments - 1:
            packed_segments.append(" ".join(units[cursor:]).strip())
            break

        remaining_segments = required_segments - segment_index
        remaining_units = len(units) - cursor
        target = target_words[segment_index]
        min_units_needed_after_current = remaining_segments - 1
        current_units: list[str] = []
        current_words = 0.0

        while cursor < len(units):
            next_unit = units[cursor]
            next_words = _estimate_spoken_seconds(next_unit) * _WORDS_PER_SECOND
            units_left_after_next = len(units) - (cursor + 1)
            if current_units and units_left_after_next < min_units_needed_after_current:
                break

            current_units.append(next_unit)
            current_words += next_words
            cursor += 1

            if current_words >= target and units_left_after_next >= min_units_needed_after_current:
                break

        if not current_units and cursor < len(units):
            current_units.append(units[cursor])
            cursor += 1

        packed_segments.append(" ".join(current_units).strip())

    return [segment for segment in packed_segments if segment]


def _partition_words_for_profile(
    words: list[str],
    *,
    profile: Any,
    required_segments: int,
) -> list[str]:
    if required_segments <= 0 or not words:
        return [" ".join(words).strip()] if words else []
    if len(words) < required_segments:
        return [" ".join(words).strip()]

    target_budgets = [
        _segment_time_budget_seconds(profile=profile, segment_index=index)
        for index in range(required_segments)
    ]
    target_words = [budget * _WORDS_PER_SECOND for budget in target_budgets]
    minimum_words = [max(6, int(round(target * 0.6))) for target in target_words]
    minimum_total = sum(minimum_words)
    if minimum_total > len(words):
        scale = len(words) / float(minimum_total)
        minimum_words = [max(1, int(round(value * scale))) for value in minimum_words]
        while sum(minimum_words) > len(words):
            for index in range(len(minimum_words) - 1, -1, -1):
                if sum(minimum_words) <= len(words):
                    break
                if minimum_words[index] > 1:
                    minimum_words[index] -= 1

    prefix_counts = [0]
    for word in words:
        prefix_counts.append(prefix_counts[-1] + 1)

    min_suffix = [0] * (required_segments + 1)
    for index in range(required_segments - 1, -1, -1):
        min_suffix[index] = min_suffix[index + 1] + minimum_words[index]

    from functools import lru_cache

    @lru_cache(maxsize=None)
    def _best_cuts(segment_index: int, start_index: int) -> tuple[float, tuple[int, ...]]:
        if segment_index == required_segments - 1:
            remaining = len(words) - start_index
            if remaining < minimum_words[segment_index]:
                return float("inf"), ()
            target = target_words[segment_index]
            cost = (remaining - target) ** 2
            return cost, (len(words),)

        best_cost = float("inf")
        best_path: tuple[int, ...] = ()
        min_end = start_index + minimum_words[segment_index]
        max_end = len(words) - min_suffix[segment_index + 1]
        if max_end < min_end:
            max_end = min_end

        for end_index in range(min_end, max_end + 1):
            current_len = prefix_counts[end_index] - prefix_counts[start_index]
            next_cost, next_path = _best_cuts(segment_index + 1, end_index)
            if next_cost == float("inf"):
                continue
            current_cost = (current_len - target_words[segment_index]) ** 2 + next_cost
            if current_cost < best_cost:
                best_cost = current_cost
                best_path = (end_index,) + next_path

        return best_cost, best_path

    _cost, cut_points = _best_cuts(0, 0)
    if not cut_points:
        return [" ".join(words).strip()]

    segments: list[str] = []
    cursor = 0
    for end_index in cut_points:
        segments.append(" ".join(words[cursor:end_index]).strip())
        cursor = end_index
    return [segment for segment in segments if segment]


def _segment_time_budget_seconds(*, profile: Any, segment_index: int) -> int:
    if segment_index <= 0:
        return int(profile.veo_base_seconds or 8)
    return int(profile.veo_extension_seconds or 7)


def _build_time_windows_for_profile(
    *,
    profile: Any,
    segment_count: int,
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    start = 0.0
    for index in range(max(segment_count, 0)):
        budget = _segment_time_budget_seconds(profile=profile, segment_index=index)
        end = start + float(budget)
        windows.append(
            {
                "segment_index": index,
                "start_seconds": round(start, 1),
                "end_seconds": round(end, 1),
                "budget_seconds": budget,
            }
        )
        start = end
    return windows


def _resolve_veo_extension_hops_target(*, segment_count: int, planned_hops: int) -> int:
    if segment_count <= 0:
        return 0
    return max(int(planned_hops or 0), 0)


def _validate_veo_extension_segment_budget(
    *,
    segment_count: int,
    planned_extension_hops: int,
    target_length_tier: Optional[int],
) -> None:
    required_segments = _required_veo_segments_for_profile_hops(planned_extension_hops)
    if segment_count >= required_segments:
        return
    raise ValidationError(
        "Veo extended chains need one complete dialogue segment per hop plus a base segment.",
        {
            "target_length_tier": target_length_tier,
            "planned_extension_hops": planned_extension_hops,
            "segments_available": segment_count,
            "segments_required": required_segments,
        },
    )


def _pack_veo_segments_for_profile(
    segments: list[str],
    *,
    planned_extension_hops: Optional[int],
    target_length_tier: Optional[int],
) -> list[str]:
    if not segments or planned_extension_hops is None or target_length_tier is None:
        return segments

    profile = get_duration_profile(target_length_tier)
    required_segments = _required_veo_segments_for_profile_hops(planned_extension_hops)
    if profile.route != VEO_EXTENDED_VIDEO_ROUTE or len(segments) < required_segments:
        return segments

    words: list[str] = []
    for segment in segments:
        words.extend(word for word in segment.split() if word)

    return _partition_words_for_profile(
        words,
        profile=profile,
        required_segments=required_segments,
    )


def _build_veo_extended_base_prompt(
    seed_data: Dict[str, Any],
    video_prompt: Optional[Dict[str, Any]] = None,
    *,
    planned_extension_hops: Optional[int] = None,
    target_length_tier: Optional[int] = None,
) -> tuple[str, Dict[str, Any]]:
    if isinstance(video_prompt, str):
        try:
            video_prompt = json.loads(video_prompt)
        except json.JSONDecodeError:
            video_prompt = {}
    if not isinstance(video_prompt, dict):
        video_prompt = {}

    prompt_character = str(video_prompt.get("character") or "").strip() or None
    prompt_style = str(video_prompt.get("style") or "").strip() or None
    prompt_action = str(video_prompt.get("action") or "").strip() or None
    prompt_scene = str(video_prompt.get("scene") or "").strip() or None
    prompt_cinematography = str(video_prompt.get("cinematography") or "").strip() or None
    prompt_ending = str(video_prompt.get("ending_directive") or "").strip() or None
    prompt_audio_block = str(video_prompt.get("audio_block") or "").strip() or None

    prompt_audio = video_prompt.get("audio") or {}
    if not isinstance(prompt_audio, dict):
        prompt_audio = {}

    script = str(
        prompt_audio.get("dialogue")
        or seed_data.get("script")
        or seed_data.get("dialog_script")
        or ""
    ).strip()
    segments = split_dialogue_sentences(script) if script else []
    if not segments and script:
        segments = [script]
    segments = _pack_veo_segments_for_profile(
        segments,
        planned_extension_hops=planned_extension_hops,
        target_length_tier=target_length_tier,
    )

    effective_hops: Optional[int] = None
    if planned_extension_hops is not None:
        _validate_veo_extension_segment_budget(
            segment_count=len(segments),
            planned_extension_hops=planned_extension_hops,
            target_length_tier=target_length_tier,
        )
        effective_hops = _resolve_veo_extension_hops_target(
            segment_count=len(segments),
            planned_hops=planned_extension_hops,
        )

    profile = get_duration_profile(target_length_tier) if target_length_tier is not None else None
    base_segment = segments[0] if segments else ""
    segment_metadata = {
        "veo_segments": segments,
        "veo_segments_total": len(segments),
        "veo_current_segment_index": 0,
        "veo_segment_time_windows": (
            _build_time_windows_for_profile(profile=profile, segment_count=len(segments))
            if profile is not None
            else []
        ),
    }
    if planned_extension_hops is not None:
        segment_metadata.update(
            {
                "veo_required_segments": _required_veo_segments_for_profile_hops(planned_extension_hops),
                "veo_planned_extension_hops_target": planned_extension_hops,
                "veo_extension_hops_target": effective_hops,
                "veo_chain_shortened_to_available_segments": False,
            }
        )
    return build_veo_prompt_segment(
        base_segment,
        include_quotes=False,
        include_ending=False,
        character=prompt_character,
        action=prompt_action,
        style=prompt_style,
        scene=prompt_scene,
        cinematography=prompt_cinematography,
        ending=prompt_ending,
        audio_block=prompt_audio_block,
        legacy_32_visuals=False,
    ), segment_metadata


@router.post("/{post_id}/generate", response_model=SuccessResponse)
async def generate_video(post_id: str, request: VideoGenerationRequest):
    """
    Submit video generation request for a post.
    Transitions post from S5_PROMPTS_BUILT to S6_QA (when complete).
    
    Per Canon § 3.2: S5_PROMPTS_BUILT → S6_QA
    Per Constitution § II: Schema-validate at edges
    Per Constitution § IX: Boundary logging with correlation IDs
    
    Args:
        post_id: UUID of the post
        request: Video generation parameters (provider, aspect_ratio, resolution)
        
    Returns:
        SuccessResponse with VideoGenerationResponse data
        
    Raises:
        HTTPException: If post not found or video_prompt_json missing
    """
    correlation_id = f"gen_video_{post_id}"
    quota_reservation_key: Optional[str] = None
    quota_reserved = False
    quota_consumed = False
    
    try:
        supabase = get_supabase().client
        
        # Fetch post with video_prompt_json
        response = supabase.table("posts").select("*").eq("id", post_id).execute()
        
        if not response.data:
            raise FlowForgeException(
                code=ErrorCode.NOT_FOUND,
                message=f"Post {post_id} not found",
                details={"post_id": post_id}
            )
        
        post = response.data[0]
        seed_data = _normalize_seed_data(post.get("seed_data"))

        if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
            raise ValidationError(
                "Removed posts cannot be submitted for video generation.",
                {"post_id": post_id}
            )

        video_prompt = _load_or_build_video_prompt(
            post=post,
            supabase_client=supabase,
            correlation_id=correlation_id,
        )
        
        provider = "vertex_ai"
        prompt_request = _build_provider_prompt_request(video_prompt, provider)

        requested_units = 0
        anchor_image_bundle = None
        veo_seed = None
        submission_result = _submit_video_request(
            provider=provider,
            prompt_text=prompt_request["prompt_text"] or "",
            negative_prompt=prompt_request.get("negative_prompt"),
            aspect_ratio=request.aspect_ratio,
            provider_aspect_ratio=request.aspect_ratio,
            requested_aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            seconds=request.seconds,
            size=request.size,
            correlation_id=correlation_id,
            provider_duration_seconds=None,
            first_frame_image=None,
            seed=veo_seed,
        )

        operation_id = submission_result["operation_id"]
        provider_model = submission_result.get("provider_model")
        requested_size = submission_result.get("requested_size")

        quota_consume_error = _consume_quota_after_acceptance(
            reservation_key=quota_reservation_key,
            operation_id=operation_id,
            units=1,
            correlation_id=correlation_id,
            provider=provider,
            post_id=post_id,
            batch_id=post.get("batch_id"),
        )
        if quota_reservation_key:
            quota_consumed = True

        record_prompt_audit(
            post_id=post_id,
            operation_id=operation_id,
            provider=provider,
            prompt_text=prompt_request["prompt_text"] or "",
            negative_prompt=prompt_request.get("negative_prompt"),
            prompt_path=prompt_request["prompt_path"],
            aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            requested_seconds=request.seconds,
            correlation_id=correlation_id,
            seed=veo_seed,
        )

        existing_metadata = post.get("video_metadata") or {}
        submission_metadata = {
            **existing_metadata,
            "requested_aspect_ratio": request.aspect_ratio,
            "requested_resolution": request.resolution,
            "requested_seconds": request.seconds,
            "requested_size": requested_size,
        }
        if veo_seed is not None:
            submission_metadata["veo_seed"] = veo_seed
        if quota_reservation_key:
            submission_metadata["quota_reservation_key"] = quota_reservation_key
            submission_metadata["quota_reserved_units"] = requested_units
        if quota_consume_error:
            submission_metadata["quota_consume_error"] = quota_consume_error
        if provider_model:
            submission_metadata["provider_model"] = provider_model
        if submission_result.get("provider_metadata"):
            submission_metadata["provider_metadata"] = submission_result["provider_metadata"]
        if anchor_image_bundle:
            submission_metadata.update(anchor_image_bundle["metadata"])

        # Normalize provider status to DB-compatible values
        provider_status = submission_result.get("status", "submitted")
        db_status = "submitted" if provider_status == "queued" else provider_status

        # CRITICAL: Log operation_id before DB update to enable recovery if update fails
        logger.warning(
            "video_operation_id_paid_request",
            post_id=post_id,
            operation_id=operation_id,
            provider=provider,
            correlation_id=correlation_id,
            message="PAID VIDEO SUBMITTED - Operation ID logged for recovery"
        )

        try:
            supabase.table("posts").update({
                "video_provider": provider,
                "video_format": request.aspect_ratio,
                "video_operation_id": operation_id,
                "video_status": db_status,
                "video_metadata": submission_metadata
            }).eq("id", post_id).execute()
        except Exception as db_error:
            logger.error(
                "video_db_update_failed_but_video_submitted",
                post_id=post_id,
                operation_id=operation_id,
                provider=provider,
                correlation_id=correlation_id,
                error=str(db_error),
                message="DATABASE UPDATE FAILED - Video is still processing at provider. Use operation_id to recover."
            )
            # Write to fallback recovery file
            _write_recovery_record(post_id, operation_id, provider, correlation_id)
            raise

        logger.info(
            "video_generation_submitted",
            post_id=post_id,
            correlation_id=correlation_id,
            provider=provider,
            provider_model=provider_model,
            aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            seconds=request.seconds,
            size=requested_size,
            operation_id=operation_id
        )

        return SuccessResponse(
            data=VideoGenerationResponse(
                post_id=post_id,
                operation_id=operation_id,
                provider=provider,
                provider_model=provider_model,
                status=submission_result.get("status", "submitted"),
                estimated_duration_seconds=submission_result.get("estimated_duration_seconds"),
                aspect_ratio=request.aspect_ratio,
                resolution=request.resolution
            ).model_dump()
        )
    
    except FlowForgeException as exc:
        if quota_reservation_key and quota_reserved and not quota_consumed:
            release_quota(
                reservation_key=quota_reservation_key,
                reason=exc.message,
                final_status="released",
                error_code=str(exc.code),
            )
        if (
            quota_reservation_key
            and provider == VEO_PROVIDER
            and exc.status_code == 429
            and not exc.details.get("blocked_before_submit")
        ):
            maybe_freeze_after_provider_429(provider=VEO_PROVIDER, reason=exc.message)
        raise
    except HTTPException:
        raise
    except Exception as e:
        if quota_reservation_key and quota_reserved and not quota_consumed:
            release_quota(
                reservation_key=quota_reservation_key,
                reason=str(e),
                final_status="released",
                error_code="unexpected_error",
            )
        logger.exception(
            "video_generation_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit video generation"
        )


@router.get("/{post_id}/status", response_model=SuccessResponse)
async def get_video_status(post_id: str):
    """
    Check video generation status for a post.
    Used by UI polling to track progress.
    
    Per Constitution § IX: Observable Implementation
    
    Args:
        post_id: UUID of the post
        
    Returns:
        SuccessResponse with VideoStatusResponse data
        
    Raises:
        HTTPException: If post not found
    """
    correlation_id = f"status_{post_id}"
    
    try:
        supabase = get_supabase().client
        response = supabase.table("posts").select("*").eq("id", post_id).execute()
        
        if not response.data:
            raise FlowForgeException(
                code="not_found",
                message=f"Post {post_id} not found",
                details={"post_id": post_id}
            )
        
        post = response.data[0]
        
        logger.info(
            "video_status_checked",
            post_id=post_id,
            correlation_id=correlation_id,
            status=post.get("video_status", "pending")
        )
        
        return SuccessResponse(
            data=VideoStatusResponse(
                post_id=post_id,
                operation_id=post.get("video_operation_id"),
                status=post.get("video_status", "pending"),
                video_url=post.get("video_url"),
                metadata=post.get("video_metadata")
            ).model_dump()
        )
    
    except FlowForgeException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "video_status_check_failed",
            post_id=post_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check video status"
        )


@router.post("/batch/{batch_id}/generate-all", response_model=SuccessResponse)
async def generate_all_videos(batch_id: str, request: BatchVideoGenerationRequest):
    """
    Generate videos for all posts in a batch.
    Submits all posts with video_prompt_json to video generation provider.
    
    Per Constitution § XIII: Idempotent operations
    Per Constitution § IX: Structured logging
    
    Args:
        batch_id: UUID of the batch
        request: Batch video generation parameters (provider, aspect_ratio, resolution)
        
    Returns:
        SuccessResponse with BatchVideoGenerationResponse data
        
    Raises:
        HTTPException: If batch not found or submission fails
    """
    correlation_id = f"gen_all_{batch_id}"
    
    try:
        supabase = get_supabase().client
        
        # Fetch all posts in batch with video_prompt_json
        response = supabase.table("posts").select("*").eq("batch_id", batch_id).execute()
        posts = response.data
        batch = get_batch_by_id(batch_id)

        if not posts:
            raise FlowForgeException(
                code=ErrorCode.NOT_FOUND,
                message=f"No posts found for batch {batch_id}",
                details={"batch_id": batch_id}
            )
        
        batch_profile = get_duration_profile(batch.get("target_length_tier")) if uses_duration_routing(batch) else None
        batch_uses_efficient_long_route = _uses_actual_efficient_long_route(batch_profile)

        submitted_count = 0
        skipped_count = 0
        submitted_post_ids = []
        prepared_submissions = []
        last_provider_model: Optional[str] = None
        batch_veo_seed = (
            random.randint(0, 2**32 - 1)
            if _should_assign_veo_seed(provider=request.provider, profile=batch_profile)
            else None
        )

        for post in posts:
            post_id = post["id"]
            seed_data = _normalize_seed_data(post.get("seed_data"))
            
            # Skip posts without prompts or already submitted
            if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
                logger.info(
                    "post_skipped_removed_from_batch",
                    post_id=post_id,
                    batch_id=batch_id
                )
                skipped_count += 1
                continue

            if post.get("video_status") in ["submitted", "processing", "completed", "extended_submitted", "extended_processing"]:
                logger.info(
                    "post_skipped_already_submitted",
                    post_id=post_id,
                    batch_id=batch_id,
                    status=post.get("video_status")
                )
                skipped_count += 1
                continue

            submission_plan = _resolve_video_submission_plan(
                batch=batch,
                requested_provider=request.provider,
                requested_seconds=request.seconds,
                aspect_ratio=request.aspect_ratio,
                resolution=request.resolution,
                size=request.size,
            )

            profile = submission_plan.get("profile")
            is_extended = profile is not None and profile.route == VEO_EXTENDED_VIDEO_ROUTE

            try:
                video_prompt = _load_or_build_video_prompt(
                    post=post,
                    supabase_client=supabase,
                    correlation_id=f"{correlation_id}_{post_id}",
                )
            except FlowForgeException as exc:
                logger.warning(
                    "post_skipped_no_prompt",
                    post_id=post_id,
                    batch_id=batch_id,
                    error=exc.message,
                )
                skipped_count += 1
                continue

            if is_extended:
                prompt_text, segment_metadata = _build_veo_extended_base_prompt(
                    seed_data,
                    video_prompt,
                    planned_extension_hops=profile.veo_extension_hops,
                    target_length_tier=profile.target_length_tier,
                )
                negative_prompt = _build_veo_negative_prompt(video_prompt)
            else:
                prompt_request = _build_provider_prompt_request(video_prompt, submission_plan["provider"])
                prompt_text = prompt_request["prompt_text"] or ""
                negative_prompt = prompt_request.get("negative_prompt")
                segment_metadata = None

            prepared_submissions.append(
                {
                    "post": post,
                    "post_id": post_id,
                    "seed_data": seed_data,
                    "submission_plan": submission_plan,
                    "profile": profile,
                    "is_extended": is_extended,
                    "prompt_text": prompt_text,
                    "negative_prompt": negative_prompt,
                    "segment_metadata": segment_metadata,
                    "quota_requested_units": chain_cost_units(profile, provider=submission_plan["provider"]),
                }
            )

        veo_submissions = [item for item in prepared_submissions if item["submission_plan"]["provider"] == VEO_PROVIDER]
        reserved_keys = []
        if veo_submissions and not quota_controls_bypassed():
            ensure_immediate_submit_slot(requested_units=len(veo_submissions), provider=VEO_PROVIDER)
            try:
                for item in veo_submissions:
                    reservation_key = build_reservation_key(
                        provider=VEO_PROVIDER,
                        post_id=item["post_id"],
                        correlation_id=correlation_id,
                    )
                    reserve_quota(
                        provider=VEO_PROVIDER,
                        post_id=item["post_id"],
                        batch_id=batch_id,
                        reservation_key=reservation_key,
                        requested_units=item["quota_requested_units"],
                        require_immediate_slot=False,
                    )
                    item["quota_reservation_key"] = reservation_key
                    reserved_keys.append(reservation_key)
            except FlowForgeException:
                for reservation_key in reserved_keys:
                    release_quota(
                        reservation_key=reservation_key,
                        reason="Batch preflight failed before any Veo submission.",
                        final_status="released",
                        error_code="batch_preflight_aborted",
                    )
                raise

        anchor_image_bundle = _resolve_global_veo_anchor_image(correlation_id) if _GLOBAL_VEO_ANCHOR_ENABLED and veo_submissions else None
        for index, item in enumerate(prepared_submissions):
            post = item["post"]
            post_id = item["post_id"]
            submission_plan = item["submission_plan"]
            profile = item["profile"]
            is_extended = item["is_extended"]
            prompt_text = item["prompt_text"]
            negative_prompt = item["negative_prompt"]
            segment_metadata = item["segment_metadata"]
            quota_reservation_key = item.get("quota_reservation_key")
            quota_consumed = False

            try:
                submission_result = _submit_video_request(
                    provider=submission_plan["provider"],
                    prompt_text=prompt_text,
                    negative_prompt=negative_prompt,
                    aspect_ratio=submission_plan["aspect_ratio"],
                    provider_aspect_ratio=submission_plan.get("provider_aspect_ratio"),
                    requested_aspect_ratio=submission_plan.get("requested_aspect_ratio"),
                    resolution=submission_plan["resolution"],
                    seconds=submission_plan["seconds"],
                    size=submission_plan["size"],
                    correlation_id=f"{correlation_id}_{post_id}",
                    provider_duration_seconds=(
                        profile.veo_base_seconds
                        if is_extended and profile is not None
                        else submission_plan["provider_target_seconds"]
                        if submission_plan["provider"] in {VEO_PROVIDER, "vertex_ai"}
                        else None
                    ),
                    first_frame_image=(
                        anchor_image_bundle["first_frame_image"]
                        if submission_plan["provider"] == VEO_PROVIDER and anchor_image_bundle
                        else None
                    ),
                    seed=batch_veo_seed,
                )
                operation_id = submission_result["operation_id"]
                provider_model = submission_result.get("provider_model")

                quota_consume_error = _consume_quota_after_acceptance(
                    reservation_key=quota_reservation_key,
                    operation_id=operation_id,
                    units=1,
                    correlation_id=correlation_id,
                    provider=submission_plan["provider"],
                    post_id=post_id,
                    batch_id=batch_id,
                )
                if quota_reservation_key:
                    quota_consumed = True

                record_prompt_audit(
                    post_id=post_id,
                    operation_id=operation_id,
                    provider=submission_plan["provider"],
                    prompt_text=prompt_text,
                    negative_prompt=negative_prompt,
                    prompt_path="veo_extended_segment" if is_extended else "batch_standard",
                    aspect_ratio=submission_plan["aspect_ratio"],
                    resolution=submission_plan["resolution"],
                    requested_seconds=submission_plan["seconds"],
                    correlation_id=f"{correlation_id}_{post_id}",
                    batch_id=batch_id,
                    seed=batch_veo_seed,
                )

                existing_metadata = post.get("video_metadata") or {}
                submission_metadata = _build_submission_metadata(
                    existing_metadata=existing_metadata,
                    submission_plan=submission_plan,
                    submission_result=submission_result,
                    segment_metadata=segment_metadata,
                )
                if batch_veo_seed is not None:
                    submission_metadata["veo_seed"] = batch_veo_seed
                if quota_reservation_key:
                    submission_metadata["quota_reservation_key"] = quota_reservation_key
                    submission_metadata["quota_reserved_units"] = item["quota_requested_units"]
                if quota_consume_error:
                    submission_metadata["quota_consume_error"] = quota_consume_error
                if submission_plan.get("profile_config"):
                    submission_metadata["veo_efficient_long_route_enabled"] = batch_uses_efficient_long_route
                if submission_plan["provider"] == VEO_PROVIDER and anchor_image_bundle:
                    submission_metadata.update(anchor_image_bundle["metadata"])

                route = profile.route if profile else None
                provider_status = submission_result.get("status", "submitted")
                db_status = get_submission_video_status(route, provider_status)

                logger.warning(
                    "video_operation_id_paid_request",
                    post_id=post_id,
                    operation_id=operation_id,
                    provider=submission_plan["provider"],
                    correlation_id=correlation_id,
                    message="PAID VIDEO SUBMITTED - Operation ID logged for recovery"
                )

                try:
                    supabase.table("posts").update({
                        "video_provider": submission_plan["provider"],
                        "video_format": submission_plan["aspect_ratio"],
                        "video_operation_id": operation_id,
                        "video_status": db_status,
                        "video_metadata": submission_metadata
                    }).eq("id", post_id).execute()
                except Exception as db_error:
                    logger.error(
                        "batch_video_db_update_failed_but_video_submitted",
                        post_id=post_id,
                        operation_id=operation_id,
                        provider=submission_plan["provider"],
                        batch_id=batch_id,
                        correlation_id=correlation_id,
                        error=str(db_error),
                        message="DATABASE UPDATE FAILED - Video is still processing at provider."
                    )
                    _write_recovery_record(post_id, operation_id, submission_plan["provider"], correlation_id)
                    skipped_count += 1
                    continue

                submitted_count += 1
                submitted_post_ids.append(post_id)
                if provider_model:
                    last_provider_model = provider_model

                logger.info(
                    "batch_video_submitted",
                    post_id=post_id,
                    batch_id=batch_id,
                    provider=submission_plan["provider"],
                    provider_model=provider_model,
                    seconds=submission_plan["seconds"],
                    size=submission_plan["size"],
                    operation_id=operation_id,
                    duration_routed=submission_plan["duration_routed"],
                )

            except FlowForgeException as exc:
                if quota_reservation_key and not quota_consumed:
                    release_quota(
                        reservation_key=quota_reservation_key,
                        reason=exc.message,
                        final_status="released",
                        error_code=str(exc.code),
                    )
                logger.warning(
                    "batch_video_submission_skipped",
                    post_id=post_id,
                    batch_id=batch_id,
                    code=exc.code,
                    message=exc.message,
                    details=exc.details
                )
                skipped_count += 1
                if (
                    submission_plan["provider"] == VEO_PROVIDER
                    and exc.status_code == 429
                    and not exc.details.get("blocked_before_submit")
                ):
                    maybe_freeze_after_provider_429(provider=VEO_PROVIDER, reason=exc.message)
                    for pending in prepared_submissions[index + 1:]:
                        pending_key = pending.get("quota_reservation_key")
                        if pending_key:
                            release_quota(
                                reservation_key=pending_key,
                                reason="Batch stopped after provider quota drift.",
                                final_status="released",
                                error_code="batch_provider_quota_drift",
                            )
                    break
            except Exception as e:
                if quota_reservation_key and not quota_consumed:
                    release_quota(
                        reservation_key=quota_reservation_key,
                        reason=str(e),
                        final_status="released",
                        error_code="unexpected_error",
                    )
                logger.exception(
                    "batch_video_submission_failed",
                    post_id=post_id,
                    batch_id=batch_id,
                    error=str(e)
                )
                skipped_count += 1
        
        logger.info(
            "batch_videos_submitted",
            batch_id=batch_id,
            correlation_id=correlation_id,
            submitted_count=submitted_count,
            skipped_count=skipped_count,
            provider=request.provider
        )

        reconcile_batch_video_pipeline_state(
            batch_id=batch_id,
            correlation_id=correlation_id,
            supabase_client=supabase,
        )
        
        return SuccessResponse(
            data=BatchVideoGenerationResponse(
                batch_id=batch_id,
                submitted_count=submitted_count,
                skipped_count=skipped_count,
                provider=request.provider,
                aspect_ratio=request.aspect_ratio,
                resolution=request.resolution,
                post_ids=submitted_post_ids,
                provider_model=last_provider_model,
                seconds=request.seconds,
                size=request.size or _map_size_from_aspect_ratio(request.aspect_ratio, request.resolution)
            ).model_dump()
        )
    
    except FlowForgeException:
        raise
    except Exception as e:
        logger.exception(
            "batch_video_generation_failed",
            batch_id=batch_id,
            correlation_id=correlation_id,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate batch videos"
        )


@router.post("/vertex", response_model=SuccessResponse)
async def generate_vertex_video(request: VertexVideoGenerationRequest):
    """Submit an explicit Vertex AI video generation request."""
    correlation_id = f"vertex_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    try:
        vertex_client = get_vertex_ai_client()
        if request.mode == "image":
            if not request.image_base64 or not request.image_mime_type:
                raise ValidationError(
                    "Vertex image mode requires image_base64 and image_mime_type.",
                    {
                        "mode": request.mode,
                        "image_base64_present": bool(request.image_base64),
                        "image_mime_type_present": bool(request.image_mime_type),
                    },
                )
            try:
                image_bytes = base64.b64decode(request.image_base64, validate=True)
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(
                    "Vertex image payload is not valid base64.",
                    {"error": str(exc)},
                ) from exc
            if not image_bytes:
                raise ValidationError(
                    "Vertex image payload is empty.",
                    {"mode": request.mode},
                )
            submission = vertex_client.submit_image_video(
                prompt=request.prompt,
                image_bytes=image_bytes,
                mime_type=request.image_mime_type,
                correlation_id=correlation_id,
                aspect_ratio=request.aspect_ratio,
                duration_seconds=request.duration_seconds,
                output_gcs_uri=request.output_gcs_uri,
                model=request.model,
                use_fast_model=request.use_fast_model,
            )
        else:
            submission = vertex_client.submit_text_video(
                prompt=request.prompt,
                correlation_id=correlation_id,
                aspect_ratio=request.aspect_ratio,
                duration_seconds=request.duration_seconds,
                output_gcs_uri=request.output_gcs_uri,
                model=request.model,
                use_fast_model=request.use_fast_model,
            )

        response = VertexVideoGenerationResponse(
            provider="vertex_ai",
            operation_id=submission["operation_id"],
            status=submission.get("status", "submitted"),
            done=bool(submission.get("done", False)),
            provider_model=submission.get("provider_model"),
            video_uri=submission.get("video_uri"),
        )
        return SuccessResponse(data=response.model_dump())
    except FlowForgeException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "vertex_video_submission_failed",
            correlation_id=correlation_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit Vertex video",
        )


def _build_veo_prompt_text(video_prompt: Dict[str, Any]) -> tuple[str, str]:
    """Build VEO-compatible prompt text from video_prompt_json. Returns (text, path)."""
    veo_prompt = video_prompt.get("veo_prompt")
    if veo_prompt:
        logger.debug(
            "veo_prompt_selected",
            prompt_length=len(veo_prompt)
        )
        return veo_prompt, "veo_prompt"

    optimized_prompt = video_prompt.get("optimized_prompt")
    if optimized_prompt:
        logger.debug(
            "veo_optimized_prompt_fallback_selected",
            prompt_length=len(optimized_prompt)
        )
        return optimized_prompt, "optimized_prompt"
    return build_full_prompt_text(video_prompt), "full_prompt_text_fallback"


def _build_veo_negative_prompt(video_prompt: Dict[str, Any]) -> Optional[str]:
    """Build VEO negativePrompt text from stored prompt metadata."""
    negative_prompt = video_prompt.get("veo_negative_prompt")
    if negative_prompt:
        logger.debug(
            "veo_negative_prompt_selected",
            prompt_length=len(negative_prompt)
        )
        return negative_prompt
    return None


def _write_recovery_record(post_id: str, operation_id: str, provider: str, correlation_id: str) -> None:
    """Write paid video operation_id to recovery file when DB update fails."""
    recovery_dir = "recovery_logs"
    os.makedirs(recovery_dir, exist_ok=True)
    
    timestamp = datetime.utcnow().isoformat()
    recovery_file = os.path.join(recovery_dir, f"video_recovery_{datetime.utcnow().strftime('%Y%m%d')}.jsonl")
    
    record = {
        "timestamp": timestamp,
        "post_id": post_id,
        "operation_id": operation_id,
        "provider": provider,
        "correlation_id": correlation_id,
        "status": "db_update_failed",
        "message": "Video submitted to provider but database update failed. Video is processing and can be recovered."
    }
    
    try:
        with open(recovery_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        logger.info(
            "recovery_record_written",
            post_id=post_id,
            operation_id=operation_id,
            recovery_file=recovery_file
        )
    except Exception as e:
        logger.error(
            "recovery_record_write_failed",
            post_id=post_id,
            operation_id=operation_id,
            error=str(e)
        )


def _consume_quota_after_acceptance(
    *,
    reservation_key: Optional[str],
    operation_id: str,
    units: int,
    correlation_id: str,
    provider: str,
    post_id: Optional[str] = None,
    batch_id: Optional[str] = None,
) -> Optional[str]:
    """Best-effort ledger consume after Veo has already accepted a paid request."""
    if not reservation_key:
        return None
    try:
        consume_quota(reservation_key=reservation_key, operation_id=operation_id, units=units)
        return None
    except FlowForgeException as exc:
        logger.error(
            "quota_consume_failed_after_provider_acceptance",
            post_id=post_id,
            batch_id=batch_id,
            provider=provider,
            correlation_id=correlation_id,
            reservation_key=reservation_key,
            operation_id=operation_id,
            code=str(exc.code),
            message=exc.message,
            details=exc.details,
        )
        return exc.message


def _build_provider_prompt_text(video_prompt: Dict[str, Any], provider: str) -> tuple[str, str]:
    """Build provider-specific prompt text. Returns (text, path)."""
    if provider == "veo_3_1":
        return _build_veo_prompt_text(video_prompt)

    # Fallback to canonical composition
    return build_full_prompt_text(video_prompt), "full_prompt_text_fallback"


def _build_provider_prompt_request(video_prompt: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """Build provider-specific prompt payload pieces."""
    prompt_text, prompt_path = _build_provider_prompt_text(video_prompt, provider)
    negative_prompt = _build_veo_negative_prompt(video_prompt) if provider == "veo_3_1" else None
    return {
        "prompt_text": prompt_text,
        "negative_prompt": negative_prompt,
        "prompt_path": prompt_path,
    }


def _submit_video_request(
    *,
    provider: str,
    prompt_text: str,
    negative_prompt: Optional[str],
    aspect_ratio: str,
    provider_aspect_ratio: Optional[str],
    requested_aspect_ratio: Optional[str],
    resolution: str,
    seconds: int,
    size: Optional[str],
    correlation_id: str,
    provider_duration_seconds: Optional[int] = None,
    first_frame_image: Optional[Dict[str, str]] = None,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Submit a video generation request to the selected provider."""

    if provider == "veo_3_1":
        veo_client = get_veo_client()
        provider_aspect = provider_aspect_ratio or aspect_ratio
        requested_aspect = requested_aspect_ratio or aspect_ratio
        veo_duration_seconds = provider_duration_seconds or seconds
        if veo_duration_seconds not in {4, 6, 8}:
            veo_duration_seconds = 8
        try:
            result = veo_client.submit_video_generation(
                prompt=prompt_text,
                negative_prompt=negative_prompt,
                correlation_id=correlation_id,
                aspect_ratio=provider_aspect,
                resolution=resolution,
                duration_seconds=veo_duration_seconds,
                first_frame_image=first_frame_image,
                seed=seed,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            try:
                error_payload = exc.response.json()
            except ValueError:
                error_payload = {"body": exc.response.text[:500]}

            error_message = error_payload.get("error", {}).get("message") if isinstance(error_payload, dict) else None
            if status_code == 429:
                raise FlowForgeException(
                    code=ErrorCode.RATE_LIMIT,
                    message=error_message or "VEO quota exhausted",
                    details={
                        "provider": provider,
                        "status_code": status_code,
                        "response": error_payload,
                    },
                    status_code=429,
                ) from exc

            raise FlowForgeException(
                code=ErrorCode.THIRD_PARTY_FAIL,
                message=error_message or "VEO video submission failed",
                details={
                    "provider": provider,
                    "status_code": status_code,
                    "response": error_payload,
                },
                status_code=503,
            ) from exc
        requested_size = _map_size_from_aspect_ratio(requested_aspect, resolution)
        provider_requested_size = _map_size_from_aspect_ratio(provider_aspect, resolution)
        return {
            "operation_id": result["operation_id"],
            "status": result.get("status", "submitted"),
            "provider_model": "veo-3.1",
            "requested_size": requested_size,
            "provider_requested_size": provider_requested_size,
            "estimated_duration_seconds": 180,
            "provider_metadata": result,
        }

    if provider == "vertex_ai":
        vertex_client = get_vertex_ai_client()
        settings = get_settings()
        image_path = Path(__file__).resolve().parents[2] / "static" / "images" / "sarah.jpg"
        vertex_duration = provider_duration_seconds or seconds
        output_gcs_uri = settings.vertex_ai_output_gcs_uri or None
        try:
            if image_path.exists():
                image_bytes = image_path.read_bytes()
                result = vertex_client.submit_image_video(
                    prompt=prompt_text,
                    image_bytes=image_bytes,
                    mime_type="image/jpeg",
                    correlation_id=correlation_id,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=vertex_duration,
                    output_gcs_uri=output_gcs_uri,
                )
            else:
                result = vertex_client.submit_text_video(
                    prompt=prompt_text,
                    correlation_id=correlation_id,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=vertex_duration,
                    output_gcs_uri=output_gcs_uri,
                )
        except ValidationError as exc:
            raise FlowForgeException(
                code=ErrorCode.THIRD_PARTY_FAIL,
                message=exc.message,
                details={
                    "provider": provider,
                    "response": exc.details,
                },
                status_code=503,
            ) from exc
        requested_size = _map_size_from_aspect_ratio(aspect_ratio, resolution)
        return {
            "operation_id": result["operation_id"],
            "status": result.get("status", "submitted"),
            "provider_model": result.get("provider_model", "vertex_ai"),
            "requested_size": requested_size,
            "provider_requested_size": requested_size,
            "estimated_duration_seconds": 180,
            "provider_metadata": result,
            "vertex_output_gcs_uri": output_gcs_uri,
        }

    raise FlowForgeException(
        code=ErrorCode.VALIDATION_ERROR,
        message="Unsupported video provider",
        details={"provider": provider},
    )


def _map_size_from_aspect_ratio(aspect_ratio: str, resolution: str) -> Optional[str]:
    """Map canonical aspect ratio + resolution pairs to pixel sizes (VEO defaults)."""
    mapping = {
        ("9:16", "720p"): "720x1280",
        ("9:16", "1080p"): "1080x1920",
        ("16:9", "720p"): "1280x720",
        ("16:9", "1080p"): "1920x1080",
    }
    return mapping.get((aspect_ratio, resolution))

