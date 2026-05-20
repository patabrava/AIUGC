from __future__ import annotations

import re
import json
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.adapters.magnific_client import get_magnific_client, normalize_lora_training_status
from app.adapters.storage_client import get_storage_client
from app.adapters.supabase_client import get_supabase
from app.core.logging import get_logger
from app.features.characters.actor_identity import actor_identity_is_ready, passed_manual_gate, pending_manual_gate
from app.features.characters import queries as character_queries
from app.features.characters.schemas import ActorTrainingSet, CharacterRecord
from app.features.characters.scene_reference import build_scene_reference_prompt, map_script_to_scene_intent

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


def _load_json_object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _single_row(response, *, label: str):
    rows = getattr(response, "data", None) or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return rows[0]


def _extract_mystic_image_url(task: dict) -> Optional[str]:
    for key in ("image_url", "url", "output_url"):
        if task.get(key):
            return str(task[key])
    images = task.get("images") or task.get("outputs") or task.get("result")
    if isinstance(images, list):
        for item in images:
            if isinstance(item, str):
                return item
            if isinstance(item, dict):
                for key in ("url", "image_url", "output_url"):
                    if item.get(key):
                        return str(item[key])
    if isinstance(images, dict):
        for key in ("url", "image_url", "output_url"):
            if images.get(key):
                return str(images[key])
    return None


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


@router.post("/character/posts/{post_id}/scene-reference/generate")
def generate_scene_reference(post_id: str):
    correlation_id = str(uuid4())
    supabase = get_supabase().client
    post = _single_row(supabase.table("posts").select("*").eq("id", post_id).execute(), label="Post")
    batch = _single_row(supabase.table("batches").select("*").eq("id", post.get("batch_id")).execute(), label="Batch")

    actor_identity = character_queries.get_active_actor_identity()
    if not actor_identity_is_ready(actor_identity):
        raise HTTPException(status_code=422, detail="ActorIdentity training is not complete")
    if batch.get("actor_identity_id") and str(batch.get("actor_identity_id")) != actor_identity.id:
        raise HTTPException(status_code=422, detail="Active ActorIdentity does not match this batch")

    seed_data = _load_json_object(post.get("seed_data"))
    script = str(seed_data.get("script") or seed_data.get("dialog_script") or post.get("topic_title") or "")
    target_length_tier = int(seed_data.get("target_length_tier") or batch.get("target_length_tier") or 8)
    intent = map_script_to_scene_intent(
        script=script,
        post_type=str(post.get("post_type") or ""),
        target_length_tier=target_length_tier,
        seed_data=seed_data,
    )
    prompt = build_scene_reference_prompt(
        actor_name=actor_identity.name,
        scene_key=intent.scene_key,
        wardrobe_key=intent.wardrobe_key,
        post_type=str(post.get("post_type") or ""),
    )

    client = get_magnific_client()
    references = []
    for candidate_idx in range(3):
        task = client.create_mystic_scene_reference(
            prompt=prompt,
            lora_id=str(actor_identity.provider_lora_id),
            strength=100,
            correlation_id=correlation_id,
            extra_options={"seed": candidate_idx + 1},
        )
        references.append(
            character_queries.create_scene_reference_candidate(
                actor_identity_id=actor_identity.id,
                post_id=post_id,
                scene_key=intent.scene_key,
                wardrobe_key=intent.wardrobe_key,
                provider_task_id=str(task.get("task_id") or ""),
                image_url=_extract_mystic_image_url(task),
                prompt=prompt,
                provider_metadata={
                    "task": task,
                    "reason_code": intent.reason_code,
                    "candidate_index": candidate_idx,
                },
                correlation_id=correlation_id,
            )
        )
    logger.info("scene_reference_generation_submitted", correlation_id=correlation_id, post_id=post_id, count=len(references))
    return RedirectResponse(url=f"/batches/{post.get('batch_id')}", status_code=303)


@router.post("/character/scene-reference/{reference_id}/approve")
def approve_scene_reference(reference_id: str):
    correlation_id = str(uuid4())
    reference = character_queries.get_scene_reference_by_id(reference_id)
    if not reference:
        raise HTTPException(status_code=404, detail="Scene reference not found")
    gate = passed_manual_gate()
    character_queries.record_scene_reference_gate(
        reference_id=reference_id,
        gate_result=gate,
        status="approved",
        correlation_id=correlation_id,
    )
    character_queries.attach_scene_reference_to_post(
        post_id=str(reference["post_id"]),
        reference_id=reference_id,
        gate_result=gate,
        correlation_id=correlation_id,
    )
    return RedirectResponse(url=f"/batches/{reference.get('batch_id') or ''}", status_code=303)


@router.post("/character/scene-reference/{reference_id}/reject")
def reject_scene_reference(reference_id: str):
    correlation_id = str(uuid4())
    reference = character_queries.get_scene_reference_by_id(reference_id)
    if not reference:
        raise HTTPException(status_code=404, detail="Scene reference not found")
    character_queries.record_scene_reference_gate(
        reference_id=reference_id,
        gate_result=pending_manual_gate("Operator rejected the scene reference; regenerate or approve another candidate"),
        status="rejected",
        correlation_id=correlation_id,
    )
    return RedirectResponse(url=f"/settings/character", status_code=303)
