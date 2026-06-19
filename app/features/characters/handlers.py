from __future__ import annotations

import json
import re
import time
from typing import Any, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.adapters.magnific_client import get_magnific_client
from app.adapters.storage_client import get_storage_client
from app.adapters.supabase_client import get_supabase
from app.core.errors import ErrorCode, FlowForgeException
from app.core.config import get_settings
from app.core.logging import get_logger
from app.features.characters.actor_identity import (
    actor_identity_is_ready,
    actor_identity_training_ready,
    passed_manual_gate,
    pending_manual_gate,
)
from app.features.characters import queries as character_queries
from app.features.characters.schemas import (
    ActorTrainingSet,
    SceneReferenceSetSummary,
    VIDEO_ACTOR_REFERENCE_ANGLE_KEYS,
)
from app.features.characters.scene_reference import (
    REQUIRED_SCENE_REFERENCE_ANGLES,
    SCENE_REFERENCE_CREATIVE_DETAILING,
    SCENE_REFERENCE_ENGINE,
    SCENE_REFERENCE_FIXED_GENERATION,
    SCENE_REFERENCE_IDENTITY_STRENGTH,
    SCENE_REFERENCE_RESOLUTION,
    build_scene_bible_provider_metadata,
    build_scene_reference_prompt_for_angle,
    get_scene_reference_angle,
    map_script_to_scene_intent,
    scene_reference_style_loras_for,
)

router = APIRouter(prefix="/settings", tags=["characters"])
templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _actor_identity_context_result(*, correlation_id: str):
    try:
        refreshed = character_queries.refresh_active_actor_identity_status(correlation_id=correlation_id)
        return refreshed or character_queries.get_active_actor_identity(), None
    except Exception as exc:  # noqa: BLE001 - settings recovery states must render without a live roster
        logger.warning(
            "actor_identity_context_load_failed",
            correlation_id=correlation_id,
            error=str(exc),
        )
        return None, "Actor readiness could not be refreshed. Retrying while settings stay available."


def _actor_identity_context(*, correlation_id: str):
    actor, _error = _actor_identity_context_result(correlation_id=correlation_id)
    return actor


def _actor_settings_context(*, request: Request, correlation_id: str) -> dict:
    actor, actor_context_error = _actor_identity_context_result(correlation_id=correlation_id)
    roster_error = None
    try:
        character_queries.sync_actor_identity_roster_from_provider(correlation_id=correlation_id)
        actors = character_queries.list_actor_identities()
        actors = character_queries.refresh_actor_identity_roster_statuses(actors, correlation_id=correlation_id)
    except Exception as exc:  # noqa: BLE001 - settings page must keep training available
        actors = []
        roster_error = "Actor roster could not be loaded. Training form is still available."
        logger.warning("actor_identity_roster_load_failed", correlation_id=correlation_id, error=str(exc))
    ready_actors = [row for row in actors if actor_identity_training_ready(row)]
    return {
        "request": request,
        "actor": actor,
        "actors": actors,
        "ready_actors": ready_actors,
        "actor_roster_error": roster_error,
        "actor_context_error": actor_context_error,
        "active_actor_updated": request.query_params.get("active_actor_updated") == "1",
    }


def _actor_form_defaults(request: Request) -> dict:
    return {
        "name": request.query_params.get("name") or "AYRA Actor Identity",
        "quality": request.query_params.get("quality") or "high",
        "gender": request.query_params.get("gender") or "woman",
        "consent_source": request.query_params.get("consent_source") or "Operator-provided reference set",
        "description": request.query_params.get("description") or "",
    }


def _actor_settings_response(
    *,
    request: Request,
    correlation_id: str,
    status_code: int = 200,
    actor_form_error: str | None = None,
    actor_activation_error: str | None = None,
    actor_form_values: dict | None = None,
):
    context = _actor_settings_context(request=request, correlation_id=correlation_id)
    actor = context.get("actor")
    context.update(
        {
            "settings_section": "actor",
            "actor_ready": actor_identity_is_ready(actor),
            "actor_form_error": actor_form_error,
            "actor_activation_error": actor_activation_error,
            "actor_form_values": actor_form_values or _actor_form_defaults(request),
        }
    )
    return templates.TemplateResponse("settings/actor.html", context, status_code=status_code)


