from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from postgrest.exceptions import APIError

from app.adapters.magnific_client import get_magnific_client
from app.adapters.magnific_client import list_lora_rows, normalize_lora_training_status
from app.adapters.supabase_client import get_supabase
from app.core.errors import ErrorCode, FlowForgeException
from app.core.logging import get_logger
from app.features.characters.actor_identity import actor_identity_training_ready, sort_actor_identity_roster
from app.features.characters.schemas import (
    ActorIdentityRecord,
    CharacterRecord,
    CharacterSnapshot,
    IdentityGateResult,
    SceneReferenceImageRecord,
    SceneReferenceSetSummary,
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


def _identity_response_rows(response: Any) -> list[ActorIdentityRecord]:
    data = getattr(response, "data", None)
    if not data:
        return []
    rows = data if isinstance(data, list) else [data]
    return [ActorIdentityRecord.model_validate(row) for row in rows if row]


def _identity_response_row(response: Any) -> Optional[ActorIdentityRecord]:
    rows = _identity_response_rows(response)
    return rows[0] if rows else None


def list_actor_identities() -> list[ActorIdentityRecord]:
    try:
        response = get_supabase().client.table("actor_identities").select("*").order("updated_at", desc=True).execute()
    except APIError as exc:
        if _is_missing_actor_identities_table(exc):
            logger.warning("actor_identities_table_missing", error=str(exc))
            return []
        raise
    identities = sort_actor_identity_roster(_identity_response_rows(response))
    logger.info("actor_identities_listed", actor_identity_count=len(identities))
    return identities


def _actor_identity_reference_index(identities: list[ActorIdentityRecord]) -> dict[str, ActorIdentityRecord]:
    index: dict[str, ActorIdentityRecord] = {}
    for identity in identities:
        for ref in (identity.provider_lora_id, identity.provider_lora_name, identity.provider_training_task_id):
            if ref:
                index[str(ref)] = identity
    return index


def sync_actor_identity_roster_from_provider(*, correlation_id: str) -> list[ActorIdentityRecord]:
    try:
        loras = get_magnific_client().list_loras(correlation_id=correlation_id)
    except Exception as exc:  # noqa: BLE001 - roster sync must never break the settings page
        logger.warning(
            "actor_identity_provider_sync_failed",
            correlation_id=correlation_id,
            error=str(exc),
        )
        return []

    existing_identities = list_actor_identities()
    identity_index = _actor_identity_reference_index(existing_identities)
    synced: list[ActorIdentityRecord] = []

    for row in list_lora_rows(loras):
        if str(row.get("type") or "").strip().lower() != "character":
            continue
        if str(row.get("category") or "").strip().lower() not in {"my-character", "character", ""}:
            continue
        status = normalize_lora_training_status(row)
        if not (status.provider_lora_id or status.provider_lora_name or status.provider_training_task_id):
            continue

        match = None
        for ref in (status.provider_lora_id, status.provider_lora_name, status.provider_training_task_id):
            if ref and str(ref) in identity_index:
                match = identity_index[str(ref)]
                break

        provider_name = str(status.provider_lora_name or "").lower()
        if match is None and "actor" not in provider_name:
            continue

        payload = {
            "name": status.provider_lora_name or status.provider_lora_id or "Magnific Actor",
            "provider": "magnific",
            "provider_training_task_id": status.provider_training_task_id,
            "provider_lora_id": status.provider_lora_id,
            "provider_lora_name": status.provider_lora_name,
            "training_status": status.training_status or status.raw_status or "queued",
            "training_phase": status.training_phase or status.phase or "queued",
            "training_progress_percent": int(status.training_progress_percent or status.progress_percent or 0),
            "training_images": match.training_images if match else [],
            "consent_source": match.consent_source if match else "Magnific provider sync",
            "training_error": status.training_error,
            "training_started_at": match.training_started_at.isoformat() if match and match.training_started_at else None,
            "training_completed_at": (
                match.training_completed_at.isoformat()
                if match and match.training_completed_at
                else None
            ),
        }

        if match is None:
            created = create_actor_identity(
                name=payload["name"],
                training_images=list(payload["training_images"]),
                consent_source=payload["consent_source"],
                correlation_id=correlation_id,
                provider=payload["provider"],
                provider_training_task_id=payload["provider_training_task_id"],
                provider_lora_id=payload["provider_lora_id"],
                provider_lora_name=payload["provider_lora_name"],
                training_status=payload["training_status"],
                training_phase=payload["training_phase"],
                training_progress_percent=payload["training_progress_percent"],
                training_error=payload["training_error"],
                training_started_at=payload["training_started_at"],
                training_completed_at=payload["training_completed_at"],
                is_active=False,
            )
            synced.append(created)
            identity_index = _actor_identity_reference_index([*existing_identities, created])
            continue

        update_actor_training_status(
            actor_identity_id=match.id,
            training_status=payload["training_status"],
            training_phase=payload["training_phase"],
            training_progress_percent=payload["training_progress_percent"],
            provider_training_task_id=payload["provider_training_task_id"],
            provider_lora_id=payload["provider_lora_id"],
            provider_lora_name=payload["provider_lora_name"],
            training_error=payload["training_error"],
            correlation_id=correlation_id,
        )
        refreshed = get_actor_identity_by_id(match.id) or match
        synced.append(refreshed)

    return sort_actor_identity_roster(synced) if synced else []


def get_actor_identity_by_id(actor_identity_id: str) -> Optional[ActorIdentityRecord]:
    try:
        response = (
            get_supabase()
            .client.table("actor_identities")
            .select("*")
            .eq("id", actor_identity_id)
            .limit(1)
            .execute()
        )
    except APIError as exc:
        if _is_missing_actor_identities_table(exc):
            logger.warning("actor_identities_table_missing", error=str(exc))
            return None
        raise
    return _identity_response_row(response)


def get_active_actor_identity() -> Optional[ActorIdentityRecord]:
    try:
        response = (
            get_supabase()
            .client.table("actor_identities")
            .select("*")
            .eq("is_active", True)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
    except APIError as exc:
        if _is_missing_actor_identities_table(exc):
            logger.warning("actor_identities_table_missing", error=str(exc))
            return None
        raise
    return _identity_response_row(response)


def create_actor_identity(
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
    is_active: bool = False,
) -> ActorIdentityRecord:
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "id": str(uuid4()),
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
        "is_active": is_active,
        "created_at": now,
        "updated_at": now,
        "training_started_at": training_started_at,
        "training_completed_at": training_completed_at,
    }
    get_supabase().client.table("actor_identities").insert(payload).execute()
    logger.info(
        "actor_identity_created",
        correlation_id=correlation_id,
        actor_identity_id=payload["id"],
        is_active=is_active,
    )
    return ActorIdentityRecord.model_validate(payload)


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
    logger.warning("upsert_active_actor_identity_deprecated", correlation_id=correlation_id)
    return create_actor_identity(
        name=name,
        training_images=training_images,
        consent_source=consent_source,
        correlation_id=correlation_id,
        provider=provider,
        provider_training_task_id=provider_training_task_id,
        provider_lora_id=provider_lora_id,
        provider_lora_name=provider_lora_name,
        training_status=training_status,
        training_phase=training_phase,
        training_progress_percent=training_progress_percent,
        training_error=training_error,
        training_started_at=training_started_at,
        training_completed_at=training_completed_at,
        is_active=False,
    )


def _restore_active_actor_identity(
    *,
    client: Any,
    previous: ActorIdentityRecord,
    correlation_id: str,
    failed_actor_identity_id: str,
) -> None:
    payload = {"is_active": True, "updated_at": datetime.now(timezone.utc).isoformat()}
    client.table("actor_identities").update(payload).eq("id", previous.id).execute()
    logger.warning(
        "actor_identity_switch_failed_restored",
        correlation_id=correlation_id,
        actor_identity_id=failed_actor_identity_id,
        restored_actor_identity_id=previous.id,
    )


def set_active_actor_identity(*, actor_identity_id: str, correlation_id: str) -> ActorIdentityRecord:
    target = get_actor_identity_by_id(actor_identity_id)
    if target is None:
        logger.warning(
            "actor_identity_switch_rejected_missing",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
        )
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="ActorIdentity was not found.",
            details={"actor_identity_id": actor_identity_id},
            status_code=422,
        )
    if not actor_identity_training_ready(target):
        logger.warning(
            "actor_identity_switch_rejected_not_ready",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
            training_phase=target.training_phase,
            training_progress_percent=target.training_progress_percent,
        )
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Only ready ActorIdentity rows can be activated.",
            details={"actor_identity_id": actor_identity_id, "training_phase": target.training_phase},
            status_code=422,
        )

    previous = get_active_actor_identity()
    now = datetime.now(timezone.utc).isoformat()
    client = get_supabase().client
    try:
        client.table("actor_identities").update({"is_active": False, "updated_at": now}).eq("is_active", True).execute()
        client.table("actor_identities").update({"is_active": True, "updated_at": now}).eq("id", target.id).execute()
        refreshed = get_actor_identity_by_id(target.id)
        if refreshed is None or not refreshed.is_active:
            raise RuntimeError("activation did not persist")
    except Exception:
        if previous is not None:
            try:
                _restore_active_actor_identity(
                    client=client,
                    previous=previous,
                    correlation_id=correlation_id,
                    failed_actor_identity_id=actor_identity_id,
                )
            except Exception as restore_exc:  # noqa: BLE001 - surface original activation failure after logging restore state
                logger.error(
                    "actor_identity_switch_failed_restore_failed",
                    correlation_id=correlation_id,
                    actor_identity_id=actor_identity_id,
                    previous_actor_identity_id=previous.id,
                    restore_error=str(restore_exc),
                )
        else:
            logger.warning(
                "actor_identity_switch_failed_no_previous",
                correlation_id=correlation_id,
                actor_identity_id=actor_identity_id,
            )
        raise
    logger.info(
        "actor_identity_switched",
        correlation_id=correlation_id,
        actor_identity_id=target.id,
        previous_actor_identity_id=previous.id if previous else None,
    )
    return refreshed


