"""Public recovery state derived from durable runs, never raw exception text."""
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

EXECUTABLE_STAGES = frozenset({"generating", "transcript_qa", "identity_qa", "voice_qa", "acoustic_qa", "composing", "uploading"})


def generation_error(run: Mapping[str, Any], takes: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    stage = str(run.get("stage") or "")
    failure = run.get("failure_envelope") or {}
    try:
        expiry = datetime.fromisoformat(str(run.get("lease_expires_at") or "").replace("Z", "+00:00"))
        active = bool(run.get("lease_owner") and expiry > datetime.now(timezone.utc))
    except (ValueError, TypeError):
        active = False
    latest = {}
    for take in takes:
        index = int(take.get("take_index") or 0)
        if index not in latest or int(take.get("attempt") or 1) > int(latest[index].get("attempt") or 1):
            latest[index] = take
    ambiguous = [t for t in latest.values() if t.get("submission_state") in {"intent_persisted", "submission_unknown"}]
    proven = bool(
        len(ambiguous) == 1
        and failure.get("code") == "submission_intent_acknowledgement_lost"
        and failure.get("provider_call_entered") is False
        and ambiguous[0].get("submission_state") == "intent_persisted"
        and str(ambiguous[0].get("id")) == str(failure.get("take_id"))
        and ambiguous[0].get("request_hash") == failure.get("request_hash")
        and not ambiguous[0].get("operation_id")
        and ambiguous[0].get("quota_state") == "reserved"
    )
    if stage == "failed" and failure.get("runtime_failure") is True:
        if active:
            return {"code": "worker_releasing", "message": "Generation stopped after an error. Check status again when the worker releases this video to retry safely.", "retry_allowed": False}
        safe = not active and (not ambiguous or proven)
        return {
            "code": "generation_stopped" if safe else "submission_unconfirmed",
            "message": (
                "Video generation stopped after an error. Retry generation to continue from saved work under the existing approval."
                if safe else
                "Generation stopped because a paid submission could not be confirmed. Contact support with this video ID before retrying; another request could charge twice."
            ),
            "retry_allowed": safe,
        }
    if stage == "generating" and ambiguous and not active:
        return {
            "code": "submission_unconfirmed",
            "message": "Generation stopped because a paid submission could not be confirmed. Contact support with this video ID before retrying; another request could charge twice.",
            "retry_allowed": False,
        }
    if stage in EXECUTABLE_STAGES and not active:
        try:
            updated = datetime.fromisoformat(str(run.get("updated_at") or "").replace("Z", "+00:00"))
            stale = (datetime.now(timezone.utc) - updated).total_seconds() >= 300
        except (ValueError, TypeError):
            stale = False
        if stale:
            return {
                "code": "worker_not_progressing",
                "message": "This video has stopped updating and no worker is active. Check status again. If it remains stopped, contact support with this video ID; saved work is preserved.",
                "retry_allowed": False,
            }
    return None
