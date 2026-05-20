from __future__ import annotations

import re
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.adapters.magnific_client import get_magnific_client, normalize_lora_training_status
from app.adapters.storage_client import get_storage_client
from app.core.logging import get_logger
from app.features.characters.actor_identity import actor_identity_is_ready
from app.features.characters import queries as character_queries
from app.features.characters.schemas import ActorTrainingSet, CharacterRecord

router = APIRouter(prefix="/settings", tags=["characters"])
templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


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


@router.get("/character")
def character_settings(request: Request):
    active = character_queries.get_active_character()
    actor_identity = character_queries.get_active_actor_identity()
    return templates.TemplateResponse(
        "settings/character.html",
        {
            "request": request,
            "character": active,
            "actor_identity": actor_identity,
            "actor_identity_ready": actor_identity_is_ready(actor_identity),
        },
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


def _provider_safe_name(name: str, actor_identity_id: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip().lower()).strip("_") or "actor"
    suffix = re.sub(r"[^a-zA-Z0-9]+", "", actor_identity_id)[0:8]
    return f"{base}_{suffix}"


@router.post("/character/actor")
async def train_actor_identity(
    request: Request,
    name: str = Form(default="Default Actor"),
    gender: str = Form(default="female"),
    quality: str = Form(default="high"),
    consent_source: str = Form(default=""),
    training_images: list[UploadFile] = File(...),
):
    correlation_id = str(uuid4())
    if len(training_images) < 8 or len(training_images) > 20:
        raise HTTPException(status_code=422, detail="ActorIdentity training requires 8-20 images")

    for idx, upload in enumerate(training_images, start=1):
        _validate_upload(f"training_image_{idx}", upload)

    storage = get_storage_client()
    uploaded_urls: list[str] = []
    for idx, upload in enumerate(training_images, start=1):
        result = storage.upload_image(
            image_bytes=_read_capped(f"training_image_{idx}", upload),
            file_name=upload.filename or f"actor-{idx}.png",
            correlation_id=correlation_id,
            content_type=upload.content_type or "image/png",
        )
        uploaded_urls.append(result["url"])

    training = ActorTrainingSet(images=uploaded_urls, consent_source=consent_source)
    identity = character_queries.upsert_active_actor_identity(
        name=name,
        training_images=training.images,
        consent_source=training.consent_source,
        correlation_id=correlation_id,
    )
    provider_name = _provider_safe_name(identity.name, identity.id)
    task = get_magnific_client().submit_character_training(
        name=provider_name,
        quality=quality,
        gender=gender,
        images=training.images,
        description=f"ActorIdentity {identity.id}",
        webhook_url=None,
        correlation_id=correlation_id,
    )
    character_queries.mark_actor_training_submitted(
        actor_identity_id=identity.id,
        provider_training_task_id=str(task.get("task_id") or ""),
        provider_lora_name=provider_name,
        raw_status=str(task.get("status") or "in_progress"),
        correlation_id=correlation_id,
    )
    logger.info(
        "actor_identity_training_started",
        correlation_id=correlation_id,
        actor_identity_id=identity.id,
        training_image_count=len(training.images),
    )
    return RedirectResponse(url="/settings/character", status_code=303)


@router.post("/character/actor/poll")
def poll_actor_identity_training(request: Request):
    correlation_id = str(uuid4())
    actor_identity = character_queries.get_active_actor_identity()
    if actor_identity is not None and not actor_identity_is_ready(actor_identity):
        loras = get_magnific_client().list_loras(correlation_id=correlation_id)
        rows = loras.get("data") if isinstance(loras.get("data"), list) else []
        match = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if actor_identity.provider_lora_id and str(row.get("id")) == str(actor_identity.provider_lora_id):
                match = row
                break
            if actor_identity.provider_lora_name and str(row.get("name")) == actor_identity.provider_lora_name:
                match = row
                break
        if match:
            status = normalize_lora_training_status(match)
            character_queries.update_actor_training_status(
                actor_identity_id=actor_identity.id,
                training_status=status.raw_status,
                training_phase=status.phase,
                training_progress_percent=status.progress_percent,
                provider_lora_id=status.provider_lora_id,
                provider_lora_name=status.provider_lora_name,
                training_error=None,
                correlation_id=correlation_id,
            )
            actor_identity = character_queries.get_active_actor_identity()

    return templates.TemplateResponse(
        "settings/character.html",
        {
            "request": request,
            "character": character_queries.get_active_character(),
            "actor_identity": actor_identity,
            "actor_identity_ready": actor_identity_is_ready(actor_identity),
        },
    )
