from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from postgrest.exceptions import APIError

from app.adapters.supabase_client import get_supabase
from app.core.logging import get_logger
from app.features.characters.schemas import ActorIdentityRecord, CharacterRecord, CharacterSnapshot

logger = get_logger(__name__)


def _is_missing_characters_table(exc: APIError) -> bool:
    text = str(exc).lower()
    return "missing response" in text or ("characters" in text and ("404" in text or "not found" in text))


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
    response = (
        get_supabase()
        .client.table("actor_identities")
        .select("*")
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    row = getattr(response, "data", None)
    return ActorIdentityRecord.model_validate(row) if row else None


def upsert_active_actor_identity(
    *,
    name: str,
    training_images: list[str],
    consent_source: str,
    correlation_id: str,
) -> ActorIdentityRecord:
    existing = get_active_actor_identity()
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "name": name.strip() or "Default Actor",
        "provider": "magnific",
        "training_images": training_images,
        "consent_source": consent_source,
        "training_status": "not_started",
        "training_phase": "not_started",
        "training_progress_percent": 0,
        "training_error": None,
        "provider_lora_id": None,
        "provider_lora_name": None,
        "provider_training_task_id": None,
        "is_active": True,
        "updated_at": now,
        "training_started_at": None,
        "training_completed_at": None,
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
