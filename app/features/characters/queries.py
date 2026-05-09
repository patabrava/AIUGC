from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from postgrest.exceptions import APIError

from app.adapters.supabase_client import get_supabase
from app.core.logging import get_logger
from app.features.characters.schemas import CharacterRecord, CharacterSnapshot

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
