from __future__ import annotations

from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.adapters.storage_client import get_storage_client
from app.core.logging import get_logger
from app.core.errors import ThirdPartyError
from app.features.characters import queries as character_queries
from app.features.characters.schemas import ActorIdentityRecord, CharacterRecord

router = APIRouter(prefix="/settings", tags=["characters"])
templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MIN_ACTOR_TRAINING_IMAGES = 8
MAX_ACTOR_TRAINING_IMAGES = 20


def _validate_upload(field_name: str, upload: Optional[UploadFile]) -> None:
    if upload is None or not upload.filename:
        raise HTTPException(status_code=422, detail=f"Missing image: {field_name}")
    if upload.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported content type for {field_name}: {upload.content_type}",
        )


def _read_capped(field_name: str, upload: UploadFile) -> bytes:
    data = upload.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"Image {field_name} exceeds 5 MB limit")
    return data


def _actor_identity_context(*, correlation_id: str) -> Optional[ActorIdentityRecord]:
    refreshed = character_queries.refresh_active_actor_identity_status(correlation_id=correlation_id)
    return refreshed or character_queries.get_active_actor_identity()


@router.get("/character")
def character_settings(request: Request):
    active = character_queries.get_active_character()
    return templates.TemplateResponse(
        "settings/character.html",
        {"request": request, "character": active},
    )


@router.get("/actor")
def actor_settings(request: Request):
    correlation_id = f"actor_settings_get_{uuid4()}"
    actor = _actor_identity_context(correlation_id=correlation_id)
    return templates.TemplateResponse(
        "settings/actor.html",
        {"request": request, "actor": actor},
    )


@router.post("/character")
async def upload_character(
    request: Request,
    name: str = Form(default="Default Character"),
    front: UploadFile = File(...),
    three_quarter: UploadFile = File(...),
    profile: UploadFile = File(...),
):
    correlation_id = str(uuid4())
    for field_name, upload in (("front", front), ("three_quarter", three_quarter), ("profile", profile)):
        _validate_upload(field_name, upload)

    storage = get_storage_client()
    uploaded: dict[str, str] = {}
    for field_name, upload in (("front", front), ("three_quarter", three_quarter), ("profile", profile)):
        result = storage.upload_image(
            image_bytes=_read_capped(field_name, upload),
            file_name=upload.filename or f"{field_name}.png",
            correlation_id=correlation_id,
            content_type=upload.content_type or "image/png",
        )
        uploaded[field_name] = result["url"]

    record: CharacterRecord = character_queries.upsert_active_character(
        name=name,
        front_image_url=uploaded["front"],
        three_quarter_image_url=uploaded["three_quarter"],
        profile_image_url=uploaded["profile"],
        correlation_id=correlation_id,
    )
    logger.info("character_settings_saved", correlation_id=correlation_id, character_id=record.id)
    return RedirectResponse(url="/settings/character", status_code=303)


@router.post("/actor")
async def upload_actor_identity(
    request: Request,
    name: str = Form(default="AYRA Actor Identity"),
    quality: str = Form(default="high"),
    gender: str = Form(default="woman"),
    consent_source: str = Form(default="Operator-provided reference set"),
    description: str = Form(default=""),
    training_images: list[UploadFile] = File(...),
):
    correlation_id = str(uuid4())
    if len(training_images) < MIN_ACTOR_TRAINING_IMAGES or len(training_images) > MAX_ACTOR_TRAINING_IMAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Actor training requires between {MIN_ACTOR_TRAINING_IMAGES} and {MAX_ACTOR_TRAINING_IMAGES} images",
        )

    for index, upload in enumerate(training_images, start=1):
        _validate_upload(f"training_images[{index}]", upload)

    storage = get_storage_client()
    uploaded_urls: list[str] = []
    for index, upload in enumerate(training_images, start=1):
        result = storage.upload_image(
            image_bytes=_read_capped(f"training_images[{index}]", upload),
            file_name=upload.filename or f"actor-training-{index}.png",
            correlation_id=correlation_id,
            content_type=upload.content_type or "image/png",
        )
        uploaded_urls.append(result["url"])

    submission = character_queries.upsert_active_actor_identity(
        name=name,
        provider="magnific",
        provider_training_task_id=None,
        provider_lora_id=None,
        provider_lora_name=None,
        training_status="queued",
        training_phase="queued",
        training_progress_percent=10,
        training_images=uploaded_urls,
        consent_source=consent_source,
        training_error=None,
    )
    logger.info(
        "actor_identity_training_dataset_uploaded",
        correlation_id=correlation_id,
        actor_identity_id=submission.id,
        image_count=len(uploaded_urls),
        quality=quality,
        gender=gender,
    )

    # The Magnific adapter is intentionally isolated so the UI can submit/retry without
    # coupling the settings page to the rest of the batch flow.
    from app.adapters.magnific_client import get_magnific_client

    try:
        status = get_magnific_client().train_character_lora(
            name=name,
            quality=quality,
            gender=gender,
            image_urls=uploaded_urls,
            correlation_id=correlation_id,
            description=description or None,
        )
    except Exception as exc:
        logger.warning(
            "actor_identity_training_submission_failed",
            correlation_id=correlation_id,
            actor_identity_id=submission.id,
            error=str(exc),
        )
        character_queries.upsert_active_actor_identity(
            name=name,
            provider="magnific",
            provider_training_task_id=submission.provider_training_task_id,
            provider_lora_id=submission.provider_lora_id,
            provider_lora_name=submission.provider_lora_name,
            training_status="failed",
            training_phase="failed",
            training_progress_percent=0,
            training_images=uploaded_urls,
            consent_source=consent_source,
            training_error=str(exc),
        )
        raise ThirdPartyError(
            "Actor training submission failed.",
            details={"provider": "magnific", "correlation_id": correlation_id, "error": str(exc)},
        ) from exc

    updated = character_queries.upsert_active_actor_identity(
        name=name,
        provider="magnific",
        provider_training_task_id=status.provider_training_task_id,
        provider_lora_id=status.provider_lora_id,
        provider_lora_name=status.provider_lora_name,
        training_status=status.training_status,
        training_phase=status.training_phase,
        training_progress_percent=status.training_progress_percent,
        training_images=uploaded_urls,
        consent_source=consent_source,
        training_error=status.training_error,
        training_started_at=None,
        training_completed_at=None,
    )
    logger.info(
        "actor_identity_training_submitted",
        correlation_id=correlation_id,
        actor_identity_id=updated.id,
        provider_training_task_id=updated.provider_training_task_id,
        provider_lora_id=updated.provider_lora_id,
        training_status=updated.training_status,
    )
    return RedirectResponse(url="/settings/actor", status_code=303)
