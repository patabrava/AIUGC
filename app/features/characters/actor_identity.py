from __future__ import annotations

from typing import Any, Optional

from app.core.errors import ErrorCode, FlowForgeException
from app.features.characters.schemas import ActorIdentityRecord, IdentityGateResult, SceneReferenceSetSummary


CHARACTER_CONSISTENCY_MODES = {
    "character_consistency",
    "character_consistency_light",
    "manual_character_consistency",
    "character_consistency_mid",
}

MANUAL_CREATION_MODES = {"manual", "manual_character_consistency"}
ACTOR_IDENTITY_VIDEO_SOURCES = {
    "actor_identity_anchor_images",
    "actor_identity_scene_reference",
    "actor_identity_scene_reference_set",
}


def is_character_consistency_mode(value: Any) -> bool:
    return str(value or "").strip() in CHARACTER_CONSISTENCY_MODES


def is_manual_creation_mode(value: Any) -> bool:
    return str(value or "").strip() in MANUAL_CREATION_MODES


def is_actor_identity_video_source(value: Any) -> bool:
    return str(value or "").strip() in ACTOR_IDENTITY_VIDEO_SOURCES


def is_character_consistency_light_mode(value: Any) -> bool:
    return str(value or "").strip() == "character_consistency_light"


def is_character_consistency_mid_mode(value: Any) -> bool:
    return str(value or "").strip() == "character_consistency_mid"


def actor_identity_training_ready(identity: Optional[ActorIdentityRecord]) -> bool:
    if identity is None:
        return False
    return (
        identity.training_phase == "ready"
        and identity.training_progress_percent == 100
        and bool(identity.provider_lora_id)
        and not identity.training_error
    )


def actor_identity_is_ready(identity: Optional[ActorIdentityRecord]) -> bool:
    return bool(identity and identity.is_active is True and actor_identity_training_ready(identity))


def derive_actor_identity_preview_images(training_images: list[str]) -> tuple[Optional[str], Optional[str]]:
    cleaned = [str(image).strip() for image in training_images if str(image).strip()]
    if not cleaned:
        return None, None
    portrait_image_url = cleaned[0]
    cover_image_url = cleaned[0]
    return portrait_image_url, cover_image_url


def actor_identity_preview_image_url(identity: Optional[ActorIdentityRecord]) -> Optional[str]:
    if identity is None:
        return None
    return identity.primary_image_url


def actor_identity_status_group(identity: ActorIdentityRecord) -> str:
    if identity.is_active:
        return "active"
    if actor_identity_training_ready(identity):
        return "ready"
    if identity.training_error or identity.training_phase == "failed" or identity.training_status == "failed":
        return "failed"
    return "training"


def actor_identity_roster_sort_key(identity: ActorIdentityRecord) -> tuple[int, float]:
    group_order = {"active": 0, "ready": 1, "training": 2, "failed": 3}
    updated_at = identity.updated_at.timestamp()
    return (group_order[actor_identity_status_group(identity)], -updated_at)


def sort_actor_identity_roster(identities: list[ActorIdentityRecord]) -> list[ActorIdentityRecord]:
    return sorted(identities, key=actor_identity_roster_sort_key)


def pending_manual_gate(reason: str) -> IdentityGateResult:
    return IdentityGateResult(status="manual_required", reason=reason, gate_type="manual", details={})


def passed_manual_gate(reason: str = "Operator approved identity match") -> IdentityGateResult:
    return IdentityGateResult(status="passed", reason=reason, gate_type="manual", details={})


def resolve_character_consistency_source(
    *,
    batch: dict[str, Any],
    active_identity: Optional[ActorIdentityRecord] = None,
) -> dict[str, Any]:
    if batch.get("actor_identity_id") or batch.get("actor_identity_snapshot"):
        return {"source": "actor_identity", "actor_identity_id": batch.get("actor_identity_id")}
    if batch.get("character_snapshot"):
        return {"source": "legacy_character_snapshot", "character_snapshot": batch.get("character_snapshot")}
    if actor_identity_is_ready(active_identity):
        return {"source": "actor_identity", "actor_identity_id": active_identity.id}
    return {"source": "blocked", "reason": "ActorIdentity training is not complete"}


