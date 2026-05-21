from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence
from uuid import uuid4

from postgrest.exceptions import APIError

from app.adapters.magnific_client import MagnificTrainingStatus, get_magnific_client
from app.adapters.supabase_client import get_supabase
from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.features.characters.schemas import ActorIdentityRecord, CharacterRecord, CharacterSnapshot

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
    if not row:
        return None
    return ActorIdentityRecord.model_validate(row)


def _persist_actor_identity(payload: dict) -> ActorIdentityRecord:
    client = get_supabase().client
    existing = get_active_actor_identity()
    if existing is None:
        payload = {**payload, "id": str(uuid4()), "created_at": payload.get("created_at") or datetime.now(timezone.utc).isoformat()}
        client.table("actor_identities").insert(payload).execute()
        logger.info("actor_identity_created", actor_identity_id=payload["id"], provider=payload.get("provider"))
        return ActorIdentityRecord.model_validate(payload)

    client.table("actor_identities").update(payload).eq("id", existing.id).execute()
    logger.info("actor_identity_updated", actor_identity_id=existing.id, provider=payload.get("provider"))
    return ActorIdentityRecord.model_validate({**existing.model_dump(mode="json"), **payload, "id": existing.id})


def upsert_active_actor_identity(
    *,
    name: str,
    provider: str,
    provider_training_task_id: Optional[str],
    provider_lora_id: Optional[str],
    provider_lora_name: Optional[str],
    training_status: str,
    training_phase: str,
    training_progress_percent: int,
    training_images: Sequence[str],
    consent_source: Optional[str],
    training_error: Optional[str] = None,
    training_started_at: Optional[str] = None,
    training_completed_at: Optional[str] = None,
) -> ActorIdentityRecord:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "name": name.strip() or "AYRA Actor Identity",
        "provider": provider.strip() or "magnific",
        "provider_training_task_id": provider_training_task_id,
        "provider_lora_id": provider_lora_id,
        "provider_lora_name": provider_lora_name,
        "training_status": training_status.strip() or "queued",
        "training_phase": training_phase.strip() or training_status.strip() or "queued",
        "training_progress_percent": int(training_progress_percent),
        "training_images": list(training_images),
        "consent_source": (consent_source or "").strip() or None,
        "training_error": (training_error or "").strip() or None,
        "training_started_at": training_started_at or now,
        "training_completed_at": training_completed_at,
        "is_active": True,
        "updated_at": now,
    }
    return _persist_actor_identity(payload)


def update_active_actor_identity_training_status(
    status: MagnificTrainingStatus,
    *,
    correlation_id: str,
) -> Optional[ActorIdentityRecord]:
    active = get_active_actor_identity()
    if active is None:
        return None
    if not status.provider_training_task_id and not status.provider_lora_id:
        return active

    payload = {
        "training_status": status.training_status,
        "training_phase": status.training_phase,
        "training_progress_percent": status.training_progress_percent,
        "provider_training_task_id": status.provider_training_task_id or active.provider_training_task_id,
        "provider_lora_id": status.provider_lora_id or active.provider_lora_id,
        "provider_lora_name": status.provider_lora_name or active.provider_lora_name,
        "training_error": status.training_error,
        "training_completed_at": datetime.now(timezone.utc).isoformat()
        if status.training_status in {"ready", "completed"}
        else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    get_supabase().client.table("actor_identities").update(payload).eq("id", active.id).execute()
    logger.info(
        "actor_identity_training_status_updated",
        correlation_id=correlation_id,
        actor_identity_id=active.id,
        provider_training_task_id=status.provider_training_task_id,
        provider_lora_id=status.provider_lora_id,
        training_status=status.training_status,
        training_phase=status.training_phase,
    )
    return ActorIdentityRecord.model_validate({**active.model_dump(mode="json"), **payload, "id": active.id})


def refresh_active_actor_identity_status(*, correlation_id: str) -> Optional[ActorIdentityRecord]:
    active = get_active_actor_identity()
    if active is None:
        return None
    if active.training_status in {"ready", "completed", "failed"}:
        return active
    if not active.provider_training_task_id and not active.provider_lora_id:
        return active
    try:
        status = get_magnific_client().poll_character_lora_status(
            provider_training_task_id=active.provider_training_task_id,
            provider_lora_id=active.provider_lora_id,
            correlation_id=correlation_id,
        )
    except ValidationError as exc:
        logger.warning(
            "actor_identity_refresh_skipped",
            correlation_id=correlation_id,
            actor_identity_id=active.id,
            error=exc.message,
        )
        return active
    except Exception as exc:  # noqa: BLE001 - status refresh should never break the settings page
        logger.warning(
            "actor_identity_refresh_failed",
            correlation_id=correlation_id,
            actor_identity_id=active.id,
            error=str(exc),
        )
        return active
    if status is None:
        return active
    return update_active_actor_identity_training_status(status, correlation_id=correlation_id)


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
