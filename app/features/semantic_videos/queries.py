"""Supabase persistence for Semantic UGC runs, takes, and approvals."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from app.adapters.supabase_client import get_supabase
from app.core.errors import NotFoundError, StateTransitionError, ValidationError


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


def create_run(payload: Mapping[str, Any], *, client=None) -> dict[str, Any]:
    response = _client(client).table("semantic_video_runs").insert(dict(payload)).execute()
    return _one_affected(response, operation="run creation")


def update_run(
    run_id: str,
    *,
    expected_revision: int,
    updates: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    if "revision" in updates:
        raise ValidationError("Semantic video revision is managed by optimistic updates.")
    payload = {**dict(updates), "revision": expected_revision + 1}
    response = (
        _client(client)
        .table("semantic_video_runs")
        .update(payload)
        .eq("id", run_id)
        .eq("revision", expected_revision)
        .execute()
    )
    return _one_affected(
        response,
        operation="run update",
        details={"run_id": run_id, "expected_revision": expected_revision},
    )


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
    response = _client(client).rpc(
        "persist_semantic_video_plan",
        {
            "p_run_id": run_id,
            "p_expected_revision": expected_revision,
            "p_run_update": dict(run_updates),
            "p_initial_takes": take_payloads,
        },
    ).execute()
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


def append_approval(payload: Mapping[str, Any], *, client=None) -> dict[str, Any]:
    response = _client(client).table("semantic_video_approvals").insert(dict(payload)).execute()
    return _one_affected(response, operation="approval append")


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


def append_attempts(
    run_id: str,
    takes: Sequence[Mapping[str, Any]],
    *,
    client=None,
) -> list[dict[str, Any]]:
    if not takes:
        raise ValidationError("Semantic video retry requires at least one take attempt.")
    payload = [{**dict(take), "run_id": run_id} for take in takes]
    rows = _rows(_client(client).table("semantic_video_takes").insert(payload).execute())
    if len(rows) != len(payload):
        raise StateTransitionError(
            "Semantic video attempt append affected an unexpected row count.",
            {"run_id": run_id, "expected_rows": len(payload), "affected_rows": len(rows)},
        )
    return rows


def cancel_pending_takes(run_id: str, *, client=None) -> list[dict[str, Any]]:
    response = (
        _client(client)
        .table("semantic_video_takes")
        .update({"submission_state": "cancelled"})
        .eq("run_id", run_id)
        .in_("submission_state", ["planned", "reserved", "intent_persisted"])
        .execute()
    )
    return _rows(response)


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


def _update_take_state(
    take_id: str,
    *,
    expected_state: Optional[str],
    updates: Mapping[str, Any],
    request_hash: Optional[str] = None,
    client=None,
    operation: str,
) -> dict[str, Any]:
    query = _client(client).table("semantic_video_takes").update(dict(updates)).eq("id", take_id)
    if expected_state is not None:
        query = query.eq("submission_state", expected_state)
    if request_hash is not None:
        query = query.eq("request_hash", request_hash)
    return _one_affected(
        query.execute(),
        operation=operation,
        details={"take_id": take_id, "expected_state": expected_state},
    )


def persist_submission_intent(
    take_id: str,
    *,
    expected_state: str,
    request_hash: str,
    intent_at: Optional[str] = None,
    client=None,
) -> dict[str, Any]:
    timestamp = intent_at or datetime.now(timezone.utc).isoformat()
    return _update_take_state(
        take_id,
        expected_state=expected_state,
        request_hash=request_hash,
        updates={
            "submission_state": "intent_persisted",
            "submission_intent_at": timestamp,
        },
        client=client,
        operation="submission intent persistence",
    )


def persist_accepted_operation(
    take_id: str,
    *,
    expected_state: str,
    operation_id: str,
    provider_model: str,
    client=None,
) -> dict[str, Any]:
    if not str(operation_id or "").strip():
        raise ValidationError("Accepted Semantic video operation requires an operation id.")
    return _update_take_state(
        take_id,
        expected_state=expected_state,
        updates={
            "submission_state": "submitted",
            "operation_id": str(operation_id),
            "provider_model": str(provider_model),
        },
        client=client,
        operation="accepted operation persistence",
    )


def persist_take_qa_artifacts(
    take_id: str,
    *,
    expected_state: Optional[str] = None,
    submission_state: Optional[str] = None,
    raw_artifact_uri: Optional[str] = None,
    raw_artifact_sha256: Optional[str] = None,
    transcript_result: Optional[Mapping[str, Any]] = None,
    identity_qa_result: Optional[Mapping[str, Any]] = None,
    voice_qa_contribution: Optional[Mapping[str, Any]] = None,
    retry_guidance: Optional[Mapping[str, Any]] = None,
    client=None,
) -> dict[str, Any]:
    values = {
        "submission_state": submission_state,
        "raw_artifact_uri": raw_artifact_uri,
        "raw_artifact_sha256": raw_artifact_sha256,
        "transcript_result": dict(transcript_result) if transcript_result is not None else None,
        "identity_qa_result": dict(identity_qa_result) if identity_qa_result is not None else None,
        "voice_qa_contribution": dict(voice_qa_contribution) if voice_qa_contribution is not None else None,
        "retry_guidance": dict(retry_guidance) if retry_guidance is not None else None,
    }
    updates = {key: value for key, value in values.items() if value is not None}
    if not updates:
        raise ValidationError("Semantic video QA/artifact persistence requires at least one field.")
    return _update_take_state(
        take_id,
        expected_state=expected_state,
        updates=updates,
        client=client,
        operation="QA and artifact persistence",
    )


def acquire_run_lease(
    *,
    worker_id: str,
    lease_seconds: int,
    client=None,
) -> Optional[dict[str, Any]]:
    if not str(worker_id or "").strip() or isinstance(lease_seconds, bool) or lease_seconds <= 0:
        raise ValidationError("Semantic video lease requires a worker id and positive lease seconds.")
    response = _client(client).rpc(
        "claim_semantic_video_run",
        {"worker_id": str(worker_id), "lease_seconds": int(lease_seconds)},
    ).execute()
    rows = _rows(response)
    if len(rows) > 1:
        raise StateTransitionError(
            "Semantic video lease claim returned more than one run.",
            {"affected_rows": len(rows)},
        )
    return rows[0] if rows else None


def release_run_lease(
    run_id: str,
    *,
    worker_id: str,
    expected_revision: int,
    client=None,
) -> dict[str, Any]:
    response = (
        _client(client)
        .table("semantic_video_runs")
        .update(
            {
                "lease_owner": None,
                "lease_expires_at": None,
                "revision": expected_revision + 1,
            }
        )
        .eq("id", run_id)
        .eq("revision", expected_revision)
        .eq("lease_owner", worker_id)
        .execute()
    )
    return _one_affected(
        response,
        operation="lease release",
        details={"run_id": run_id, "worker_id": worker_id, "expected_revision": expected_revision},
    )


def complete_run(
    run_id: str,
    *,
    expected_revision: int,
    final_video_uri: str,
    final_video_sha256: str,
    final_caption_uri: Optional[str] = None,
    final_caption_sha256: Optional[str] = None,
    client=None,
) -> dict[str, Any]:
    if not str(final_video_uri or "").strip() or not str(final_video_sha256 or "").strip():
        raise ValidationError("Semantic video completion requires final video URI and SHA-256.")
    return update_run(
        run_id,
        expected_revision=expected_revision,
        updates={
            "stage": "completed",
            "final_video_uri": final_video_uri,
            "final_video_sha256": final_video_sha256,
            "final_caption_uri": final_caption_uri,
            "final_caption_sha256": final_caption_sha256,
            "failure_envelope": None,
            "lease_owner": None,
            "lease_expires_at": None,
        },
        client=client,
    )


def fail_run(
    run_id: str,
    *,
    expected_revision: int,
    failure_envelope: Mapping[str, Any],
    client=None,
) -> dict[str, Any]:
    if not isinstance(failure_envelope, Mapping) or not failure_envelope:
        raise ValidationError("Semantic video failure requires a non-empty failure envelope.")
    return update_run(
        run_id,
        expected_revision=expected_revision,
        updates={
            "stage": "failed",
            "failure_envelope": dict(failure_envelope),
            "lease_owner": None,
            "lease_expires_at": None,
        },
        client=client,
    )


__all__ = [
    "append_approval",
    "append_attempts",
    "cancel_pending_takes",
    "acquire_run_lease",
    "complete_run",
    "create_run",
    "get_run_by_post",
    "get_run",
    "list_approvals",
    "list_attempts",
    "load_semantic_video_context",
    "persist_accepted_operation",
    "persist_semantic_video_plan",
    "persist_submission_intent",
    "persist_take_qa_artifacts",
    "release_run_lease",
    "update_run",
    "fail_run",
]