def ensure_video_scene_reference_ready(
    *,
    batch: dict[str, Any],
    post: dict[str, Any],
    scene_reference: Optional[dict[str, Any]],
    route: Optional[str],
) -> dict[str, Any]:
    if batch.get("character_snapshot") and not batch.get("actor_identity_id"):
        return {"source": "legacy_character_snapshot", "compatible": True}
    if not is_character_consistency_mode(batch.get("creation_mode")):
        return {"source": "not_character_consistency", "compatible": True}
    if not batch.get("actor_identity_id"):
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Character Consistency batch is missing ActorIdentity metadata.",
            details={"batch_id": batch.get("id")},
            status_code=422,
        )
    if not scene_reference or scene_reference.get("status") != "approved":
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="ActorIdentity video generation requires an approved SceneReferenceImage before submit.",
            details={"post_id": post.get("id"), "batch_id": batch.get("id")},
            status_code=422,
        )
    gate = scene_reference.get("identity_gate_result") or {}
    if gate.get("status") != "passed":
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="SceneReferenceImage identity gate has not passed.",
            details={"post_id": post.get("id"), "gate": gate},
            status_code=422,
        )
    return {
        "source": "actor_identity_scene_reference",
        "compatible": True,
        "route": route,
        "scene_reference": scene_reference,
    }


def _scene_reference_set_has_actor_identity_confirmation(scene_reference_set: SceneReferenceSetSummary) -> bool:
    for row in scene_reference_set.approved_rows:
        gate = row.get("identity_gate_result") if isinstance(row.get("identity_gate_result"), dict) else {}
        details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
        if details.get("actor_identity_match_confirmed") is not True:
            return False
    return True


def _metadata_int_equals(value: Any, expected: int) -> bool:
    try:
        return int(value) == expected
    except (TypeError, ValueError):
        return False


def _scene_reference_row_has_lora_identity_lock(row: dict[str, Any], *, batch_actor_identity_id: Any) -> bool:
    actor_identity_id = str(row.get("actor_identity_id") or "").strip()
    if not actor_identity_id or actor_identity_id != str(batch_actor_identity_id or "").strip():
        return False

    metadata = row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}
    identity_contract = (
        metadata.get("identity_lock_contract") if isinstance(metadata.get("identity_lock_contract"), dict) else {}
    )
    if str(identity_contract.get("actor_identity_id") or "").strip() != actor_identity_id:
        return False
    if identity_contract.get("prompt_lora_handle_required") is not True:
        return False
    if identity_contract.get("styling_characters_required") is not True:
        return False
    if not _metadata_int_equals(identity_contract.get("identity_strength"), 100):
        return False

    mystic_request = metadata.get("mystic_request") if isinstance(metadata.get("mystic_request"), dict) else {}
    if any(key in mystic_request for key in ("structure_reference", "style_reference", "model")):
        return False

    styling = mystic_request.get("styling") if isinstance(mystic_request.get("styling"), dict) else {}
    characters = styling.get("characters") if isinstance(styling.get("characters"), list) else []
    first_character = characters[0] if characters and isinstance(characters[0], dict) else {}
    provider_lora_id = str(identity_contract.get("provider_lora_id") or "").strip()
    if not provider_lora_id or str(first_character.get("id") or "").strip() != provider_lora_id:
        return False
    if not _metadata_int_equals(first_character.get("strength"), 100):
        return False

    provider_lora_name = str(identity_contract.get("provider_lora_name") or "").strip()
    prompt_text = str(mystic_request.get("prompt") or row.get("prompt") or "")
    return bool(provider_lora_name and f"@{provider_lora_name}::100" in prompt_text)


