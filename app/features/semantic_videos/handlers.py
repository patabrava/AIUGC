"""Explicit free-plan and approval API for Semantic UGC videos."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping, Optional
from uuid import uuid4

from fastapi import APIRouter, Request

from app.adapters.storage_client import get_storage_client
from app.core.errors import NotFoundError, StateTransitionError, SuccessResponse, ValidationError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.video_profiles import script_word_count
from app.features.shot_frames.service import (
    ShotFrameReference,
)
from app.features.shot_frames.wheelchair_scene_plate import (
    generate_scene_plate_candidates,
)
from app.features.shot_production.duration import build_semantic_duration_contract
from app.features.shot_production.provenance import (
    build_semantic_script_snapshot,
)
from app.features.semantic_videos.queries import (
    approve_initial_plan_transition,
    approve_master_transition,
    approve_retry_transition,
    cancel_run_transition,
    finalize_candidate_generation,
    get_actor_scene_plate_anchor,
    get_run_by_post,
    list_approvals as list_approvals,
    list_attempts,
    load_semantic_video_context,
    persist_semantic_video_plan,
    reclaim_candidate_reservation,
    release_candidate_reservation,
    reserve_candidate_generation,
)
from app.features.semantic_videos.schemas import (
    ApprovalResponse,
    CancellationRequest,
    CancellationResponse,
    CandidateGenerationRequest,
    CandidateGenerationResponse,
    CandidateResponse,
    MasterApprovalRequest,
    MasterApprovalResponse,
    PlanApprovalRequest,
    PlanCreateRequest,
    PlanResponse,
    PlanTakeResponse,
    ProgressResponse,
    ProgressTakeResponse,
    RetryApprovalRequest,
)
from app.features.semantic_videos.service import compile_semantic_video_plan
from app.features.semantic_videos.visual_contract import (
    build_actor_reference_fingerprint,
    build_visual_contract,
    validate_visual_contract,
)


router = APIRouter(prefix="/semantic-videos/posts", tags=["semantic-videos"])
logger = get_logger(__name__)

_SCENE_PLATE_AUDIT_TEXT = (
    "Wheelchair scene plate generated from two immutable actor references and one "
    "actor-free location before any Veo request."
)
_FINAL_WORD_TARGET_PATTERN = re.compile(
    r"(?:final spoken word near|final word ends no later than)\s+"
    r"([0-9]+(?:\.[0-9]+)?)\s+seconds",
    re.IGNORECASE,
)
_MAX_RETRY_SPEECH_WORDS_PER_SECOND = 3.0
_MIN_RETRY_ESTIMATED_SPEECH_RATIO = 0.80
_CANDIDATE_RESERVATION_SECONDS = 1800


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _retry_contract_hash(
    *,
    plan_hash: str,
    revision: int,
    indexes: list[int],
    request_hashes: list[str],
    provider_seconds: int,
    quota_units: int,
    estimated_cost: str,
) -> str:
    basis = "\n".join(
        (
            "semantic-retry-contract-v1",
            plan_hash,
            str(revision),
            ",".join(str(index) for index in indexes),
            ",".join(request_hashes),
            str(provider_seconds),
            str(quota_units),
            estimated_cost,
        )
    )
    return sha256(basis.encode("utf-8")).hexdigest()


def _finite_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _prompt_final_word_target_seconds(prompt: str) -> Optional[float]:
    targets = [
        target
        for match in _FINAL_WORD_TARGET_PATTERN.finditer(str(prompt or ""))
        if (target := _finite_float(match.group(1))) is not None and target >= 0.0
    ]
    return min(targets) if targets else None


def _minimum_feasible_final_word_end_seconds(
    *,
    persisted_take: Mapping[str, Any],
    manifest_take: Mapping[str, Any],
) -> float:
    estimated_speech = _finite_float(persisted_take.get("estimated_speech_seconds"))
    word_count_value = persisted_take.get("word_count")
    word_count = (
        int(word_count_value)
        if not isinstance(word_count_value, bool)
        and isinstance(word_count_value, (int, float))
        and math.isfinite(float(word_count_value))
        and int(word_count_value) > 0
        else None
    )
    minimum_spoken_durations = []
    if estimated_speech is not None and estimated_speech > 0.0:
        minimum_spoken_durations.append(
            estimated_speech * _MIN_RETRY_ESTIMATED_SPEECH_RATIO
        )
    if word_count is not None:
        minimum_spoken_durations.append(
            word_count / _MAX_RETRY_SPEECH_WORDS_PER_SECOND
        )
    if not minimum_spoken_durations:
        raise StateTransitionError(
            "Semantic video retry cannot derive a minimum feasible speech deadline.",
            {
                "word_count": word_count_value,
                "estimated_speech_seconds": persisted_take.get(
                    "estimated_speech_seconds"
                ),
            },
        )

    transcript = manifest_take.get("transcript_qa")
    first_word_start = _finite_float(
        transcript.get("first_word_start_seconds")
        if isinstance(transcript, Mapping)
        else None
    )
    minimum_deadline = max(0.0, first_word_start or 0.0) + max(
        minimum_spoken_durations
    )
    return math.ceil((minimum_deadline - 1e-9) * 100.0) / 100.0


def _native_duration_retry_action(
    value: Mapping[str, Any],
    *,
    previous_prompt: str = "",
    persisted_take: Optional[Mapping[str, Any]] = None,
) -> str:
    qa_failure = value.get("qa_failure")
    details = qa_failure.get("details") if isinstance(qa_failure, Mapping) else None
    if not isinstance(details, Mapping):
        return ""
    recommended_action = " ".join(str(details.get("recommended_action") or "").split())
    if str(details.get("failure_type") or "") != "native_duration_shortfall":
        return recommended_action

    retry_indexes = details.get("recommended_retry_take_indexes")
    if not isinstance(retry_indexes, list) or not retry_indexes:
        return recommended_action
    try:
        retry_index = int(retry_indexes[-1])
    except (TypeError, ValueError):
        return recommended_action

    manifest = value.get("pipeline_manifest")
    takes = manifest.get("takes") if isinstance(manifest, Mapping) else None
    if not isinstance(takes, list):
        return recommended_action
    retry_take = next(
        (
            take
            for take in takes
            if isinstance(take, Mapping)
            and str(take.get("index")) == str(retry_index)
        ),
        None,
    )
    if not isinstance(retry_take, Mapping):
        return recommended_action

    beat = retry_take.get("beat")
    provider_duration = _finite_float(
        beat.get("provider_duration_seconds") if isinstance(beat, Mapping) else None
    ) or _finite_float(retry_take.get("duration_seconds"))
    if provider_duration is None or provider_duration <= 0:
        return recommended_action

    transcript = retry_take.get("transcript_qa")
    current_final_word = _finite_float(
        transcript.get("final_word_end_seconds")
        if isinstance(transcript, Mapping)
        else None
    )
    latest_final_word = _finite_float(details.get("latest_safe_final_word_end_seconds"))
    if latest_final_word is None:
        required_seconds = _finite_float(details.get("required_seconds"))
        available_by_take = details.get("available_seconds_by_take")
        safe_by_take = details.get("cadence_safe_available_seconds_by_take")
        raw_available = (
            _finite_float(available_by_take.get(str(retry_index)))
            if isinstance(available_by_take, Mapping)
            else None
        )
        if (
            current_final_word is None
            or required_seconds is None
            or raw_available is None
            or not isinstance(safe_by_take, Mapping)
        ):
            return recommended_action
        other_safe = sum(
            safe
            for index, raw_safe in safe_by_take.items()
            if str(index) != str(retry_index)
            and (safe := _finite_float(raw_safe)) is not None
        )
        post_word_guard = max(
            0.0,
            provider_duration - current_final_word - raw_available,
        )
        required_final_tail = max(0.0, required_seconds - other_safe)
        latest_final_word = max(
            0.0,
            provider_duration - required_final_tail - post_word_guard,
        )

    previous_target = _prompt_final_word_target_seconds(previous_prompt)
    if previous_target is not None:
        measured_overshoot = (
            max(0.0, current_final_word - previous_target)
            if current_final_word is not None
            else 0.0
        )
        latest_final_word = min(
            previous_target,
            latest_final_word - measured_overshoot,
        )

    latest_final_word = min(provider_duration, max(0.0, latest_final_word))
    if persisted_take is not None:
        minimum_deadline = _minimum_feasible_final_word_end_seconds(
            persisted_take=persisted_take,
            manifest_take=retry_take,
        )
        if latest_final_word + 1e-9 < minimum_deadline:
            raise StateTransitionError(
                "Semantic video retry timing is below the minimum feasible speech deadline.",
                {
                    "calculated_final_word_end_seconds": latest_final_word,
                    "minimum_feasible_final_word_end_seconds": minimum_deadline,
                    "word_count": persisted_take.get("word_count"),
                    "estimated_speech_seconds": persisted_take.get(
                        "estimated_speech_seconds"
                    ),
                },
            )
    conservative_deadline = math.floor((latest_final_word + 1e-9) * 100.0) / 100.0
    return (
        "For this retry, this timing overrides any earlier final-word timing target. "
        "Regenerate only the final take. Pace the exact spoken beat so its final word "
        f"ends no later than {conservative_deadline:.2f} seconds, then continue natural "
        f"silent motion and room tone through {provider_duration:.2f} seconds. Do not "
        "add speech or freeze."
    )


def _retry_guidance_text(
    value: Any,
    *,
    previous_prompt: str = "",
    persisted_take: Optional[Mapping[str, Any]] = None,
) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        rpc_guidance = next(
            (
                " ".join(str(value[key]).split())
                for key in ("guidance", "prompt_suffix", "instruction", "message")
                if str(value.get(key) or "").strip()
            ),
            "",
        )
        actionable = _native_duration_retry_action(
            value,
            previous_prompt=previous_prompt,
            persisted_take=persisted_take,
        )
        if rpc_guidance and actionable and rpc_guidance in actionable:
            text = actionable
        elif rpc_guidance and actionable and actionable not in rpc_guidance:
            text = f"{rpc_guidance} Actionable correction: {actionable}"
        else:
            text = rpc_guidance or actionable
    else:
        text = ""
    text = " ".join(text.split())
    if not text:
        raise StateTransitionError(
            "Semantic video retry requires persisted QA retry guidance."
        )
    if isinstance(value, dict) and (value.get("qa_failure") or {}).get("stage") == "transcript_qa":
        manifest = value.get("pipeline_manifest")
        takes = manifest.get("takes") if isinstance(manifest, dict) else []
        for take in takes or []:
            transcript = take.get("transcript_qa") if isinstance(take, dict) else None
            if not isinstance(transcript, dict):
                continue
            reasons = transcript.get("failure_reasons") or []
            expected_words = transcript.get("expected_words") or []
            if "missing_first_word" in reasons and expected_words:
                first_word = str(expected_words[0]).strip()
                if first_word:
                    text = (
                        f"{text} Start with the complete first word '{first_word}' clearly. "
                        "Do not omit or clip its opening syllable."
                    )
                break
    return text


def _trusted_veo_price() -> Decimal:
    value = get_settings().semantic_ugc_veo_price_per_provider_second_usd
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Semantic video configured Veo price must be positive.") from exc
    if not price.is_finite() or price <= 0:
        raise ValidationError("Semantic video configured Veo price must be positive.")
    return price


def _approved_script(post: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    seed = post.get("seed_data") if isinstance(post.get("seed_data"), dict) else {}
    review_status = str(post.get("script_review_status") or seed.get("script_review_status") or "").strip().lower()
    if review_status != "approved":
        raise ValidationError(
            "Semantic video reference generation requires an approved script.",
            {"script_review_status": review_status or None},
        )
    script = " ".join(
        str(
            post.get("script")
            or post.get("approved_script")
            or seed.get("script")
            or seed.get("dialog_script")
            or post.get("topic_rotation")
            or ""
        ).split()
    )
    if not script:
        raise ValidationError("Semantic video reference generation requires non-empty script text.")
    snapshot = {
        "text": script,
        "review_status": review_status,
        "word_count": script_word_count(script),
    }
    return script, snapshot


def _ordered_reference_rows(reference: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actor_rows = reference.get("actor_references")
    location = reference.get("location_reference")
    if not isinstance(actor_rows, list) or len(actor_rows) != 2:
        raise ValidationError(
            "Semantic video candidate generation requires exactly two ordered actor references.",
            {"actor_reference_count": len(actor_rows) if isinstance(actor_rows, list) else 0},
        )
    expected_roles = ["actor_front", "actor_three_quarter"]
    if [str(row.get("role") or "") for row in actor_rows if isinstance(row, dict)] != expected_roles:
        raise ValidationError(
            "Semantic video actor reference roles must be actor_front then actor_three_quarter."
        )
    if not isinstance(location, dict) or str(location.get("role") or "") != "location":
        raise ValidationError("Semantic video candidate generation requires one actor-free location reference.")
    for row in [*actor_rows, location]:
        if not str(row.get("storage_uri") or "").strip():
            raise ValidationError("Semantic video references require durable storage URIs.")
    return [dict(row) for row in actor_rows], dict(location)


def _assert_scene_plate_master(
    *,
    reference_snapshot: Mapping[str, Any],
    master_snapshot: Mapping[str, Any],
) -> None:
    actor_rows, location = _ordered_reference_rows(dict(reference_snapshot))
    required_reference_fields = ("sha256", "byte_length", "mime_type")
    missing_reference_fields = {
        str(row.get("role") or "reference"): [
            field for field in required_reference_fields if row.get(field) in (None, "")
        ]
        for row in [*actor_rows, location]
    }
    missing_reference_fields = {
        role: fields for role, fields in missing_reference_fields.items() if fields
    }
    if missing_reference_fields:
        raise ValidationError(
            "Semantic scene plate requires immutable verified source references.",
            {"missing_reference_fields": missing_reference_fields},
        )
    actor_reference_fingerprint = build_actor_reference_fingerprint(actor_rows)
    if (
        str(reference_snapshot.get("actor_reference_fingerprint") or "").lower()
        != actor_reference_fingerprint
    ):
        raise ValidationError(
            "Semantic scene plate is not bound to the immutable actor references."
        )
    visual_contract = validate_visual_contract(reference_snapshot.get("visual_contract"))
    master_hash = str(master_snapshot.get("sha256") or "").strip().lower()
    if (
        not str(master_snapshot.get("storage_uri") or "").strip()
        or str(master_snapshot.get("mime_type") or "").lower() != "image/png"
        or int(master_snapshot.get("byte_length") or 0) <= 0
        or len(master_hash) != 64
        or not str(master_snapshot.get("provider_model") or "").strip()
    ):
        raise ValidationError("Semantic scene-plate master metadata is incomplete.")
    source_hashes = {
        str(row.get("sha256") or "").strip().lower()
        for row in [*actor_rows, location]
    }
    if master_hash in source_hashes:
        raise ValidationError("Semantic scene plate cannot be an unchanged source reference.")
    if str(master_snapshot.get("visual_contract_hash") or "").lower() != visual_contract[
        "contract_hash"
    ]:
        raise ValidationError("Semantic scene plate is not bound to the frozen visual contract.")
    if (
        str(master_snapshot.get("actor_reference_fingerprint") or "").lower()
        != actor_reference_fingerprint
    ):
        raise ValidationError("Semantic scene plate actor-reference lineage is invalid.")
    derivation_mode = str(master_snapshot.get("derivation_mode") or "").strip()
    if derivation_mode not in {"bootstrap", "canonical_anchor"}:
        raise ValidationError("Semantic scene plate derivation lineage is invalid.")
    canonical_anchor = reference_snapshot.get("canonical_anchor")
    if derivation_mode == "bootstrap":
        if master_snapshot.get("canonical_anchor_id") not in (None, "") or master_snapshot.get(
            "canonical_anchor_sha256"
        ) not in (None, ""):
            raise ValidationError("Bootstrap scene plate cannot claim a pre-existing anchor.")
    else:
        if not isinstance(canonical_anchor, Mapping):
            raise ValidationError("Derived semantic scene plate requires its canonical anchor snapshot.")
        anchor_id = str(canonical_anchor.get("id") or "").strip()
        anchor_hash = str(canonical_anchor.get("master_sha256") or "").strip().lower()
        if (
            not anchor_id
            or len(anchor_hash) != 64
            or str(master_snapshot.get("canonical_anchor_id") or "").strip() != anchor_id
            or str(master_snapshot.get("canonical_anchor_sha256") or "").strip().lower()
            != anchor_hash
            or str(canonical_anchor.get("actor_reference_fingerprint") or "").lower()
            != actor_reference_fingerprint
        ):
            raise ValidationError("Derived semantic scene plate canonical-anchor lineage is invalid.")
        if master_hash == anchor_hash:
            raise ValidationError("Derived semantic scene plate cannot be the unchanged canonical anchor.")
    candidates = master_snapshot.get("candidates")
    if isinstance(candidates, list) and candidates:
        selected_index = int(master_snapshot.get("approved_candidate_index") or master_snapshot.get("index") or 0)
        selected = next(
            (
                row
                for row in candidates
                if isinstance(row, Mapping) and int(row.get("index") or 0) == selected_index
            ),
            None,
        )
        if not isinstance(selected, Mapping) or str(selected.get("sha256") or "").lower() != master_hash:
            raise ValidationError("Semantic scene plate is not one of the persisted candidates.")


def _download_reference(row: dict[str, Any], *, role: str, request: Request) -> tuple[ShotFrameReference, dict[str, Any]]:
    storage_uri = str(row["storage_uri"])
    image_bytes = get_storage_client().download_video(
        video_url=storage_uri,
        correlation_id=str(getattr(request.state, "correlation_id", "semantic-reference")),
    )
    actual_hash = sha256(image_bytes).hexdigest()
    expected_hash = str(row.get("sha256") or "").strip().lower()
    if expected_hash and expected_hash != actual_hash:
        raise StateTransitionError(
            "Semantic video reference bytes changed after snapshot.",
            {"role": role, "expected_sha256": expected_hash, "actual_sha256": actual_hash},
        )
    mime_type = str(row.get("mime_type") or "image/png")
    snapshot = {
        **row,
        "role": role,
        "storage_uri": storage_uri,
        "mime_type": mime_type,
        "byte_length": len(image_bytes),
        "sha256": actual_hash,
    }
    return ShotFrameReference(role=role, mime_type=mime_type, image_bytes=image_bytes), snapshot


def _download_actor_scene_plate_anchor(
    anchor: Mapping[str, Any],
    *,
    actor_identity_id: str,
    actor_reference_fingerprint: str,
    request: Request,
) -> tuple[ShotFrameReference, dict[str, Any]]:
    anchor_id = str(anchor.get("id") or "").strip()
    anchor_actor_id = str(anchor.get("actor_identity_id") or "").strip()
    anchor_fingerprint = str(anchor.get("actor_reference_fingerprint") or "").strip().lower()
    expected_hash = str(anchor.get("master_sha256") or "").strip().lower()
    expected_length = int(anchor.get("master_byte_length") or 0)
    mime_type = str(anchor.get("master_mime_type") or "").strip().lower()
    storage_uri = str(anchor.get("master_storage_uri") or "").strip()
    provider_model = str(anchor.get("provider_model") or "").strip()
    if (
        not anchor_id
        or anchor_actor_id != actor_identity_id
        or anchor_fingerprint != actor_reference_fingerprint
        or not storage_uri
        or len(expected_hash) != 64
        or expected_length <= 0
        or mime_type != "image/png"
        or not provider_model
    ):
        raise StateTransitionError("Semantic actor scene-plate anchor metadata is invalid.")
    image_bytes = get_storage_client().download_video(
        video_url=storage_uri,
        correlation_id=str(getattr(request.state, "correlation_id", "semantic-anchor")),
    )
    actual_hash = sha256(image_bytes).hexdigest()
    if actual_hash != expected_hash or len(image_bytes) != expected_length:
        raise StateTransitionError(
            "Semantic actor scene-plate anchor bytes changed after approval.",
            {
                "anchor_id": anchor_id,
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
                "expected_byte_length": expected_length,
                "actual_byte_length": len(image_bytes),
            },
        )
    snapshot = {
        "id": anchor_id,
        "actor_identity_id": anchor_actor_id,
        "actor_reference_fingerprint": anchor_fingerprint,
        "source_run_id": str(anchor.get("source_run_id") or "").strip() or None,
        "master_storage_uri": storage_uri,
        "master_sha256": actual_hash,
        "master_byte_length": len(image_bytes),
        "master_mime_type": mime_type,
        "provider_model": provider_model,
        "visual_contract_hash": str(anchor.get("visual_contract_hash") or "").strip().lower(),
    }
    return (
        ShotFrameReference(
            role="canonical_scene_plate",
            mime_type=mime_type,
            image_bytes=image_bytes,
        ),
        snapshot,
    )


def _reference_run_payload(
    *,
    context: dict[str, Any],
    reference_snapshot: dict[str, Any],
    master_snapshot: dict[str, Any],
) -> dict[str, Any]:
    post = context["post"]
    batch = context["batch"]
    contract = build_semantic_duration_contract(batch.get("target_duration_seconds"))
    script_snapshot = _approved_semantic_script_snapshot(context)
    return {
        "post_id": str(post["id"]),
        "batch_id": str(batch["id"]),
        "requested_duration_seconds": contract.requested_duration_seconds,
        "duration_contract": contract.as_dict(),
        "duration_contract_hash": contract.contract_hash,
        "script_snapshot": script_snapshot,
        "script_hash": _canonical_hash(script_snapshot),
        "actor_identity_id": reference_snapshot.get("actor_identity_id"),
        "actor_snapshot": deepcopy(reference_snapshot.get("actor") or {}),
        "reference_snapshot": reference_snapshot,
        "reference_hash": _canonical_hash(reference_snapshot),
        "master_snapshot": master_snapshot,
        "master_hash": None,
        "stage": "awaiting_reference_approval",
        "plan_snapshot": None,
        "plan_hash": None,
        "provider_model": None,
        "resolution": None,
        "estimated_cost_usd": None,
        "artifact_prefix": f"semantic-videos/{batch['id']}/{post['id']}",
        "failure_envelope": None,
    }


def _run_or_404(post_id: str) -> dict[str, Any]:
    run = get_run_by_post(post_id)
    if not run:
        raise NotFoundError("Semantic video run not found.", {"post_id": post_id})
    return run


def _reference_snapshot(context: dict[str, Any], run: dict[str, Any] | None = None) -> dict[str, Any]:
    reference = deepcopy(context.get("reference") or {})
    if run:
        persisted_reference = run.get("reference_snapshot")
        if isinstance(persisted_reference, dict) and persisted_reference:
            reference = deepcopy(persisted_reference)
        persisted_master = run.get("master_snapshot")
        if isinstance(persisted_master, dict) and persisted_master:
            reference["master"] = deepcopy(persisted_master)
    return reference


def _reference_source_identity(reference: dict[str, Any]) -> dict[str, Any]:
    actor_rows, location_row = _ordered_reference_rows(reference)
    return {
        "actor_identity_id": str(reference.get("actor_identity_id") or ""),
        "scene_key": str(reference.get("scene_key") or location_row.get("scene_key") or ""),
        "scene_description": str(reference.get("scene_description") or ""),
        "wardrobe_key": str(reference.get("wardrobe_key") or ""),
        "wardrobe_description": str(reference.get("wardrobe_description") or ""),
        "actor_reference_uris": [str(row["storage_uri"]) for row in actor_rows],
        "location_reference_uri": str(location_row["storage_uri"]),
    }


def _approved_semantic_script_snapshot(context: Mapping[str, Any]) -> dict[str, Any]:
    post = context.get("post") if isinstance(context.get("post"), Mapping) else {}
    batch = context.get("batch") if isinstance(context.get("batch"), Mapping) else {}
    _script, snapshot = _approved_script(dict(post))
    contract = build_semantic_duration_contract(batch.get("target_duration_seconds"))
    return build_semantic_script_snapshot(
        text=str(snapshot["text"]),
        review_status=str(snapshot["review_status"]),
        word_count=int(snapshot["word_count"]),
        creation_mode=str(batch.get("creation_mode") or "semantic_ugc"),
        target_duration_seconds=contract.requested_duration_seconds,
    )


def _assert_reference_sources_current(
    *,
    context: Mapping[str, Any],
    run: Mapping[str, Any],
) -> None:
    persisted_reference = run.get("reference_snapshot")
    if not isinstance(persisted_reference, dict) or not persisted_reference:
        raise StateTransitionError(
            "Semantic video persisted reference snapshot is no longer available."
        )
    current_reference = _reference_snapshot(dict(context))
    try:
        current_identity = _reference_source_identity(current_reference)
        persisted_identity = _reference_source_identity(persisted_reference)
    except ValidationError as exc:
        raise StateTransitionError(
            "Semantic video reference sources changed after candidate generation."
        ) from exc
    if current_identity != persisted_identity:
        raise StateTransitionError(
            "Semantic video reference sources changed after candidate generation.",
            {
                "persisted_source_identity": persisted_identity,
                "current_source_identity": current_identity,
            },
        )


def _assert_visual_restart_has_no_paid_evidence(run_id: str) -> None:
    attempts = list_attempts(run_id)
    unpaid_states = {"", "planned", "reserved", "cancelled"}
    paid_evidence_fields = (
        "operation_id",
        "provider_video_uri",
        "raw_artifact_uri",
        "raw_artifact_sha256",
    )
    unsafe = [
        {
            "take_index": attempt.get("take_index"),
            "attempt": attempt.get("attempt"),
            "submission_state": attempt.get("submission_state"),
        }
        for attempt in attempts
        if str(attempt.get("submission_state") or "") not in unpaid_states
        or any(attempt.get(field) not in (None, "") for field in paid_evidence_fields)
    ]
    if unsafe:
        raise StateTransitionError(
            "Semantic video visual restart is blocked because paid take evidence exists.",
            {"paid_take_evidence": unsafe},
        )


def _assert_candidate_lineage_current(run: Mapping[str, Any]) -> None:
    reference = run.get("reference_snapshot")
    if not isinstance(reference, Mapping) or not reference:
        raise ValidationError("Semantic scene-plate reference lineage is unavailable.")
    actor_rows, _location = _ordered_reference_rows(dict(reference))
    fingerprint = build_actor_reference_fingerprint(actor_rows)
    if str(reference.get("actor_reference_fingerprint") or "").lower() != fingerprint:
        raise ValidationError("Semantic scene-plate actor-reference lineage is stale.")
    visual_contract = validate_visual_contract(reference.get("visual_contract"))
    master = run.get("master_snapshot")
    if not isinstance(master, Mapping) or not master:
        return
    candidates = master.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return
    derivation_mode = str(master.get("derivation_mode") or "")
    if (
        derivation_mode not in {"bootstrap", "canonical_anchor"}
        or str(master.get("actor_reference_fingerprint") or "").lower()
        != fingerprint
        or str(master.get("visual_contract_hash") or "").lower()
        != visual_contract["contract_hash"]
    ):
        raise ValidationError("Semantic scene-plate candidate lineage is stale.")
    for candidate in candidates:
        if (
            not isinstance(candidate, Mapping)
            or str(candidate.get("actor_reference_fingerprint") or "").lower()
            != fingerprint
            or str(candidate.get("visual_contract_hash") or "").lower()
            != visual_contract["contract_hash"]
            or str(candidate.get("derivation_mode") or "") != derivation_mode
        ):
            raise ValidationError("Semantic scene-plate candidate lineage is stale.")


def _plan_response(run: dict[str, Any], takes: list[dict[str, Any]]) -> PlanResponse:
    snapshot = run.get("plan_snapshot") if isinstance(run.get("plan_snapshot"), dict) else {}
    return PlanResponse(
        run_id=str(run["id"]),
        revision=int(run.get("revision") or 0),
        stage=str(run.get("stage") or ""),
        plan_hash=str(run.get("plan_hash") or ""),
        requested_duration_seconds=int(run["requested_duration_seconds"]),
        take_count=int(snapshot.get("take_count") or len(takes)),
        billable_provider_seconds=int(snapshot.get("billable_provider_seconds") or 0),
        quota_units=int(snapshot.get("quota_units") or 0),
        price_per_provider_second_usd=str(snapshot.get("price_per_provider_second_usd") or "0.00"),
        estimated_cost_usd=str(snapshot.get("estimated_cost_usd") or run.get("estimated_cost_usd") or "0.00"),
        takes=[
            PlanTakeResponse(
                take_index=int(take["take_index"]),
                attempt=int(take.get("attempt") or 1),
                beat_text=str(take.get("beat_text") or ""),
                provider_duration_seconds=int(take.get("provider_duration_seconds") or 0),
                request_hash=str(take.get("request_hash") or ""),
                submission_state=str(take.get("submission_state") or "planned"),
            )
            for take in takes
        ],
    )


@router.post("/{post_id}/candidates", response_model=SuccessResponse)
def generate_candidates(
    post_id: str,
    payload: CandidateGenerationRequest,
    request: Request,
):
    context = load_semantic_video_context(post_id)
    existing = get_run_by_post(post_id)
    effective_expected_revision = payload.expected_revision
    if existing:
        revision = int(existing.get("revision") or 0)
        stage = str(existing.get("stage") or "")
        if stage in {"completed", "failed"}:
            existing = None
            effective_expected_revision = None
        elif stage in {"awaiting_reference_approval", "awaiting_paid_approval"}:
            stale_reason: str | None = None
            has_persisted_reference = isinstance(
                existing.get("reference_snapshot"), dict
            ) and bool(existing.get("reference_snapshot"))
            if has_persisted_reference:
                try:
                    _assert_reference_sources_current(context=context, run=existing)
                except StateTransitionError:
                    stale_reason = "Location or wardrobe changed after scene-plate generation."
            elif stage == "awaiting_paid_approval":
                stale_reason = (
                    "The unpaid scene plate predates the immutable reference snapshot."
                )
            if stale_reason is None and stage == "awaiting_paid_approval":
                persisted_reference = existing.get("reference_snapshot")
                persisted_master = existing.get("master_snapshot")
                try:
                    _assert_scene_plate_master(
                        reference_snapshot=(
                            persisted_reference
                            if isinstance(persisted_reference, Mapping)
                            else {}
                        ),
                        master_snapshot=(
                            persisted_master
                            if isinstance(persisted_master, Mapping)
                            else {}
                        ),
                    )
                except ValidationError:
                    stale_reason = (
                        "The unpaid scene plate predates the immutable actor-anchor lineage."
                    )
            if stale_reason is None and stage == "awaiting_reference_approval":
                try:
                    _assert_candidate_lineage_current(existing)
                except ValidationError:
                    stale_reason = (
                        "The pending scene plates predate the immutable actor-anchor lineage."
                    )
            if stale_reason is not None:
                _assert_visual_restart_has_no_paid_evidence(str(existing["id"]))
                cancel_run_transition(
                    str(existing["id"]),
                    expected_revision=revision,
                    cancelled_by=str(
                        getattr(request.state, "user_email", None)
                        or "semantic-visual-override"
                    ),
                    reason=(
                        f"{stale_reason} The stale unpaid visual run was invalidated."
                    ),
                    correlation_id=str(
                        getattr(request.state, "correlation_id", None) or ""
                    ),
                )
                existing = None
                effective_expected_revision = None
    if existing:
        revision = int(existing.get("revision") or 0)
        if str(existing.get("stage") or "") != "awaiting_reference_approval":
            raise StateTransitionError(
                "Semantic video candidates cannot replace a run in its current stage.",
                {"stage": existing.get("stage")},
            )
        if payload.expected_revision is None or payload.expected_revision != revision:
            raise StateTransitionError(
                "Semantic video candidate generation revision is stale.",
                {"expected_revision": payload.expected_revision, "actual_revision": revision},
            )
        reservation_token = str(existing.get("candidate_reservation_token") or "").strip()
        reservation_expires_at = str(existing.get("candidate_reservation_expires_at") or "").strip()
        if reservation_token and reservation_expires_at:
            try:
                expires_at = datetime.fromisoformat(reservation_expires_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise StateTransitionError(
                    "Semantic video candidate reservation expiry is invalid."
                ) from exc
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                existing = reclaim_candidate_reservation(
                    run_id=str(existing["id"]),
                    expected_revision=revision,
                )
                effective_expected_revision = int(existing["revision"])

    _script, _script_snapshot = _approved_script(context["post"])
    reference = deepcopy(context.get("reference") or {})
    actor_rows, location_row = _ordered_reference_rows(reference)

    actor_front, actor_front_snapshot = _download_reference(
        actor_rows[0], role="actor_front", request=request
    )
    actor_three_quarter, actor_three_quarter_snapshot = _download_reference(
        actor_rows[1],
        role="actor_three_quarter",
        request=request,
    )
    location, location_snapshot = _download_reference(
        location_row, role="location", request=request
    )
    actor_reference_fingerprint = build_actor_reference_fingerprint(
        [actor_front_snapshot, actor_three_quarter_snapshot]
    )
    actor_identity_id = str(reference.get("actor_identity_id") or "").strip()
    if not actor_identity_id:
        raise ValidationError("Semantic scene-plate generation requires an actor identity.")
    anchor = get_actor_scene_plate_anchor(
        actor_identity_id=actor_identity_id,
        actor_reference_fingerprint=actor_reference_fingerprint,
    )
    canonical_scene_plate = None
    canonical_anchor_snapshot = None
    if anchor is not None:
        canonical_scene_plate, canonical_anchor_snapshot = _download_actor_scene_plate_anchor(
            anchor,
            actor_identity_id=actor_identity_id,
            actor_reference_fingerprint=actor_reference_fingerprint,
            request=request,
        )
    persisted_reference = {
        **reference,
        "actor_references": [actor_front_snapshot, actor_three_quarter_snapshot],
        "location_reference": location_snapshot,
        "actor_reference_fingerprint": actor_reference_fingerprint,
    }
    if canonical_anchor_snapshot is not None:
        persisted_reference["canonical_anchor"] = canonical_anchor_snapshot
    else:
        persisted_reference.pop("canonical_anchor", None)
    visual_contract = build_visual_contract(persisted_reference)
    persisted_reference["visual_contract"] = visual_contract
    persisted_reference.pop("master", None)
    reservation_token = str(uuid4())
    reservation_owner = str(
        getattr(request.state, "user_email", None)
        or getattr(request.state, "correlation_id", None)
        or "semantic-candidates"
    )
    reserved = reserve_candidate_generation(
        post_id,
        expected_revision=effective_expected_revision,
        run_create=_reference_run_payload(
            context=context,
            reference_snapshot=persisted_reference,
            master_snapshot={},
        ),
        reservation_owner=reservation_owner,
        reservation_token=reservation_token,
        reservation_seconds=_CANDIDATE_RESERVATION_SECONDS,
    )
    try:
        generated = generate_scene_plate_candidates(
            actor_references=[actor_front, actor_three_quarter],
            location_reference=location,
            canonical_scene_plate=canonical_scene_plate,
            scene=visual_contract["scene_description"],
            wardrobe=visual_contract["wardrobe_description"],
            candidate_count=payload.candidate_count,
        )
        if len(generated.candidates) != payload.candidate_count:
            raise StateTransitionError(
                "Semantic scene-plate generation returned an unexpected candidate count.",
                {"expected": payload.candidate_count, "actual": len(generated.candidates)},
            )
        correlation_id = str(
            getattr(request.state, "correlation_id", "semantic-scene-plates")
        )
        expected_derivation_mode = (
            "canonical_anchor" if canonical_anchor_snapshot is not None else "bootstrap"
        )
        derivation_mode = str(
            getattr(generated, "derivation_mode", expected_derivation_mode)
        ).strip()
        if derivation_mode != expected_derivation_mode:
            raise StateTransitionError(
                "Semantic scene-plate generator returned invalid anchor lineage."
            )
        candidates = []
        for candidate in generated.candidates:
            candidate_hash = sha256(candidate.image_bytes).hexdigest()
            uploaded = get_storage_client().upload_image(
                image_bytes=candidate.image_bytes,
                file_name=(
                    f"semantic-{post_id}-scene-plate-{candidate.index}-"
                    f"{candidate_hash[:12]}.png"
                ),
                correlation_id=correlation_id,
                content_type=candidate.mime_type,
            )
            candidates.append(
                {
                    "index": int(candidate.index),
                    "storage_uri": str(uploaded["url"]),
                    "storage_key": uploaded.get("storage_key"),
                    "mime_type": str(candidate.mime_type),
                    "byte_length": len(candidate.image_bytes),
                    "sha256": candidate_hash,
                    "provider_model": str(candidate.provider_model),
                    "visual_contract_hash": visual_contract["contract_hash"],
                    "actor_reference_fingerprint": actor_reference_fingerprint,
                    "derivation_mode": derivation_mode,
                    "canonical_anchor_id": (
                        canonical_anchor_snapshot["id"]
                        if canonical_anchor_snapshot is not None
                        else None
                    ),
                    "canonical_anchor_sha256": (
                        canonical_anchor_snapshot["master_sha256"]
                        if canonical_anchor_snapshot is not None
                        else None
                    ),
                    "canonical_anchor_source_run_id": (
                        canonical_anchor_snapshot.get("source_run_id")
                        if canonical_anchor_snapshot is not None
                        else None
                    ),
                }
            )
        master_snapshot = {
            "candidates": candidates,
            "visual_contract": visual_contract,
            "visual_contract_hash": visual_contract["contract_hash"],
            "actor_reference_fingerprint": actor_reference_fingerprint,
            "derivation_mode": derivation_mode,
            "canonical_anchor_id": (
                canonical_anchor_snapshot["id"]
                if canonical_anchor_snapshot is not None
                else None
            ),
            "canonical_anchor_sha256": (
                canonical_anchor_snapshot["master_sha256"]
                if canonical_anchor_snapshot is not None
                else None
            ),
            "canonical_anchor_source_run_id": (
                canonical_anchor_snapshot.get("source_run_id")
                if canonical_anchor_snapshot is not None
                else None
            ),
            "prompt_writer_system_prompt": _SCENE_PLATE_AUDIT_TEXT,
            "prompt_writer_system_prompt_sha256": sha256(
                _SCENE_PLATE_AUDIT_TEXT.encode("utf-8")
            ).hexdigest(),
            "prompt_writer_output": generated.prompts[0],
            "composition_prompt": generated.prompts[0],
            "scene_plate_prompts": list(generated.prompts),
        }
        run_payload = _reference_run_payload(
            context=context,
            reference_snapshot=persisted_reference,
            master_snapshot=master_snapshot,
        )
        run = finalize_candidate_generation(
            str(reserved["id"]),
            reserved_revision=int(reserved.get("revision") or 0),
            reservation_token=reservation_token,
            run_updates=run_payload,
        )
    except Exception:
        try:
            release_candidate_reservation(
                run_id=str(reserved["id"]),
                expected_revision=int(reserved.get("revision") or 0),
                reservation_token=reservation_token,
            )
        except Exception as release_exc:  # noqa: BLE001
            logger.exception(
                "semantic_video_candidate_reservation_release_failed",
                run_id=str(reserved.get("id") or ""),
                error=str(release_exc),
            )
        raise
    persisted_master = (
        run.get("master_snapshot") if isinstance(run.get("master_snapshot"), dict) else {}
    )
    persisted_candidates = persisted_master.get("candidates")
    if (
        not isinstance(persisted_candidates, list)
        or len(persisted_candidates) != payload.candidate_count
        or any(not isinstance(candidate, dict) for candidate in persisted_candidates)
    ):
        raise StateTransitionError(
            "Semantic video candidate finalization returned an invalid persisted contract."
        )
    response = CandidateGenerationResponse(
        run_id=str(run["id"]),
        revision=int(run.get("revision") or 0),
        stage=str(run["stage"]),
        candidates=[CandidateResponse(**candidate) for candidate in persisted_candidates],
    )
    return SuccessResponse(data=response.model_dump(mode="json"))


@router.post("/{post_id}/master-approve", response_model=SuccessResponse)
def approve_master(post_id: str, payload: MasterApprovalRequest, request: Request):
    run = _run_or_404(post_id)
    revision = int(run.get("revision") or 0)
    if revision != payload.expected_revision:
        raise StateTransitionError(
            "Semantic video master approval revision is stale.",
            {"expected_revision": payload.expected_revision, "actual_revision": revision},
        )
    if str(run.get("stage") or "") != "awaiting_reference_approval":
        raise StateTransitionError(
            "Semantic video run is not awaiting master approval.",
            {"stage": run.get("stage")},
        )
    _assert_reference_sources_current(
        context=load_semantic_video_context(post_id),
        run=run,
    )
    master_state = run.get("master_snapshot") if isinstance(run.get("master_snapshot"), dict) else {}
    candidates = master_state.get("candidates") if isinstance(master_state.get("candidates"), list) else []
    selected = next(
        (dict(candidate) for candidate in candidates if int(candidate.get("index") or 0) == payload.candidate_index),
        None,
    )
    if selected is None:
        raise ValidationError(
            "Semantic video master candidate does not exist.",
            {"candidate_index": payload.candidate_index},
        )
    master_contract_hash = str(master_state.get("visual_contract_hash") or "").lower()
    selected_contract_hash = str(selected.get("visual_contract_hash") or "").lower()
    if not master_contract_hash or selected_contract_hash != master_contract_hash:
        raise ValidationError(
            "Semantic scene-plate candidate is not bound to the frozen visual contract."
        )
    reference_snapshot = run.get("reference_snapshot")
    if not isinstance(reference_snapshot, dict) or not reference_snapshot:
        raise ValidationError("Semantic video canonical actor reference is unavailable.")
    selected_for_validation = {
        **selected,
        "candidates": candidates,
    }
    _assert_scene_plate_master(
        reference_snapshot=reference_snapshot,
        master_snapshot=selected_for_validation,
    )
    approved_by = str(getattr(request.state, "user_email", "unknown"))
    updated, approval = approve_master_transition(
        str(run["id"]),
        expected_revision=revision,
        candidate_index=payload.candidate_index,
        approved_by=approved_by,
        reason=payload.reason,
    )
    approved_snapshot = (
        dict(updated["master_snapshot"])
        if isinstance(updated.get("master_snapshot"), dict)
        else {}
    )
    master_hash = str(updated.get("master_hash") or approval.get("contract_hash") or "")
    response = MasterApprovalResponse(
        run_id=str(updated["id"]),
        revision=int(updated["revision"]),
        stage=str(updated["stage"]),
        approval_id=str(approval["id"]),
        approved_candidate_index=payload.candidate_index,
        master_hash=master_hash,
        master_snapshot=approved_snapshot,
    )
    return SuccessResponse(data=response.model_dump(mode="json"))


@router.post("/{post_id}/plan", response_model=SuccessResponse)
def create_free_plan(post_id: str, payload: PlanCreateRequest, request: Request):
    trusted_price = _trusted_veo_price()
    existing = get_run_by_post(post_id)
    if not existing:
        raise NotFoundError("Semantic video run not found.", {"post_id": post_id})
    if str(existing.get("stage") or "") != "awaiting_paid_approval":
        raise StateTransitionError(
            "Semantic video run is not awaiting paid plan creation.",
            {"stage": existing.get("stage")},
        )
    if int(existing.get("revision") or 0) != payload.expected_revision:
        raise StateTransitionError(
            "Semantic video plan revision is stale.",
            {"expected_revision": payload.expected_revision, "actual_revision": existing.get("revision")},
        )
    context = load_semantic_video_context(post_id)
    _assert_reference_sources_current(context=context, run=existing)
    reference = _reference_snapshot(context, existing)
    master = reference.get("master")
    if not isinstance(master, dict) or not str(master.get("storage_uri") or "").strip():
        raise ValidationError("Semantic video planning requires an approved master frame.")
    master_bytes = get_storage_client().download_video(
        video_url=str(master["storage_uri"]),
        correlation_id=str(getattr(request.state, "correlation_id", "semantic-plan")),
    )
    compiled = compile_semantic_video_plan(
        post_snapshot=context["post"],
        batch_snapshot=context["batch"],
        reference_snapshot=reference,
        approved_frame_bytes=master_bytes,
        price_per_provider_second=trusted_price,
        base_seed=payload.base_seed,
        resolution=payload.resolution,
    )
    run, takes = persist_semantic_video_plan(
        str(existing["id"]),
        expected_revision=payload.expected_revision,
        run_updates=compiled.run_payload,
        takes=compiled.take_payloads,
    )
    return SuccessResponse(data=_plan_response(run, takes).model_dump(mode="json"))


@router.get("/{post_id}/progress", response_model=SuccessResponse)
def get_progress(post_id: str):
    run = _run_or_404(post_id)
    takes = list_attempts(str(run["id"]))
    master_snapshot = run.get("master_snapshot") if isinstance(run.get("master_snapshot"), dict) else {}
    candidates = master_snapshot.get("candidates") if isinstance(master_snapshot.get("candidates"), list) else []
    candidate_count = len(candidates)
    reservation_token = str(run.get("candidate_reservation_token") or "").strip()
    reservation_expires_at = str(run.get("candidate_reservation_expires_at") or "").strip()
    if reservation_token and reservation_expires_at:
        try:
            reservation_expiry = datetime.fromisoformat(reservation_expires_at.replace("Z", "+00:00"))
            if reservation_expiry.tzinfo is None:
                reservation_expiry = reservation_expiry.replace(tzinfo=timezone.utc)
            reservation_started = reservation_expiry - timedelta(seconds=_CANDIDATE_RESERVATION_SECONDS)
            updated_at = datetime.fromisoformat(str(run.get("updated_at") or "").replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            finalization_persisted = (
                candidate_count == 3
                and updated_at > reservation_started + timedelta(seconds=1)
            )
            if finalization_persisted:
                candidate_generation_status = "ready"
            else:
                candidate_generation_status = (
                    "generating" if reservation_expiry > datetime.now(timezone.utc) else "stalled"
                )
        except ValueError:
            candidate_generation_status = "stalled"
    elif candidate_count == 3:
        candidate_generation_status = "ready"
    else:
        candidate_generation_status = "idle"
    latest: dict[int, dict[str, Any]] = {}
    for take in takes:
        index = int(take["take_index"])
        if index not in latest or int(take.get("attempt") or 1) >= int(latest[index].get("attempt") or 1):
            latest[index] = take
    ordered = [latest[index] for index in sorted(latest)]
    generated_states = {"completed", "qa_failed", "failed"}
    failed_states = {"qa_failed", "failed"}
    progress = ProgressResponse(
        run_id=str(run["id"]),
        revision=int(run.get("revision") or 0),
        stage=str(run.get("stage") or ""),
        candidate_generation_status=candidate_generation_status,
        candidate_count=candidate_count,
        plan_hash=run.get("plan_hash"),
        total_takes=len(ordered),
        generated_takes=sum(str(take.get("submission_state")) in generated_states for take in ordered),
        verified_takes=sum(
            bool((take.get("transcript_result") or {}).get("passed"))
            for take in ordered
        ),
        failed_take_indexes=[
            int(take["take_index"])
            for take in ordered
            if str(take.get("submission_state")) in failed_states
        ],
        takes=[
            ProgressTakeResponse(
                take_index=int(take["take_index"]),
                attempt=int(take.get("attempt") or 1),
                submission_state=str(take.get("submission_state") or "planned"),
                provider_duration_seconds=int(take.get("provider_duration_seconds") or 0),
                request_hash=str(take.get("request_hash") or ""),
                transcript_passed=bool((take.get("transcript_result") or {}).get("passed")),
                identity_passed=bool((take.get("identity_qa_result") or {}).get("passed")),
            )
            for take in ordered
        ],
    )
    return SuccessResponse(data=progress.model_dump(mode="json"))


def _assert_plan_sources_current(
    *,
    post_id: str,
    run: dict[str, Any],
    takes: list[dict[str, Any]],
    request: Request,
    require_current_compiler_plan: bool = True,
) -> None:
    context = load_semantic_video_context(post_id)
    current_reference = _reference_snapshot(context)
    persisted_reference = run.get("reference_snapshot")
    if not isinstance(persisted_reference, dict) or not persisted_reference:
        raise StateTransitionError("Semantic video persisted reference snapshot is no longer available.")
    persisted_actor = (
        run.get("actor_snapshot")
        if isinstance(run.get("actor_snapshot"), dict)
        else persisted_reference.get("actor")
    )
    current_actor = current_reference.get("actor")
    if (
        not isinstance(current_actor, dict)
        or not isinstance(persisted_actor, dict)
        or current_actor != persisted_actor
        or persisted_reference.get("actor") != persisted_actor
    ):
        raise StateTransitionError(
            "Semantic video actor descriptive snapshot changed after candidate generation."
        )
    persisted_reference_hash = _canonical_hash(persisted_reference)
    if persisted_reference_hash != str(run.get("reference_hash") or ""):
        raise StateTransitionError(
            "Semantic video persisted reference contract changed after planning."
        )
    _assert_reference_sources_current(context=context, run=run)

    try:
        current_script_snapshot = _approved_semantic_script_snapshot(context)
    except (ValidationError, ValueError) as exc:
        raise StateTransitionError("Semantic video approved script changed after planning.") from exc
    current_script_hash = _canonical_hash(current_script_snapshot)
    if current_script_hash != str(run.get("script_hash") or ""):
        raise StateTransitionError(
            "Semantic video approved script changed after planning.",
            {"persisted_script_hash": run.get("script_hash"), "current_script_hash": current_script_hash},
        )

    try:
        current_duration_contract = build_semantic_duration_contract(
            context["batch"].get("target_duration_seconds")
        )
    except (ValidationError, ValueError) as exc:
        raise StateTransitionError("Semantic video duration contract changed after planning.") from exc
    if current_duration_contract.contract_hash != str(run.get("duration_contract_hash") or ""):
        raise StateTransitionError(
            "Semantic video duration contract changed after planning.",
            {
                "persisted_duration_contract_hash": run.get("duration_contract_hash"),
                "current_duration_contract_hash": current_duration_contract.contract_hash,
            },
        )

    current_actor_rows, current_location_row = _ordered_reference_rows(current_reference)
    persisted_actor_rows, persisted_location_row = _ordered_reference_rows(persisted_reference)
    reference_rows = [
        ("actor_front", current_actor_rows[0], persisted_actor_rows[0]),
        ("actor_three_quarter", current_actor_rows[1], persisted_actor_rows[1]),
        ("location", current_location_row, persisted_location_row),
    ]
    fresh_reference_snapshots = []
    for role, current_row, persisted_row in reference_rows:
        current_mime = str(current_row.get("mime_type") or "image/png")
        persisted_mime = str(persisted_row.get("mime_type") or "image/png")
        declared_hash = current_row.get("sha256")
        declared_bytes = current_row.get("byte_length")
        if (
            current_mime != persisted_mime
            or (
                declared_hash is not None
                and str(declared_hash).lower() != str(persisted_row.get("sha256") or "").lower()
            )
            or (
                declared_bytes is not None
                and int(declared_bytes) != int(persisted_row.get("byte_length") or -1)
            )
        ):
            raise StateTransitionError(
                "Semantic video reference metadata changed after candidate generation.",
                {"role": role},
            )
        current_source = {
            key: value
            for key, value in current_row.items()
            if key not in {"sha256", "byte_length"}
        }
        fresh_snapshot = _download_reference(current_source, role=role, request=request)[1]
        fresh_reference_snapshots.append((role, fresh_snapshot, persisted_row))
    for role, fresh_snapshot, persisted_snapshot in fresh_reference_snapshots:
        expected_hash = str(persisted_snapshot.get("sha256") or "")
        expected_bytes = persisted_snapshot.get("byte_length")
        if (
            not expected_hash
            or fresh_snapshot["sha256"] != expected_hash
            or (
                expected_bytes is not None
                and fresh_snapshot["byte_length"] != expected_bytes
            )
        ):
            raise StateTransitionError(
                "Semantic video reference bytes changed after candidate generation.",
                {
                    "role": role,
                    "expected_sha256": expected_hash or None,
                    "actual_sha256": fresh_snapshot["sha256"],
                    "expected_bytes": expected_bytes,
                    "actual_bytes": fresh_snapshot["byte_length"],
                },
            )

    master = run.get("master_snapshot")
    if not isinstance(master, dict) or not str(master.get("storage_uri") or "").strip():
        raise StateTransitionError("Semantic video approved master is no longer available.")
    _assert_scene_plate_master(
        reference_snapshot=persisted_reference,
        master_snapshot=master,
    )
    current_master = current_reference.get("master")
    if isinstance(current_master, dict) and str(current_master.get("storage_uri") or "").strip():
        master_fields_changed = (
            str(current_master["storage_uri"]) != str(master["storage_uri"])
            or str(current_master.get("mime_type") or "image/png")
            != str(master.get("mime_type") or "image/png")
            or (
                current_master.get("sha256") is not None
                and str(current_master.get("sha256") or "").lower()
                != str(master.get("sha256") or "").lower()
            )
            or (
                current_master.get("byte_length") is not None
                and int(current_master["byte_length"]) != int(master.get("byte_length") or -1)
            )
        )
        if master_fields_changed:
            raise StateTransitionError(
                "Semantic video approved master metadata changed after planning."
            )
    master_bytes = get_storage_client().download_video(
        video_url=str(master["storage_uri"]),
        correlation_id=str(getattr(request.state, "correlation_id", "semantic-approval")),
    )
    actual_master_hash = sha256(master_bytes).hexdigest()
    expected_master_hash = str(run.get("master_hash") or master.get("sha256") or "")
    if (
        actual_master_hash != expected_master_hash
        or int(master.get("byte_length") or -1) != len(master_bytes)
    ):
        raise StateTransitionError(
            "Semantic video approved master bytes changed after planning.",
            {
                "expected_sha256": expected_master_hash or None,
                "actual_sha256": actual_master_hash,
                "expected_bytes": master.get("byte_length"),
                "actual_bytes": len(master_bytes),
            },
        )

    initial_takes = [take for take in takes if int(take.get("attempt") or 1) == 1]
    if not initial_takes:
        raise StateTransitionError("Semantic video plan has no persisted initial takes.")
    plan = run.get("plan_snapshot") if isinstance(run.get("plan_snapshot"), dict) else {}
    planned_takes = plan.get("takes")
    ordered_initial = sorted(initial_takes, key=lambda take: int(take.get("take_index") or 0))
    initial_contract = [
        {
            "take_index": int(take.get("take_index") or 0),
            "provider_duration_seconds": int(take.get("provider_duration_seconds") or 0),
            "request_hash": str(take.get("request_hash") or ""),
        }
        for take in ordered_initial
    ]
    planned_request_hashes = [str(value) for value in plan.get("request_hashes") or []]
    if (
        not isinstance(planned_takes, list)
        or initial_contract != planned_takes
        or int(plan.get("take_count") or 0) != len(initial_contract)
        or planned_request_hashes != [take["request_hash"] for take in initial_contract]
    ):
        raise StateTransitionError(
            "Semantic video persisted initial take contract changed after planning."
        )
    if not require_current_compiler_plan:
        # A paid retry must continue the already approved immutable take contract.
        # Recompiling it with a newer prompt compiler would turn a safe deploy into
        # false source drift even though every source and stored byte hash above is
        # unchanged. The retry RPC still verifies the original take fields, hashes,
        # seed lineage, guidance snapshot, and approved plan hash atomically.
        return

    reference = deepcopy(persisted_reference)
    reference["master"] = deepcopy(master)
    compiled = compile_semantic_video_plan(
        post_snapshot=context["post"],
        batch_snapshot=context["batch"],
        reference_snapshot=reference,
        approved_frame_bytes=master_bytes,
        price_per_provider_second=plan.get("price_per_provider_second_usd") or "0.40",
        base_seed=min(int(take.get("seed") or 0) for take in initial_takes),
        resolution=str(run.get("resolution") or "1080p"),
    )
    if compiled.plan_hash != str(run.get("plan_hash") or ""):
        raise StateTransitionError(
            "Semantic video plan sources changed after planning.",
            {"persisted_plan_hash": run.get("plan_hash"), "current_plan_hash": compiled.plan_hash},
        )


@router.post("/{post_id}/approve", response_model=SuccessResponse)
def approve_initial_plan(post_id: str, payload: PlanApprovalRequest, request: Request):
    run = _run_or_404(post_id)
    revision = int(run.get("revision") or 0)
    if revision != payload.expected_revision:
        raise StateTransitionError(
            "Semantic video approval revision is stale.",
            {"expected_revision": payload.expected_revision, "actual_revision": revision},
        )
    if str(run.get("stage") or "") != "awaiting_paid_approval":
        raise StateTransitionError(
            "Semantic video run is not awaiting initial paid approval.",
            {"stage": run.get("stage")},
        )
    if str(run.get("plan_hash") or "") != payload.plan_hash:
        raise StateTransitionError(
            "Semantic video approval hash is stale.",
            {"approved_hash": payload.plan_hash, "current_hash": run.get("plan_hash")},
        )
    all_takes = list_attempts(str(run["id"]))
    _assert_plan_sources_current(
        post_id=post_id,
        run=run,
        takes=all_takes,
        request=request,
    )
    takes = [take for take in all_takes if int(take.get("attempt") or 1) == 1]
    plan = run.get("plan_snapshot") if isinstance(run.get("plan_snapshot"), dict) else {}
    indexes = sorted(int(take["take_index"]) for take in takes)
    updated, approval = approve_initial_plan_transition(
        str(run["id"]),
        expected_revision=revision,
        plan_hash=payload.plan_hash,
        approved_by=str(getattr(request.state, "user_email", "unknown")),
        reason=payload.reason,
    )
    try:
        approved_indexes = [int(index) for index in approval["approved_take_indexes"]]
        approved_seconds = int(approval["approved_provider_seconds"])
        quota_units = int(approval["quota_units"])
        estimated_cost_value = Decimal(str(approval["estimated_cost_usd"]))
        contract_hash = str(approval["contract_hash"])
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise StateTransitionError(
            "Semantic video initial approval returned an incomplete persisted contract."
        ) from exc
    expected_seconds = int(plan.get("billable_provider_seconds") or 0)
    expected_quota = int(plan.get("quota_units") or 0)
    try:
        expected_cost = Decimal(str(plan["estimated_cost_usd"]))
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise StateTransitionError("Semantic video persisted plan billing contract is invalid.") from exc
    if (
        approved_indexes != indexes
        or approved_seconds != expected_seconds
        or quota_units != expected_quota
        or estimated_cost_value != expected_cost
        or contract_hash != payload.plan_hash
    ):
        raise StateTransitionError(
            "Semantic video initial approval returned a mismatched persisted contract."
        )
    estimated_cost = str(approval["estimated_cost_usd"])
    response = ApprovalResponse(
        run_id=str(updated["id"]),
        revision=int(updated["revision"]),
        stage=str(updated["stage"]),
        approval_id=str(approval["id"]),
        contract_hash=contract_hash,
        approved_take_indexes=approved_indexes,
        approved_provider_seconds=approved_seconds,
        quota_units=quota_units,
        estimated_cost_usd=estimated_cost,
    )
    return SuccessResponse(data=response.model_dump(mode="json"))


@router.post("/{post_id}/retry-approve", response_model=SuccessResponse)
def approve_retry(post_id: str, payload: RetryApprovalRequest, request: Request):
    run = _run_or_404(post_id)
    revision = int(run.get("revision") or 0)
    if revision != payload.expected_revision:
        raise StateTransitionError(
            "Semantic video retry approval revision is stale.",
            {"expected_revision": payload.expected_revision, "actual_revision": revision},
        )
    if str(run.get("stage") or "") != "retry_approval_required":
        raise StateTransitionError(
            "Semantic video run is not awaiting retry approval.",
            {"stage": run.get("stage")},
        )
    if str(run.get("plan_hash") or "") != payload.plan_hash:
        raise StateTransitionError(
            "Semantic video retry approval plan hash is stale.",
            {"approved_hash": payload.plan_hash, "current_hash": run.get("plan_hash")},
        )

    all_takes = list_attempts(str(run["id"]))
    _assert_plan_sources_current(
        post_id=post_id,
        run=run,
        takes=all_takes,
        request=request,
        require_current_compiler_plan=False,
    )
    latest: dict[int, dict[str, Any]] = {}
    initial: dict[int, dict[str, Any]] = {}
    for take in all_takes:
        index = int(take["take_index"])
        if index not in latest or int(take.get("attempt") or 1) >= int(latest[index].get("attempt") or 1):
            latest[index] = take
        if index not in initial or int(take.get("attempt") or 1) < int(initial[index].get("attempt") or 1):
            initial[index] = take
    failed_indexes = {
        index
        for index, take in latest.items()
        if str(take.get("submission_state") or "") in {"qa_failed", "failed"}
    }
    requested_indexes = set(payload.failed_take_indexes)
    if not requested_indexes.issubset(failed_indexes):
        raise StateTransitionError(
            "Semantic video retry approval may target only currently failed take indexes.",
            {
                "requested_take_indexes": sorted(requested_indexes),
                "failed_take_indexes": sorted(failed_indexes),
            },
        )

    retry_takes = []
    for index in payload.failed_take_indexes:
        previous = latest[index]
        original = initial[index]
        attempt = int(previous.get("attempt") or 1) + 1
        guidance_snapshot = deepcopy(previous.get("retry_guidance") or {})
        submission_error = previous.get("submission_error")
        if (
            not guidance_snapshot
            and isinstance(submission_error, dict)
            and str(submission_error.get("code") or "") == "provider_operation_failed"
        ):
            guidance_snapshot = {
                "guidance": (
                    "Preserve the original delivery exactly; the provider operation failed "
                    "internally before producing a usable take."
                ),
                "source": "provider_internal_failure",
            }
        previous_contract = previous.get("request_contract")
        previous_prompt = (
            str(previous_contract.get("prompt") or "")
            if isinstance(previous_contract, Mapping)
            else ""
        )
        guidance_text = _retry_guidance_text(
            guidance_snapshot,
            previous_prompt=previous_prompt,
            persisted_take=previous,
        )
        request_contract = deepcopy(original.get("request_contract") or {})
        base_prompt = str(request_contract.get("prompt") or "").strip()
        if not base_prompt:
            raise StateTransitionError(
                "Semantic video retry requires the original persisted provider prompt."
            )
        if guidance_text in base_prompt:
            raise StateTransitionError(
                "Semantic video retry guidance is already present in the base prompt."
            )
        retry_prompt = f"{base_prompt} Retry delivery correction: {guidance_text}"
        beat_text = str(previous.get("beat_text") or "")
        if beat_text != str(original.get("beat_text") or "") or retry_prompt.count(beat_text) != 1:
            raise StateTransitionError(
                "Semantic video retry must preserve the exact scripted beat once."
            )
        previous_seed = int(previous.get("seed") or 0)
        retry_seed = previous_seed + 1000
        request_contract.update(
            {
                "attempt": attempt,
                "prompt": retry_prompt,
                "seed": retry_seed,
                "retry_of_request_hash": str(previous.get("request_hash") or ""),
                "retry_guidance": guidance_snapshot,
            }
        )
        canonical_request_json = _canonical_json(request_contract)
        request_hash = sha256(canonical_request_json.encode("utf-8")).hexdigest()
        request_contract["canonical_request_json"] = canonical_request_json
        if request_hash == str(previous.get("request_hash") or ""):
            raise StateTransitionError(
                "Semantic video retry request hash must differ from the failed attempt."
            )
        retry_takes.append(
            {
                "take_index": index,
                "attempt": attempt,
                "beat_text": beat_text,
                "word_count": int(previous.get("word_count") or 0),
                "estimated_speech_seconds": previous.get("estimated_speech_seconds") or 0,
                "provider_duration_seconds": int(previous.get("provider_duration_seconds") or 0),
                "shot_transform": deepcopy(previous.get("shot_transform") or {}),
                "shot_hash": str(previous.get("shot_hash") or ""),
                "prompt_hash": sha256(retry_prompt.encode("utf-8")).hexdigest(),
                "negative_prompt_hash": previous.get("negative_prompt_hash"),
                "provider_model": str(previous.get("provider_model") or run.get("provider_model") or ""),
                "seed": retry_seed,
                "request_contract": request_contract,
                "request_hash": request_hash,
                "submission_state": "planned",
                "retry_guidance": guidance_snapshot,
            }
        )
    provider_seconds = sum(int(take["provider_duration_seconds"]) for take in retry_takes)
    plan = run.get("plan_snapshot") if isinstance(run.get("plan_snapshot"), dict) else {}
    price = Decimal(str(plan.get("price_per_provider_second_usd") or "0.40"))
    incremental_cost = (price * Decimal(provider_seconds)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cost_text = format(incremental_cost, ".2f")
    retry_hash = _retry_contract_hash(
        plan_hash=payload.plan_hash,
        revision=revision,
        indexes=payload.failed_take_indexes,
        request_hashes=[take["request_hash"] for take in retry_takes],
        provider_seconds=provider_seconds,
        quota_units=len(retry_takes),
        estimated_cost=cost_text,
    )
    updated, approval, persisted_retry_takes = approve_retry_transition(
        str(run["id"]),
        expected_revision=revision,
        plan_hash=payload.plan_hash,
        retry_takes=retry_takes,
        contract_hash=retry_hash,
        approved_by=str(getattr(request.state, "user_email", "unknown")),
        reason=payload.reason,
    )
    persisted_indexes = sorted(int(index) for index in approval.get("approved_take_indexes") or [])
    persisted_seconds = int(approval.get("approved_provider_seconds") or 0)
    persisted_quota = int(approval.get("quota_units") or 0)
    persisted_cost = str(approval.get("estimated_cost_usd") or "")
    persisted_hash = str(approval.get("contract_hash") or "")
    persisted_request_hashes = sorted(str(take.get("request_hash") or "") for take in persisted_retry_takes)
    if (
        persisted_indexes != payload.failed_take_indexes
        or persisted_seconds != provider_seconds
        or persisted_quota != len(retry_takes)
        or persisted_hash != retry_hash
        or Decimal(persisted_cost) != incremental_cost
        or persisted_request_hashes != sorted(take["request_hash"] for take in retry_takes)
    ):
        raise StateTransitionError(
            "Semantic video persisted retry approval does not match the approved contract."
        )
    response = ApprovalResponse(
        run_id=str(updated["id"]),
        revision=int(updated["revision"]),
        stage=str(updated["stage"]),
        approval_id=str(approval["id"]),
        contract_hash=persisted_hash,
        approved_take_indexes=persisted_indexes,
        approved_provider_seconds=persisted_seconds,
        quota_units=persisted_quota,
        estimated_cost_usd=persisted_cost,
    )
    return SuccessResponse(data=response.model_dump(mode="json"))


@router.post("/{post_id}/cancel", response_model=SuccessResponse)
def cancel_run(post_id: str, payload: CancellationRequest, request: Request):
    run = _run_or_404(post_id)
    revision = int(run.get("revision") or 0)
    if revision != payload.expected_revision:
        raise StateTransitionError(
            "Semantic video cancellation revision is stale.",
            {"expected_revision": payload.expected_revision, "actual_revision": revision},
        )
    if str(run.get("stage") or "") in {"completed", "failed"}:
        raise StateTransitionError(
            "Semantic video terminal runs cannot be cancelled.",
            {"stage": run.get("stage")},
        )
    updated, cancelled_count = cancel_run_transition(
        str(run["id"]),
        expected_revision=revision,
        cancelled_by=str(getattr(request.state, "user_email", "unknown")),
        reason=payload.reason,
        correlation_id=str(getattr(request.state, "correlation_id", "")),
    )
    response = CancellationResponse(
        run_id=str(updated["id"]),
        revision=int(updated["revision"]),
        stage="failed",
        cancelled_take_count=cancelled_count,
        reason=payload.reason,
    )
    return SuccessResponse(data=response.model_dump(mode="json"))


__all__ = ["router"]
