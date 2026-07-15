"""Supabase persistence for Semantic UGC runs, takes, and approvals."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Optional, Sequence

from postgrest.exceptions import APIError

from app.adapters.supabase_client import get_supabase
from app.core.errors import NotFoundError, StateTransitionError, ValidationError
from app.features.characters.scene_reference import get_scene_bible
from app.features.scenes.queries import require_canonical_scene_asset


_SEMANTIC_DEFAULT_SCENE_KEY = "home_office_advice_a"
_SEMANTIC_DEFAULT_WARDROBE = (
    "Use the wardrobe visible in actor reference Image 1 as the sole wardrobe reference."
)


def _client(client=None):
    return client or get_supabase().client


def _rows(response) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return [dict(data)]
    return [dict(row) for row in (data or [])]


def _one_affected(response, *, operation: str, details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    rows = _rows(response)
    if len(rows) != 1:
        raise StateTransitionError(
            f"Semantic video {operation} lost its optimistic revision race.",
            {**(details or {}), "affected_rows": len(rows)},
        )
    return rows[0]


def _execute_transition_rpc(query, *, operation: str) -> Any:
    try:
        return query.execute()
    except APIError as exc:
        if str(getattr(exc, "code", "")) == "40001":
            raise StateTransitionError(f"Semantic video {operation} conflict.") from exc
        raise


def _transition_result(query, *, operation: str) -> dict[str, Any]:
    response = _execute_transition_rpc(query, operation=operation)
    result = getattr(response, "data", None)
    if isinstance(result, list):
        if len(result) != 1:
            raise StateTransitionError(
                f"Semantic video {operation} returned an unexpected result count.",
                {"result_count": len(result)},
            )
        result = result[0]
    if not isinstance(result, Mapping):
        raise StateTransitionError(
            f"Semantic video {operation} returned an invalid contract."
        )
    return dict(result)


def _image_only_actor_snapshot(
    batch: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    raw_actor = reference.get("actor")
    if not isinstance(raw_actor, Mapping):
        raw_actor = batch.get("actor_identity_snapshot")
    if not isinstance(raw_actor, Mapping):
        raw_actor = {}
    raw_actor_references = reference.get("actor_references")
    actor_references = raw_actor_references if isinstance(raw_actor_references, list) else []
    raw_urls = raw_actor.get("reference_image_urls")
    urls = [str(url).strip() for url in raw_urls if str(url).strip()] if isinstance(raw_urls, list) else []
    if not urls:
        urls = [
            str(row.get("storage_uri") or "").strip()
            for row in actor_references
            if isinstance(row, Mapping) and str(row.get("storage_uri") or "").strip()
        ]
    return {
        "actor_identity_id": str(
            raw_actor.get("actor_identity_id")
            or reference.get("actor_identity_id")
            or batch.get("actor_identity_id")
            or ""
        ).strip(),
        "name": str(raw_actor.get("name") or "Semantic UGC actor").strip(),
        "reference_image_urls": urls[:2],
    }


def _canonical_semantic_location() -> tuple[dict[str, Any], str]:
    asset = require_canonical_scene_asset(
        scene_key=_SEMANTIC_DEFAULT_SCENE_KEY,
        aspect_ratio="9:16",
        image_size="1K",
    )
    bible = get_scene_bible(asset.scene_key)
    return (
        {
            "role": "location",
            "storage_uri": asset.image_url,
            "storage_key": asset.storage_key,
            "mime_type": "image/png",
            "scene_key": asset.scene_key,
            "scene_bible_version": asset.scene_bible_version,
        },
        bible.scene_identity,
    )


def _complete_semantic_reference(
    batch: Mapping[str, Any],
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    completed = deepcopy(dict(reference))
    actor = _image_only_actor_snapshot(batch, completed)
    completed["actor_identity_id"] = actor["actor_identity_id"]
    completed["actor"] = actor
    actor_rows = completed.get("actor_references")
    if not isinstance(actor_rows, list) or not actor_rows:
        completed["actor_references"] = [
            {"role": role, "storage_uri": url, "mime_type": "image/png"}
            for role, url in zip(
                ("actor_front", "actor_three_quarter"),
                actor["reference_image_urls"],
            )
        ]
    if not isinstance(completed.get("location_reference"), dict):
        location, scene_description = _canonical_semantic_location()
        completed["location_reference"] = location
        completed.setdefault("scene_description", scene_description)
    completed.setdefault("wardrobe_description", _SEMANTIC_DEFAULT_WARDROBE)
    return completed


def load_semantic_video_context(post_id: str, *, client=None) -> dict[str, Any]:
    response = (
        _client(client)
        .table("posts")
        .select("*,batches(*)")
        .eq("id", post_id)
        .limit(1)
        .execute()
    )
    rows = _rows(response)
    if not rows:
        raise NotFoundError("Semantic video post not found.", {"post_id": post_id})
    post = rows[0]
    joined_batch = post.pop("batches", None)
    if isinstance(joined_batch, list):
        batch = dict(joined_batch[0]) if joined_batch else {}
    else:
        batch = dict(joined_batch or {})
    if not batch:
        raise NotFoundError("Semantic video batch not found.", {"post_id": post_id})

    seed_data = post.get("seed_data") if isinstance(post.get("seed_data"), dict) else {}
    explicit_reference = seed_data.get("semantic_reference_snapshot")
    if isinstance(explicit_reference, dict):
        reference = deepcopy(explicit_reference)
    else:
        actor = batch.get("actor_identity_snapshot") if isinstance(batch.get("actor_identity_snapshot"), dict) else {}
        urls = actor.get("reference_image_urls") if isinstance(actor.get("reference_image_urls"), list) else []
        location = seed_data.get("semantic_location_reference") or actor.get("location_reference")
        reference = {
            "actor_identity_id": batch.get("actor_identity_id"),
            "actor": actor,
            "actor_references": [
                {"role": role, "storage_uri": url, "mime_type": "image/png"}
                for role, url in zip(("actor_front", "actor_three_quarter"), urls[:2])
            ],
            "location_reference": deepcopy(location) if isinstance(location, dict) else None,
        }
    reference = _complete_semantic_reference(batch, reference)
    master = seed_data.get("semantic_master_snapshot")
    if isinstance(master, dict):
        reference["master"] = deepcopy(master)
    return {"post": post, "batch": batch, "reference": reference}


def get_run_by_post(post_id: str, *, client=None) -> Optional[dict[str, Any]]:
    response = (
        _client(client)
        .table("semantic_video_runs")
        .select("*")
        .eq("post_id", post_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = _rows(response)
    return rows[0] if rows else None


def reserve_candidate_generation(
    post_id: str,
    *,
    expected_revision: Optional[int],
    run_create: Mapping[str, Any],
    reservation_owner: str,
    reservation_token: str,
    reservation_seconds: int,
    client=None,
) -> dict[str, Any]:
    response = _execute_transition_rpc(
        _client(client).rpc(
            "reserve_semantic_video_candidates",
            {
                "p_post_id": post_id,
                "p_expected_revision": expected_revision,
                "p_run_create": dict(run_create),
                "p_reservation_owner": reservation_owner,
                "p_reservation_token": reservation_token,
                "p_reservation_seconds": reservation_seconds,
            },
        ),
        operation="candidate reservation",
    )
    return _one_affected(response, operation="candidate reservation")


def finalize_candidate_generation(
    run_id: str,
    *,
    reserved_revision: int,
    reservation_token: str,
    run_updates: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    response = _execute_transition_rpc(
        _client(client).rpc(
            "finalize_semantic_video_candidates",
            {
                "p_run_id": run_id,
                "p_reserved_revision": reserved_revision,
                "p_reservation_token": reservation_token,
                "p_run_update": dict(run_updates),
            },
        ),
        operation="candidate finalization",
    )
    return _one_affected(response, operation="candidate finalization")


def approve_master_transition(
    run_id: str,
    *,
    expected_revision: int,
    candidate_index: int,
    approved_by: str,
    reason: Optional[str],
    client=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _transition_result(
        _client(client).rpc(
            "approve_semantic_video_master",
            {
                "p_run_id": run_id,
                "p_expected_revision": expected_revision,
                "p_candidate_index": candidate_index,
                "p_approved_by": approved_by,
                "p_reason": reason,
            },
        ),
        operation="master approval",
    )
    run = result.get("run")
    approval = result.get("approval")
    if not isinstance(run, Mapping) or not isinstance(approval, Mapping):
        raise StateTransitionError("Semantic video master approval returned an invalid contract.")
    return dict(run), dict(approval)


def approve_initial_plan_transition(
    run_id: str,
    *,
    expected_revision: int,
    plan_hash: str,
    approved_by: str,
    reason: Optional[str],
    client=None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = _transition_result(
        _client(client).rpc(
            "approve_semantic_video_initial_plan",
            {
                "p_run_id": run_id,
                "p_expected_revision": expected_revision,
                "p_plan_hash": plan_hash,
                "p_approved_by": approved_by,
                "p_reason": reason,
            },
        ),
        operation="initial plan approval",
    )
    run = result.get("run")
    approval = result.get("approval")
    if not isinstance(run, Mapping) or not isinstance(approval, Mapping):
        raise StateTransitionError("Semantic video initial plan approval returned an invalid contract.")
    return dict(run), dict(approval)


def approve_retry_transition(
    run_id: str,
    *,
    expected_revision: int,
    plan_hash: str,
    retry_takes: Sequence[Mapping[str, Any]],
    contract_hash: str,
    approved_by: str,
    reason: Optional[str],
    client=None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    result = _transition_result(
        _client(client).rpc(
            "approve_semantic_video_retry",
            {
                "p_run_id": run_id,
                "p_expected_revision": expected_revision,
                "p_plan_hash": plan_hash,
                "p_retry_takes": [dict(take) for take in retry_takes],
                "p_contract_hash": contract_hash,
                "p_approved_by": approved_by,
                "p_reason": reason,
            },
        ),
        operation="retry approval",
    )
    run = result.get("run")
    approval = result.get("approval")
    takes = result.get("takes")
    if (
        not isinstance(run, Mapping)
        or not isinstance(approval, Mapping)
        or not isinstance(takes, list)
        or any(not isinstance(take, Mapping) for take in takes)
    ):
        raise StateTransitionError("Semantic video retry approval returned an invalid contract.")
    return dict(run), dict(approval), [dict(take) for take in takes]


def cancel_run_transition(
    run_id: str,
    *,
    expected_revision: int,
    cancelled_by: str,
    reason: str,
    correlation_id: str,
    client=None,
) -> tuple[dict[str, Any], int]:
    result = _transition_result(
        _client(client).rpc(
            "cancel_semantic_video_run",
            {
                "p_run_id": run_id,
                "p_expected_revision": expected_revision,
                "p_cancelled_by": cancelled_by,
                "p_reason": reason,
                "p_correlation_id": correlation_id,
            },
        ),
        operation="cancellation",
    )
    run = result.get("run")
    cancelled_count = result.get("cancelled_take_count")
    if not isinstance(run, Mapping) or isinstance(cancelled_count, bool) or not isinstance(cancelled_count, int):
        raise StateTransitionError("Semantic video cancellation returned an invalid contract.")
    return dict(run), cancelled_count


def persist_semantic_video_plan(
    run_id: str,
    *,
    expected_revision: int,
    run_updates: Mapping[str, Any],
    takes: Sequence[Mapping[str, Any]],
    client=None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not takes:
        raise ValidationError("Semantic video plan requires at least one initial take.")
    if "revision" in run_updates:
        raise ValidationError("Semantic video revision is managed by atomic plan persistence.")
    take_payloads = [dict(take) for take in takes]
    if [int(take.get("take_index", -1)) for take in take_payloads] != list(range(len(take_payloads))):
        raise ValidationError("Semantic video initial takes must be in exact zero-based order.")
    response = _execute_transition_rpc(
        _client(client).rpc(
            "persist_semantic_video_plan",
            {
                "p_run_id": run_id,
                "p_expected_revision": expected_revision,
                "p_run_update": dict(run_updates),
                "p_initial_takes": take_payloads,
            },
        ),
        operation="plan persistence",
    )
    result = getattr(response, "data", None)
    if isinstance(result, list):
        if len(result) != 1:
            raise StateTransitionError(
                "Semantic video atomic plan persistence returned an unexpected result count.",
                {"run_id": run_id, "result_count": len(result)},
            )
        result = result[0]
    if not isinstance(result, Mapping):
        raise StateTransitionError(
            "Semantic video atomic plan persistence returned an invalid result.",
            {"run_id": run_id},
        )
    run = result.get("run")
    persisted_takes = result.get("takes")
    if not isinstance(run, Mapping) or not isinstance(persisted_takes, list):
        raise StateTransitionError(
            "Semantic video atomic plan persistence returned an invalid contract.",
            {"run_id": run_id},
        )
    rows = [dict(take) for take in persisted_takes if isinstance(take, Mapping)]
    if (
        len(rows) != len(take_payloads)
        or [int(take.get("take_index", -1)) for take in rows] != list(range(len(take_payloads)))
        or any(int(take.get("attempt", 0)) != 1 for take in rows)
    ):
        raise StateTransitionError(
            "Semantic video atomic plan persistence returned unexpected initial takes.",
            {"run_id": run_id, "expected_rows": len(take_payloads), "affected_rows": len(rows)},
        )
    return dict(run), rows


def list_attempts(run_id: str, *, client=None) -> list[dict[str, Any]]:
    response = (
        _client(client)
        .table("semantic_video_takes")
        .select("*")
        .eq("run_id", run_id)
        .order("take_index")
        .order("attempt")
        .execute()
    )
    return _rows(response)


def list_approvals(run_id: str, *, client=None) -> list[dict[str, Any]]:
    response = (
        _client(client)
        .table("semantic_video_approvals")
        .select("*")
        .eq("run_id", run_id)
        .order("created_at")
        .execute()
    )
    return _rows(response)


def resume_qa_review(
    *,
    run_id: str,
    expected_revision: int,
    plan_hash: str,
    client=None,
) -> dict[str, Any]:
    if expected_revision < 0 or not str(plan_hash or "").strip():
        raise ValidationError("Semantic video QA resume requires a revision and plan hash.")
    return _worker_rpc(
        "resume_semantic_video_qa_review",
        {
            "p_run_id": str(run_id),
            "p_expected_revision": int(expected_revision),
            "p_plan_hash": str(plan_hash),
        },
        operation="QA review resume",
        client=client,
    )


def apply_visual_remediation(
    *,
    run_id: str,
    expected_revision: int,
    plan_hash: str,
    take_index: int,
    expected_raw_sha256: str,
    remediated_raw_uri: str,
    remediated_raw_sha256: str,
    transformation: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    if (
        expected_revision < 0
        or take_index < 0
        or not str(plan_hash or "").strip()
        or not isinstance(transformation, Mapping)
    ):
        raise ValidationError("Semantic video visual remediation contract is invalid.")
    return _worker_rpc(
        "apply_semantic_video_visual_remediation",
        {
            "p_run_id": str(run_id),
            "p_expected_revision": int(expected_revision),
            "p_plan_hash": str(plan_hash),
            "p_take_index": int(take_index),
            "p_expected_raw_sha256": str(expected_raw_sha256),
            "p_remediated_raw_uri": str(remediated_raw_uri),
            "p_remediated_raw_sha256": str(remediated_raw_sha256),
            "p_transformation": dict(transformation),
        },
        operation="visual remediation",
        client=client,
    )


def get_run(run_id: str, *, client=None, required: bool = True) -> Optional[dict[str, Any]]:
    response = (
        _client(client)
        .table("semantic_video_runs")
        .select("*")
        .eq("id", run_id)
        .limit(1)
        .execute()
    )
    rows = _rows(response)
    if rows:
        return rows[0]
    if required:
        raise NotFoundError("Semantic video run not found.", {"run_id": run_id})
    return None


def _worker_rpc(
    function_name: str,
    payload: Mapping[str, Any],
    *,
    operation: str,
    client=None,
) -> dict[str, Any]:
    return _transition_result(
        _client(client).rpc(function_name, dict(payload)),
        operation=operation,
    )


def _worker_fence_payload(
    *, run_id: str, take_id: Optional[str], worker_id: str, lease_token: str
) -> dict[str, Any]:
    if not str(run_id or "").strip() or not str(worker_id or "").strip() or not str(lease_token or "").strip():
        raise ValidationError("Semantic video worker transition requires a run and lease fence.")
    payload = {
        "p_run_id": str(run_id),
        "p_worker_id": str(worker_id),
        "p_lease_token": str(lease_token),
    }
    if take_id is not None:
        if not str(take_id or "").strip():
            raise ValidationError("Semantic video worker take transition requires a take id.")
        payload["p_take_id"] = str(take_id)
    return payload


def reserve_paid_submission(
    *, run_id: str, take_id: str, worker_id: str, lease_token: str, client=None
) -> dict[str, Any]:
    return _worker_rpc(
        "reserve_semantic_video_submission",
        _worker_fence_payload(
            run_id=run_id, take_id=take_id, worker_id=worker_id, lease_token=lease_token
        ),
        operation="paid submission reservation",
        client=client,
    )


def persist_worker_exception(
    *,
    run_id: str,
    worker_id: str,
    lease_token: str,
    stage: str,
    error: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    if not str(stage or "").strip() or not isinstance(error, Mapping):
        raise ValidationError("Semantic video worker exception requires a stage and error.")
    return _worker_rpc(
        "persist_semantic_video_worker_exception",
        {
            **_worker_fence_payload(
                run_id=run_id,
                take_id=None,
                worker_id=worker_id,
                lease_token=lease_token,
            ),
            "p_stage": str(stage),
            "p_error": dict(error),
        },
        operation="worker exception persistence",
        client=client,
    )


def persist_worker_submission_intent(
    *,
    run_id: str,
    take_id: str,
    worker_id: str,
    lease_token: str,
    request_hash: str,
    client=None,
) -> dict[str, Any]:
    if not str(request_hash or "").strip():
        raise ValidationError("Semantic video submission intent requires a request hash.")
    return _worker_rpc(
        "persist_semantic_video_submission_intent",
        {
            **_worker_fence_payload(
                run_id=run_id, take_id=take_id, worker_id=worker_id, lease_token=lease_token
            ),
            "p_request_hash": str(request_hash),
        },
        operation="submission intent persistence",
        client=client,
    )


def persist_worker_accepted_operation(
    *,
    run_id: str,
    take_id: str,
    worker_id: str,
    lease_token: str,
    operation_id: str,
    provider_model: str,
    client=None,
) -> dict[str, Any]:
    if not str(operation_id or "").strip() or not str(provider_model or "").strip():
        raise ValidationError("Accepted Semantic video operation requires an id and model.")
    return _worker_rpc(
        "persist_semantic_video_accepted_operation",
        {
            **_worker_fence_payload(
                run_id=run_id, take_id=take_id, worker_id=worker_id, lease_token=lease_token
            ),
            "p_operation_id": str(operation_id),
            "p_provider_model": str(provider_model),
        },
        operation="accepted operation persistence",
        client=client,
    )


def persist_worker_submission_unknown(
    *,
    run_id: str,
    take_id: str,
    worker_id: str,
    lease_token: str,
    error: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    if not isinstance(error, Mapping) or not error:
        raise ValidationError("Unknown semantic video submission requires an error envelope.")
    return _worker_rpc(
        "persist_semantic_video_submission_unknown",
        {
            **_worker_fence_payload(
                run_id=run_id, take_id=take_id, worker_id=worker_id, lease_token=lease_token
            ),
            "p_error": dict(error),
        },
        operation="unknown submission persistence",
        client=client,
    )


def persist_worker_provider_failure(
    *,
    run_id: str,
    take_id: str,
    worker_id: str,
    lease_token: str,
    error: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    if not isinstance(error, Mapping) or not error:
        raise ValidationError("Semantic video provider failure requires an error envelope.")
    return _worker_rpc(
        "persist_semantic_video_provider_failure",
        {
            **_worker_fence_payload(
                run_id=run_id, take_id=take_id, worker_id=worker_id, lease_token=lease_token
            ),
            "p_error": dict(error),
        },
        operation="provider failure persistence",
        client=client,
    )


def persist_worker_completed_take(
    *,
    run_id: str,
    take_id: str,
    worker_id: str,
    lease_token: str,
    provider_video_uri: str,
    raw_artifact_uri: str,
    raw_artifact_sha256: str,
    client=None,
) -> dict[str, Any]:
    return _worker_rpc(
        "persist_semantic_video_completed_take",
        {
            **_worker_fence_payload(
                run_id=run_id, take_id=take_id, worker_id=worker_id, lease_token=lease_token
            ),
            "p_provider_video_uri": str(provider_video_uri),
            "p_raw_artifact_uri": str(raw_artifact_uri),
            "p_raw_artifact_sha256": str(raw_artifact_sha256),
        },
        operation="completed take persistence",
        client=client,
    )


def acquire_run_lease(
    *,
    run_id: Optional[str] = None,
    worker_id: str,
    lease_seconds: int,
    client=None,
) -> Optional[dict[str, Any]]:
    if not str(worker_id or "").strip() or isinstance(lease_seconds, bool) or lease_seconds <= 0:
        raise ValidationError("Semantic video lease requires a worker id and positive lease seconds.")
    response = _client(client).rpc(
        "claim_semantic_video_run",
        {
            "worker_id": str(worker_id),
            "lease_seconds": int(lease_seconds),
            "requested_run_id": str(run_id) if run_id is not None else None,
        },
    ).execute()
    rows = _rows(response)
    if len(rows) > 1:
        raise StateTransitionError(
            "Semantic video lease claim returned more than one run.",
            {"affected_rows": len(rows)},
        )
    return rows[0] if rows else None


def advance_worker_stage(
    *,
    run_id: str,
    worker_id: str,
    lease_token: str,
    expected_stage: str,
    next_stage: str,
    artifacts: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    return _worker_rpc(
        "advance_semantic_video_stage",
        {
            **_worker_fence_payload(
                run_id=run_id, take_id=None, worker_id=worker_id, lease_token=lease_token
            ),
            "p_expected_stage": str(expected_stage),
            "p_next_stage": str(next_stage),
            "p_artifacts": dict(artifacts),
        },
        operation="stage advancement",
        client=client,
    )


def require_worker_retry_approval(
    *,
    run_id: str,
    worker_id: str,
    lease_token: str,
    expected_stage: str,
    failed_take_indexes: Sequence[int],
    evidence: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    return _worker_rpc(
        "require_semantic_video_retry_approval",
        {
            **_worker_fence_payload(
                run_id=run_id, take_id=None, worker_id=worker_id, lease_token=lease_token
            ),
            "p_expected_stage": str(expected_stage),
            "p_failed_take_indexes": [int(index) for index in failed_take_indexes],
            "p_evidence": dict(evidence),
        },
        operation="retry approval requirement",
        client=client,
    )


def release_worker_lease(
    *, run_id: str, worker_id: str, lease_token: str, client=None
) -> dict[str, Any]:
    return _worker_rpc(
        "release_semantic_video_lease",
        _worker_fence_payload(
            run_id=run_id, take_id=None, worker_id=worker_id, lease_token=lease_token
        ),
        operation="lease release",
        client=client,
    )


def complete_worker_run(
    *,
    run_id: str,
    worker_id: str,
    lease_token: str,
    final_video_uri: str,
    final_video_sha256: str,
    final_caption_uri: str,
    final_caption_sha256: str,
    artifact_manifest: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    return _worker_rpc(
        "complete_semantic_video_run",
        {
            **_worker_fence_payload(
                run_id=run_id, take_id=None, worker_id=worker_id, lease_token=lease_token
            ),
            "p_final_video_uri": str(final_video_uri),
            "p_final_video_sha256": str(final_video_sha256),
            "p_final_caption_uri": str(final_caption_uri),
            "p_final_caption_sha256": str(final_caption_sha256),
            "p_artifact_manifest": dict(artifact_manifest),
        },
        operation="run completion",
        client=client,
    )


__all__ = [
    "approve_initial_plan_transition",
    "approve_master_transition",
    "approve_retry_transition",
    "cancel_run_transition",
    "acquire_run_lease",
    "advance_worker_stage",
    "complete_worker_run",
    "get_run_by_post",
    "get_run",
    "list_approvals",
    "list_attempts",
    "load_semantic_video_context",
    "persist_semantic_video_plan",
    "persist_worker_accepted_operation",
    "persist_worker_completed_take",
    "persist_worker_provider_failure",
    "persist_worker_submission_intent",
    "persist_worker_submission_unknown",
    "release_worker_lease",
    "require_worker_retry_approval",
    "reserve_paid_submission",
]