def _scene_reference_set_has_lora_identity_lock(
    scene_reference_set: SceneReferenceSetSummary,
    *,
    batch_actor_identity_id: Any,
) -> bool:
    return all(
        _scene_reference_row_has_lora_identity_lock(row, batch_actor_identity_id=batch_actor_identity_id)
        for row in scene_reference_set.approved_rows
    )


def scene_reference_set_has_lora_identity_lock(
    scene_reference_set: SceneReferenceSetSummary,
    *,
    batch_actor_identity_id: Any = None,
) -> bool:
    resolved_actor_identity_id = batch_actor_identity_id or getattr(scene_reference_set, "actor_identity_id", None)
    if not resolved_actor_identity_id and scene_reference_set.approved_rows:
        resolved_actor_identity_id = scene_reference_set.approved_rows[0].get("actor_identity_id")
    return _scene_reference_set_has_lora_identity_lock(
        scene_reference_set,
        batch_actor_identity_id=resolved_actor_identity_id,
    )


def scene_reference_set_has_actor_identity_confirmation(scene_reference_set: SceneReferenceSetSummary) -> bool:
    return _scene_reference_set_has_actor_identity_confirmation(scene_reference_set)


def ensure_video_scene_reference_set_ready(
    *,
    batch: dict[str, Any],
    post: dict[str, Any],
    scene_reference_set: Optional[SceneReferenceSetSummary],
    route: Optional[str],
) -> dict[str, Any]:
    if batch.get("character_snapshot") and not batch.get("actor_identity_id"):
        return {"source": "legacy_character_snapshot", "compatible": True}
    if not is_character_consistency_mode(batch.get("creation_mode")):
        return {"source": "not_character_consistency", "compatible": True}
    if not batch.get("actor_identity_id"):
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Character Consistency batch is missing ActorIdentity metadata.",
            details={"batch_id": batch.get("id")},
            status_code=422,
        )
    if scene_reference_set is None or not scene_reference_set.is_ready:
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="ActorIdentity video generation requires three approved SceneReferenceImages before submit.",
            details={
                "post_id": post.get("id"),
                "batch_id": batch.get("id"),
                "missing_angle_keys": scene_reference_set.missing_angle_keys if scene_reference_set else [],
            },
            status_code=422,
        )
    if not scene_reference_set_has_actor_identity_confirmation(scene_reference_set):
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="ActorIdentity video generation requires operator-confirmed actor identity match for all three approved SceneReferenceImages.",
            details={
                "post_id": post.get("id"),
                "batch_id": batch.get("id"),
                "reference_set_id": scene_reference_set.reference_set_id,
            },
            status_code=422,
        )
    if not scene_reference_set_has_lora_identity_lock(
        scene_reference_set,
        batch_actor_identity_id=batch.get("actor_identity_id"),
    ):
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="ActorIdentity video generation requires LoRA identity lock metadata on all three approved SceneReferenceImages.",
            details={
                "post_id": post.get("id"),
                "batch_id": batch.get("id"),
                "reference_set_id": scene_reference_set.reference_set_id,
            },
            status_code=422,
        )
    if route in {"extended", "veo_extended"}:
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Character Consistency video generation requires an 8-second VEO base request; the current extended route cannot safely submit actor identity reference anchors.",
            details={
                "post_id": post.get("id"),
                "batch_id": batch.get("id"),
                "route": route,
                "reference_set_id": scene_reference_set.reference_set_id,
            },
            status_code=422,
        )
    return {
        "source": "actor_identity_scene_reference_set",
        "compatible": True,
        "route": route,
        "scene_reference_set": scene_reference_set,
    }


def build_video_identity_gate_result(*, video_url: Optional[str], automated_available: bool) -> IdentityGateResult:
    if not video_url:
        return IdentityGateResult(
            status="failed",
            reason="Video URL missing; cannot verify identity",
            gate_type="unavailable",
        )
    if not automated_available:
        return IdentityGateResult(
            status="manual_required",
            reason="Video identity requires manual review because automated face gate is not configured",
            gate_type="manual",
        )
    return IdentityGateResult(status="pending", reason="Automated video identity gate queued", gate_type="automated")
