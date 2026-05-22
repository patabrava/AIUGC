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
from urllib.parse import urlparse

from pydantic import ValidationError as PydanticValidationError
import httpx

from app.adapters.supabase_client import get_supabase
from app.adapters.veo_client import get_veo_client, select_veo_model_id
from app.adapters.vertex_ai_client import get_vertex_ai_client
from app.core.config import get_settings
from app.core.errors import FlowForgeException, SuccessResponse, ValidationError, ErrorCode
from app.core.logging import get_logger
from app.core.video_profiles import (
    DEFAULT_TARGET_LENGTH_TIER,
    SUPPORTED_TARGET_LENGTH_TIERS,
    VEO_EXTENDED_VIDEO_ROUTE,
    VEO_PROVIDER,
    get_duration_profile,
    get_duration_profile_for_creation_mode,
    get_profile_route_config,
    get_submission_video_status,
    script_word_count,
    uses_duration_routing,
)
from app.features.batches.queries import get_batch_by_id
from app.features.batches.state_machine import reconcile_batch_video_pipeline_state
from app.features.characters.actor_identity import (
    ensure_video_scene_reference_set_ready,
    is_character_consistency_light_mode,
    is_character_consistency_mode,
)
from app.features.characters import queries as character_queries
from app.features.characters.schemas import SceneReferenceSetSummary
from app.features.posts.prompt_text import build_full_prompt_text
from app.features.posts.prompt_builder import (
    DEFAULT_CHARACTER,
    DEFAULT_CINEMATOGRAPHY,
    DEFAULT_STYLE,
    LEGACY_32_CINEMATOGRAPHY,
    LEGACY_32_STYLE,
    LEGACY_SHORT_CHARACTER,
    build_video_prompt_from_seed,
    build_negative_prompt,
    ensure_scene_plan,
    build_lean_veo_base_prompt,
    build_veo_prompt_segment,
    split_dialogue_sentences,
    sync_video_prompt_with_seed_data,
    validate_video_prompt,
)
from app.features.topics.topic_validation import (
    resolve_effective_script_text,
    validate_script_duration_contract,
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


def _resolve_non_duration_provider(requested_provider: Optional[str]) -> str:
    if requested_provider in {None, VEO_PROVIDER, "vertex_ai"}:
        return "vertex_ai"
    return requested_provider


def _configured_veo_reference_image_paths(settings: Any) -> list[str]:
    raw_paths = str(getattr(settings, "veo_reference_image_paths", "") or "")
    return [path.strip() for path in raw_paths.split(",") if path.strip()]


def _load_global_veo_reference_assets(
    *,
    correlation_id: str,
    strict: bool,
) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    if not bool(getattr(settings, "veo_use_reference_images", False)):
        return None

    configured_paths = _configured_veo_reference_image_paths(settings)
    if not configured_paths:
        if strict:
            raise ValidationError(
                "Veo reference images are enabled but no image paths are configured.",
                {"reference_image_paths": configured_paths},
            )
        logger.warning("veo_reference_images_missing_paths", correlation_id=correlation_id)
        return None

    if len(configured_paths) > 3:
        raise ValidationError(
            "Veo reference image generation supports at most three subject images.",
            {"reference_image_count": len(configured_paths), "reference_image_paths": configured_paths},
        )

    root_dir = Path(__file__).resolve().parents[3]
    reference_images: list[Dict[str, str]] = []
    metadata_items: list[Dict[str, Any]] = []

    for configured_path in configured_paths:
        image_path = Path(configured_path)
        if not image_path.is_absolute():
            image_path = root_dir / configured_path

        if not image_path.exists():
            if strict:
                raise ValidationError(
                    "Configured Veo reference image is missing.",
                    {"reference_image_path": configured_path},
                )
            logger.warning(
                "veo_reference_image_missing_text_only_fallback",
                correlation_id=correlation_id,
                reference_image_path=configured_path,
            )
            return None

        try:
            image_bytes = image_path.read_bytes()
        except OSError as exc:
            if strict:
                raise ValidationError(
                    "Configured Veo reference image could not be read.",
                    {"reference_image_path": configured_path, "error": str(exc)},
                ) from exc
            logger.warning(
                "veo_reference_image_unreadable_text_only_fallback",
                correlation_id=correlation_id,
                reference_image_path=configured_path,
                error=str(exc),
            )
            return None

        if not image_bytes:
            if strict:
                raise ValidationError(
                    "Configured Veo reference image is empty.",
                    {"reference_image_path": configured_path},
                )
            logger.warning(
                "veo_reference_image_empty_text_only_fallback",
                correlation_id=correlation_id,
                reference_image_path=configured_path,
            )
            return None

        mime_type = mimetypes.guess_type(image_path.name)[0] or ""
        if mime_type not in {"image/png", "image/jpeg"}:
            raise ValidationError(
                "Configured Veo reference image must be PNG or JPEG.",
                {"reference_image_path": configured_path, "mime_type": mime_type},
            )

        reference_images.append(
            {
                "mime_type": mime_type,
                "data_base64": base64.b64encode(image_bytes).decode("ascii"),
            }
        )
        metadata_items.append(
            {
                "path": configured_path,
                "mime_type": mime_type,
                "size_bytes": len(image_bytes),
            }
        )

    return {
        "reference_images": reference_images,
        "metadata": {
            "reference_images_enabled": True,
            "reference_image_count": len(reference_images),
            "reference_image_assets": metadata_items,
        },
    }


def _download_image_bytes(url: str) -> bytes:
    response = httpx.get(
        url,
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=None),
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.content


def _load_character_snapshot_assets(
    *,
    snapshot: Optional[Dict[str, Any]],
    correlation_id: str,
) -> Optional[Dict[str, Any]]:
    if not snapshot:
        return None

    urls = [
        snapshot["front_image_url"],
        snapshot["three_quarter_image_url"],
        snapshot["profile_image_url"],
    ]
    reference_images: list[Dict[str, str]] = []
    for url in urls:
        mime_type = mimetypes.guess_type(urlparse(url).path)[0] or "image/png"
        if mime_type not in {"image/png", "image/jpeg"}:
            mime_type = "image/png"
        reference_images.append(
            {
                "mime_type": mime_type,
                "data_base64": base64.b64encode(_download_image_bytes(url)).decode("ascii"),
            }
        )

    logger.info(
        "veo_character_snapshot_loaded",
        correlation_id=correlation_id,
        character_id=snapshot.get("character_id"),
        image_count=len(reference_images),
    )
    return {
        "reference_images": reference_images,
        "metadata": {
            "reference_images_enabled": True,
            "reference_image_count": len(reference_images),
            "character_id": snapshot.get("character_id"),
            "character_name": snapshot.get("name"),
            "source": "batch_character_snapshot",
        },
    }


def _load_scene_reference_asset(
    *,
    scene_reference: Optional[Dict[str, Any]],
    correlation_id: str,
) -> Optional[Dict[str, Any]]:
    if not scene_reference:
        return None
    image_url = scene_reference.get("image_url")
    if not image_url:
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Approved SceneReferenceImage is missing an image URL.",
            details={"scene_reference_image_id": scene_reference.get("id")},
            status_code=422,
        )
    mime_type = mimetypes.guess_type(urlparse(str(image_url)).path)[0] or "image/png"
    if mime_type not in {"image/png", "image/jpeg"}:
        mime_type = "image/png"
    reference_image = {
        "mime_type": mime_type,
        "data_base64": base64.b64encode(_download_image_bytes(str(image_url))).decode("ascii"),
    }
    logger.info(
        "actor_scene_reference_loaded",
        correlation_id=correlation_id,
        scene_reference_image_id=scene_reference.get("id"),
        actor_identity_id=scene_reference.get("actor_identity_id"),
    )
    return {
        "reference_images": [reference_image],
        "metadata": {
            "reference_images_enabled": True,
            "reference_image_count": 1,
            "actor_identity_id": scene_reference.get("actor_identity_id"),
            "scene_reference_image_id": scene_reference.get("id"),
            "scene_key": scene_reference.get("scene_key"),
            "wardrobe_key": scene_reference.get("wardrobe_key"),
            "still_identity_gate_result": scene_reference.get("identity_gate_result"),
            "source": "actor_identity_scene_reference",
        },
    }


