from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from postgrest.exceptions import APIError

from app.adapters.magnific_client import get_magnific_client
from app.adapters.supabase_client import get_supabase
from app.core.logging import get_logger
from app.features.characters.schemas import (
    ActorIdentityRecord,
    CharacterRecord,
    CharacterSnapshot,
    IdentityGateResult,
    SceneReferenceImageRecord,
)

logger = get_logger(__name__)


def _is_missing_characters_table(exc: APIError) -> bool:
    text = str(exc).lower()
    return "missing response" in text or ("characters" in text and ("404" in text or "not found" in text))


def _is_missing_actor_identities_table(exc: APIError) -> bool:
    text = str(exc).lower()
    return "missing response" in text or ("actor_identities" in text and ("404" in text or "not found" in text))


def get_active_character() -> Optional[CharacterRecord]:
    try:
        response = (
            get_supabase()
            .client.table("characters")
            .select("*")
            .eq("is_active", True)
            .maybe_single()
            .execute()
        )
    except APIError as exc:
        if _is_missing_characters_table(exc):
            logger.warning("characters_table_missing", error=str(exc))
            return None
        raise
    row = getattr(response, "data", None)
    if not row:
        return None
    return CharacterRecord.model_validate(row)


def upsert_active_character(
    *,
    name: str,
    front_image_url: str,
    three_quarter_image_url: str,
    profile_image_url: str,
    correlation_id: str,
) -> CharacterRecord:
    existing = get_active_character()
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "name": name.strip() or "Default Character",
        "front_image_url": front_image_url,
        "three_quarter_image_url": three_quarter_image_url,
        "profile_image_url": profile_image_url,
        "is_active": True,
        "updated_at": now,
    }

    client = get_supabase().client
    if existing is None:
        payload["id"] = str(uuid4())
        payload["created_at"] = now
        client.table("characters").insert(payload).execute()
        logger.info("character_created", correlation_id=correlation_id, character_id=payload["id"])
        return CharacterRecord.model_validate(payload)

    client.table("characters").update(payload).eq("id", existing.id).execute()
    logger.info("character_updated", correlation_id=correlation_id, character_id=existing.id)
    return CharacterRecord.model_validate(
        {
            **payload,
            "id": existing.id,
            "created_at": existing.created_at,
        }
    )


def snapshot_for_batch(character: CharacterRecord) -> CharacterSnapshot:
    return CharacterSnapshot(
        character_id=character.id,
        name=character.name,
        front_image_url=character.front_image_url,
        three_quarter_image_url=character.three_quarter_image_url,
        profile_image_url=character.profile_image_url,
        snapshotted_at=datetime.now(timezone.utc),
    )


def _merge_identity_timestamps(payload: dict[str, Any], existing: Optional[ActorIdentityRecord] = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    merged = dict(payload)
    merged.setdefault("created_at", existing.created_at if existing else now)
    merged.setdefault("updated_at", now)
    return merged


def get_active_actor_identity() -> Optional[ActorIdentityRecord]:
    try:
        response = (
            get_supabase()
            .client.table("actor_identities")
            .select("*")
            .eq("is_active", True)
            .maybe_single()
            .execute()
        )
    except APIError as exc:
        if _is_missing_actor_identities_table(exc):
            logger.warning("actor_identities_table_missing", error=str(exc))
            return None
        raise
    row = getattr(response, "data", None)
    return ActorIdentityRecord.model_validate(row) if row else None


def upsert_active_actor_identity(
    *,
    name: str,
    training_images: list[str],
    consent_source: Optional[str] = None,
    correlation_id: Optional[str] = None,
    provider: str = "magnific",
    provider_training_task_id: Optional[str] = None,
    provider_lora_id: Optional[str] = None,
    provider_lora_name: Optional[str] = None,
    training_status: str = "not_started",
    training_phase: str = "not_started",
    training_progress_percent: int = 0,
    training_error: Optional[str] = None,
    training_started_at: Optional[str] = None,
    training_completed_at: Optional[str] = None,
) -> ActorIdentityRecord:
    existing = get_active_actor_identity()
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "name": name.strip() or "Default Actor",
        "provider": provider.strip() or "magnific",
        "training_images": training_images,
        "consent_source": (consent_source or "").strip() or None,
        "training_status": training_status,
        "training_phase": training_phase,
        "training_progress_percent": int(training_progress_percent),
        "training_error": (training_error or "").strip() or None,
        "provider_lora_id": provider_lora_id,
        "provider_lora_name": provider_lora_name,
        "provider_training_task_id": provider_training_task_id,
        "is_active": True,
        "updated_at": now,
        "training_started_at": training_started_at,
        "training_completed_at": training_completed_at,
    }
    client = get_supabase().client
    if existing is None:
        payload["id"] = str(uuid4())
        payload["created_at"] = now
        client.table("actor_identities").insert(payload).execute()
        logger.info("actor_identity_created", correlation_id=correlation_id, actor_identity_id=payload["id"])
        return ActorIdentityRecord.model_validate(payload)

    client.table("actor_identities").update(payload).eq("id", existing.id).execute()
    logger.info("actor_identity_replaced", correlation_id=correlation_id, actor_identity_id=existing.id)
    return ActorIdentityRecord.model_validate(_merge_identity_timestamps({**payload, "id": existing.id}, existing))


