"""
Durable quota guard for Veo submissions.

This slice reserves full-chain budget before submit, consumes units only after
provider acceptance, and freezes submissions when provider quota drifts from the
local ledger.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.adapters.supabase_client import get_supabase
from app.core.config import get_settings
from app.core.errors import ErrorCode, FlowForgeException
from app.core.logging import get_logger
from app.core.video_profiles import DurationProfile

logger = get_logger(__name__)

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
VEO_PROVIDER = "veo_3_1"


def _settings_limits() -> Dict[str, int]:
    settings = get_settings()
    return {
        "daily_limit": settings.veo_daily_generation_limit,
        "minute_limit": settings.veo_minute_generation_limit,
        "soft_buffer": settings.veo_quota_soft_buffer,
    }


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _next_pacific_reset(now: Optional[datetime] = None) -> datetime:
    current = now or _now_utc()
    pacific_now = current.astimezone(PACIFIC_TZ)
    next_midnight = datetime.combine(
        pacific_now.date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=PACIFIC_TZ,
    )
    return next_midnight.astimezone(timezone.utc)


def build_quota_day_pt(now: Optional[datetime] = None) -> str:
    current = now or _now_utc()
    return current.astimezone(PACIFIC_TZ).date().isoformat()


def chain_cost_units(profile: Optional[DurationProfile], *, provider: str) -> int:
    if provider != VEO_PROVIDER:
        return 0
    if profile is None:
        return 1
    return 1 + max(int(profile.veo_extension_hops or 0), 0)


def build_reservation_key(*, provider: str, post_id: str, correlation_id: str, kind: str = "video_chain") -> str:
    return f"{provider}:{kind}:{post_id}:{correlation_id}:{uuid4().hex[:12]}"


def _rpc(function_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = get_supabase().client.rpc(function_name, payload).execute()
    data = response.data
    if isinstance(data, list):
        return data[0] if data else {}
    return data or {}


def get_quota_snapshot(*, provider: str = VEO_PROVIDER) -> Dict[str, Any]:
    payload = {
        "p_provider": provider,
        **{f"p_{key}": value for key, value in _settings_limits().items()},
    }
    return _rpc("get_video_provider_quota_snapshot", payload)


def _quota_block_message(reason: str, snapshot: Dict[str, Any], requested_units: int) -> str:
    if reason == "provider_frozen":
        return (
            "VEO submissions are temporarily frozen after a provider quota rejection. "
            "No request was sent."
        )
    if reason == "minute_quota_exhausted":
        remaining = snapshot.get("minute_remaining_units", 0)
        limit = snapshot.get("minute_limit", 0)
        return (
            f"Blocked before submission: this request needs an immediate Veo slot, "
            f"but only {remaining} of {limit} minute slots remain. No request was sent."
        )
    remaining = snapshot.get("daily_remaining_units", 0)
    limit = snapshot.get("daily_limit", 0)
    return (
        f"Blocked before submission: this request needs {requested_units} Veo generations, "
        f"but only {remaining} of {limit} remain today. No request was sent."
    )


def raise_quota_block(reason: str, snapshot: Dict[str, Any], *, requested_units: int) -> None:
    raise FlowForgeException(
        code=ErrorCode.RATE_LIMIT,
        message=_quota_block_message(reason, snapshot, requested_units),
        details={
            "provider": VEO_PROVIDER,
            "reason": reason,
            "requested_units": requested_units,
            "snapshot": snapshot,
            "blocked_before_submit": True,
        },
        status_code=429,
    )


def ensure_immediate_submit_slot(*, requested_units: int = 1, provider: str = VEO_PROVIDER) -> Dict[str, Any]:
    snapshot = get_quota_snapshot(provider=provider)
    if snapshot.get("frozen"):
        raise_quota_block("provider_frozen", snapshot, requested_units=requested_units)
    if snapshot.get("minute_remaining_units", 0) < requested_units:
        raise_quota_block("minute_quota_exhausted", snapshot, requested_units=requested_units)
    return snapshot


def reserve_quota(
    *,
    provider: str,
    post_id: Optional[str],
    batch_id: Optional[str],
    reservation_key: str,
    requested_units: int,
    require_immediate_slot: bool,
    reservation_kind: str = "video_chain",
) -> Dict[str, Any]:
    payload = {
        "p_provider": provider,
        "p_reservation_key": reservation_key,
        "p_requested_units": requested_units,
        "p_post_id": post_id,
        "p_batch_id": batch_id,
        "p_reservation_kind": reservation_kind,
        "p_require_immediate_slot": require_immediate_slot,
        **{f"p_{key}": value for key, value in _settings_limits().items()},
    }
    result = _rpc("reserve_video_provider_quota", payload)
    if not result.get("allowed"):
        raise_quota_block(str(result.get("reason") or "daily_quota_exhausted"), result, requested_units=requested_units)
    logger.info(
        "quota_reserved",
        provider=provider,
        post_id=post_id,
        batch_id=batch_id,
        reservation_key=reservation_key,
        requested_units=requested_units,
        require_immediate_slot=require_immediate_slot,
    )
    return result


def consume_quota(*, reservation_key: str, operation_id: Optional[str], units: int = 1) -> Dict[str, Any]:
    result = _rpc(
        "consume_video_provider_quota",
        {
            "p_reservation_key": reservation_key,
            "p_units": units,
            "p_operation_id": operation_id,
        },
    )
    if not result.get("allowed"):
        raise FlowForgeException(
            code=ErrorCode.INTERNAL_ERROR,
            message="Quota ledger failed to consume an accepted Veo operation.",
            details={
                "reservation_key": reservation_key,
                "operation_id": operation_id,
                "result": result,
            },
            status_code=500,
        )
    logger.info(
        "quota_unit_consumed",
        reservation_key=reservation_key,
        operation_id=operation_id,
        units=units,
        remaining_units=result.get("remaining_units"),
    )
    return result


def release_quota(
    *,
    reservation_key: str,
    reason: str,
    final_status: str,
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    result = _rpc(
        "release_video_provider_quota",
        {
            "p_reservation_key": reservation_key,
            "p_reason": reason,
            "p_final_status": final_status,
            "p_error_code": error_code,
        },
    )
    logger.info(
        "quota_units_released",
        reservation_key=reservation_key,
        reason=reason,
        final_status=final_status,
        released_units=result.get("released_units"),
    )
    return result


def freeze_provider_quota(*, provider: str, reason: str) -> Dict[str, Any]:
    freeze_until = _next_pacific_reset()
    result = _rpc(
        "freeze_video_provider_quota",
        {
            "p_provider": provider,
            "p_freeze_until": freeze_until.isoformat(),
            "p_reason": reason,
        },
    )
    logger.warning(
        "quota_guard_frozen_after_provider_429",
        provider=provider,
        freeze_until=result.get("freeze_until"),
        reason=reason,
    )
    return result


def maybe_freeze_after_provider_429(*, provider: str, reason: str) -> None:
    settings = get_settings()
    if not settings.veo_quota_freeze_on_unexpected_429:
        return
    freeze_provider_quota(provider=provider, reason=reason)