def _load_scene_reference_set_assets(
    *,
    scene_reference_set: Optional[SceneReferenceSetSummary],
    correlation_id: str,
) -> Optional[Dict[str, Any]]:
    if not scene_reference_set:
        return None
    if not scene_reference_set.is_ready:
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="ActorIdentity video generation requires three approved SceneReferenceImages before submit.",
            details={
                "reference_set_id": scene_reference_set.reference_set_id,
                "missing_angle_keys": scene_reference_set.missing_angle_keys,
            },
            status_code=422,
        )
    reference_images: list[Dict[str, str]] = []
    scene_reference_ids: list[str] = []
    angle_keys: list[str] = []
    for row in scene_reference_set.approved_rows:
        image_url = row.get("image_url")
        if not image_url:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Approved SceneReferenceImage is missing an image URL.",
                details={"scene_reference_image_id": row.get("id")},
                status_code=422,
            )
        mime_type = mimetypes.guess_type(urlparse(str(image_url)).path)[0] or "image/png"
        if mime_type not in {"image/png", "image/jpeg"}:
            mime_type = "image/png"
        metadata = row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}
        reference_images.append(
            {
                "mime_type": mime_type,
                "data_base64": base64.b64encode(_download_image_bytes(str(image_url))).decode("ascii"),
            }
        )
        scene_reference_ids.append(str(row.get("id") or ""))
        angle_keys.append(str(metadata.get("angle_key") or ""))

    logger.info(
        "actor_scene_reference_set_loaded",
        correlation_id=correlation_id,
        reference_set_id=scene_reference_set.reference_set_id,
        reference_image_count=len(reference_images),
    )
    first_row = scene_reference_set.approved_rows[0] if scene_reference_set.approved_rows else {}
    return {
        "reference_images": reference_images,
        "metadata": {
            "reference_images_enabled": True,
            "reference_image_count": len(reference_images),
            "actor_identity_id": first_row.get("actor_identity_id"),
            "scene_reference_set_id": scene_reference_set.reference_set_id,
            "scene_reference_image_ids": scene_reference_ids,
            "scene_reference_angle_keys": angle_keys,
            "scene_key": first_row.get("scene_key"),
            "wardrobe_key": first_row.get("wardrobe_key"),
            "source": "actor_identity_scene_reference_set",
        },
    }


def _resolve_extended_provider_aspect_ratio(route: Optional[str], requested_aspect_ratio: str) -> str:
    """Extended runs keep the requested aspect ratio when the REST path is used."""
    return requested_aspect_ratio


def _is_manual_video_post(batch: Dict[str, Any], seed_data: Optional[Dict[str, Any]]) -> bool:
    if str(batch.get("creation_mode") or "").strip() == "manual":
        return True
    return isinstance(seed_data, dict) and seed_data.get("manual_draft") is True