def refresh_active_actor_identity_status(*, correlation_id: str) -> Optional[ActorIdentityRecord]:
    active = get_active_actor_identity()
    if active is None:
        return None
    if active.training_phase == "ready" or active.training_status in {"ready", "completed", "succeeded", "failed"}:
        return active
    if not active.provider_training_task_id and not active.provider_lora_id:
        return active
    try:
        status = get_magnific_client().poll_character_lora_status(
            provider_training_task_id=active.provider_training_task_id,
            provider_lora_id=active.provider_lora_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # noqa: BLE001 - settings render must survive provider outages
        logger.warning(
            "actor_identity_refresh_failed",
            correlation_id=correlation_id,
            actor_identity_id=active.id,
            error=str(exc),
        )
        return active
    if status is None:
        return active
    update_actor_training_status(
        actor_identity_id=active.id,
        training_status=str(status.training_status or status.raw_status),
        training_phase=str(status.training_phase or status.phase),
        training_progress_percent=int(status.training_progress_percent or status.progress_percent or 0),
        provider_lora_id=status.provider_lora_id,
        provider_lora_name=status.provider_lora_name,
        training_error=status.training_error,
        correlation_id=correlation_id,
    )
    return get_active_actor_identity()


def mark_actor_training_submitted(
    *,
    actor_identity_id: str,
    provider_training_task_id: str,
    provider_lora_name: str,
    raw_status: str,
    correlation_id: str,
) -> None:
    payload = {
        "provider_training_task_id": provider_training_task_id,
        "provider_lora_name": provider_lora_name,
        "training_status": raw_status,
        "training_phase": "training",
        "training_progress_percent": 10,
        "training_error": None,
        "training_started_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    get_supabase().client.table("actor_identities").update(payload).eq("id", actor_identity_id).execute()
    logger.info(
        "actor_identity_training_submitted",
        correlation_id=correlation_id,
        actor_identity_id=actor_identity_id,
        provider_training_task_id=provider_training_task_id,
    )


def update_actor_training_status(
    *,
    actor_identity_id: str,
    training_status: str,
    training_phase: str,
    training_progress_percent: int,
    provider_lora_id: Optional[str] = None,
    provider_lora_name: Optional[str] = None,
    training_error: Optional[str] = None,
    correlation_id: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "training_status": training_status,
        "training_phase": training_phase,
        "training_progress_percent": training_progress_percent,
        "training_error": training_error,
        "updated_at": now,
    }
    if provider_lora_id:
        payload["provider_lora_id"] = provider_lora_id
    if provider_lora_name:
        payload["provider_lora_name"] = provider_lora_name
    if training_phase == "ready":
        payload["training_completed_at"] = now
    get_supabase().client.table("actor_identities").update(payload).eq("id", actor_identity_id).execute()
    logger.info(
        "actor_identity_training_status_updated",
        correlation_id=correlation_id,
        actor_identity_id=actor_identity_id,
        training_phase=training_phase,
        training_progress_percent=training_progress_percent,
    )


def create_scene_reference_candidate(
    *,
    actor_identity_id: str,
    post_id: str,
    scene_key: str,
    wardrobe_key: str,
    provider_task_id: Optional[str],
    image_url: Optional[str],
    prompt: str,
    provider_metadata: dict[str, Any],
    correlation_id: str,
) -> SceneReferenceImageRecord:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "id": str(uuid4()),
        "actor_identity_id": actor_identity_id,
        "post_id": post_id,
        "scene_key": scene_key,
        "wardrobe_key": wardrobe_key,
        "provider": "magnific",
        "provider_task_id": provider_task_id,
        "image_url": image_url,
        "prompt": prompt,
        "provider_metadata": provider_metadata,
        "identity_gate_result": {
            "status": "manual_required",
            "reason": "Operator must approve the generated actor scene reference",
            "gate_type": "manual",
            "details": {},
        },
        "status": "generated" if image_url else "submitted",
        "created_at": now,
        "updated_at": now,
    }
    get_supabase().client.table("scene_reference_images").insert(payload).execute()
    logger.info(
        "scene_reference_candidate_created",
        correlation_id=correlation_id,
        post_id=post_id,
        actor_identity_id=actor_identity_id,
        scene_key=scene_key,
        wardrobe_key=wardrobe_key,
    )
    return SceneReferenceImageRecord.model_validate(payload)


def mark_scene_reference_generated(
    *,
    reference_id: str,
    image_url: str,
    provider_metadata: dict[str, Any],
    correlation_id: str,
) -> None:
    payload = {
        "image_url": image_url,
        "provider_metadata": provider_metadata,
        "status": "generated",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    get_supabase().client.table("scene_reference_images").update(payload).eq("id", reference_id).execute()
    logger.info("scene_reference_generated", correlation_id=correlation_id, scene_reference_image_id=reference_id)


def record_scene_reference_gate(
    *,
    reference_id: str,
    gate_result: IdentityGateResult,
    status: str,
    correlation_id: str,
) -> None:
    payload = {
        "identity_gate_result": gate_result.model_dump(mode="json"),
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    get_supabase().client.table("scene_reference_images").update(payload).eq("id", reference_id).execute()
    logger.info(
        "scene_reference_gate_recorded",
        correlation_id=correlation_id,
        scene_reference_image_id=reference_id,
        gate_status=gate_result.status,
        reference_status=status,
    )


def get_scene_reference_by_id(reference_id: str) -> Optional[dict[str, Any]]:
    response = get_supabase().client.table("scene_reference_images").select("*").eq("id", reference_id).maybe_single().execute()
    return getattr(response, "data", None)


def get_approved_scene_reference_for_post(post_id: str) -> Optional[dict[str, Any]]:
    response = (
        get_supabase()
        .client.table("scene_reference_images")
        .select("*")
        .eq("post_id", post_id)
        .eq("status", "approved")
        .limit(1)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    return rows[0] if rows else None


def list_scene_references_for_posts(post_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not post_ids:
        return {}
    response = (
        get_supabase()
        .client.table("scene_reference_images")
        .select("*")
        .in_("post_id", post_ids)
        .order("created_at", desc=True)
        .execute()
    )
    rows = getattr(response, "data", None) or []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        post_id = str(row.get("post_id") or "")
        if post_id:
            grouped.setdefault(post_id, []).append(row)
    return grouped


def attach_scene_reference_to_post(
    *,
    post_id: str,
    reference_id: str,
    gate_result: IdentityGateResult,
    correlation_id: str,
) -> None:
    payload = {
        "scene_reference_image_id": reference_id,
        "identity_gate_result": gate_result.model_dump(mode="json"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    get_supabase().client.table("posts").update(payload).eq("id", post_id).execute()
    logger.info("post_scene_reference_attached", correlation_id=correlation_id, post_id=post_id, reference_id=reference_id)
