"""
Seedance 2.5 evaluation runs for Semantic UGC and Manual Semantic UGC posts.

One evaluation re-submits the exact frame and prompt that one Veo take received
(the approved master's deterministic shot crop plus the take's persisted prompt)
to the selected Seedance host and stores the validated MP4 under its own storage
path. Evaluations never write ``posts.video_*``, ``semantic_video_runs``/``takes``,
captions, or publishing: Veo stays the only production provider for Semantic
delivery, and a Seedance provider failure never falls back to a paid Veo request.

Lifecycle (PostgreSQL owns every transition, see
``supabase/migrations/20261001000000_runway_video_evaluations.sql``):
reserved -> submitting -> submitted -> processing -> completed | failed | cancelled,
with ``submission_unknown`` as the blocked state for ambiguous acknowledgements.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
import re
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Mapping, Optional
from urllib.parse import urlparse
from uuid import uuid4

from app.adapters.runway_client import (
    DATA_URI_MAX_CHARS,
    HTTPS_PROMPT_IMAGE_MAX_CHARS,
    PROMPT_TEXT_MAX_CHARS,
    RUNWAY_API_VERSION,
    SEED_MAX,
    SEEDANCE_2_5_MODEL,
    SEEDANCE_2_5_PORTRAIT_RATIOS,
    TASK_STATUSES_ACTIVE,
    RunwayError,
    RunwayTask,
    build_image_data_uri,
    get_runway_client,
    parse_allowed_hosts,
)
from app.adapters.modelark_client import estimate_cost, cost_units, ASSET_URI
from app.features.runway_evaluations.providers import profile, setting, client_factory, ProviderSettings
from app.core.errors import ErrorCode, FlowForgeException, StateTransitionError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

PROVIDER = "runway"
PROVIDER_MODEL = SEEDANCE_2_5_MODEL
QUOTA_PROVIDER = "runway_seedance_2_5"
EVALUATION_DURATION_SECONDS = 8
EVALUATION_ASPECT_RATIO = "9:16"
SEMANTIC_EVALUATION_MODES = frozenset({"semantic_ugc", "manual_semantic_ugc"})
ACTIVE_STATUSES = ("reserved", "submitting", "submitted", "processing", "submission_unknown")
SOURCE_ROLE = "semantic_take_shot_frame"
SOURCE_LINEAGE = "semantic_approved_master"
ACTOR_REFERENCE_ROLES = ("actor_front", "actor_three_quarter")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEASE_SECONDS = 300
_RESERVED_STALE_SECONDS = 300
_SUBMITTING_STALE_SECONDS = 900
_MAX_POLL_BACKOFF_SECONDS = 300
_DURATION_TOLERANCE_SECONDS = 0.75
_ASPECT_TOLERANCE = 0.01
_MIN_HEIGHT_RATIO = 0.9


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreditEstimate:
    resolution: str
    duration_seconds: int
    credits_per_second: int
    minimum_credits: int
    credits: int
    usd: Decimal
    unit: str = "runway_credit"
    pricing: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "resolution": self.resolution,
            "duration_seconds": self.duration_seconds,
            "credits_per_second": self.credits_per_second,
            "minimum_credits": self.minimum_credits,
            "credits": self.credits,
            "usd": str(self.usd),
            "pricing_basis": "modelark_output_tokens" if self.unit == "usd_microdollar" else "configured_runway_list_price",
            "unit": self.unit,
            **self.pricing,
        }


def estimate_credits(*, resolution: str, duration_seconds: int, settings: Any) -> CreditEstimate:
    """Output-only Seedance 2.5 estimate: max(seconds x rate, minimum). Image references are free."""
    if profile(settings).name == "modelark":
        pricing = estimate_cost(resolution, int(duration_seconds), settings)
        units = pricing["reserved_units"]
        return CreditEstimate(resolution, int(duration_seconds), 0, 0, units,
                              Decimal(units) / 1_000_000, "usd_microdollar", pricing)
    rates = {
        "480p": int(setting(settings, "seedance_credits_per_second_480p")),
        "720p": int(setting(settings, "seedance_credits_per_second_720p")),
        "1080p": int(setting(settings, "seedance_credits_per_second_1080p")),
    }
    if resolution not in rates:
        raise ValidationError("Unsupported Seedance 2.5 resolution.", {"resolution": resolution})
    minimum = int(setting(settings, "seedance_minimum_credits"))
    credits = max(int(duration_seconds) * rates[resolution], minimum)
    usd = (Decimal(credits) * Decimal(str(setting(settings, "usd_per_credit")))).quantize(Decimal("0.01"))
    return CreditEstimate(
        resolution=resolution,
        duration_seconds=int(duration_seconds),
        credits_per_second=rates[resolution],
        minimum_credits=minimum,
        credits=credits,
        usd=usd,
    )


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


@dataclass
class EvaluationContext:
    post: Dict[str, Any]
    batch: Dict[str, Any]
    run: Optional[Dict[str, Any]]
    takes: List[Dict[str, Any]] = field(default_factory=list)


def _int(value: Any, default: int = -1) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _csv(value: Any) -> tuple[str, ...]:
    return tuple(entry.strip().lower() for entry in str(value or "").split(",") if entry.strip())


def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _reason(code: str, message: str, http_status: int = 422) -> Dict[str, Any]:
    return {"code": code, "message": message, "http_status": http_status}


def operator_authorized(email: Optional[str], settings: Any) -> bool:
    normalized = str(email or "").strip().lower()
    reviewer = str(getattr(settings, "reviewer_login_email", "") or "").strip().lower()
    allowed = set(_csv(setting(settings, "evaluation_operator_emails", "")))
    return bool(normalized) and normalized in allowed and normalized != reviewer


def configuration_reasons(settings: Any, operator_email: Optional[str]) -> List[Dict[str, Any]]:
    reasons = []
    if not bool(setting(settings, "evaluation_enabled", False)):
        reasons.append(_reason("feature_flag_disabled", "Seedance evaluations are disabled (provider enable flag is off).", 403))
    if not str(setting(settings, "api_key", "") or "").strip():
        reasons.append(_reason("credentials_missing", "provider API key is not configured on the server.", 503))
    if not parse_allowed_hosts(setting(settings, "output_allowed_hosts", "")):
        reasons.append(
            _reason(
                "output_hosts_not_configured",
                "provider output host allowlist is empty, so a finished output could not be downloaded safely.",
                503,
            )
        )
    if not operator_authorized(operator_email, settings):
        reasons.append(
            _reason("operator_not_authorized", "This account is not listed in provider operator allowlist.", 403)
        )
    return reasons


def select_take(takes: List[Dict[str, Any]], take_index: int) -> Optional[Dict[str, Any]]:
    """Return the newest attempt for one take index that carries a persisted request contract."""
    matching = [
        take
        for take in takes
        if _int(take.get("take_index")) == take_index and isinstance(take.get("request_contract"), Mapping)
    ]
    if not matching:
        return None
    return max(matching, key=lambda take: _int(take.get("attempt"), 1))


def _matched_resolution(take: Optional[Dict[str, Any]], run: Optional[Dict[str, Any]]) -> Optional[str]:
    contract = _mapping((take or {}).get("request_contract"))
    value = str(contract.get("resolution") or (run or {}).get("resolution") or "").strip().lower()
    return value or None


def evaluate_eligibility(
    *,
    context: EvaluationContext,
    take_index: int,
    resolution: Optional[str],
    settings: Any,
    operator_email: Optional[str],
    active_count: Optional[int] = None,
    budget_snapshot: Optional[Dict[str, Any]] = None,
    moderation_failure: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Free check of everything that can block a paid submission; never calls Runway."""
    reasons = configuration_reasons(settings, operator_email)
    if moderation_failure:
        reasons.append(_reason(
            "previous_provider_moderation",
            "The Seedance provider previously rejected this exact source image and prompt. Provider review is required before another submission; no paid request was sent.",
            409,
        ))
    post, batch, run = context.post, context.batch, context.run
    creation_mode = str(batch.get("creation_mode") or "").strip()
    if creation_mode not in SEMANTIC_EVALUATION_MODES:
        reasons.append(
            _reason("unsupported_creation_mode", "Seedance evaluations support only Semantic UGC and Manual Semantic UGC posts.")
        )
    seed_data = _mapping(post.get("seed_data"))
    if seed_data.get("script_review_status") == "removed" or seed_data.get("video_excluded") is True:
        reasons.append(_reason("post_removed", "Removed posts cannot be evaluated.", 409))

    take: Optional[Dict[str, Any]] = None
    if run is None:
        reasons.append(_reason("semantic_run_missing", "This post has no Semantic video run with an approved plan yet.", 409))
    else:
        master = _mapping(run.get("master_snapshot"))
        master_hash = str(run.get("master_hash") or master.get("sha256") or "").strip().lower()
        if not str(master.get("storage_uri") or "").strip() or not _SHA256.fullmatch(master_hash):
            reasons.append(_reason("approved_master_missing", "The Semantic run has no approved scene-plate master.", 409))
        if not isinstance(run.get("plan_snapshot"), Mapping):
            reasons.append(_reason("semantic_plan_missing", "Build the free Semantic plan before evaluating a take.", 409))
        take = select_take(context.takes, take_index)
        if take is None:
            reasons.append(_reason("take_not_found", f"The Semantic plan has no take {take_index}.", 404))
        else:
            contract = _mapping(take.get("request_contract"))
            transform = _mapping(take.get("shot_transform"))
            duration = _int(contract.get("provider_duration_seconds"), _int(take.get("provider_duration_seconds")))
            if duration != EVALUATION_DURATION_SECONDS:
                reasons.append(
                    _reason("unsupported_take_duration", "Phase one evaluates only 8-second takes.")
                )
            shot_hash = str(transform.get("output_sha256") or "").strip().lower()
            if not _SHA256.fullmatch(shot_hash) or str(contract.get("shot_sha256") or "").lower() != shot_hash:
                reasons.append(_reason("shot_contract_missing", "The take has no verifiable shot-frame contract.", 409))
            prompt = str(contract.get("prompt") or "")
            if not prompt.strip():
                reasons.append(_reason("take_prompt_missing", "The take has no persisted prompt.", 409))
            elif len(prompt) > PROMPT_TEXT_MAX_CHARS:
                reasons.append(_reason("take_prompt_too_long", "The take prompt exceeds the provider's 15000-character limit."))

    resolved_resolution = str(resolution or _matched_resolution(take, run) or "").strip().lower()
    allowed_resolutions = _csv(setting(settings, "evaluation_allowed_resolutions", ""))
    estimate: Optional[CreditEstimate] = None
    if resolved_resolution not in profile(settings).ratios or resolved_resolution not in allowed_resolutions:
        reasons.append(
            _reason(
                "unsupported_resolution",
                f"Resolution {resolved_resolution or 'unknown'} is not allowed; allowed: {', '.join(allowed_resolutions) or 'none'}.",
            )
        )
    else:
        estimate = estimate_credits(
            resolution=resolved_resolution,
            duration_seconds=EVALUATION_DURATION_SECONDS,
            settings=settings,
        )
        if estimate.credits > int(setting(settings, "evaluation_max_credits_per_run")):
            reasons.append(
                _reason(
                    "estimate_exceeds_run_cap",
                    f"Estimated {estimate.credits} credits exceeds the per-run cap of {setting(settings, 'evaluation_max_credits_per_run')}.",
                )
            )

    budget: Dict[str, Any] = {"daily_credit_limit": int(setting(settings, "evaluation_daily_credit_limit"))}
    if budget_snapshot:
        remaining = _int(budget_snapshot.get("daily_remaining_units"), 0)
        budget.update(
            {
                "daily_committed_credits": _int(budget_snapshot.get("daily_committed_units"), 0),
                "daily_remaining_credits": remaining,
                "frozen": bool(budget_snapshot.get("frozen")),
            }
        )
        if estimate is not None and remaining < estimate.credits:
            reasons.append(_reason("daily_budget_exhausted", "Today's Seedance provider budget cannot cover this evaluation.", 429))
    if active_count is not None:
        budget["active_evaluations"] = active_count
        budget["max_active_evaluations"] = int(setting(settings, "evaluation_max_active"))
        if active_count >= int(setting(settings, "evaluation_max_active")):
            reasons.append(_reason("concurrency_limit_reached", "Another Seedance provider evaluation is still active.", 429))

    take_contract = _mapping((take or {}).get("request_contract"))
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "provider": profile(settings).name,
        "provider_model": profile(settings).model,
        "evaluation_only": True,
        "post_id": str(post.get("id") or ""),
        "creation_mode": creation_mode or None,
        "semantic_run_id": str(run.get("id")) if run else None,
        "take_index": take_index,
        "take_attempt": _int(take.get("attempt"), 1) if take else None,
        "request": {
            "endpoint": "POST " + profile(settings).endpoint,
            "resolution": resolved_resolution or None,
            "ratio": profile(settings).ratios.get(resolved_resolution),
            "aspect_ratio": EVALUATION_ASPECT_RATIO,
            "duration_seconds": EVALUATION_DURATION_SECONDS,
            "audio": True,
            "prompt_image_role": SOURCE_ROLE,
        },
        "estimate": estimate.as_dict() if estimate else None,
        "budget": budget,
        "matched_veo_take": {
            "provider_model": take_contract.get("provider_model") or (take or {}).get("provider_model"),
            "resolution": take_contract.get("resolution"),
            "request_hash": (take or {}).get("request_hash"),
            "submission_state": (take or {}).get("submission_state"),
        }
        if take
        else None,
    }


