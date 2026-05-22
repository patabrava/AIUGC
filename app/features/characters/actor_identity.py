from __future__ import annotations

from typing import Any, Optional

from app.core.errors import ErrorCode, FlowForgeException
from app.features.characters.schemas import ActorIdentityRecord, IdentityGateResult, SceneReferenceSetSummary


CHARACTER_CONSISTENCY_MODES = {
    "character_consistency",
    "character_consistency_light",
    "character_consistency_mid",
}


def is_character_consistency_mode(value: Any) -> bool:
    return str(value or "").strip() in CHARACTER_CONSISTENCY_MODES


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