def _ready_actor_identity_for_batch(batch: dict):
    actor_identity_id = str(batch.get("actor_identity_id") or "").strip()
    actor_identity = (
        character_queries.get_actor_identity_by_id(actor_identity_id)
        if actor_identity_id
        else character_queries.get_active_actor_identity()
    )
    return _ready_scene_reference_actor_identity(actor_identity)


def _ready_actor_identity_for_reference(reference: dict):
    actor_identity_id = str(reference.get("actor_identity_id") or "").strip()
    if not actor_identity_id:
        raise HTTPException(status_code=422, detail="Scene reference is missing ActorIdentity metadata")
    actor_identity = character_queries.get_actor_identity_by_id(actor_identity_id)
    return _ready_scene_reference_actor_identity(actor_identity)


def _ready_scene_reference_actor_identity(actor_identity):
    if not actor_identity_training_ready(actor_identity):
        raise HTTPException(status_code=422, detail="ActorIdentity training is not complete")
    if not str(actor_identity.provider_lora_name or "").strip():
        raise HTTPException(status_code=422, detail="ActorIdentity provider LoRA name is required for scene reference generation")
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
    return RedirectResponse(url="/settings/actor", status_code=303)


@router.get("/actor")
def actor_settings(request: Request):
    correlation_id = f"actor_settings_get_{uuid4()}"
    return _actor_settings_response(request=request, correlation_id=correlation_id)


@router.post("/actor/active")
def activate_actor_identity(
    request: Request,
    actor_identity_id: str = Form(...),
):
    correlation_id = str(uuid4())
    try:
        active_actor = character_queries.set_active_actor_identity(
            actor_identity_id=actor_identity_id,
            correlation_id=correlation_id,
        )
        try:
            from app.features.batches import queries as batch_queries

            batch_queries.sync_pending_character_consistency_batches_to_actor(
                active_actor=active_actor,
                correlation_id=correlation_id,
            )
        except Exception as exc:  # noqa: BLE001 - activation must stick even if eligible batch sync falls back to submit-time repair
            logger.warning(
                "actor_identity_activation_batch_sync_failed",
                correlation_id=correlation_id,
                actor_identity_id=actor_identity_id,
                error=str(exc),
            )
    except FlowForgeException as exc:
        logger.warning(
            "actor_identity_activation_rejected",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
            error=exc.message,
        )
        return _actor_settings_response(
            request=request,
            correlation_id=correlation_id,
            status_code=exc.status_code,
            actor_activation_error=exc.message,
        )
    except ValueError as exc:
        logger.warning(
            "actor_identity_activation_rejected",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
            error=str(exc),
        )
        return _actor_settings_response(
            request=request,
            correlation_id=correlation_id,
            status_code=422,
            actor_activation_error=str(exc),
        )
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
    logger.info(
        "legacy_character_settings_upload_blocked",
        route=request.url.path,
        submitted_name=name,
        front_filename=front.filename if front else None,
        three_quarter_filename=three_quarter.filename if three_quarter else None,
        profile_filename=profile.filename if profile else None,
    )
    return RedirectResponse(url="/settings/actor", status_code=303)


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


def _mystic_task_without_request_payload(task: dict) -> dict:
    return {key: value for key, value in task.items() if key != "_request_payload"}


def _mystic_request_payload(task: dict) -> dict:
    request_payload = task.get("_request_payload")
    return dict(request_payload) if isinstance(request_payload, dict) else {}


def _image_extension(content_type: str) -> str:
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized == "image/jpeg":
        return "jpg"
    if normalized == "image/webp":
        return "webp"
    return "png"


