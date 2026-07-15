"""Explicit free-plan and approval API for Semantic UGC videos."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request

from app.adapters.storage_client import get_storage_client
from app.core.errors import NotFoundError, StateTransitionError, SuccessResponse, ValidationError
from app.core.config import get_settings
from app.core.video_profiles import script_word_count
from app.features.shot_frames.service import (
    ShotFrameReference,
    generate_shot_frame_candidates,
    load_raw_camera_system_prompt,
)
from app.features.shot_production.duration import build_semantic_duration_contract
from app.features.semantic_videos.queries import (
    approve_initial_plan_transition,
    approve_master_transition,
    approve_retry_transition,
    cancel_run_transition,
    finalize_candidate_generation,
    get_run_by_post,
    list_approvals as list_approvals,
    list_attempts,
    load_semantic_video_context,
    persist_semantic_video_plan,
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


router = APIRouter(prefix="/semantic-videos/posts", tags=["semantic-videos"])


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


def _retry_guidance_text(value: Any) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = next(
            (
                str(value[key])
                for key in ("guidance", "prompt_suffix", "instruction", "message")
                if str(value.get(key) or "").strip()
            ),
            "",
        )
    else:
        text = ""
    text = " ".join(text.split())
    if not text:
        raise StateTransitionError(
            "Semantic video retry requires persisted QA retry guidance."
        )
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


def _reference_run_payload(
    *,
    context: dict[str, Any],
    reference_snapshot: dict[str, Any],
    master_snapshot: dict[str, Any],
) -> dict[str, Any]:
    post = context["post"]
    batch = context["batch"]
    _script, script_snapshot = _approved_script(post)
    contract = build_semantic_duration_contract(batch.get("target_duration_seconds"))
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
        "scene_description": str(reference.get("scene_description") or ""),
        "wardrobe_description": str(reference.get("wardrobe_description") or ""),
        "actor_reference_uris": [str(row["storage_uri"]) for row in actor_rows],
        "location_reference_uri": str(location_row["storage_uri"]),
    }


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
    existing = get_run_by_post(post_id)
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

    context = load_semantic_video_context(post_id)
    script, _script_snapshot = _approved_script(context["post"])
    reference = deepcopy(context.get("reference") or {})
    actor_rows, location_row = _ordered_reference_rows(reference)
    actor = reference.get("actor") if isinstance(reference.get("actor"), dict) else {}

    actor_front, actor_front_snapshot = _download_reference(actor_rows[0], role="actor_front", request=request)
    actor_three_quarter, actor_three_quarter_snapshot = _download_reference(
        actor_rows[1],
        role="actor_three_quarter",
        request=request,
    )
    location, location_snapshot = _download_reference(location_row, role="location", request=request)
    persisted_reference = {
        **reference,
        "actor_references": [actor_front_snapshot, actor_three_quarter_snapshot],
        "location_reference": location_snapshot,
    }
    persisted_reference.pop("master", None)
    reservation_token = str(uuid4())
    reservation_owner = str(
        getattr(request.state, "user_email", None)
        or getattr(request.state, "correlation_id", None)
        or "semantic-candidates"
    )
    reserved = reserve_candidate_generation(
        post_id,
        expected_revision=payload.expected_revision,
        run_create=_reference_run_payload(
            context=context,
            reference_snapshot=persisted_reference,
            master_snapshot={},
        ),
        reservation_owner=reservation_owner,
        reservation_token=reservation_token,
        reservation_seconds=1800,
    )
    generated = generate_shot_frame_candidates(
        script=script,
        actor_name=str(actor.get("name") or "Semantic UGC actor"),
        scene_description=str(reference.get("scene_description") or "Approved actor-free location reference."),
        wardrobe_description=str(reference.get("wardrobe_description") or "Preserve wardrobe from actor reference Image 1."),
        actor_references=[actor_front, actor_three_quarter],
        location_reference=location,
        candidate_count=payload.candidate_count,
    )
    if len(generated.candidates) != payload.candidate_count:
        raise StateTransitionError(
            "Semantic video candidate generation returned an unexpected candidate count.",
            {"expected": payload.candidate_count, "actual": len(generated.candidates)},
        )
    correlation_id = str(getattr(request.state, "correlation_id", "semantic-candidates"))
    candidates = []
    for candidate in generated.candidates:
        candidate_hash = sha256(candidate.image_bytes).hexdigest()
        uploaded = get_storage_client().upload_image(
            image_bytes=candidate.image_bytes,
            file_name=f"semantic-{post_id}-candidate-{candidate.index}-{candidate_hash[:12]}.png",
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
            }
        )
    prompt_writer_system_prompt = load_raw_camera_system_prompt()
    master_snapshot = {
        "candidates": candidates,
        "prompt_writer_system_prompt": prompt_writer_system_prompt,
        "prompt_writer_system_prompt_sha256": sha256(
            prompt_writer_system_prompt.encode("utf-8")
        ).hexdigest(),
        "prompt_writer_output": str(generated.prompt_writer_output),
        "composition_prompt": str(generated.composition_prompt),
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
    try:
        current_source_identity = _reference_source_identity(current_reference)
        persisted_source_identity = _reference_source_identity(persisted_reference)
    except ValidationError as exc:
        raise StateTransitionError("Semantic video reference sources changed after candidate generation.") from exc
    if current_source_identity != persisted_source_identity:
        raise StateTransitionError(
            "Semantic video reference sources changed after candidate generation.",
            {
                "persisted_source_identity": persisted_source_identity,
                "current_source_identity": current_source_identity,
            },
        )

    try:
        _script, current_script_snapshot = _approved_script(context["post"])
    except ValidationError as exc:
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

    reference = deepcopy(persisted_reference)
    reference["master"] = deepcopy(master)
    initial_takes = [take for take in takes if int(take.get("attempt") or 1) == 1]
    if not initial_takes:
        raise StateTransitionError("Semantic video plan has no persisted initial takes.")
    plan = run.get("plan_snapshot") if isinstance(run.get("plan_snapshot"), dict) else {}
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
        guidance_text = _retry_guidance_text(guidance_snapshot)
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
