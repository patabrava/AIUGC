from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from postgrest.exceptions import APIError

from app.adapters.supabase_client import get_supabase
from app.core.errors import ErrorCode, FlowForgeException
from app.core.logging import get_logger
from app.features.characters.scene_reference import (
    SCENE_BIBLES,
    get_scene_bible,
    map_script_to_scene_intent,
)
from app.features.scenes.schemas import CanonicalSceneAssetRecord

logger = get_logger(__name__)


def _is_missing_canonical_scene_assets_table(exc: APIError) -> bool:
    text = str(exc).lower()
    return "missing response" in text or ("canonical_scene_assets" in text and ("404" in text or "not found" in text))


def _normalize_scene_text(scene_text: Optional[str]) -> str:
    cleaned = str(scene_text or "").strip()
    if cleaned.startswith("Scene:"):
        cleaned = cleaned[len("Scene:"):].strip()
    return " ".join(cleaned.split())


def resolve_canonical_scene_key(
    *,
    scene_text: Optional[str] = None,
    prompt_text: Optional[str] = None,
    post_type: Optional[str] = None,
    seed_data: Optional[dict[str, Any]] = None,
    target_length_tier: int = 8,
) -> str:
    # An explicit, valid scene key/alias in scene_text is authoritative.
    direct_scene_key = str(scene_text or "").strip()
    if direct_scene_key:
        try:
            return get_scene_bible(direct_scene_key).scene_id
        except KeyError:
            pass

    # The per-post script/topic is the only per-video discriminator under the canon's scene
    # model. When the post carries real content, the content router decides (a specialized
    # scene on an explicit keyword, otherwise a deterministic neutral-pool rotation) and that
    # must win over scene_text/prompt_text, which carry the batch-level scene_plan prose
    # (shared across every post of the same post_type, and the verbatim home identity under
    # the fallback plan). Without this, those posts collapse onto one canonical scene image
    # and it never switches per video.
    sd = seed_data or {}
    script = str(sd.get("script") or sd.get("dialog_script") or "")
    intent = map_script_to_scene_intent(
        script=script,
        post_type=str(post_type or sd.get("post_type") or "value"),
        target_length_tier=target_length_tier,
        seed_data=sd,
    )
    content_fields = ("script", "dialog_script", "topic_title", "canonical_topic", "research_title", "topic")
    if any(str(sd.get(field) or "").strip() for field in content_fields):
        return intent.scene_key

    # No per-post content (internal fallback callers): honor a canonical scene named in the
    # scene/prompt text if present, otherwise the content router's default.
    normalized_scene = _normalize_scene_text(scene_text)
    if not normalized_scene and prompt_text:
        prompt_text_value = str(prompt_text)
        if "Scene:" in prompt_text_value:
            scene_block = prompt_text_value.split("Scene:", 1)[1].split("\n\n", 1)[0]
            normalized_scene = _normalize_scene_text(scene_block)

    if normalized_scene:
        for bible in SCENE_BIBLES.values():
            normalized_identity = " ".join(str(bible.scene_identity).split())
            if normalized_scene == normalized_identity or normalized_identity in normalized_scene:
                return bible.scene_id

    return intent.scene_key


def get_canonical_scene_asset(
    *,
    scene_key: str,
    scene_bible_version: Optional[int] = None,
    aspect_ratio: str = "9:16",
    image_size: str = "1K",
) -> Optional[CanonicalSceneAssetRecord]:
    scene_bible = get_scene_bible(scene_key)
    target_version = scene_bible_version or scene_bible.version
    try:
        response = (
            get_supabase()
            .client.table("canonical_scene_assets")
            .select("*")
            .eq("scene_key", scene_bible.scene_id)
            .eq("scene_bible_version", target_version)
            .eq("aspect_ratio", aspect_ratio)
            .eq("image_size", image_size)
            .order("created_at", desc=True)
            .limit(1)
            .maybe_single()
            .execute()
        )
    except APIError as exc:
        if _is_missing_canonical_scene_assets_table(exc):
            logger.warning("canonical_scene_assets_table_missing", error=str(exc))
            return None
        raise
    row = getattr(response, "data", None)
    if not row:
        return None
    return CanonicalSceneAssetRecord.model_validate(row)


def require_canonical_scene_asset(
    *,
    scene_key: str,
    aspect_ratio: str = "9:16",
    image_size: str = "1K",
) -> CanonicalSceneAssetRecord:
    record = get_canonical_scene_asset(scene_key=scene_key, aspect_ratio=aspect_ratio, image_size=image_size)
    if record is None or record.status != "generated" or not record.image_url:
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message=(
                "Character-consistency video generation requires a generated canonical scene image for the selected scene."
            ),
            details={
                "scene_key": scene_key,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
            },
            status_code=422,
        )
    return record


def create_canonical_scene_asset(
    *,
    scene_key: str,
    provider: str,
    provider_model: str,
    system_prompt_name: str,
    prompt_text: str,
    aspect_ratio: str,
    image_size: str,
    image_url: Optional[str],
    storage_key: Optional[str],
    provider_metadata: dict[str, Any],
    correlation_id: str,
    status: str = "generated",
) -> CanonicalSceneAssetRecord:
    scene_bible = get_scene_bible(scene_key)
    existing = get_canonical_scene_asset(
        scene_key=scene_bible.scene_id,
        scene_bible_version=scene_bible.version,
        aspect_ratio=aspect_ratio,
        image_size=image_size,
    )
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "id": existing.id if existing else str(uuid4()),
        "scene_key": scene_bible.scene_id,
        "scene_bible_version": scene_bible.version,
        "status": status,
        "provider": provider,
        "provider_model": provider_model,
        "system_prompt_name": system_prompt_name,
        "prompt_text": prompt_text,
        "aspect_ratio": aspect_ratio,
        "image_size": image_size,
        "image_url": image_url,
        "storage_key": storage_key,
        "provider_metadata": provider_metadata,
        "generated_at": now if image_url else None,
        "created_at": existing.created_at.isoformat() if existing else now,
        "updated_at": now,
    }
    if existing:
        get_supabase().client.table("canonical_scene_assets").update(payload).eq("id", existing.id).execute()
    else:
        get_supabase().client.table("canonical_scene_assets").insert(payload).execute()
    logger.info(
        "canonical_scene_asset_created",
        correlation_id=correlation_id,
        scene_key=scene_bible.scene_id,
        scene_bible_version=scene_bible.version,
        image_url=image_url,
    )
    return CanonicalSceneAssetRecord.model_validate(payload)