def _store_scene_reference_image_url(
    *,
    image_url: Optional[str],
    file_stem: str,
    correlation_id: str,
) -> tuple[Optional[str], dict]:
    if not image_url:
        return None, {}

    response = httpx.get(image_url, follow_redirects=True, timeout=60.0)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "image/png").split(";", 1)[0].strip() or "image/png"
    extension = _image_extension(content_type)
    uploaded = get_storage_client().upload_image(
        image_bytes=response.content,
        file_name=f"{file_stem}.{extension}",
        correlation_id=correlation_id,
        content_type=content_type,
    )
    return uploaded["url"], {
        "durable_image_storage": {
            "storage_provider": uploaded.get("storage_provider"),
            "storage_key": uploaded.get("storage_key"),
            "file_type": uploaded.get("file_type"),
            "size": uploaded.get("size"),
        },
        "provider_source_image_rehosted": True,
    }


def _poll_scene_reference_rows_until_generated(
    *,
    rows: list[dict[str, Any]],
    correlation_id: str,
) -> list[dict[str, Any]]:
    pending = [
        row
        for row in rows
        if not row.get("image_url") and str(row.get("provider_task_id") or "").strip()
    ]
    if not pending:
        return rows

    settings = get_settings()
    timeout_seconds = max(1, int(settings.magnific_timeout_seconds or 60))
    poll_seconds = max(1, int(settings.magnific_poll_seconds or 3))
    deadline = time.monotonic() + timeout_seconds
    client = get_magnific_client()
    resolved_by_id = {str(row.get("id") or ""): dict(row) for row in rows}

    while pending and time.monotonic() <= deadline:
        next_pending: list[dict[str, Any]] = []
        for row in pending:
            reference_id = str(row.get("id") or "")
            task_id = str(row.get("provider_task_id") or "").strip()
            task = client.get_mystic_task(task_id=task_id, correlation_id=correlation_id)
            image_url = _extract_mystic_image_url(task)
            if not image_url:
                next_pending.append(row)
                continue

            metadata = row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}
            durable_image_url, durable_image_metadata = _store_scene_reference_image_url(
                image_url=image_url,
                file_stem=f"scene-reference-{reference_id}-{task_id}",
                correlation_id=correlation_id,
            )
            updated_metadata = {
                **metadata,
                **durable_image_metadata,
                "poll_task": _mystic_task_without_request_payload(task),
                "auto_polled_for_video_submission": True,
            }
            stored_image_url = durable_image_url or image_url
            character_queries.mark_scene_reference_generated(
                reference_id=reference_id,
                image_url=stored_image_url,
                provider_metadata=updated_metadata,
                correlation_id=correlation_id,
            )
            resolved_by_id[reference_id] = {
                **row,
                "status": "generated",
                "image_url": stored_image_url,
                "provider_metadata": updated_metadata,
            }

        pending = next_pending
        if pending and time.monotonic() <= deadline:
            time.sleep(poll_seconds)

    return [resolved_by_id.get(str(row.get("id") or ""), row) for row in rows]


def _scene_reference_identity_contract(actor_identity) -> dict:
    return {
        "actor_identity_id": actor_identity.id,
        "actor_identity_name": actor_identity.name,
        "provider": actor_identity.provider,
        "provider_lora_id": actor_identity.provider_lora_id,
        "provider_lora_name": actor_identity.provider_lora_name,
        "identity_strength": SCENE_REFERENCE_IDENTITY_STRENGTH,
        "prompt_lora_handle_required": True,
        "styling_characters_required": True,
    }


def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _post_batch_id(post_id: str) -> str:
    post = _single_row(
        get_supabase().client.table("posts").select("batch_id").eq("id", post_id).execute(),
        label="Post",
    )
    return str(post.get("batch_id") or "")


def _provider_safe_name(name: str, actor_identity_id: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip().lower()).strip("_") or "actor"
    suffix = re.sub(r"[^a-zA-Z0-9]+", "", actor_identity_id)[0:8]
    return f"{base}_{suffix}"