def _count_script_words(script: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", script))


def _estimate_speech_seconds_for_script(script: str) -> float:
    """Estimate spoken duration in seconds for a script using the project's
    canonical 2.5 words-per-second cadence (matches `_estimate_spoken_seconds`
    used elsewhere)."""
    word_count = _count_script_words(script)
    if word_count <= 0:
        return 0.0
    return word_count / _WORDS_PER_SECOND


def _resolve_manual_target_length_tier(seed_data: Optional[Dict[str, Any]]) -> int:
    """Auto-derive the target tier for a MANUAL post from script word count.

    Replaces the old sentence-count heuristic that silently capped:
      - long single-sentence scripts at tier 8 (~7.5s),
      - long two/three-sentence scripts at tier 16 (~14.5s).

    Algorithm:
      estimated_speech_seconds = word_count / _WORDS_PER_SECOND
      pick the smallest tier whose `provider_target_seconds` >= estimate
      fall back to the highest supported tier when the script overflows.

    Topic-based batches keep using the explicit tier on the batch row; this
    function is only invoked when `_is_manual_video_post(...)` is True.
    """
    if not isinstance(seed_data, dict):
        return DEFAULT_TARGET_LENGTH_TIER

    script = str(seed_data.get("script") or seed_data.get("dialog_script") or "").strip()
    if not script:
        return DEFAULT_TARGET_LENGTH_TIER

    estimated_seconds = _estimate_speech_seconds_for_script(script)
    if estimated_seconds <= 0:
        return DEFAULT_TARGET_LENGTH_TIER

    sorted_tiers = sorted(SUPPORTED_TARGET_LENGTH_TIERS)
    for tier in sorted_tiers:
        profile = get_duration_profile(tier)
        if profile.provider_target_seconds >= estimated_seconds:
            return tier
    # Script overflows our biggest supported tier — return the max and let
    # the partitioner pack the surplus words (Veo will speed-talk slightly).
    return sorted_tiers[-1]


def _resolve_video_submission_plan(
    *,
    batch: Dict[str, Any],
    requested_provider: Optional[str],
    requested_seconds: Optional[int],
    aspect_ratio: str,
    resolution: str,
    size: Optional[str],
    seed_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if uses_duration_routing(batch):
        requested_target_length_tier = batch.get("target_length_tier")
        manual_auto_resolved = _is_manual_video_post(batch, seed_data)
        target_length_tier = (
            _resolve_manual_target_length_tier(seed_data)
            if manual_auto_resolved
            else requested_target_length_tier
        )
        profile = get_duration_profile_for_creation_mode(target_length_tier, batch.get("creation_mode"))
        profile_config = get_profile_route_config(profile)
        resolved_resolution = "720p" if profile.route == VEO_EXTENDED_VIDEO_ROUTE else resolution
        provider_aspect_ratio = _resolve_extended_provider_aspect_ratio(profile.route, aspect_ratio)
        requested_size = size or _map_size_from_aspect_ratio(aspect_ratio, resolved_resolution)
        return {
            "provider": "vertex_ai",
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
            "manual_duration_auto_resolved": manual_auto_resolved,
            "manual_requested_target_length_tier": requested_target_length_tier if manual_auto_resolved else None,
        }

    provider = _resolve_non_duration_provider(requested_provider)
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


def _validate_post_duration_contract_for_video(
    *,
    post: Dict[str, Any],
    batch: Dict[str, Any],
    video_prompt: Dict[str, Any],
) -> Dict[str, Any]:
    seed_data = _normalize_seed_data(post.get("seed_data"))
    batch_tier = batch.get("target_length_tier")
    seed_tier = seed_data.get("target_length_tier")
    if batch_tier is None and seed_tier is None:
        return {
            "status": "skipped_no_declared_tier",
            "post_id": post.get("id"),
            "target_length_tier": None,
        }
    target_tier = int(batch_tier or seed_tier or DEFAULT_TARGET_LENGTH_TIER)

    if _is_manual_video_post(batch, seed_data) or target_tier not in {8, 16, 32}:
        return {
            "status": "skipped_manual_or_unsupported_tier",
            "post_id": post.get("id"),
            "target_length_tier": target_tier,
        }

    if batch_tier is not None and seed_tier is not None and int(seed_tier) != int(batch_tier):
        raise ValidationError(
            f"Post {post.get('id')} target tier mismatch: seed_data.target_length_tier={seed_tier}, batch.target_length_tier={batch_tier}.",
            {"post_id": post.get("id"), "seed_target_length_tier": seed_tier, "batch_target_length_tier": batch_tier},
        )

    script = resolve_effective_script_text(seed_data, video_prompt)
    return validate_script_duration_contract(
        script=script,
        post_type=post.get("post_type"),
        target_length_tier=target_tier,
        row_id=post.get("id"),
        table="posts",
    )


def _load_or_build_video_prompt(
    *,
    post: Dict[str, Any],
    supabase_client,
    correlation_id: str,
    batch: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    video_prompt = post.get("video_prompt_json")
    if isinstance(video_prompt, str):
        try:
            video_prompt = json.loads(video_prompt)
        except json.JSONDecodeError:
            video_prompt = None

    if isinstance(video_prompt, dict) and video_prompt:
        seed_data = _normalize_seed_data(post.get("seed_data"))
        legacy_32_visuals = bool(
            post.get("target_length_tier") == 32
            or seed_data.get("target_length_tier") == 32
        )
        video_metadata = post.get("video_metadata")
        if isinstance(video_metadata, str):
            try:
                video_metadata = json.loads(video_metadata)
            except json.JSONDecodeError:
                video_metadata = {}
        if isinstance(video_metadata, dict) and video_metadata.get("target_length_tier") == 32:
            legacy_32_visuals = True
        creation_mode = str((batch or {}).get("creation_mode") or "").strip()
        use_legacy_short_character = is_character_consistency_mode(creation_mode) and not is_character_consistency_light_mode(creation_mode)

        synced_prompt = sync_video_prompt_with_seed_data(
            video_prompt,
            seed_data,
            legacy_32_visuals=legacy_32_visuals,
            use_legacy_short_character=use_legacy_short_character,
        )
        if synced_prompt != video_prompt:
            supabase_client.table("posts").update({
                "video_prompt_json": synced_prompt,
            }).eq("id", post["id"]).execute()
            post["video_prompt_json"] = synced_prompt
            logger.info(
                "video_prompt_synced_from_seed_data",
                post_id=post.get("id"),
                batch_id=post.get("batch_id"),
                correlation_id=correlation_id,
            )
            return synced_prompt
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
        scene_plan = None
        if batch:
            scene_plan = ensure_scene_plan(
                batch,
                topic_titles=[str(post.get("topic_title") or "").strip()],
                correlation_id=correlation_id,
            )
        built_prompt = build_video_prompt_from_seed(
            seed_data,
            legacy_32_visuals=False,
            use_legacy_short_character=(
                is_character_consistency_mode(str((batch or {}).get("creation_mode") or "").strip())
                and not is_character_consistency_light_mode(str((batch or {}).get("creation_mode") or "").strip())
            ),
            post_type=str(post.get("post_type") or "value"),
            scene_plan=scene_plan,
            prompt_style=str((batch or {}).get("creation_mode") or "standard").strip(),
        )
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
    creation_mode: Optional[str] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    metadata = dict(existing_metadata)
    for key in (
        "error",
        "error_type",
        "failed_at",
        "provider_status_code",
        "provider_response_body",
        "last_polled_by",
        "last_polled_at",
        "last_poll_recovery",
    ):
        metadata.pop(key, None)
    requested_aspect_ratio = submission_plan.get("requested_aspect_ratio") or submission_plan["aspect_ratio"]
    requested_size = submission_result.get("requested_size") or submission_plan.get("requested_size")
    provider_aspect_ratio = submission_plan.get("provider_aspect_ratio") or requested_aspect_ratio
    metadata = {
        **metadata,
        "requested_aspect_ratio": requested_aspect_ratio,
        "requested_resolution": submission_plan["resolution"],
        "requested_seconds": submission_plan["seconds"],
        "requested_size": requested_size,
        "provider_aspect_ratio": provider_aspect_ratio,
        "poller_environment": settings.environment,
        "poller_scope": _resolve_poller_scope(settings),
    }
    if creation_mode:
        metadata["creation_mode"] = creation_mode

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
    requested_model = submission_result.get("requested_model")
    if requested_model:
        metadata["requested_model"] = requested_model
    if submission_result.get("provider_metadata"):
        metadata["provider_metadata"] = submission_result["provider_metadata"]
    if segment_metadata:
        metadata.update(segment_metadata)

    return metadata


def _resolve_poller_scope(settings: Any) -> str:
    app_url = str(getattr(settings, "app_url", "") or "").strip()
    if app_url:
        parsed = urlparse(app_url if "://" in app_url else f"https://{app_url}")
        host = (parsed.hostname or "").strip().lower()
        if host:
            return host

    app_host = str(getattr(settings, "app_host", "") or "").strip().lower()
    if app_host:
        return app_host

    return str(getattr(settings, "environment", "") or "development").strip().lower() or "development"


def _uses_actual_efficient_long_route(profile: Optional[Any]) -> bool:
    """Efficient long-route profiles use an 8s base with 1 or 3 extension hops."""
    return bool(
        profile is not None
        and profile.route == VEO_EXTENDED_VIDEO_ROUTE
        and profile.veo_base_seconds == 8
        and profile.veo_extension_hops in {1, 3}
    )


def _should_assign_veo_seed(*, provider: str, profile: Optional[Any]) -> bool:
    """Use one seed across an extended chain when the provider accepts it."""
    return provider in {VEO_PROVIDER, "vertex_ai"} and _uses_actual_efficient_long_route(profile)


def _required_veo_segments_for_profile_hops(hops_target: int) -> int:
    return max(int(hops_target or 0), 0) + 1


def _estimate_spoken_seconds(text: str) -> float:
    words = [word for word in str(text).split() if word]
    if not words:
        return 0.0
    return len(words) / _WORDS_PER_SECOND


def _minimum_words_for_veo_window(*, budget_seconds: int, is_final_segment: bool) -> int:
    if budget_seconds >= 8:
        return 16
    return 12 if is_final_segment else 14


def _segment_word_count(segment: str) -> int:
    return script_word_count(segment)


def _build_veo_segment_spoken_budgets(
    *,
    segments: list[str],
    profile: Any,
) -> list[dict[str, Any]]:
    budgets: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        budget_seconds = _segment_time_budget_seconds(profile=profile, segment_index=index)
        is_final = index == len(segments) - 1
        minimum_words = _minimum_words_for_veo_window(
            budget_seconds=budget_seconds,
            is_final_segment=is_final,
        )
        word_count = _segment_word_count(segment)
        budgets.append(
            {
                "segment_index": index,
                "budget_seconds": budget_seconds,
                "word_count": word_count,
                "minimum_words": minimum_words,
            }
        )
    return budgets


def _segments_meet_veo_spoken_budget(
    *,
    segments: list[str],
    profile: Any,
    required_segments: int,
) -> bool:
    if len(segments) < required_segments:
        return False
    budgets = _build_veo_segment_spoken_budgets(segments=segments, profile=profile)
    return all(item["word_count"] >= item["minimum_words"] for item in budgets[:required_segments])


def _minimum_veo_segment_words(
    *,
    profile: Any,
    required_segments: int,
) -> list[float]:
    return [
        float(
            _minimum_words_for_veo_window(
                budget_seconds=_segment_time_budget_seconds(profile=profile, segment_index=index),
                is_final_segment=index == required_segments - 1,
            )
        )
        for index in range(required_segments)
    ]


def _validate_veo_segment_spoken_budget(
    *,
    segments: list[str],
    profile: Any,
    target_length_tier: Optional[int],
    planned_extension_hops: Optional[int],
) -> None:
    if profile.route != VEO_EXTENDED_VIDEO_ROUTE:
        return

    required_segments = _required_veo_segments_for_profile_hops(
        planned_extension_hops if planned_extension_hops is not None else profile.veo_extension_hops
    )
    budgets = _build_veo_segment_spoken_budgets(segments=segments, profile=profile)
    if len(segments) < required_segments:
        raise ValidationError(
            "Veo extended chain does not have enough distinct spoken segments.",
            details={
                "target_length_tier": target_length_tier,
                "veo_required_segments": required_segments,
                "veo_segments_total": len(segments),
                "veo_planned_extension_hops_target": planned_extension_hops,
                "veo_segment_spoken_budgets": budgets,
            },
        )

    failures = [item for item in budgets[:required_segments] if item["word_count"] < item["minimum_words"]]
    if not failures:
        return
    worst = max(failures, key=lambda item: item["minimum_words"] - item["word_count"])
    segment_index = int(worst["segment_index"])
    raise ValidationError(
        "Veo extended segment is too short for its assigned duration window.",
        details={
            "target_length_tier": target_length_tier,
            "segment_index": segment_index,
            "budget_seconds": worst["budget_seconds"],
            "word_count": worst["word_count"],
            "minimum_words": worst["minimum_words"],
            "segment_preview": str(segments[segment_index])[:180],
            "veo_required_segments": required_segments,
            "veo_planned_extension_hops_target": planned_extension_hops,
            "veo_segment_spoken_budgets": budgets,
        },
    )


def _extended_action_without_embedded_dialogue(action: Optional[str], script: str) -> Optional[str]:
    cleaned = str(action or "").strip()
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if "she says" in lowered or "dialogue:" in lowered:
        return None
    script_words = [word for word in str(script or "").split() if word]
    if len(script_words) >= 8:
        snippet = " ".join(script_words[:8]).lower()
        if snippet and snippet in lowered:
            return None
    return cleaned


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


def _split_dialogue_text_by_word_budget(
    text: str,
    *,
    target_words: list[float],
    required_segments: int,
) -> list[str]:
    words = [word for word in str(text).split() if word]
    if required_segments <= 0 or len(words) < required_segments:
        return [text.strip()] if str(text).strip() else []

    chunks: list[str] = []
    cursor = 0
    total_words = len(words)
    for index in range(required_segments):
        remaining_segments = required_segments - index
        remaining_words = total_words - cursor
        if remaining_segments <= 1:
            end = total_words
        else:
            remaining_target_words = sum(target_words[index:required_segments])
            if remaining_target_words > 0:
                desired_take = round(remaining_words * (target_words[index] / remaining_target_words))
            else:
                desired_take = round(remaining_words / remaining_segments)
            max_take = remaining_words - (remaining_segments - 1)
            take = min(max(int(desired_take), 1), max_take)
            end = cursor + take
        chunks.append(" ".join(words[cursor:end]).strip())
        cursor = end
    return [chunk for chunk in chunks if chunk]


def _expand_dialogue_units_for_required_segments(
    units: list[str],
    *,
    target_words: list[float],
    required_segments: int,
) -> list[str]:
    if len(units) >= required_segments:
        return units

    target_base_words = target_words[0] if target_words else 0
    clause_units = _split_dialogue_units_for_time_balance(
        units,
        target_base_words=target_base_words,
    )
    if len(clause_units) >= required_segments:
        return clause_units

    return _split_dialogue_text_by_word_budget(
        " ".join(units),
        target_words=target_words,
        required_segments=required_segments,
    )


def _partition_dialogue_units_for_profile(
    units: list[str],
    *,
    profile: Any,
    required_segments: int,
) -> list[str]:
    if required_segments <= 0 or not units:
        return units

    target_budgets = [
        _segment_time_budget_seconds(profile=profile, segment_index=index)
        for index in range(required_segments)
    ]
    target_words = [budget * _WORDS_PER_SECOND for budget in target_budgets]
    units = _expand_dialogue_units_for_required_segments(
        units,
        target_words=target_words,
        required_segments=required_segments,
    )
    if len(units) < required_segments:
        return units

    unit_word_counts = [len(unit.split()) for unit in units]
    prefix_counts = [0]
    for count in unit_word_counts:
        prefix_counts.append(prefix_counts[-1] + count)

    from functools import lru_cache

    @lru_cache(maxsize=None)
    def _best_cuts(segment_index: int, start_index: int) -> tuple[float, tuple[int, ...]]:
        if segment_index == required_segments - 1:
            remaining = len(units) - start_index
            if remaining < 1:
                return float("inf"), ()
            words_in_segment = prefix_counts[len(units)] - prefix_counts[start_index]
            cost = (words_in_segment - target_words[segment_index]) ** 2
            return cost, (len(units),)

        best_cost = float("inf")
        best_path: tuple[int, ...] = ()
        remaining_segments_after_current = required_segments - segment_index - 1
        min_end = start_index + 1
        max_end = len(units) - remaining_segments_after_current

        for end_index in range(min_end, max_end + 1):
            words_in_segment = prefix_counts[end_index] - prefix_counts[start_index]
            next_cost, next_path = _best_cuts(segment_index + 1, end_index)
            if next_cost == float("inf"):
                continue
            current_cost = (words_in_segment - target_words[segment_index]) ** 2 + next_cost
            if current_cost < best_cost:
                best_cost = current_cost
                best_path = (end_index,) + next_path

        return best_cost, best_path

    _cost, cut_points = _best_cuts(0, 0)
    if not cut_points:
        return units

    packed_segments: list[str] = []
    cursor = 0
    for end_index in cut_points:
        packed_segments.append(" ".join(units[cursor:end_index]).strip())
        cursor = end_index
    return [segment for segment in packed_segments if segment]


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


def _resolve_veo_extension_hops_target(
    *,
    segments: list[str],
    planned_hops: int,
    profile: Any,
    estimated_duration_s: Optional[float] = None,
) -> int:
    planned_hops = max(int(planned_hops or 0), 0)
    if not segments:
        return 0
    if profile.route != VEO_EXTENDED_VIDEO_ROUTE:
        return planned_hops

    for candidate_hops in range(planned_hops, -1, -1):
        if estimated_duration_s is not None:
            candidate_seconds = (
                _segment_time_budget_seconds(profile=profile, segment_index=0)
                + candidate_hops * _segment_time_budget_seconds(profile=profile, segment_index=1)
            )
            if candidate_seconds > estimated_duration_s + 0.5:
                continue

        packed_segments = _pack_veo_segments_for_profile(
            segments,
            planned_extension_hops=candidate_hops,
            target_length_tier=profile.target_length_tier,
        )
        required_segments = _required_veo_segments_for_profile_hops(candidate_hops)
        if len(packed_segments) >= required_segments:
            return candidate_hops

    return 0


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
    if profile.route != VEO_EXTENDED_VIDEO_ROUTE:
        return segments

    packed_segments = _partition_dialogue_units_for_profile(
        segments,
        profile=profile,
        required_segments=required_segments,
    )
    if _segments_meet_veo_spoken_budget(
        segments=packed_segments,
        profile=profile,
        required_segments=required_segments,
    ):
        return packed_segments

    min_targets = _minimum_veo_segment_words(profile=profile, required_segments=required_segments)
    total_words = sum(_segment_word_count(segment) for segment in segments)
    if total_words < sum(min_targets):
        return packed_segments

    budget_segments = _split_dialogue_text_by_word_budget(
        " ".join(segments),
        target_words=min_targets,
        required_segments=required_segments,
    )
    if _segments_meet_veo_spoken_budget(
        segments=budget_segments,
        profile=profile,
        required_segments=required_segments,
    ):
        return budget_segments
    return packed_segments


def _build_veo_extended_base_prompt(
    seed_data: Dict[str, Any],
    video_prompt: Optional[Dict[str, Any]] = None,
    *,
    planned_extension_hops: Optional[int] = None,
    target_length_tier: Optional[int] = None,
    creation_mode: str = "automated",
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
    raw_segments = split_dialogue_sentences(script) if script else []
    if not raw_segments and script:
        raw_segments = [script]

    segments = raw_segments
    profile = get_duration_profile(target_length_tier) if target_length_tier is not None else None
    estimated_duration_s: Optional[float] = None
    try:
        if seed_data.get("estimated_duration_s") is not None:
            estimated_duration_s = float(seed_data["estimated_duration_s"])
    except (TypeError, ValueError):
        estimated_duration_s = None

    effective_hops: Optional[int] = None
    if planned_extension_hops is not None:
        effective_hops = max(int(planned_extension_hops or 0), 0)
        segments = _pack_veo_segments_for_profile(
            raw_segments,
            planned_extension_hops=effective_hops,
            target_length_tier=target_length_tier,
        )

    base_segment = segments[0] if segments else ""
    if profile is not None and profile.route == VEO_EXTENDED_VIDEO_ROUTE:
        if target_length_tier == 32:
            prompt_character = LEGACY_SHORT_CHARACTER
            prompt_style = LEGACY_32_STYLE
            prompt_cinematography = LEGACY_32_CINEMATOGRAPHY
            prompt_scene = None
            prompt_action = None
        else:
            prompt_character = prompt_character or DEFAULT_CHARACTER
            prompt_style = prompt_style or DEFAULT_STYLE
            prompt_cinematography = prompt_cinematography or DEFAULT_CINEMATOGRAPHY
            prompt_action = _extended_action_without_embedded_dialogue(prompt_action, script)
        prompt_ending = None
        prompt_audio_block = None
        _validate_veo_segment_spoken_budget(
            segments=segments,
            profile=profile,
            target_length_tier=target_length_tier,
            planned_extension_hops=effective_hops,
        )
    segment_metadata = {
        "veo_segments": segments,
        "veo_segments_total": len(segments),
        "veo_current_segment_index": 0,
        "veo_segment_time_windows": (
            _build_time_windows_for_profile(profile=profile, segment_count=len(segments))
            if profile is not None
            else []
        ),
        "veo_segment_spoken_budgets": (
            _build_veo_segment_spoken_budgets(segments=segments, profile=profile)
            if profile is not None and profile.route == VEO_EXTENDED_VIDEO_ROUTE
            else []
        ),
    }
    if planned_extension_hops is not None:
        planned_required_segments = _required_veo_segments_for_profile_hops(planned_extension_hops)
        effective_required_segments = _required_veo_segments_for_profile_hops(effective_hops or 0)
        segment_metadata.update(
            {
                "veo_required_segments": planned_required_segments,
                "veo_planned_required_segments": planned_required_segments,
                "veo_effective_required_segments": effective_required_segments,
                "veo_planned_extension_hops_target": planned_extension_hops,
                "veo_extension_hops_target": effective_hops,
                "veo_chain_shortened_to_available_segments": effective_hops < planned_extension_hops,
            }
        )
    if is_character_consistency_light_mode(creation_mode):
        return build_lean_veo_base_prompt(
            base_segment,
            include_final_ending=False,
        ), segment_metadata

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
        legacy_32_visuals=bool(target_length_tier == 32),
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
    submission_plan: Dict[str, Any] = {"provider": "vertex_ai"}
    
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
        batch = get_batch_by_id(post.get("batch_id"))

        if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
            raise ValidationError(
                "Removed posts cannot be submitted for video generation.",
                {"post_id": post_id}
            )

        video_prompt = _load_or_build_video_prompt(
            post=post,
            supabase_client=supabase,
            correlation_id=correlation_id,
            batch=batch,
        )
        script_contract = _validate_post_duration_contract_for_video(
            post=post,
            batch=batch,
            video_prompt=video_prompt,
        )

        submission_plan = _resolve_video_submission_plan(
            batch=batch,
            requested_provider=request.provider,
            requested_seconds=request.seconds,
            aspect_ratio=request.aspect_ratio,
            resolution=request.resolution,
            size=request.size,
            seed_data=seed_data,
        )
        profile = submission_plan.get("profile")
        is_extended = profile is not None and profile.route == VEO_EXTENDED_VIDEO_ROUTE
        route = profile.route if profile else None
        approved_scene_reference_set = None
        scene_reference_check = ensure_video_scene_reference_set_ready(
            batch=batch,
            post=post,
            scene_reference_set=character_queries.get_approved_scene_reference_set_for_post(post_id),
            route=route,
        )
        if scene_reference_check.get("source") == "actor_identity_scene_reference_set":
            approved_scene_reference_set = scene_reference_check["scene_reference_set"]

        if is_extended:
            prompt_text, segment_metadata = _build_veo_extended_base_prompt(
                seed_data,
                video_prompt,
                planned_extension_hops=profile.veo_extension_hops,
                target_length_tier=profile.target_length_tier,
                creation_mode=str(batch.get("creation_mode") or "automated"),
            )
            prompt_request = {
                "prompt_text": prompt_text,
                "negative_prompt": (
                    build_negative_prompt(creation_mode=str(batch.get("creation_mode") or ""), is_extension=True)
                    if is_character_consistency_mode(batch.get("creation_mode"))
                    else _build_veo_negative_prompt(video_prompt)
                ),
                "prompt_path": "veo_extended_base_prompt",
            }
        else:
            prompt_request = _build_provider_prompt_request(
                video_prompt,
                submission_plan["provider"],
                creation_mode=str(batch.get("creation_mode") or "automated"),
                is_extension=False,
            )
            segment_metadata = None

        requested_units = 0
        anchor_image_bundle = None
        veo_seed = (
            random.randint(0, 2**32 - 1)
            if _should_assign_veo_seed(provider=submission_plan["provider"], profile=profile)
            else None
        )
        if is_extended:
            logger.info(
                "veo_extended_preflight_passed",
                post_id=post_id,
                batch_id=post.get("batch_id"),
                target_length_tier=profile.target_length_tier if profile else None,
                segments=segment_metadata.get("veo_segment_spoken_budgets") if segment_metadata else None,
            )
        submission_result = _submit_video_request(
            provider=submission_plan["provider"],
            model=request.model,
            prompt_text=prompt_request["prompt_text"] or "",
            negative_prompt=prompt_request.get("negative_prompt"),
            aspect_ratio=submission_plan["aspect_ratio"],
            provider_aspect_ratio=submission_plan.get("provider_aspect_ratio"),
            requested_aspect_ratio=submission_plan.get("requested_aspect_ratio"),
            resolution=submission_plan["resolution"],
            seconds=submission_plan["seconds"],
            size=submission_plan["size"],
            correlation_id=correlation_id,
            provider_duration_seconds=(
                profile.veo_base_seconds
                if is_extended and profile is not None
                else submission_plan["provider_target_seconds"]
                if submission_plan["provider"] in {VEO_PROVIDER, "vertex_ai"}
                else None
            ),
            first_frame_image=None,
            seed=veo_seed,
            creation_mode=str(batch.get("creation_mode") or "automated"),
            character_snapshot=batch.get("character_snapshot"),
            scene_reference_set=approved_scene_reference_set,
        )

        operation_id = submission_result["operation_id"]
        provider_model = submission_result.get("provider_model")
        requested_size = submission_result.get("requested_size")

        quota_consume_error = _consume_quota_after_acceptance(
            reservation_key=quota_reservation_key,
            operation_id=operation_id,
            units=1,
            correlation_id=correlation_id,
            provider=submission_plan["provider"],
            post_id=post_id,
            batch_id=post.get("batch_id"),
        )
        if quota_reservation_key:
            quota_consumed = True

        record_prompt_audit(
            post_id=post_id,
            operation_id=operation_id,
            provider=submission_plan["provider"],
            prompt_text=prompt_request["prompt_text"] or "",
            negative_prompt=prompt_request.get("negative_prompt"),
            prompt_path=prompt_request["prompt_path"],
            aspect_ratio=submission_plan["aspect_ratio"],
            resolution=submission_plan["resolution"],
            requested_seconds=submission_plan["seconds"],
            correlation_id=correlation_id,
            seed=veo_seed,
        )

        existing_metadata = post.get("video_metadata") or {}
        submission_metadata = _build_submission_metadata(
            existing_metadata=existing_metadata,
            submission_plan=submission_plan,
            submission_result=submission_result,
            segment_metadata=segment_metadata,
            creation_mode=str(batch.get("creation_mode") or "automated"),
        )
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
        if approved_scene_reference_set:
            submission_metadata["actor_identity_source"] = "actor_identity_scene_reference_set"
            submission_metadata["actor_identity_id"] = batch.get("actor_identity_id")
            submission_metadata["scene_reference_set_id"] = approved_scene_reference_set.reference_set_id
            submission_metadata["scene_reference_image_ids"] = [
                str(row.get("id") or "") for row in approved_scene_reference_set.approved_rows
            ]
            submission_metadata["scene_reference_angle_keys"] = [
                str((row.get("provider_metadata") or {}).get("angle_key") or "")
                for row in approved_scene_reference_set.approved_rows
            ]
        if anchor_image_bundle:
            submission_metadata.update(anchor_image_bundle["metadata"])
        submission_metadata["script_duration_contract"] = script_contract

        # Normalize provider status to DB-compatible values
        provider_status = submission_result.get("status", "submitted")
        db_status = "submitted" if provider_status == "queued" else provider_status

        # CRITICAL: Log operation_id before DB update to enable recovery if update fails
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
                "video_db_update_failed_but_video_submitted",
                post_id=post_id,
                operation_id=operation_id,
                provider=submission_plan["provider"],
                correlation_id=correlation_id,
                error=str(db_error),
                message="DATABASE UPDATE FAILED - Video is still processing at provider. Use operation_id to recover."
            )
            # Write to fallback recovery file
            _write_recovery_record(post_id, operation_id, submission_plan["provider"], correlation_id)
            raise

        logger.info(
            "video_generation_submitted",
            post_id=post_id,
            correlation_id=correlation_id,
            provider=submission_plan["provider"],
            provider_model=provider_model,
            aspect_ratio=submission_plan["aspect_ratio"],
            resolution=submission_plan["resolution"],
            seconds=submission_plan["seconds"],
            size=requested_size,
            operation_id=operation_id
        )

        return SuccessResponse(
            data=VideoGenerationResponse(
                post_id=post_id,
                operation_id=operation_id,
                provider=submission_plan["provider"],
                provider_model=provider_model,
                status=submission_result.get("status", "submitted"),
                estimated_duration_seconds=submission_result.get("estimated_duration_seconds"),
                aspect_ratio=submission_plan["aspect_ratio"],
                resolution=submission_plan["resolution"]
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
            and submission_plan["provider"] == VEO_PROVIDER
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
        batch_submission_provider = "vertex_ai" if uses_duration_routing(batch) else _resolve_non_duration_provider(request.provider)
        batch_veo_seed = (
            random.randint(0, 2**32 - 1)
            if _should_assign_veo_seed(provider=batch_submission_provider, profile=batch_profile)
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
                seed_data=seed_data,
            )

            profile = submission_plan.get("profile")
            is_extended = profile is not None and profile.route == VEO_EXTENDED_VIDEO_ROUTE
            scene_reference_check = ensure_video_scene_reference_set_ready(
                batch=batch,
                post=post,
                scene_reference_set=character_queries.get_approved_scene_reference_set_for_post(post_id),
                route=profile.route if profile else None,
            )
            approved_scene_reference_set = (
                scene_reference_check["scene_reference_set"]
                if scene_reference_check.get("source") == "actor_identity_scene_reference_set"
                else None
            )

            try:
                video_prompt = _load_or_build_video_prompt(
                    post=post,
                    supabase_client=supabase,
                    correlation_id=f"{correlation_id}_{post_id}",
                    batch=batch,
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

            script_contract = _validate_post_duration_contract_for_video(
                post=post,
                batch=batch,
                video_prompt=video_prompt,
            )

            if is_extended:
                prompt_text, segment_metadata = _build_veo_extended_base_prompt(
                    seed_data,
                    video_prompt,
                    planned_extension_hops=profile.veo_extension_hops,
                    target_length_tier=profile.target_length_tier,
                    creation_mode=str(batch.get("creation_mode") or "automated"),
                )
                negative_prompt = (
                    build_negative_prompt(creation_mode=str(batch.get("creation_mode") or ""), is_extension=True)
                    if is_character_consistency_mode(batch.get("creation_mode"))
                    else _build_veo_negative_prompt(video_prompt)
                )
            else:
                prompt_request = _build_provider_prompt_request(
                    video_prompt,
                    submission_plan["provider"],
                    creation_mode=str(batch.get("creation_mode") or "automated"),
                    is_extension=False,
                )
                prompt_text = prompt_request["prompt_text"] or ""
                negative_prompt = prompt_request.get("negative_prompt")
                segment_metadata = None

            prepared_submissions.append(
                {
                    "post": post,
                    "post_id": post_id,
                    "seed_data": seed_data,
                    "submission_plan": submission_plan,
                    "model": request.model,
                    "profile": profile,
                    "is_extended": is_extended,
                    "prompt_text": prompt_text,
                    "negative_prompt": negative_prompt,
                    "segment_metadata": segment_metadata,
                    "script_contract": script_contract,
                    "quota_requested_units": chain_cost_units(profile, provider=submission_plan["provider"]),
                    "scene_reference_set": approved_scene_reference_set,
                }
            )
            if is_extended:
                logger.info(
                    "veo_extended_preflight_passed",
                    post_id=post_id,
                    batch_id=batch_id,
                    target_length_tier=profile.target_length_tier if profile else None,
                    segments=segment_metadata.get("veo_segment_spoken_budgets") if segment_metadata else None,
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

        for index, item in enumerate(prepared_submissions):
            post = item["post"]
            post_id = item["post_id"]
            submission_plan = item["submission_plan"]
            profile = item["profile"]
            is_extended = item["is_extended"]
            prompt_text = item["prompt_text"]
            negative_prompt = item["negative_prompt"]
            segment_metadata = item["segment_metadata"]
            script_contract = item["script_contract"]
            quota_reservation_key = item.get("quota_reservation_key")
            quota_consumed = False

            try:
                submission_result = _submit_video_request(
                    provider=submission_plan["provider"],
                    model=item.get("model"),
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
                    first_frame_image=None,
                    seed=batch_veo_seed,
                    creation_mode=str(batch.get("creation_mode") or "automated"),
                    character_snapshot=batch.get("character_snapshot"),
                    scene_reference_set=item.get("scene_reference_set"),
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
                    creation_mode=str(batch.get("creation_mode") or "automated"),
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
                if item.get("scene_reference_set"):
                    scene_reference_set = item["scene_reference_set"]
                    submission_metadata["actor_identity_source"] = "actor_identity_scene_reference_set"
                    submission_metadata["actor_identity_id"] = batch.get("actor_identity_id")
                    submission_metadata["scene_reference_set_id"] = scene_reference_set.reference_set_id
                    submission_metadata["scene_reference_image_ids"] = [
                        str(row.get("id") or "") for row in scene_reference_set.approved_rows
                    ]
                    submission_metadata["scene_reference_angle_keys"] = [
                        str((row.get("provider_metadata") or {}).get("angle_key") or "")
                        for row in scene_reference_set.approved_rows
                    ]
                submission_metadata["script_duration_contract"] = script_contract
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
    if provider in {VEO_PROVIDER, "vertex_ai"}:
        return _build_veo_prompt_text(video_prompt)

    # Fallback to canonical composition
    return build_full_prompt_text(video_prompt), "full_prompt_text_fallback"


def _build_provider_prompt_request(
    video_prompt: Dict[str, Any],
    provider: str,
    *,
    creation_mode: str = "automated",
    is_extension: bool = False,
) -> Dict[str, Any]:
    """Build provider-specific prompt payload pieces."""
    prompt_text, prompt_path = _build_provider_prompt_text(video_prompt, provider)
    if provider in {VEO_PROVIDER, "vertex_ai"} and is_character_consistency_mode(creation_mode):
        negative_prompt = build_negative_prompt(creation_mode=creation_mode, is_extension=is_extension)
    else:
        negative_prompt = _build_veo_negative_prompt(video_prompt) if provider in {VEO_PROVIDER, "vertex_ai"} else None
    return {
        "prompt_text": prompt_text,
        "negative_prompt": negative_prompt,
        "prompt_path": prompt_path,
    }


def _submit_video_request(
    *,
    provider: str,
    model: Optional[str] = None,
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
    creation_mode: str = "automated",
    character_snapshot: Optional[Dict[str, Any]] = None,
    scene_reference: Optional[Dict[str, Any]] = None,
    scene_reference_set: Optional[SceneReferenceSetSummary] = None,
) -> Dict[str, Any]:
    """Submit a video generation request to the selected provider."""

    if provider == "veo_3_1":
        veo_client = get_veo_client()
        provider_aspect = provider_aspect_ratio or aspect_ratio
        requested_aspect = requested_aspect_ratio or aspect_ratio
        veo_duration_seconds = provider_duration_seconds or seconds
        mode = str(creation_mode or "automated").strip()
        model_name = select_veo_model_id(creation_mode=mode) if is_character_consistency_mode(mode) else (model or select_veo_model_id(creation_mode=mode))
        if is_character_consistency_mode(mode):
            if scene_reference_set:
                if veo_duration_seconds == 4:
                    raise FlowForgeException(
                        code=ErrorCode.VALIDATION_ERROR,
                        message="ActorIdentity video route cannot consume approved scene references on a 4s base request.",
                        details={"scene_reference_set_id": scene_reference_set.reference_set_id},
                        status_code=422,
                    )
                reference_bundle = _load_scene_reference_set_assets(
                    scene_reference_set=scene_reference_set,
                    correlation_id=correlation_id,
                )
            elif scene_reference:
                if veo_duration_seconds == 4:
                    raise FlowForgeException(
                        code=ErrorCode.VALIDATION_ERROR,
                        message="ActorIdentity video route cannot consume approved scene references on a 4s base request.",
                        details={"scene_reference_image_id": scene_reference.get("id")},
                        status_code=422,
                    )
                reference_bundle = _load_scene_reference_asset(
                    scene_reference=scene_reference,
                    correlation_id=correlation_id,
                )
            else:
                reference_bundle = _load_character_snapshot_assets(
                    snapshot=character_snapshot,
                    correlation_id=correlation_id,
                )
        else:
            reference_bundle = _load_global_veo_reference_assets(correlation_id=correlation_id, strict=False)
        reference_images = reference_bundle["reference_images"] if reference_bundle else None
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
                first_frame_image=None,
                reference_images=reference_images,
                seed=seed,
                model=model_name,
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
        provider_metadata = dict(result)
        if reference_bundle:
            provider_metadata.update(reference_bundle["metadata"])
        return {
            "operation_id": result["operation_id"],
            "status": result.get("status", "submitted"),
            "provider_model": result.get("provider_model", model_name),
            "requested_size": requested_size,
            "provider_requested_size": provider_requested_size,
            "estimated_duration_seconds": 180,
            "provider_metadata": provider_metadata,
        }

    if provider == "vertex_ai":
        vertex_client = get_vertex_ai_client()
        settings = get_settings()
        vertex_duration = provider_duration_seconds or seconds
        output_gcs_uri = settings.vertex_ai_output_gcs_uri or None
        mode = str(creation_mode or "automated").strip()
        reference_bundle = None
        reference_skip_metadata: Dict[str, Any] = {}
        if is_character_consistency_mode(mode):
            if scene_reference_set:
                if vertex_duration != 8:
                    raise FlowForgeException(
                        code=ErrorCode.VALIDATION_ERROR,
                        message="ActorIdentity video route cannot consume approved scene references unless the base request is 8 seconds.",
                        details={
                            "scene_reference_set_id": scene_reference_set.reference_set_id,
                            "provider_duration_seconds": vertex_duration,
                        },
                        status_code=422,
                    )
                reference_bundle = _load_scene_reference_set_assets(
                    scene_reference_set=scene_reference_set,
                    correlation_id=correlation_id,
                )
            elif scene_reference:
                if vertex_duration != 8:
                    raise FlowForgeException(
                        code=ErrorCode.VALIDATION_ERROR,
                        message="ActorIdentity video route cannot consume approved scene references unless the base request is 8 seconds.",
                        details={
                            "scene_reference_image_id": scene_reference.get("id"),
                            "provider_duration_seconds": vertex_duration,
                        },
                        status_code=422,
                    )
                reference_bundle = _load_scene_reference_asset(
                    scene_reference=scene_reference,
                    correlation_id=correlation_id,
                )
            elif vertex_duration == 8:
                reference_bundle = _load_character_snapshot_assets(
                    snapshot=character_snapshot,
                    correlation_id=correlation_id,
                )
            else:
                reference_skip_metadata = {
                    "reference_images_enabled": False,
                    "reference_images_skipped_reason": "vertex_reference_images_support_only_8s_base",
                    "character_id": character_snapshot.get("character_id") if character_snapshot else None,
                    "character_name": character_snapshot.get("name") if character_snapshot else None,
                    "source": "batch_character_snapshot",
                }
                logger.info(
                    "vertex_character_snapshot_references_skipped_for_legacy_duration",
                    correlation_id=correlation_id,
                    character_id=reference_skip_metadata.get("character_id"),
                    duration_seconds=vertex_duration,
                )
        reference_images = reference_bundle["reference_images"] if reference_bundle else None
        try:
            result = vertex_client.submit_text_video(
                prompt=prompt_text,
                correlation_id=correlation_id,
                aspect_ratio=aspect_ratio,
                duration_seconds=vertex_duration,
                output_gcs_uri=output_gcs_uri,
                model=model,
                reference_images=reference_images,
                negative_prompt=negative_prompt,
                seed=seed,
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
        provider_metadata = dict(result)
        if reference_bundle:
            provider_metadata.update(reference_bundle["metadata"])
        if reference_skip_metadata:
            provider_metadata.update(reference_skip_metadata)
        if output_gcs_uri:
            provider_metadata["vertex_output_gcs_uri"] = output_gcs_uri
        return {
            "operation_id": result["operation_id"],
            "status": result.get("status", "submitted"),
            "provider_model": result.get("provider_model", "vertex_ai"),
            "requested_model": model,
            "requested_size": requested_size,
            "provider_requested_size": requested_size,
            "estimated_duration_seconds": 180,
            "provider_metadata": provider_metadata,
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
