from __future__ import annotations

from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.adapters.llm_client import get_llm_client
from app.adapters.storage_client import get_storage_client
from app.core.errors import SuccessResponse
from app.core.logging import get_logger
from app.features.characters.scene_reference import get_scene_bible
from app.features.scenes import queries as scene_queries

router = APIRouter(prefix="/settings/scenes", tags=["scenes"])
logger = get_logger(__name__)

CANONICAL_SCENE_SYSTEM_PROMPT_NAME = "reality_first_prompt_v1"
CANONICAL_SCENE_SYSTEM_PROMPT = """Reality-First Prompt
Mission: Write high-control prompts that reliably produce believable real-world results by anchoring hard constraints, observable details, one priority focus, and targeted negatives.
Operating principles:
- Hard constraints first.
- Use photographer and designer language, not vague aesthetics.
- Keep the scene physically stageable and visually plausible.
- Define realism using observable cues and true-to-life material behavior.
- Keep lighting, color, and camera behavior internally consistent.
- Use targeted strict negatives to prevent the AI look, glow, fake HDR, overprocessing, and text artifacts.
Output rule:
- Produce one production-ready image-generation prompt for a single photoreal vertical scene image.
- Keep the language direct and implementation-ready.
"""


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


def _build_canonical_scene_prompt(scene_key: str) -> str:
    bible = get_scene_bible(scene_key)
    return (
        f"Constraints: 9:16 vertical, photoreal smartphone-style interior or exterior scene still, no person in frame, "
        f"the image must function as the canonical environment anchor for repeated video generation. "
        f"Depict this exact place identity: {bible.scene_identity}\n\n"
        f"Composition: {bible.composition} Keep the camera locked to a believable smartphone perspective, "
        f"medium room-context framing, and preserve the anchor objects in stable relative positions.\n\n"
        f"Environment: {bible.generation_anchor}. Lighting: {bible.lighting}\n\n"
        f"Materials and textures: show true-to-life surfaces, natural material separation, believable wear, and no stylization.\n\n"
        "Primary focus:\n"
        "- scene-layout consistency\n"
        "- anchor-object identity stability\n"
        "- photoreal lighting and material realism\n"
        "- sparse uncluttered background with no drift props\n\n"
        "Color and light: true-to-life white balance, natural contrast, realistic exposure, no washed-out grading, no pastel fade.\n\n"
        "Camera behavior: modern smartphone realism, slight natural edge softness, no heavy sharpening, no synthetic HDR glow.\n\n"
        f"Overall aesthetic: quiet documentary realism, consistent with a reusable canonical scene plate for UGC video production.\n\n"
        "Strict negatives: people, hands, wheelchair, text, logos, watermark, UI overlays, fake HDR, bloom, glow, over-sharpening, "
        f"{', '.join(bible.scene_specific_rejectors)}"
    )


def generate_canonical_scene_asset(*, scene_key: str, correlation_id: str, force: bool = False):
    scene_bible = get_scene_bible(scene_key)
    existing = scene_queries.get_canonical_scene_asset(scene_key=scene_bible.scene_id)
    if existing and existing.status == "generated" and existing.image_url and not force:
        return existing

    prompt_text = _build_canonical_scene_prompt(scene_bible.scene_id)
    image_result = get_llm_client().generate_gemini_image(
        prompt=prompt_text,
        system_prompt=CANONICAL_SCENE_SYSTEM_PROMPT,
        model="nanobananapro",
        temperature=0.6,
        max_tokens=2048,
        aspect_ratio="9:16",
        image_size="1K",
    )
    extension = _mime_extension(str(image_result.get("mime_type") or "image/png"))
    object_key = (
        f"{get_storage_client().image_prefix}/canonical-scenes/{scene_bible.scene_id}/"
        f"v{scene_bible.version}.{extension}"
    )
    uploaded = get_storage_client().ensure_image(
        image_bytes=image_result["image_bytes"],
        object_key=object_key,
        correlation_id=correlation_id,
        content_type=str(image_result.get("mime_type") or "image/png"),
    )
    provider_metadata = {
        **scene_bible.provider_metadata(),
        "generation_kind": "canonical_scene_anchor",
        "prompt_text": prompt_text,
        "system_prompt_name": CANONICAL_SCENE_SYSTEM_PROMPT_NAME,
        "aspect_ratio": "9:16",
        "image_size": "1K",
    }
    return scene_queries.create_canonical_scene_asset(
        scene_key=scene_bible.scene_id,
        provider="vertex_gemini",
        provider_model=str(image_result.get("model") or "gemini-3-pro-image-preview"),
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