def _row_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "model_dump"):
        return row.model_dump(mode="json")
    return dict(row)


def _video_scene_reference_validation_error(
    *,
    message: str,
    post_id: str,
    reference_set_id: Optional[str] = None,
    rows: Optional[list[dict[str, Any]]] = None,
) -> FlowForgeException:
    return FlowForgeException(
        code=ErrorCode.VALIDATION_ERROR,
        message=message,
        details={
            "post_id": post_id,
            "reference_set_id": reference_set_id,
            "row_count": len(rows or []),
        },
        status_code=422,
    )


def _validate_video_scene_reference_rows(
    *,
    post_id: str,
    reference_set_id: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(rows) != len(VIDEO_ACTOR_REFERENCE_ANGLE_KEYS):
        raise _video_scene_reference_validation_error(
            message="Character Consistency VEO submission requires exactly two approved Magnific actor-in-scene reference images plus one canonical scene plate.",
            post_id=post_id,
            reference_set_id=reference_set_id,
            rows=rows,
        )
    if any(not row.get("image_url") for row in rows):
        raise _video_scene_reference_validation_error(
            message="Character Consistency VEO submission requires both Magnific actor-in-scene reference images to have durable image URLs.",
            post_id=post_id,
            reference_set_id=reference_set_id,
            rows=rows,
        )
    expected_angles = set(VIDEO_ACTOR_REFERENCE_ANGLE_KEYS)
    actual_angles = {
        str((row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}).get("angle_key") or "")
        for row in rows
    }
    if actual_angles != expected_angles:
        raise _video_scene_reference_validation_error(
            message="Character Consistency VEO submission requires one front and one three-quarter Magnific actor-in-scene reference.",
            post_id=post_id,
            reference_set_id=reference_set_id,
            rows=rows,
        )
    return rows


