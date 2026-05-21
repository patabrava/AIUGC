from __future__ import annotations

import re
import json
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.adapters.magnific_client import get_magnific_client, list_lora_rows, normalize_lora_training_status
from app.adapters.storage_client import get_storage_client
from app.adapters.supabase_client import get_supabase
from app.core.errors import FlowForgeException
from app.core.logging import get_logger
from app.features.characters.actor_identity import (
    actor_identity_is_ready,
    actor_identity_training_ready,
    passed_manual_gate,
    pending_manual_gate,
)
from app.features.characters import queries as character_queries
from app.features.characters.schemas import ActorTrainingSet, CharacterRecord
from app.features.characters.scene_reference import (
    REQUIRED_SCENE_REFERENCE_ANGLES,
    build_scene_reference_prompt_for_angle,
    get_scene_reference_angle,
    map_script_to_scene_intent,
)

router = APIRouter(prefix="/settings", tags=["characters"])
templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _actor_identity_context(*, correlation_id: str):
    refreshed = character_queries.refresh_active_actor_identity_status(correlation_id=correlation_id)
    return refreshed or character_queries.get_active_actor_identity()


def _actor_settings_context(*, request: Request, correlation_id: str) -> dict:
    actor = _actor_identity_context(correlation_id=correlation_id)
    roster_error = None
    try:
        character_queries.sync_actor_identity_roster_from_provider(correlation_id=correlation_id)
        actors = character_queries.list_actor_identities()
        actors = character_queries.refresh_actor_identity_roster_statuses(
            actors,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # noqa: BLE001 - settings page must keep training available
        actors = []
        roster_error = "Actor roster could not be loaded. Training form is still available."
        logger.warning(
            "actor_identity_roster_load_failed",
            correlation_id=correlation_id,
            error=str(exc),
        )
    ready_actors = [row for row in actors if actor_identity_training_ready(row)]
    return {
        "request": request,
        "actor": actor,
        "actors": actors,
        "ready_actors": ready_actors,
        "actor_roster_error": roster_error,
        "active_actor_updated": request.query_params.get("active_actor_updated") == "1",
    }


def _ready_actor_identity_for_batch(batch: dict):
    actor_identity_id = str(batch.get("actor_identity_id") or "").strip()
    actor_identity = (
        character_queries.get_actor_identity_by_id(actor_identity_id)
        if actor_identity_id
        else character_queries.get_active_actor_identity()
    )
    if not actor_identity_training_ready(actor_identity):
        raise HTTPException(status_code=422, detail="ActorIdentity training is not complete")
    return actor_identity


def _ready_actor_identity_for_reference(reference: dict):
    actor_identity_id = str(reference.get("actor_identity_id") or "").strip()
    if not actor_identity_id:
        raise HTTPException(status_code=422, detail="Scene reference is missing ActorIdentity metadata")
    actor_identity = character_queries.get_actor_identity_by_id(actor_identity_id)
    if not actor_identity_training_ready(actor_identity):
        raise HTTPException(status_code=422, detail="ActorIdentity training is not complete")
    return actor_identity


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
    roster_error = None
    try:
        actors = character_queries.refresh_actor_identity_roster_statuses(
            character_queries.list_actor_identities(),
            correlation_id=f"character_settings_{uuid4()}",
        )
    except Exception as exc:  # noqa: BLE001 - legacy settings page must still render
        actors = []
        roster_error = "Actor roster could not be loaded."
        logger.warning("character_settings_actor_roster_load_failed", error=str(exc))
    return templates.TemplateResponse(
        "settings/character.html",
        {
            "request": request,
            "character": active,
            "actor_identity": actor_identity,
            "actor_identity_ready": actor_identity_is_ready(actor_identity),
            "actors": actors,
            "actor_roster_error": roster_error,
        },
    )


@router.get("/actor")
def actor_settings(request: Request):
    correlation_id = f"actor_settings_get_{uuid4()}"
    return templates.TemplateResponse(
        "settings/actor.html",
        _actor_settings_context(request=request, correlation_id=correlation_id),
    )


@router.post("/actor/active")
def activate_actor_identity(
    request: Request,
    actor_identity_id: str = Form(...),
):
    correlation_id = str(uuid4())
    try:
        character_queries.set_active_actor_identity(
            actor_identity_id=actor_identity_id,
            correlation_id=correlation_id,
        )
    except FlowForgeException as exc:
        logger.warning(
            "actor_identity_activation_rejected",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
            error=exc.message,
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except ValueError as exc:
        logger.warning(
            "actor_identity_activation_rejected",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
            error=str(exc),
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "actor_identity_activation_route_failed",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
        )
        raise HTTPException(
            status_code=500,
            detail="ActorIdentity activation failed; the previous active actor was restored when possible.",
        ) from exc
    return RedirectResponse(url="/settings/actor?active_actor_updated=1", status_code=303)


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
    images = task.get("generated") or task.get("images") or task.get("outputs") or task.get("result")
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


def _post_batch_id(post_id: str) -> str:
    post = _single_row(
        get_supabase().client.table("posts").select("batch_id").eq("id", post_id).execute(),
        label="Post",
    )
    return str(post.get("batch_id") or "")


@router.post("/actor")
@router.post("/character/actor")
async def train_actor_identity(
    request: Request,
    name: str = Form(default="Default Actor"),
    gender: str = Form(default="female"),
    quality: str = Form(default="high"),
    consent_source: str = Form(default=""),
    description: str = Form(default=""),
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
    if request.url.path == "/settings/actor":
        from app.adapters import magnific_client as magnific_adapter

        identity = character_queries.create_actor_identity(
            name=name,
            provider="magnific",
            provider_training_task_id=None,
            provider_lora_id=None,
            provider_lora_name=None,
            training_status="queued",
            training_phase="queued",
            training_progress_percent=10,
            training_images=training.images,
            consent_source=training.consent_source,
            training_error=None,
            correlation_id=correlation_id,
            is_active=False,
        )
        status = magnific_adapter.get_magnific_client().train_character_lora(
            name=name,
            quality=quality,
            gender=gender,
            image_urls=training.images,
            correlation_id=correlation_id,
            description=description or None,
        )
        character_queries.update_actor_training_status(
            actor_identity_id=identity.id,
            provider_training_task_id=status.provider_training_task_id,
            provider_lora_id=status.provider_lora_id,
            provider_lora_name=status.provider_lora_name,
            training_status=str(status.training_status or status.raw_status),
            training_phase=str(status.training_phase or status.phase),
            training_progress_percent=int(status.training_progress_percent or status.progress_percent or 0),
            training_error=status.training_error,
            correlation_id=correlation_id,
        )
        identity = character_queries.get_actor_identity_by_id(identity.id) or identity
        redirect_url = "/settings/actor"
    else:
        identity = character_queries.create_actor_identity(
            name=name,
            training_images=training.images,
            consent_source=training.consent_source,
            correlation_id=correlation_id,
            is_active=False,
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
        redirect_url = "/settings/character"
    logger.info(
        "actor_identity_training_started",
        correlation_id=correlation_id,
        actor_identity_id=identity.id,
        training_image_count=len(training.images),
    )
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/character/actor/poll")
def poll_actor_identity_training(request: Request):
    correlation_id = str(uuid4())
    actor_identity = character_queries.get_active_actor_identity()
    if actor_identity is not None and not actor_identity_is_ready(actor_identity):
        loras = get_magnific_client().list_loras(correlation_id=correlation_id)
        match = None
        for row in list_lora_rows(loras):
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

    actor_identity = _ready_actor_identity_for_batch(batch)

    seed_data = _load_json_object(post.get("seed_data"))
    script = str(seed_data.get("script") or seed_data.get("dialog_script") or post.get("topic_title") or "")
    target_length_tier = int(seed_data.get("target_length_tier") or batch.get("target_length_tier") or 8)
    intent = map_script_to_scene_intent(
        script=script,
        post_type=str(post.get("post_type") or ""),
        target_length_tier=target_length_tier,
        seed_data=seed_data,
    )
    client = get_magnific_client()
    reference_set_id = str(uuid4())
    references = []
    for angle in REQUIRED_SCENE_REFERENCE_ANGLES:
        prompt = build_scene_reference_prompt_for_angle(
            actor_name=actor_identity.name,
            scene_key=intent.scene_key,
            wardrobe_key=intent.wardrobe_key,
            post_type=str(post.get("post_type") or ""),
            angle_key=angle.key,
            provider_lora_name=actor_identity.provider_lora_name,
        )
        task = client.create_mystic_scene_reference(
            prompt=prompt,
            lora_id=str(actor_identity.provider_lora_id),
            strength=100,
            correlation_id=correlation_id,
            extra_options={"seed": angle.seed_offset},
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
                    "angle_key": angle.key,
                    "angle_label": angle.label,
                    "reference_set_id": reference_set_id,
                    "reference_set_status": "pending_review",
                },
                reference_set_id=reference_set_id,
                angle_key=angle.key,
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
    if not reference.get("image_url"):
        raise HTTPException(status_code=422, detail="Scene reference image is not generated yet")
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
    return RedirectResponse(url=f"/batches/{_post_batch_id(str(reference.get('post_id')))}", status_code=303)


@router.post("/character/scene-reference/{reference_id}/poll")
def poll_scene_reference(reference_id: str):
    correlation_id = str(uuid4())
    reference = character_queries.get_scene_reference_by_id(reference_id)
    if not reference:
        raise HTTPException(status_code=404, detail="Scene reference not found")
    task_id = str(reference.get("provider_task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=422, detail="Scene reference has no provider task id")

    task = get_magnific_client().get_mystic_task(task_id=task_id, correlation_id=correlation_id)
    image_url = _extract_mystic_image_url(task)
    if image_url:
        metadata = reference.get("provider_metadata") if isinstance(reference.get("provider_metadata"), dict) else {}
        character_queries.mark_scene_reference_generated(
            reference_id=reference_id,
            image_url=image_url,
            provider_metadata={**metadata, "poll_task": task},
            correlation_id=correlation_id,
        )
    return RedirectResponse(url=f"/batches/{_post_batch_id(str(reference.get('post_id')))}", status_code=303)


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
    return RedirectResponse(url=f"/batches/{_post_batch_id(str(reference.get('post_id')))}", status_code=303)


@router.post("/character/scene-reference/{reference_id}/regenerate")
def regenerate_scene_reference(reference_id: str):
    correlation_id = str(uuid4())
    reference = character_queries.get_scene_reference_by_id(reference_id)
    if not reference:
        raise HTTPException(status_code=404, detail="Scene reference not found")

    metadata = reference.get("provider_metadata") if isinstance(reference.get("provider_metadata"), dict) else {}
    angle_key = str(metadata.get("angle_key") or "")
    reference_set_id = str(metadata.get("reference_set_id") or "")
    if not angle_key or not reference_set_id:
        raise HTTPException(status_code=422, detail="Scene reference is missing set metadata")

    angle = get_scene_reference_angle(angle_key)
    actor_identity = _ready_actor_identity_for_reference(reference)

    prompt = build_scene_reference_prompt_for_angle(
        actor_name=actor_identity.name,
        scene_key=str(reference.get("scene_key") or ""),
        wardrobe_key=str(reference.get("wardrobe_key") or ""),
        post_type="",
        angle_key=angle.key,
        provider_lora_name=actor_identity.provider_lora_name,
    )
    task = get_magnific_client().create_mystic_scene_reference(
        prompt=prompt,
        lora_id=str(actor_identity.provider_lora_id),
        strength=100,
        correlation_id=correlation_id,
        extra_options={"seed": angle.seed_offset + 1000},
    )
    character_queries.create_scene_reference_candidate(
        actor_identity_id=actor_identity.id,
        post_id=str(reference["post_id"]),
        scene_key=str(reference.get("scene_key") or ""),
        wardrobe_key=str(reference.get("wardrobe_key") or ""),
        provider_task_id=str(task.get("task_id") or ""),
        image_url=_extract_mystic_image_url(task),
        prompt=prompt,
        provider_metadata={
            "task": task,
            "angle_key": angle.key,
            "angle_label": angle.label,
            "reference_set_id": reference_set_id,
            "reference_set_status": "pending_review",
            "regenerated_from_reference_id": reference_id,
        },
        reference_set_id=reference_set_id,
        angle_key=angle.key,
        correlation_id=correlation_id,
    )
    character_queries.record_scene_reference_gate(
        reference_id=reference_id,
        gate_result=pending_manual_gate("This reference was superseded by an individual regeneration"),
        status="rejected",
        correlation_id=correlation_id,
    )
    return RedirectResponse(url=f"/batches/{_post_batch_id(str(reference.get('post_id')))}", status_code=303)


@router.post("/character/posts/{post_id}/scene-reference/regenerate-all")
def regenerate_scene_reference_set(post_id: str):
    return generate_scene_reference(post_id)
