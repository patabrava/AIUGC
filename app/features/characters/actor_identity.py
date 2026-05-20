from __future__ import annotations

from typing import Any, Optional

from app.core.errors import ErrorCode, FlowForgeException
from app.features.characters.schemas import ActorIdentityRecord, IdentityGateResult


def actor_identity_is_ready(identity: Optional[ActorIdentityRecord]) -> bool:
    if identity is None:
        return False
    return (
        identity.is_active is True
        and identity.training_phase == "ready"
        and identity.training_progress_percent == 100
        and bool(identity.provider_lora_id)
    )


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