# ---------------------------------------------------------------------------
# Source verification (mirrors the Semantic worker's pre-paid gates for one take)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifiedSource:
    image_bytes: bytes
    mime_type: str
    sha256: str
    byte_length: int
    provenance: Dict[str, Any]


def _semantic_contracts() -> Any:
    from app.features.semantic_videos import visual_contract

    return visual_contract


def verify_semantic_take_source(
    *,
    run: Mapping[str, Any],
    take: Mapping[str, Any],
    storage: Any,
    correlation_id: str,
    contracts: Any = None,
) -> VerifiedSource:
    """Re-download and checksum every authority behind one take's frame before paid work."""
    from app.features.shot_production.shot_deck import derive_shot_deck

    contracts = contracts or _semantic_contracts()
    run_id = str(run.get("id") or "")
    master = _mapping(run.get("master_snapshot"))
    reference = _mapping(run.get("reference_snapshot"))
    actor_references = reference.get("actor_references")
    if not isinstance(actor_references, list) or len(actor_references) != 2:
        raise ValidationError("The Seedance provider evaluation requires two immutable actor references.")
    verified_actor_rows = []
    for index, (row, expected_role) in enumerate(zip(actor_references, ACTOR_REFERENCE_ROLES)):
        if not isinstance(row, Mapping) or str(row.get("role") or "") != expected_role:
            raise StateTransitionError(
                "Semantic actor reference order changed before the Seedance provider evaluation.",
                {"index": index, "expected_role": expected_role},
            )
        reference_bytes = storage.download_video(
            video_url=str(row.get("storage_uri") or ""),
            correlation_id=f"{correlation_id}_{expected_role}",
        )
        if (
            sha256(reference_bytes).hexdigest() != str(row.get("sha256") or "").lower()
            or len(reference_bytes) != _int(row.get("byte_length"))
            or not str(row.get("mime_type") or "").lower().startswith("image/")
        ):
            raise StateTransitionError("Semantic actor reference changed before the Seedance provider evaluation.", {"role": expected_role})
        verified_actor_rows.append(dict(row))
    actor_fingerprint = contracts.build_actor_reference_fingerprint(verified_actor_rows)
    if actor_fingerprint != str(reference.get("actor_reference_fingerprint") or "").lower():
        raise StateTransitionError("Semantic actor-reference fingerprint changed before the Seedance provider evaluation.")
    generation_contract = contracts.validate_scene_plate_generation_contract(
        reference.get("scene_plate_generation_contract"),
        actor_reference_fingerprint=actor_fingerprint,
    )
    visual_contract = contracts.validate_visual_contract(reference.get("visual_contract"))
    if (
        str(master.get("generation_contract_hash") or "").lower() != generation_contract["contract_hash"]
        or str(master.get("visual_contract_hash") or "").lower() != visual_contract["contract_hash"]
        or str(master.get("actor_reference_fingerprint") or "").lower() != actor_fingerprint
        or str(master.get("provider_model") or "") != generation_contract["model"]
    ):
        raise StateTransitionError("Semantic approved master contract changed before the Seedance provider evaluation.")
    contracts.validate_approved_scene_plate_identity(
        master,
        actor_reference_fingerprint=actor_fingerprint,
        generation_contract=generation_contract,
    )
    plan = _mapping(run.get("plan_snapshot"))
    if (
        str(plan.get("generation_contract_hash") or "").lower() != generation_contract["contract_hash"]
        or str(plan.get("actor_reference_fingerprint") or "").lower() != actor_fingerprint
        or str(plan.get("visual_contract_hash") or "").lower() != visual_contract["contract_hash"]
    ):
        raise StateTransitionError("Semantic plan no longer matches its identity contracts.")

    master_bytes = storage.download_video(
        video_url=str(master.get("storage_uri") or ""),
        correlation_id=f"{correlation_id}_master",
    )
    expected_master_hash = str(run.get("master_hash") or master.get("sha256") or "").lower()
    if sha256(master_bytes).hexdigest() != expected_master_hash or len(master_bytes) != _int(master.get("byte_length")):
        raise StateTransitionError("Semantic approved master changed before the Seedance provider evaluation.")

    take_index = _int(take.get("take_index"))
    transform = _mapping(take.get("shot_transform"))
    contract = _mapping(take.get("request_contract"))
    deck = derive_shot_deck(
        approved_master_bytes=master_bytes,
        expected_sha256=expected_master_hash,
        mime_type=str(master.get("mime_type") or "image/png"),
        shot_count=take_index + 1,
    )
    shot = deck[take_index]
    expected_shot_hash = str(transform.get("output_sha256") or "").lower()
    if shot.output_sha256 != expected_shot_hash or str(contract.get("shot_sha256") or "").lower() != expected_shot_hash:
        raise StateTransitionError("Semantic shot frame changed before the Seedance provider evaluation.")

    provenance = {
        "semantic_run_id": run_id,
        "semantic_run_stage": run.get("stage"),
        "script_hash": run.get("script_hash"),
        "approved_master_sha256": expected_master_hash,
        "approved_master_storage_uri": master.get("storage_uri"),
        "approved_master_mime_type": master.get("mime_type"),
        "actor_reference_fingerprint": actor_fingerprint,
        "generation_contract_hash": generation_contract["contract_hash"],
        "visual_contract_hash": visual_contract["contract_hash"],
        "take_id": str(take.get("id") or ""),
        "take_index": take_index,
        "take_attempt": _int(take.get("attempt"), 1),
        "shot_name": shot.name,
        "shot_sha256": shot.output_sha256,
        "beat_text_sha256": sha256(str(take.get("beat_text") or "").encode("utf-8")).hexdigest(),
        "veo_request_hash": take.get("request_hash"),
        "veo_provider_model": contract.get("provider_model") or take.get("provider_model"),
        "veo_resolution": contract.get("resolution"),
        "veo_submission_state": take.get("submission_state"),
        "veo_raw_artifact_uri": take.get("raw_artifact_uri"),
        "veo_raw_artifact_sha256": take.get("raw_artifact_sha256"),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    return VerifiedSource(
        image_bytes=shot.image_bytes,
        mime_type=shot.mime_type,
        sha256=shot.output_sha256,
        byte_length=len(shot.image_bytes),
        provenance=provenance,
    )


def build_prompt_image(*, source: VerifiedSource, storage_factory: Callable[[], Any], correlation_id: str, settings: Any = None) -> tuple[str, Dict[str, Any]]:
    """Send the exact verified bytes: inline when they fit the data-URI cap, else via a content-addressed R2 URL."""
    if profile(settings).name == "modelark" and setting(settings, "reference_asset_uri", ""):
        uri = setting(settings, "reference_asset_uri")
        if not ASSET_URI.fullmatch(uri) or setting(settings, "reference_asset_sha256", "") != source.sha256:
            raise StateTransitionError("The trusted ModelArk asset must match the exact verified source checksum.")
        return uri, {"transport": "modelark_trusted_asset", "asset_uri": uri}
    data_uri = build_image_data_uri(source.image_bytes, source.mime_type)
    if len(data_uri) <= DATA_URI_MAX_CHARS:
        return data_uri, {"transport": "data_uri"}
    storage = storage_factory()
    prefix = str(getattr(storage, "image_prefix", "") or "").strip("/")
    extension = "png" if source.mime_type == "image/png" else "jpg"
    object_key = f"{prefix}/runway-evaluations/sources/{source.sha256}.{extension}".lstrip("/")
    uploaded = storage.upload_video(
        video_bytes=source.image_bytes,
        file_name=f"{source.sha256}.{extension}",
        correlation_id=correlation_id,
        content_type=source.mime_type,
        object_key=object_key,
    )
    url = str(uploaded.get("url") or "")
    if str(uploaded.get("sha256") or "") != source.sha256:
        raise StateTransitionError("Uploaded Seedance provider source frame does not match the verified bytes.")
    if not url.startswith("https://") or len(url) > HTTPS_PROMPT_IMAGE_MAX_CHARS:
        raise ValidationError("The Seedance provider source frame URL is not a usable HTTPS URL.")
    return url, {"transport": "https_storage_url", "storage_key": object_key, "url": url}


def _canonical_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def build_request_contract(
    *,
    take: Mapping[str, Any],
    resolution: str,
    estimate: CreditEstimate,
    source: VerifiedSource,
    prompt_image_meta: Mapping[str, Any],
    settings: Any = None,
) -> Dict[str, Any]:
    contract = _mapping(take.get("request_contract"))
    prompt = str(contract.get("prompt") or "")
    negative = str(contract.get("negative_prompt") or "")
    seed = contract.get("seed") if contract.get("seed") is not None else take.get("seed")
    seed_value = _int(seed, -1)
    return {
        "provider": profile(settings).name,
        "provider_model": profile(settings).model,
        "api_version": profile(settings).api_version,
        "endpoint": profile(settings).endpoint,
        "resolution": resolution,
        "ratio": profile(settings).ratios[resolution],
        "aspect_ratio": EVALUATION_ASPECT_RATIO,
        "duration_seconds": EVALUATION_DURATION_SECONDS,
        "audio": True,
        "seed": seed_value if 0 <= seed_value <= profile(settings).seed_max else None,
        "seed_omitted_out_of_provider_range": seed_value > profile(settings).seed_max,
        "prompt_source": "semantic_take_request_contract",
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(prompt),
        "negative_prompt_dropped": bool(negative.strip()),
        "negative_prompt_sha256": sha256(negative.encode("utf-8")).hexdigest() if negative.strip() else None,
        "prompt_image": {
            "role": SOURCE_ROLE,
            "lineage": SOURCE_LINEAGE,
            "mime_type": source.mime_type,
            "sha256": source.sha256,
            "byte_length": source.byte_length,
            **{key: value for key, value in prompt_image_meta.items() if key in {"transport", "storage_key", "asset_uri"}},
        },
        "estimated_credits": estimate.credits,
        "estimated_usd": str(estimate.usd),
        "credits_per_second": estimate.credits_per_second,
        "minimum_credits": estimate.minimum_credits,
        "pricing": estimate.as_dict(),
        "budget_unit": estimate.unit,
    }


# ---------------------------------------------------------------------------
# Environment scoping (must match workers/video_poller.py semantics)
# ---------------------------------------------------------------------------


def poller_environment(settings: Any) -> str:
    return str(getattr(settings, "environment", "") or "").strip().lower() or "development"


def poller_scope(settings: Any) -> str:
    app_url = str(getattr(settings, "app_url", "") or "").strip()
    if app_url:
        parsed = urlparse(app_url if "://" in app_url else f"https://{app_url}")
        host = (parsed.hostname or "").strip().lower()
        if host:
            return host
    app_host = str(getattr(settings, "app_host", "") or "").strip().lower()
    return app_host or poller_environment(settings)


# ---------------------------------------------------------------------------
# Persistence (RPC-only writes)
# ---------------------------------------------------------------------------


class RunwayEvaluationRepository:
    """Thin PostgREST wrapper; every write goes through a SECURITY DEFINER RPC."""

    def __init__(self, client: Any = None, provider: str = "runway") -> None:
        self._client = client
        self.provider = provider

    def _db(self) -> Any:
        if self._client is not None:
            return self._client
        from app.adapters.supabase_client import get_supabase

        return get_supabase().client

    def _rpc(self, name: str, payload: Dict[str, Any]) -> Any:
        return self._db().rpc(name, payload).execute().data

    def _one(self, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = self._rpc(name, payload)
        if isinstance(data, list):
            data = data[0] if data else {}
        return dict(data or {})

    def create(self, payload: Dict[str, Any], *, daily_credit_limit: int, max_active: int, min_submit_interval_seconds: int) -> Dict[str, Any]:
        return self._one(
            "create_runway_video_evaluation",
            {
                "p_payload": payload,
                "p_daily_credit_limit": int(daily_credit_limit),
                "p_max_active": int(max_active),
                "p_min_submit_interval_seconds": int(min_submit_interval_seconds),
            },
        )

    def mark_submitting(self, evaluation_id: str) -> Dict[str, Any]:
        return self._one("mark_runway_video_evaluation_submitting", {"p_evaluation_id": evaluation_id})

    def acknowledge(self, evaluation_id: str, *, task_id: str, provider_estimated_credits: Optional[float], cancel_reason: Optional[str] = None) -> Dict[str, Any]:
        return self._one(
            "acknowledge_runway_video_evaluation",
            {
                "p_evaluation_id": evaluation_id,
                "p_task_id": task_id,
                "p_provider_estimated_credits": provider_estimated_credits,
                "p_cancel_reason": cancel_reason,
            },
        )

    def fail_before_submit(self, evaluation_id: str, *, code: str, message: str, details: Dict[str, Any]) -> Dict[str, Any]:
        return self._one(
            "fail_runway_video_evaluation_before_submit",
            {"p_evaluation_id": evaluation_id, "p_error_code": code, "p_error_message": message, "p_error_details": details},
        )

    def mark_submission_unknown(self, evaluation_id: str, *, code: str, message: str, details: Dict[str, Any]) -> Dict[str, Any]:
        return self._one(
            "mark_runway_video_evaluation_submission_unknown",
            {"p_evaluation_id": evaluation_id, "p_error_code": code, "p_error_message": message, "p_error_details": details},
        )

    def reconcile_stale(self, *, reserved_stale_seconds: int, submitting_stale_seconds: int) -> Dict[str, Any]:
        return self._one(
            "reconcile_stale_runway_video_evaluations",
            {"p_reserved_stale_seconds": reserved_stale_seconds, "p_submitting_stale_seconds": submitting_stale_seconds},
        )

    def claim(self, *, worker_id: str, lease_seconds: int, limit: int, environment: str, scope: str) -> List[Dict[str, Any]]:
        data = self._rpc(
            "claim_seedance_video_evaluations" if self.provider == "modelark" else "claim_runway_video_evaluations",
            {
                **({"p_provider": self.provider} if self.provider == "modelark" else {}),
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
                "p_limit": limit,
                "p_poller_environment": environment,
                "p_poller_scope": scope,
            },
        )
        return [dict(row) for row in (data or []) if isinstance(row, Mapping)]

    def record_poll(
        self,
        evaluation_id: str,
        *,
        lease_token: str,
        provider_status: Optional[str],
        progress: Optional[float],
        next_poll_at: datetime,
        poll_error: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._one(
            "record_runway_video_evaluation_poll",
            {
                "p_evaluation_id": evaluation_id,
                "p_lease_token": lease_token,
                "p_provider_status": provider_status,
                "p_progress": progress,
                "p_next_poll_at": next_poll_at.isoformat(),
                "p_poll_error": poll_error,
            },
        )

    def complete(self, evaluation_id: str, *, lease_token: str, output: Dict[str, Any], actual_credits: Optional[int]) -> Dict[str, Any]:
        return self._one(
            "complete_runway_video_evaluation",
            {"p_evaluation_id": evaluation_id, "p_lease_token": lease_token, "p_output": output, "p_actual_credits": actual_credits},
        )

    def fail_after_submit(
        self,
        evaluation_id: str,
        *,
        lease_token: str,
        final_status: str,
        code: str,
        message: str,
        details: Dict[str, Any],
        provider_status: Optional[str] = None,
        actual_credits: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._one(
            "fail_runway_video_evaluation_after_submit",
            {
                "p_evaluation_id": evaluation_id,
                "p_lease_token": lease_token,
                "p_final_status": final_status,
                "p_error_code": code,
                "p_error_message": message,
                "p_error_details": details,
                "p_provider_status": provider_status,
                "p_actual_credits": actual_credits,
            },
        )

    def list_for_post(self, post_id: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        response = (
            self._db()
            .table("runway_video_evaluations")
            .select("*")
            .eq("post_id", post_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [dict(row) for row in (response.data or [])]

    def find_moderated_request(self, *, post_id: str, prompt_sha256: str, shot_sha256: str) -> Optional[Dict[str, Any]]:
        response = (
            self._db().table("runway_video_evaluations")
            .select("id,error_details")
            .eq("post_id", post_id)
            .in_("provider_model", [PROVIDER_MODEL, "dreamina-seedance-2-5-260628"])
            .eq("status", "failed")
            .eq("request_contract->>prompt_sha256", prompt_sha256)
            .eq("request_contract->prompt_image->>sha256", shot_sha256)
            .or_("error_details->>failure_code.ilike.*SAFETY*,error_details->>failure_code.ilike.*Sensitive*,error_details->>failure_code.ilike.*ContentRisk*,error_details->>failure_code.ilike.*Portrait*")
            .limit(1).execute()
        )
        return dict(response.data[0]) if response.data else None

    def count_active(self) -> int:
        response = (
            self._db()
            .table("runway_video_evaluations")
            .select("id")
            .eq("provider", self.provider)
            .in_("status", list(ACTIVE_STATUSES))
            .execute()
        )
        return len(response.data or [])


def load_evaluation_context(post_id: str, *, client: Any = None) -> EvaluationContext:
    from app.adapters.supabase_client import get_supabase
    from app.features.semantic_videos import queries as semantic_queries

    database = client or get_supabase().client
    response = database.table("posts").select("*,batches(*)").eq("id", post_id).limit(1).execute()
    rows = response.data or []
    if not rows:
        raise FlowForgeException(code=ErrorCode.NOT_FOUND, message="Post not found.", details={"post_id": post_id}, status_code=404)
    post = dict(rows[0])
    joined = post.pop("batches", None)
    batch = dict(joined[0]) if isinstance(joined, list) and joined else dict(joined or {})
    run = semantic_queries.get_run_by_post(post_id)
    takes = semantic_queries.list_attempts(str(run["id"])) if run else []
    return EvaluationContext(post=post, batch=batch, run=run, takes=list(takes))


# ---------------------------------------------------------------------------
# Media validation
# ---------------------------------------------------------------------------


class MediaValidationError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def probe_mp4(
    content: bytes,
    *,
    expected_duration_seconds: float,
    expected_ratio: str,
    expect_audio: bool,
    ffprobe_bin: str = "ffprobe",
    timeout_seconds: float = 60.0,
) -> Dict[str, Any]:
    """Prove the bytes are a playable MP4 with the requested duration, framing, and an audio track."""
    if len(content) < 12 or content[4:8] != b"ftyp":
        raise MediaValidationError("media_not_mp4", "The Seedance provider output is not an MP4 container.")
    with tempfile.NamedTemporaryFile(suffix=".mp4") as handle:
        handle.write(content)
        handle.flush()
        try:
            result = subprocess.run(
                [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", handle.name],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise MediaValidationError("media_probe_unavailable", "ffprobe is not installed on this worker.") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaValidationError("media_corrupt", "ffprobe timed out on the Seedance provider output.") from exc
    if result.returncode != 0:
        raise MediaValidationError("media_corrupt", "ffprobe could not read the Seedance provider output.", {"stderr": result.stderr[-300:]})
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaValidationError("media_corrupt", "ffprobe returned unreadable metadata.") from exc
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaValidationError("media_missing_video", "The Seedance provider output has no video stream.")
    width, height = _int(video.get("width"), 0), _int(video.get("height"), 0)
    try:
        duration = float((payload.get("format") or {}).get("duration") or video.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    expected_width, expected_height = (int(part) for part in expected_ratio.split(":"))
    probe = {
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "video_codec": video.get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "has_audio": audio is not None,
        "expected_ratio": expected_ratio,
        "expected_duration_seconds": expected_duration_seconds,
    }
    if width <= 0 or height <= 0:
        raise MediaValidationError("media_resolution_mismatch", "The Seedance provider output has no usable frame size.", probe)
    expected_aspect = expected_width / expected_height
    if abs((width / height) - expected_aspect) / expected_aspect > _ASPECT_TOLERANCE or height < expected_height * _MIN_HEIGHT_RATIO:
        raise MediaValidationError("media_resolution_mismatch", "The Seedance provider output framing does not match the requested ratio.", probe)
    if abs(duration - float(expected_duration_seconds)) > _DURATION_TOLERANCE_SECONDS:
        raise MediaValidationError("media_duration_mismatch", "The Seedance provider output duration does not match the request.", probe)
    if expect_audio and audio is None:
        raise MediaValidationError("media_missing_audio", "The Seedance provider output has no audio track.", probe)
    return probe


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def write_recovery_record(record: Dict[str, Any]) -> None:
    """Persist an accepted task id when the database acknowledgement fails."""
    recovery_dir = "recovery_logs"
    os.makedirs(recovery_dir, exist_ok=True)
    path = os.path.join(recovery_dir, f"runway_evaluation_recovery_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({**record, "timestamp": datetime.now(timezone.utc).isoformat()}) + "\n")


@dataclass
class EvaluationDependencies:
    settings: Any
    repository: Any
    storage_factory: Callable[[], Any]
    runway_client_factory: Callable[[Any], Any]
    load_context: Callable[[str], EvaluationContext]
    budget_snapshot: Optional[Callable[[int], Dict[str, Any]]] = None
    probe_media: Callable[..., Dict[str, Any]] = probe_mp4
    recovery_writer: Callable[[Dict[str, Any]], None] = write_recovery_record
    contracts: Any = None
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def default_dependencies() -> EvaluationDependencies:
    from app.adapters.storage_client import get_storage_client
    from app.core.config import get_settings
    from app.features.videos.quota_guard import get_seedance_budget_snapshot

    settings = get_settings()
    return EvaluationDependencies(
        settings=settings,
        repository=RunwayEvaluationRepository(provider=profile(settings).name),
        storage_factory=get_storage_client,
        runway_client_factory=client_factory,
        load_context=load_evaluation_context,
        budget_snapshot=lambda limit: get_seedance_budget_snapshot(provider=profile(settings).quota, daily_unit_limit=limit),
    )


_PUBLIC_FIELDS = (
    "id",
    "post_id",
    "batch_id",
    "semantic_run_id",
    "take_index",
    "take_attempt",
    "provider",
    "provider_model",
    "status",
    "requested_resolution",
    "requested_ratio",
    "requested_duration_seconds",
    "estimated_credits",
    "estimated_usd",
    "provider_estimated_credits",
    "actual_credits",
    "task_id",
    "last_provider_status",
    "provider_progress",
    "requested_by",
    "submit_intent_at",
    "submitted_at",
    "completed_at",
    "output_url",
    "output_sha256",
    "output_byte_length",
    "output_probe",
    "error_code",
    "error_message",
    "request_contract",
    "source_provenance",
    "created_at",
)


def serialize_evaluation(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: row.get(key) for key in _PUBLIC_FIELDS}


def _http_error_code(http_status: int) -> ErrorCode:
    return {
        403: ErrorCode.AUTH_FAIL,
        404: ErrorCode.NOT_FOUND,
        409: ErrorCode.STATE_TRANSITION_ERROR,
        429: ErrorCode.RATE_LIMIT,
    }.get(http_status, ErrorCode.VALIDATION_ERROR if http_status < 500 else ErrorCode.INTERNAL_ERROR)


def _previous_moderation(context: EvaluationContext, take_index: int, repository: Any) -> Optional[Dict[str, Any]]:
    take = select_take(context.takes, take_index)
    contract = _mapping((take or {}).get("request_contract"))
    prompt = str(contract.get("prompt") or "")
    shot_hash = str(contract.get("shot_sha256") or "").lower()
    if not prompt.strip() or not _SHA256.fullmatch(shot_hash):
        return None
    return repository.find_moderated_request(
        post_id=str(context.post["id"]),
        prompt_sha256=sha256(prompt.encode("utf-8")).hexdigest(),
        shot_sha256=shot_hash,
    )


def preview_evaluation(
    *,
    post_id: str,
    take_index: int,
    resolution: Optional[str],
    operator_email: Optional[str],
    deps: Optional[EvaluationDependencies] = None,
) -> Dict[str, Any]:
    deps = deps or default_dependencies()
    settings = deps.settings
    context = deps.load_context(post_id)
    moderation_failure = _previous_moderation(context, take_index, deps.repository)
    active_count = None
    budget_snapshot = None
    try:
        active_count = deps.repository.count_active()
        if deps.budget_snapshot is not None:
            budget_snapshot = deps.budget_snapshot(int(setting(settings, "evaluation_daily_credit_limit")))
    except Exception as exc:  # noqa: BLE001 - preview stays readable; submit re-checks atomically
        logger.warning("runway_evaluation_preview_budget_unavailable", post_id=post_id, error_type=type(exc).__name__)
    return evaluate_eligibility(
        context=context,
        take_index=take_index,
        resolution=resolution,
        settings=settings,
        operator_email=operator_email,
        active_count=active_count,
        budget_snapshot=budget_snapshot,
        moderation_failure=moderation_failure,
    )


def _block_message(reason: str) -> str:
    return {
        "concurrency_limit_reached": "Another Seedance provider evaluation is still active. No request was sent.",
        "submission_paced": "Seedance evaluations are paced; wait before submitting again. No request was sent.",
        "daily_quota_exhausted": "Today's Seedance provider budget cannot cover this evaluation. No request was sent.",
        "provider_frozen": "The Seedance provider submissions are frozen in the quota ledger. No request was sent.",
    }.get(reason, "The Seedance provider evaluation was blocked before submission. No request was sent.")


def submit_evaluation(
    *,
    post_id: str,
    take_index: int,
    resolution: Optional[str],
    confirm_estimated_credits: int,
    operator_email: str,
    correlation_id: str,
    deps: Optional[EvaluationDependencies] = None,
) -> Dict[str, Any]:
    """Submit one explicitly confirmed, paid Seedance 2.5 evaluation. Never retries ambiguous work."""
    deps = deps or default_dependencies()
    settings = deps.settings
    context = deps.load_context(post_id)
    preview = evaluate_eligibility(
        context=context,
        take_index=take_index,
        resolution=resolution,
        settings=settings,
        operator_email=operator_email,
        moderation_failure=_previous_moderation(context, take_index, deps.repository),
    )
    if not preview["eligible"]:
        first = preview["reasons"][0]
        raise FlowForgeException(
            code=_http_error_code(first["http_status"]),
            message=first["message"],
            details={"reasons": preview["reasons"], "blocked_before_submit": True},
            status_code=first["http_status"],
        )
    resolved_resolution = preview["request"]["resolution"]
    estimate = estimate_credits(resolution=resolved_resolution, duration_seconds=EVALUATION_DURATION_SECONDS, settings=settings)
    if int(confirm_estimated_credits) != estimate.credits:
        raise FlowForgeException(
            code=ErrorCode.STATE_TRANSITION_ERROR,
            message="The confirmed credit estimate no longer matches the server estimate. No request was sent.",
            details={"estimate": estimate.as_dict(), "confirmed_credits": confirm_estimated_credits, "blocked_before_submit": True},
            status_code=409,
        )

    run = context.run or {}
    take = select_take(context.takes, take_index) or {}
    client = deps.runway_client_factory(settings)
    source = verify_semantic_take_source(
        run=run,
        take=take,
        storage=deps.storage_factory(),
        correlation_id=correlation_id,
        contracts=deps.contracts,
    )
    prompt_image, prompt_image_meta = build_prompt_image(
        source=source,
        storage_factory=deps.storage_factory,
        correlation_id=correlation_id,
        settings=settings,
    )
    request_contract = build_request_contract(
        take=take,
        resolution=resolved_resolution,
        estimate=estimate,
        source=source,
        prompt_image_meta=prompt_image_meta,
        settings=settings,
    )
    take_contract = _mapping(take.get("request_contract"))
    payload = {
        "post_id": post_id,
        "semantic_run_id": str(run.get("id")),
        "take_id": str(take.get("id")),
        "take_index": take_index,
        "take_attempt": _int(take.get("attempt"), 1),
        "provider": profile(settings).name,
        "quota_provider": profile(settings).quota,
        "provider_model": profile(settings).model,
        "requested_resolution": resolved_resolution,
        "requested_ratio": request_contract["ratio"],
        "requested_duration_seconds": EVALUATION_DURATION_SECONDS,
        "request_contract": request_contract,
        "request_hash": _canonical_hash(request_contract),
        "source_provenance": {**source.provenance, "creation_mode": preview["creation_mode"]},
        "estimated_credits": estimate.credits,
        "estimated_usd": str(estimate.usd),
        "reservation_key": f"{profile(settings).quota}:evaluation:{post_id}:{uuid4().hex}",
        "requested_by": str(operator_email).strip().lower(),
        "poller_environment": poller_environment(settings),
        "poller_scope": poller_scope(settings),
    }
    created = deps.repository.create(
        payload,
        daily_credit_limit=int(setting(settings, "evaluation_daily_credit_limit")),
        max_active=int(setting(settings, "evaluation_max_active")),
        min_submit_interval_seconds=int(setting(settings, "evaluation_min_submit_interval_seconds")),
    )
    if not created.get("allowed"):
        reason = str(created.get("reason") or "blocked")
        raise FlowForgeException(
            code=ErrorCode.RATE_LIMIT,
            message=_block_message(reason),
            details={
                "reason": reason,
                "blocked_before_submit": True,
                "retry_after_seconds": created.get("retry_after_seconds"),
                "quota": created.get("quota"),
            },
            status_code=429,
        )
    evaluation_id = str(created["evaluation"]["id"])
    try:
        deps.repository.mark_submitting(evaluation_id)
    except Exception as exc:
        # The Seedance provider was never called, so the reservation can be released safely.
        try:
            deps.repository.fail_before_submit(
                evaluation_id,
                code="submit_intent_not_recorded",
                message="The submit intent could not be recorded; The Seedance provider was not called.",
                details={"error_type": type(exc).__name__},
            )
        except Exception:  # noqa: BLE001 - stale reconciliation releases it after the threshold
            logger.exception("runway_evaluation_release_after_intent_failure_failed", evaluation_id=evaluation_id)
        raise

    try:
        accepted = client.submit_image_to_video(
            prompt_image=prompt_image,
            prompt_text=str(take_contract.get("prompt") or ""),
            ratio=request_contract["ratio"],
            duration_seconds=EVALUATION_DURATION_SECONDS,
            audio=True,
            seed=request_contract["seed"],
            correlation_id=correlation_id,
        )
    except RunwayError as exc:
        details = {"provider": profile(settings).name, "kind": exc.kind, "status_code": exc.status_code, **exc.details}
        if exc.proves_no_task:
            deps.repository.fail_before_submit(evaluation_id, code=f"runway_{exc.kind}", message=exc.message, details=details)
            status_code = 429 if exc.kind == "rate_limited" else 422 if exc.kind == "rejected" else 503
            raise FlowForgeException(
                code=ErrorCode.RATE_LIMIT if status_code == 429 else ErrorCode.THIRD_PARTY_FAIL,
                message=f"The Seedance provider did not create a task: {exc.message}",
                details={
                    **details,
                    "evaluation_id": evaluation_id,
                    "no_task_created": True,
                    "retry_after_seconds": exc.retry_after_seconds,
                },
                status_code=status_code,
            ) from exc
        deps.repository.mark_submission_unknown(
            evaluation_id,
            code="runway_submission_ambiguous",
            message=exc.message,
            details=details,
        )
        raise FlowForgeException(
            code=ErrorCode.THIRD_PARTY_FAIL,
            message=(
                "The Seedance provider did not confirm whether the task was created. The evaluation is blocked for "
                "reconciliation and will not be resubmitted."
            ),
            details={**details, "evaluation_id": evaluation_id, "submission_unknown": True},
            status_code=502,
        ) from exc
    except Exception as exc:
        deps.repository.mark_submission_unknown(
            evaluation_id,
            code="runway_submission_unexpected_error",
            message=str(exc)[:300],
            details={"error_type": type(exc).__name__},
        )
        raise

    task_id = str(accepted["task_id"])
    provider_estimate = accepted.get("estimated_credits")
    logger.warning(
        "runway_evaluation_paid_task_accepted",
        evaluation_id=evaluation_id,
        post_id=post_id,
        task_id=task_id,
        correlation_id=correlation_id,
        reserved_credits=estimate.credits,
        provider_estimated_credits=provider_estimate,
        message="PAID RUNWAY TASK ACCEPTED - task id logged for recovery",
    )
    cancel_reason = None
    cancel_confirmed = None
    if provider_estimate is not None and float(provider_estimate) > estimate.credits:
        cancel_reason = "provider_estimate_exceeds_reservation"
        cancel_confirmed = client.cancel_task(task_id, correlation_id=correlation_id)
        logger.error(
            "runway_evaluation_estimate_drift_cancelled",
            evaluation_id=evaluation_id,
            task_id=task_id,
            reserved_credits=estimate.credits,
            provider_estimated_credits=provider_estimate,
            cancel_confirmed=cancel_confirmed,
        )
    try:
        acknowledged = deps.repository.acknowledge(
            evaluation_id,
            task_id=task_id,
            provider_estimated_credits=provider_estimate,
            cancel_reason=cancel_reason,
        )
    except Exception as exc:
        record = {"evaluation_id": evaluation_id, "post_id": post_id, "task_id": task_id, "provider": profile(settings).name, "correlation_id": correlation_id}
        try:
            deps.recovery_writer(record)
        except Exception:  # noqa: BLE001 - the WARNING log above already carries the task id
            logger.exception("runway_evaluation_recovery_record_failed", evaluation_id=evaluation_id, task_id=task_id)
        logger.error("runway_evaluation_acknowledgement_failed", evaluation_id=evaluation_id, task_id=task_id, error_type=type(exc).__name__)
        raise FlowForgeException(
            code=ErrorCode.INTERNAL_ERROR,
            message="The Seedance provider accepted the task but the acknowledgement could not be saved. Do not resubmit; the task id was logged for recovery.",
            details={"evaluation_id": evaluation_id, "task_id": task_id},
            status_code=500,
        ) from exc
    if cancel_reason:
        raise FlowForgeException(
            code=ErrorCode.STATE_TRANSITION_ERROR,
            message="The Seedance provider estimated more credits than the reservation, so the accepted task was cancelled.",
            details={
                "evaluation_id": evaluation_id,
                "task_id": task_id,
                "reserved_credits": estimate.credits,
                "provider_estimated_credits": provider_estimate,
                "cancel_confirmed": cancel_confirmed,
            },
            status_code=409,
        )
    return serialize_evaluation(acknowledged)


def list_evaluations(*, post_id: str, deps: Optional[EvaluationDependencies] = None) -> List[Dict[str, Any]]:
    deps = deps or default_dependencies()
    return [serialize_evaluation(row) for row in deps.repository.list_for_post(post_id)]


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _poll_backoff_seconds(row: Mapping[str, Any], settings: Any) -> float:
    errors = _int(row.get("poll_error_count"), 0)
    return float(min(_MAX_POLL_BACKOFF_SECONDS, int(setting(settings, "poll_interval_seconds")) * (2 ** min(errors + 1, 6))))


def poll_runway_evaluations(*, worker_id: str, deps: Optional[EvaluationDependencies] = None, limit: int = 5) -> Dict[str, Any]:
    """One poller sweep: crash recovery, then poll leased tasks for this environment only."""
    deps = deps or default_dependencies()
    settings = deps.settings
    if not str(setting(settings, "api_key", "") or "").strip():
        return {"claimed": 0, "skipped": "credentials_missing"}
    try:
        reconciled = deps.repository.reconcile_stale(
            reserved_stale_seconds=_RESERVED_STALE_SECONDS,
            submitting_stale_seconds=_SUBMITTING_STALE_SECONDS,
        )
        if reconciled.get("released") or reconciled.get("submission_unknown"):
            logger.warning("runway_evaluation_stale_reconciled", **reconciled)
    except Exception as exc:  # noqa: BLE001 - polling accepted tasks matters more than recovery
        logger.warning("runway_evaluation_reconcile_failed", error_type=type(exc).__name__)
    rows = deps.repository.claim(
        worker_id=worker_id,
        lease_seconds=_LEASE_SECONDS,
        limit=limit,
        environment=poller_environment(settings),
        scope=poller_scope(settings),
    )
    if not rows:
        return {"claimed": 0}
    client = deps.runway_client_factory(settings)
    outcomes = []
    for row in rows:
        try:
            outcomes.append(process_claimed_evaluation(row, client=client, deps=deps))
        except Exception as exc:  # noqa: BLE001 - the lease expires and the next sweep retries
            logger.exception("runway_evaluation_poll_failed", evaluation_id=row.get("id"), error_type=type(exc).__name__)
            outcomes.append("error")
    return {"claimed": len(rows), "outcomes": outcomes}


def process_claimed_evaluation(row: Mapping[str, Any], *, client: Any, deps: EvaluationDependencies) -> str:
    settings = deps.settings
    if row.get("provider", profile(settings).name) != profile(settings).name:
        raise StateTransitionError("Persisted evaluation provider does not match the selected poll client.")
    repository = deps.repository
    evaluation_id = str(row["id"])
    lease_token = str(row["lease_token"])
    task_id = str(row.get("task_id") or "")
    correlation_id = f"runway_eval_{evaluation_id}"
    now = deps.now()

    def fail(code: str, message: str, *, details: Optional[Dict[str, Any]] = None, final_status: str = "failed",
             provider_status: Optional[str] = None, actual_credits: Optional[int] = None) -> str:
        repository.fail_after_submit(
            evaluation_id,
            lease_token=lease_token,
            final_status=final_status,
            code=code,
            message=message,
            details=details or {},
            provider_status=provider_status,
            actual_credits=actual_credits,
        )
        logger.warning("runway_evaluation_failed", evaluation_id=evaluation_id, task_id=task_id, error_code=code, final_status=final_status)
        return final_status

    def retry_later(delay_seconds: float, *, provider_status: Optional[str], poll_error: Dict[str, Any]) -> str:
        repository.record_poll(
            evaluation_id,
            lease_token=lease_token,
            provider_status=provider_status,
            progress=None,
            next_poll_at=now + timedelta(seconds=delay_seconds),
            poll_error=poll_error,
        )
        return "retry_scheduled"

    submitted_at = _parse_timestamp(row.get("submitted_at"))
    if submitted_at and (now - submitted_at).total_seconds() > int(setting(settings, "poll_max_age_seconds")):
        cancelled = client.cancel_task(task_id, correlation_id=correlation_id)
        return fail(
            "poll_timeout",
            "The Seedance task did not finish within the polling window; cancellation status is recorded separately.",
            details={"max_age_seconds": int(setting(settings, "poll_max_age_seconds")), "cancel_confirmed": cancelled},
            provider_status=row.get("last_provider_status"),
        )

    try:
        task: RunwayTask = client.get_task(task_id, correlation_id=correlation_id)
        if profile(settings).name == "modelark" and task.status == "SUCCEEDED" and task.usage_tokens is not None:
            pricing = _mapping(_mapping(row.get("request_contract")).get("pricing"))
            rate = pricing.get("usd_per_million_tokens")
            if rate is None:
                return fail("pricing_contract_missing", "The persisted ModelArk token rate is missing.")
            task = replace(task, cost_credits=cost_units(task.usage_tokens, rate))
    except RunwayError as exc:
        if exc.kind == "not_found":
            return fail("provider_task_missing", exc.message)
        if exc.kind == "invalid_response":
            return fail("provider_response_invalid", exc.message)
        delay = exc.retry_after_seconds if exc.kind == "rate_limited" and exc.retry_after_seconds else _poll_backoff_seconds(row, settings)
        return retry_later(delay, provider_status=None, poll_error={"kind": exc.kind, "status_code": exc.status_code})

    if task.status in TASK_STATUSES_ACTIVE:
        repository.record_poll(
            evaluation_id,
            lease_token=lease_token,
            provider_status=task.status,
            progress=task.progress,
            next_poll_at=now + timedelta(seconds=int(setting(settings, "poll_interval_seconds"))),
        )
        return "processing"
    if task.status in {"FAILED", "CANCELLED"}:
        return fail(
            f"provider_{task.status.lower()}",
            task.failure or f"The Seedance provider reported the task as {task.status}.",
            details={"failure_code": task.failure_code},
            final_status="failed" if task.status == "FAILED" else "cancelled",
            provider_status=task.status,
            actual_credits=task.cost_credits,
        )
    if task.status != "SUCCEEDED":
        return fail("unknown_provider_status", "The Seedance provider returned an unknown task status.", details={"provider_status": task.status[:40]})
    return _complete_evaluation(row, task=task, client=client, deps=deps, fail=fail, retry_later=retry_later)


def _complete_evaluation(row, *, task: RunwayTask, client: Any, deps: EvaluationDependencies, fail, retry_later) -> str:
    evaluation_id = str(row["id"])
    lease_token = str(row["lease_token"])
    correlation_id = f"runway_eval_{evaluation_id}"
    settings = deps.settings
    if not task.output_urls:
        return fail("provider_output_missing", "The Seedance provider reported success without an output.", provider_status=task.status, actual_credits=task.cost_credits)
    try:
        output = client.download_output(task.output_urls[0], correlation_id=correlation_id)
    except RunwayError as exc:
        if exc.kind in {"unsafe_output", "invalid_media"}:
            return fail(f"runway_{exc.kind}", exc.message, details=exc.details, provider_status=task.status, actual_credits=task.cost_credits)
        # Expired or transient: the next GET /v1/tasks/{id} returns fresh output URLs.
        return retry_later(
            _poll_backoff_seconds(row, settings),
            provider_status=task.status,
            poll_error={"kind": exc.kind, "status_code": exc.status_code, "stage": "download"},
        )
    try:
        probe = deps.probe_media(
            output.content,
            expected_duration_seconds=_int(row.get("requested_duration_seconds"), EVALUATION_DURATION_SECONDS),
            expected_ratio=str(row.get("requested_ratio") or ""),
            expect_audio=True,
        )
    except MediaValidationError as exc:
        return fail(exc.code, exc.message, details=exc.details, provider_status=task.status, actual_credits=task.cost_credits)

    output_sha256 = sha256(output.content).hexdigest()
    try:
        storage = deps.storage_factory()
        prefix = str(getattr(storage, "object_prefix", "") or "").strip("/")
        object_key = f"{prefix}/{profile(settings).storage_prefix}/{evaluation_id}/{output_sha256[:16]}.mp4".lstrip("/")
        uploaded = storage.upload_video(
            video_bytes=output.content,
            file_name=f"{profile(settings).name}-evaluation-{evaluation_id}.mp4",
            correlation_id=correlation_id,
            content_type="video/mp4",
            object_key=object_key,
        )
        verification = storage.verify_video_upload(
            storage_key=str(uploaded.get("storage_key") or object_key),
            expected_size=len(output.content),
            expected_sha256=output_sha256,
        )
    except Exception as exc:  # noqa: BLE001 - storage is retried; the provider task is untouched
        return retry_later(
            _poll_backoff_seconds(row, settings),
            provider_status=task.status,
            poll_error={"kind": "storage_upload_failed", "error_type": type(exc).__name__},
        )
    if not verification.get("passed"):
        return retry_later(
            _poll_backoff_seconds(row, settings),
            provider_status=task.status,
            poll_error={"kind": "storage_verification_failed", "reasons": verification.get("failure_reasons")},
        )
    deps.repository.complete(
        evaluation_id,
        lease_token=lease_token,
        output={
            "storage_key": str(uploaded.get("storage_key") or object_key),
            "url": str(uploaded.get("url") or ""),
            "sha256": output_sha256,
            "byte_length": len(output.content),
            "probe": {**probe, "source_host": output.host, "source_content_type": output.content_type,
                      "usage_completion_tokens": task.usage_tokens, "budget_unit": profile(settings).unit},
        },
        actual_credits=task.cost_credits,
    )
    logger.info(
        "runway_evaluation_completed",
        evaluation_id=evaluation_id,
        task_id=task.task_id,
        output_sha256=output_sha256,
        size_bytes=len(output.content),
        actual_credits=task.cost_credits,
    )
    return "completed"


__all__ = [
    "EvaluationContext",
    "EvaluationDependencies",
    "MediaValidationError",
    "RunwayEvaluationRepository",
    "build_prompt_image",
    "build_request_contract",
    "configuration_reasons",
    "estimate_credits",
    "evaluate_eligibility",
    "list_evaluations",
    "operator_authorized",
    "poll_runway_evaluations",
    "preview_evaluation",
    "probe_mp4",
    "process_claimed_evaluation",
    "select_take",
    "serialize_evaluation",
    "submit_evaluation",
    "verify_semantic_take_source",
]


def poll_seedance_evaluations(*, worker_id: str) -> Dict[str, Any]:
    """Poll each host independently, including accepted historical Runway tasks."""
    from app.core.config import get_settings
    from app.features.videos.quota_guard import get_seedance_budget_snapshot
    settings = get_settings()
    results = {}
    for name in ("modelark", "runway"):
        scoped = ProviderSettings(settings, name)
        if not setting(scoped, "api_key", ""):
            continue
        deps = default_dependencies()
        deps.settings = scoped
        deps.repository = RunwayEvaluationRepository(provider=name)
        deps.budget_snapshot = lambda limit, scoped=scoped: get_seedance_budget_snapshot(provider=profile(scoped).quota, daily_unit_limit=limit)
        try:
            results[name] = poll_runway_evaluations(worker_id=worker_id, deps=deps)
        except Exception as exc:
            logger.warning("seedance_host_poll_failed", provider=name, error_type=type(exc).__name__)
            results[name] = {"error": type(exc).__name__}
    return {"claimed": sum(result.get("claimed", 0) for result in results.values()), "providers": results}