def refresh_actor_identity_status(
    identity: ActorIdentityRecord,
    *,
    correlation_id: str,
) -> ActorIdentityRecord:
    if identity.training_phase == "ready" or identity.training_status in {"ready", "completed", "succeeded", "failed"}:
        return identity
    if not identity.provider_training_task_id and not identity.provider_lora_id:
        return identity
    try:
        status = get_magnific_client().poll_character_lora_status(
            provider_training_task_id=identity.provider_training_task_id,
            provider_lora_id=identity.provider_lora_id,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # noqa: BLE001 - settings render must survive provider outages
        logger.warning(
            "actor_identity_refresh_failed",
            correlation_id=correlation_id,
            actor_identity_id=identity.id,
            error=str(exc),
        )
        return identity
    if status is None:
        return identity
    update_actor_training_status(
        actor_identity_id=identity.id,
        training_status=str(status.training_status or status.raw_status),
        training_phase=str(status.training_phase or status.phase),
        training_progress_percent=int(status.training_progress_percent or status.progress_percent or 0),
        provider_training_task_id=status.provider_training_task_id,
        provider_lora_id=status.provider_lora_id,
        provider_lora_name=status.provider_lora_name,
        training_error=status.training_error,
        correlation_id=correlation_id,
    )
    return get_actor_identity_by_id(identity.id) or identity


def refresh_actor_identity_roster_statuses(
    identities: list[ActorIdentityRecord],
    *,
    correlation_id: str,
) -> list[ActorIdentityRecord]:
    refreshed = [
        refresh_actor_identity_status(identity, correlation_id=correlation_id)
        for identity in identities
    ]
    return sort_actor_identity_roster(refreshed)


def refresh_active_actor_identity_status(*, correlation_id: str) -> Optional[ActorIdentityRecord]:
    active = get_active_actor_identity()
    if active is None:
        return None
    refresh_actor_identity_status(active, correlation_id=correlation_id)
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
    provider_training_task_id: Optional[str] = None,
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
    if provider_training_task_id:
        payload["provider_training_task_id"] = provider_training_task_id
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
    reference_set_id: Optional[str] = None,
    angle_key: Optional[str] = None,
) -> SceneReferenceImageRecord:
    now = datetime.now(timezone.utc).isoformat()
    metadata = dict(provider_metadata)
    if reference_set_id:
        metadata["reference_set_id"] = reference_set_id
    if angle_key:
        metadata["angle_key"] = angle_key
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
        "provider_metadata": metadata,
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