def _create_scene_reference_candidates(
    *,
    post: dict[str, Any],
    batch: dict[str, Any],
    correlation_id: str,
    angle_keys: Optional[tuple[str, ...]] = None,
) -> tuple[str, list[dict[str, Any]]]:
    post_id = str(post.get("id") or "")
    actor_identity = _ready_actor_identity_for_batch(batch)

    seed_data = _load_json_object(post.get("seed_data"))
    script = str(seed_data.get("script") or seed_data.get("dialog_script") or post.get("topic_title") or "")
    target_length_tier = int(seed_data.get("target_length_tier") or batch.get("target_length_tier") or 8)
    intent_seed_data = {
        **seed_data,
        "topic_title": seed_data.get("topic_title") or post.get("topic_title") or "",
        "topic": seed_data.get("topic") or post.get("topic_title") or "",
    }
    intent = map_script_to_scene_intent(
        script=script,
        post_type=str(post.get("post_type") or ""),
        target_length_tier=target_length_tier,
        seed_data=intent_seed_data,
    )
    client = get_magnific_client()
    reference_set_id = str(uuid4())
    references: list[dict[str, Any]] = []
    scene_bible_metadata = build_scene_bible_provider_metadata(intent.scene_key)
    scene_style_loras = scene_reference_style_loras_for(
        intent.scene_key,
        get_settings().scene_reference_style_loras,
    )
    requested_angle_keys = set(angle_keys or tuple(angle.key for angle in REQUIRED_SCENE_REFERENCE_ANGLES))
    for angle in REQUIRED_SCENE_REFERENCE_ANGLES:
        if angle.key not in requested_angle_keys:
            continue
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
            strength=SCENE_REFERENCE_IDENTITY_STRENGTH,
            correlation_id=correlation_id,
            resolution=SCENE_REFERENCE_RESOLUTION,
            fixed_generation=SCENE_REFERENCE_FIXED_GENERATION,
            style_loras=scene_style_loras,
            extra_options={
                "engine": SCENE_REFERENCE_ENGINE,
                "creative_detailing": SCENE_REFERENCE_CREATIVE_DETAILING,
            },
        )
        task_id = str(task.get("task_id") or "")
        durable_image_url, durable_image_metadata = _store_scene_reference_image_url(
            image_url=_extract_mystic_image_url(task),
            file_stem=f"scene-reference-{reference_set_id}-{angle.key}-{task_id or 'pending'}",
            correlation_id=correlation_id,
        )
        references.append(
            _row_dict(
                character_queries.create_scene_reference_candidate(
                    actor_identity_id=actor_identity.id,
                    post_id=post_id,
                    scene_key=intent.scene_key,
                    wardrobe_key=intent.wardrobe_key,
                    provider_task_id=task_id,
                    image_url=durable_image_url,
                    prompt=prompt,
                    provider_metadata={
                        **scene_bible_metadata,
                        **durable_image_metadata,
                        "task": _mystic_task_without_request_payload(task),
                        "mystic_request": _mystic_request_payload(task),
                        "scene_style_loras": scene_style_loras,
                        "identity_lock_contract": _scene_reference_identity_contract(actor_identity),
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
        )
    logger.info("scene_reference_generation_submitted", correlation_id=correlation_id, post_id=post_id, count=len(references))
    return reference_set_id, references


def create_auto_approved_scene_reference_set_for_video(
    *,
    post: dict[str, Any],
    batch: dict[str, Any],
    correlation_id: str,
) -> SceneReferenceSetSummary:
    post_id = str(post.get("id") or "")
    existing = character_queries.get_approved_video_actor_scene_reference_set_for_post(post_id)
    if existing:
        return existing

    try:
        reference_set_id, created_rows = _create_scene_reference_candidates(
            post=post,
            batch=batch,
            correlation_id=correlation_id,
            angle_keys=VIDEO_ACTOR_REFERENCE_ANGLE_KEYS,
        )
        stored_rows = character_queries.list_scene_references_for_set(
            post_id=post_id,
            reference_set_id=reference_set_id,
        )
    except HTTPException as exc:
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message=str(exc.detail),
            details={"post_id": post_id, "batch_id": batch.get("id")},
            status_code=exc.status_code,
        ) from exc

    rows = [_row_dict(row) for row in (stored_rows or created_rows)]
    rows = _poll_scene_reference_rows_until_generated(rows=rows, correlation_id=correlation_id)
    _validate_video_scene_reference_rows(post_id=post_id, reference_set_id=reference_set_id, rows=rows)

    gate = passed_manual_gate("Automatically approved two Magnific actor LoRA scene references for hybrid VEO submission")
    gate.details.update(
        {
            "scene_consistency_set_approved": True,
            "actor_identity_match_confirmed": True,
            "reference_set_id": reference_set_id,
            "auto_approved_for_video_submission": True,
            "hybrid_reference_bundle_approved": True,
            "actor_scene_reference_count": len(VIDEO_ACTOR_REFERENCE_ANGLE_KEYS),
        }
    )
    gated_rows = [
        _row_dict(row)
        for row in character_queries.record_scene_reference_set_gate(
            post_id=post_id,
            reference_set_id=reference_set_id,
            gate_result=gate,
            status="approved",
            correlation_id=correlation_id,
        )
    ] or rows
    character_queries.attach_scene_reference_to_post(
        post_id=post_id,
        reference_id=_front_reference_id(rows),
        gate_result=gate,
        correlation_id=correlation_id,
    )

    approved = character_queries.get_approved_video_actor_scene_reference_set_for_post(post_id)
    if approved:
        return approved

    gate_payload = gate.model_dump(mode="json")
    fallback_rows = [
        {
            **row,
            "status": "approved",
            "identity_gate_result": gate_payload,
            "provider_metadata": {
                **(row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}),
                "reference_set_status": "approved",
            },
        }
        for row in (gated_rows or rows)
    ]
    summary = SceneReferenceSetSummary.from_rows(
        post_id=post_id,
        reference_set_id=reference_set_id,
        rows=fallback_rows,
    )
    if summary.is_video_actor_ready:
        return summary
    raise _video_scene_reference_validation_error(
        message="Generated Magnific actor-in-scene references could not be approved for hybrid Character Consistency VEO submission.",
        post_id=post_id,
        reference_set_id=reference_set_id,
        rows=fallback_rows,
    )


@router.post("/actor")
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
    actor_form_values = {
        "name": name,
        "gender": gender,
        "quality": quality,
        "consent_source": consent_source,
        "description": description,
    }
    if len(training_images) < 8 or len(training_images) > 20:
        return _actor_settings_response(
            request=request,
            correlation_id=correlation_id,
            status_code=422,
            actor_form_error="Upload between 8 and 20 images.",
            actor_form_values=actor_form_values,
        )

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
    logger.info(
        "actor_identity_training_started",
        correlation_id=correlation_id,
        actor_identity_id=identity.id,
        training_image_count=len(training.images),
    )
    return RedirectResponse(url="/settings/actor", status_code=303)


@router.post("/actor/poll")
def poll_actor_settings_training(request: Request):
    correlation_id = str(uuid4())
    actor, actor_context_error = _actor_identity_context_result(correlation_id=correlation_id)
    return templates.TemplateResponse(
        "settings/_actor_training_status.html",
        {
            "request": request,
            "actor": actor,
            "actor_ready": actor_identity_is_ready(actor),
            "actor_context_error": actor_context_error,
        },
    )


@router.post("/character/posts/{post_id}/scene-reference/generate")
def generate_scene_reference(post_id: str):
    correlation_id = str(uuid4())
    supabase = get_supabase().client
    post = _single_row(supabase.table("posts").select("*").eq("id", post_id).execute(), label="Post")
    batch = _single_row(supabase.table("batches").select("*").eq("id", post.get("batch_id")).execute(), label="Batch")

    _create_scene_reference_candidates(post=post, batch=batch, correlation_id=correlation_id)
    return RedirectResponse(url=f"/batches/{post.get('batch_id')}", status_code=303)


def _scene_reference_set_rows_or_422(post_id: str, reference_set_id: str) -> list[dict]:
    rows = character_queries.list_scene_references_for_set(post_id=post_id, reference_set_id=reference_set_id)
    if len(rows) != len(REQUIRED_SCENE_REFERENCE_ANGLES):
        raise HTTPException(status_code=422, detail="Scene reference set must contain exactly three generated scene references")
    if any(not row.get("image_url") for row in rows):
        raise HTTPException(status_code=422, detail="Scene reference set must contain exactly three generated scene references")
    expected_angles = {angle.key for angle in REQUIRED_SCENE_REFERENCE_ANGLES}
    actual_angles = {
        str((row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}).get("angle_key") or "")
        for row in rows
    }
    if actual_angles != expected_angles:
        raise HTTPException(status_code=422, detail="Scene reference set must contain one generated image for each required angle")
    return rows


