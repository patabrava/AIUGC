from __future__ import annotations

from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.adapters.storage_client import get_storage_client
from app.core.logging import get_logger
from app.features.characters import queries as character_queries
from app.features.characters.schemas import CharacterRecord

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
    return templates.TemplateResponse(
        "settings/character.html",
        {"request": request, "character": active},
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