def _reference_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("provider_metadata")
    return metadata if isinstance(metadata, dict) else {}


def select_latest_reference_set_id(rows: list[dict[str, Any]]) -> Optional[str]:
    latest_by_set: dict[str, str] = {}
    for row in rows:
        metadata = _reference_metadata(row)
        reference_set_id = str(metadata.get("reference_set_id") or "")
        angle_key = str(metadata.get("angle_key") or "")
        if not reference_set_id or not angle_key:
            continue
        latest_by_set[reference_set_id] = max(
            latest_by_set.get(reference_set_id, ""),
            str(row.get("created_at") or ""),
        )
    if not latest_by_set:
        return None
    return max(latest_by_set.items(), key=lambda item: item[1])[0]


def filter_reference_rows_for_set(rows: list[dict[str, Any]], reference_set_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(_reference_metadata(row).get("reference_set_id") or "") == reference_set_id
    ]


def list_scene_references_for_post(post_id: str) -> list[dict[str, Any]]:
    response = (
        get_supabase()
        .client.table("scene_reference_images")
        .select("*")
        .eq("post_id", post_id)
        .order("created_at", desc=False)
        .execute()
    )
    return getattr(response, "data", None) or []


def get_latest_scene_reference_set_for_post(post_id: str) -> Optional[SceneReferenceSetSummary]:
    rows = list_scene_references_for_post(post_id)
    reference_set_id = select_latest_reference_set_id(rows)
    if not reference_set_id:
        return None
    return SceneReferenceSetSummary.from_rows(
        post_id=post_id,
        reference_set_id=reference_set_id,
        rows=filter_reference_rows_for_set(rows, reference_set_id),
    )


def get_approved_scene_reference_set_for_post(post_id: str) -> Optional[SceneReferenceSetSummary]:
    summary = get_latest_scene_reference_set_for_post(post_id)
    if summary is None or not summary.is_ready:
        return None
    return summary


def get_approved_scene_reference_for_post(post_id: str) -> Optional[dict[str, Any]]:
    summary = get_approved_scene_reference_set_for_post(post_id)
    if summary is None:
        return None
    return summary.approved_rows[0]


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
