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