def _front_reference_id(rows: list[dict]) -> str:
    for row in rows:
        metadata = row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}
        if metadata.get("angle_key") == "front_mid":
            return str(row["id"])
    raise HTTPException(status_code=422, detail="Scene reference set is missing the front angle")


@router.post("/character/posts/{post_id}/scene-reference/sets/{reference_set_id}/approve")
def approve_scene_reference_set(post_id: str, reference_set_id: str):
    correlation_id = str(uuid4())
    rows = _scene_reference_set_rows_or_422(post_id, reference_set_id)
    gate = passed_manual_gate("Operator approved actor identity and scene consistency for the full scene reference set")
    gate.details.update(
        {
            "scene_consistency_set_approved": True,
            "actor_identity_match_confirmed": True,
            "reference_set_id": reference_set_id,
        }
    )
    character_queries.record_scene_reference_set_gate(
        post_id=post_id,
        reference_set_id=reference_set_id,
        gate_result=gate,
        status="approved",
        correlation_id=correlation_id,
    )
    character_queries.attach_scene_reference_to_post(
        post_id=post_id,
        reference_id=_front_reference_id(rows),
        gate_result=gate,
        correlation_id=correlation_id,
    )
    return RedirectResponse(url=f"/batches/{_post_batch_id(post_id)}", status_code=303)


