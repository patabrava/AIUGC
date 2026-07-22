from __future__ import annotations

from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.adapters.storage_client import get_storage_client
from app.core.errors import SuccessResponse
from app.core.logging import get_logger
from app.features.characters.scene_reference import get_scene_bible
from app.features.scenes import queries as scene_queries
from app.features.scenes.background_comparison import generate_raw_camera_background

router = APIRouter(prefix="/settings/scenes", tags=["scenes"])
logger = get_logger(__name__)

CANONICAL_SCENE_SYSTEM_PROMPT_NAME = "raw_camera_casting_realism_v2"


class GenerateCanonicalSceneRequest(BaseModel):
    scene_key: str = Field(..., min_length=1, max_length=120)
    force: bool = False


def _mime_extension(mime_type: str) -> str:
    normalized = str(mime_type or "").lower()
    if "png" in normalized:
        return "png"
    if "webp" in normalized:
        return "webp"
    if "jpeg" in normalized or "jpg" in normalized:
        return "jpg"
    return "bin"


def generate_canonical_scene_asset(*, scene_key: str, correlation_id: str, force: bool = False):
    scene_bible = get_scene_bible(scene_key)
    existing = scene_queries.get_canonical_scene_asset(scene_key=scene_bible.scene_id)
    if existing and existing.status == "generated" and existing.image_url and not force:
        return existing

    image_result = generate_raw_camera_background(
        scene_key=scene_bible.scene_id,
        image_size="1K",
    )
    prompt_text = image_result.prompt_writer_output
    extension = _mime_extension(image_result.mime_type)
    object_key = (
        f"{get_storage_client().image_prefix}/canonical-scenes/{scene_bible.scene_id}/"
        f"v{scene_bible.version}.{extension}"
    )
    uploaded = get_storage_client().ensure_image(
        image_bytes=image_result.image_bytes,
        object_key=object_key,
        correlation_id=correlation_id,
        content_type=image_result.mime_type,
    )
    provider_metadata = {
        **scene_bible.provider_metadata(),
        "generation_kind": "canonical_scene_anchor",
        "prompt_text": prompt_text,
        "prompt_writer_brief": image_result.prompt_writer_brief,
        "system_prompt_name": CANONICAL_SCENE_SYSTEM_PROMPT_NAME,
        "production_prompt_default": True,
        "aspect_ratio": "9:16",
        "image_size": "1K",
    }
    return scene_queries.create_canonical_scene_asset(
        scene_key=scene_bible.scene_id,
        provider="vertex_gemini",
        provider_model=image_result.provider_model,
        system_prompt_name=CANONICAL_SCENE_SYSTEM_PROMPT_NAME,
        prompt_text=prompt_text,
        aspect_ratio="9:16",
        image_size="1K",
        image_url=uploaded["url"],
        storage_key=uploaded.get("storage_key"),
        provider_metadata=provider_metadata,
        correlation_id=correlation_id,
    )


@router.post("/canonical/generate", response_model=SuccessResponse)
def generate_canonical_scene(request: GenerateCanonicalSceneRequest):
    correlation_id = str(uuid4())
    try:
        record = generate_canonical_scene_asset(
            scene_key=request.scene_key,
            correlation_id=correlation_id,
            force=request.force,
        )
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown canonical scene key: {request.scene_key}") from exc
    except Exception as exc:
        logger.exception(
            "canonical_scene_generation_failed",
            correlation_id=correlation_id,
            scene_key=request.scene_key,
        )
        raise HTTPException(status_code=500, detail=f"Canonical scene generation failed: {exc}") from exc
    return SuccessResponse(
        data={
            "scene_key": record.scene_key,
            "scene_bible_version": record.scene_bible_version,
            "image_url": record.image_url,
            "storage_key": record.storage_key,
            "provider_model": record.provider_model,
            "status": record.status,
        }
    )