@router.post("/character/posts/{post_id}/scene-reference/sets/{reference_set_id}/reject")
def reject_scene_reference_set(post_id: str, reference_set_id: str):
    correlation_id = str(uuid4())
    rows = character_queries.list_scene_references_for_set(post_id=post_id, reference_set_id=reference_set_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Scene reference set not found")
    gate = pending_manual_gate("Operator rejected scene consistency. Regenerate the full set before video submission.")
    character_queries.record_scene_reference_set_gate(
        post_id=post_id,
        reference_set_id=reference_set_id,
        gate_result=gate,
        status="rejected",
        correlation_id=correlation_id,
    )
    return RedirectResponse(url=f"/batches/{_post_batch_id(post_id)}", status_code=303)


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
def poll_scene_reference(request: Request, reference_id: str):
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
        durable_image_url, durable_image_metadata = _store_scene_reference_image_url(
            image_url=image_url,
            file_stem=f"scene-reference-{reference_id}-{task_id}",
            correlation_id=correlation_id,
        )
        character_queries.mark_scene_reference_generated(
            reference_id=reference_id,
            image_url=durable_image_url or image_url,
            provider_metadata={**metadata, **durable_image_metadata, "poll_task": task},
            correlation_id=correlation_id,
        )
        if _is_htmx_request(request):
            response = PlainTextResponse("", status_code=200)
            response.headers["HX-Refresh"] = "true"
            return response
    elif _is_htmx_request(request):
        return PlainTextResponse("", status_code=204)
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
    scene_key = str(reference.get("scene_key") or "")
    try:
        scene_bible_metadata = build_scene_bible_provider_metadata(scene_key)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="Scene reference has unknown scene bible metadata") from exc
    scene_style_loras = scene_reference_style_loras_for(
        scene_key,
        get_settings().scene_reference_style_loras,
    )
    actor_identity = _ready_actor_identity_for_reference(reference)

    prompt = build_scene_reference_prompt_for_angle(
        actor_name=actor_identity.name,
        scene_key=scene_key,
        wardrobe_key=str(reference.get("wardrobe_key") or ""),
        post_type="",
        angle_key=angle.key,
        provider_lora_name=actor_identity.provider_lora_name,
    )
    task = get_magnific_client().create_mystic_scene_reference(
        prompt=prompt,
        lora_id=str(actor_identity.provider_lora_id),
        strength=SCENE_REFERENCE_IDENTITY_STRENGTH,
        correlation_id=correlation_id,
        resolution=SCENE_REFERENCE_RESOLUTION,
        fixed_generation=SCENE_REFERENCE_FIXED_GENERATION,
        style_loras=scene_style_loras,
        extra_options={
            "engine": SCENE_REFERENCE_ENGINE,
            "creative_detailing": SCENE_REFERENCE_CREATIVE_DETAILING,
        },
    )
    task_id = str(task.get("task_id") or "")
    durable_image_url, durable_image_metadata = _store_scene_reference_image_url(
        image_url=_extract_mystic_image_url(task),
        file_stem=f"scene-reference-{reference_set_id}-{angle.key}-{task_id or 'pending'}",
        correlation_id=correlation_id,
    )
    character_queries.create_scene_reference_candidate(
        actor_identity_id=actor_identity.id,
        post_id=str(reference["post_id"]),
        scene_key=scene_key,
        wardrobe_key=str(reference.get("wardrobe_key") or ""),
        provider_task_id=task_id,
        image_url=durable_image_url,
        prompt=prompt,
        provider_metadata={
            **scene_bible_metadata,
            **durable_image_metadata,
            "task": _mystic_task_without_request_payload(task),
            "mystic_request": _mystic_request_payload(task),
            "scene_style_loras": scene_style_loras,
            "identity_lock_contract": _scene_reference_identity_contract(actor_identity),
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
