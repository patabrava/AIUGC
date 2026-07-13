"""Pure compilation for persisted Semantic UGC video plans.

This module deliberately depends only on deterministic shot-production primitives.
It cannot instantiate image or video providers and performs no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID

from app.core.errors import ValidationError
from app.core.video_profiles import script_word_count
from app.features.shot_production.duration import build_semantic_duration_contract
from app.features.shot_production.planner import plan_editorial_beats
from app.features.shot_production.prompts import compile_veo_take_requests
from app.features.shot_production.shot_deck import derive_shot_deck


DEFAULT_PRICE_PER_PROVIDER_SECOND_USD = Decimal("0.40")
DEFAULT_RESOLUTION = "1080p"
_MONEY_QUANTUM = Decimal("0.01")


@dataclass(frozen=True)
class CompiledSemanticVideoPlan:
    """JSON-safe database payloads and their immutable approval hash."""

    run_payload: dict[str, Any]
    take_payloads: tuple[dict[str, Any], ...]
    plan_hash: str


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError("Semantic video snapshots require finite numeric values.")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError("Semantic video snapshots require finite decimal values.")
        return format(value, "f")
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview, str)):
        return [_json_safe(item) for item in value]
    raise ValidationError(
        "Semantic video snapshots must contain JSON-safe values.",
        {"value_type": type(value).__name__},
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_mapping(value: Mapping[str, Any] | None, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise ValidationError(f"Semantic video planning requires an explicit {name} snapshot.")
    return dict(value)


def _resolve_script(post_snapshot: Mapping[str, Any]) -> tuple[str, str]:
    seed_data = post_snapshot.get("seed_data")
    seed = seed_data if isinstance(seed_data, Mapping) else {}
    review_status = str(
        post_snapshot.get("script_review_status") or seed.get("script_review_status") or ""
    ).strip().lower()
    if review_status != "approved":
        raise ValidationError(
            "Semantic video planning requires an approved script.",
            {"script_review_status": review_status or None},
        )
    script = str(
        post_snapshot.get("script")
        or post_snapshot.get("approved_script")
        or seed.get("script")
        or seed.get("dialog_script")
        or post_snapshot.get("topic_rotation")
        or ""
    )
    script = " ".join(script.split())
    if not script:
        raise ValidationError("Semantic video planning requires non-empty approved script text.")
    return script, review_status


def _resolve_price(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError("Semantic video price must be a non-negative finite decimal.")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Semantic video price must be a non-negative finite decimal.") from exc
    if not price.is_finite() or price < 0:
        raise ValidationError("Semantic video price must be a non-negative finite decimal.")
    return price


def _resolve_master_snapshot(reference_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    value = reference_snapshot.get("master") or reference_snapshot.get("master_snapshot")
    if not isinstance(value, Mapping):
        raise ValidationError("Semantic video planning requires an approved master snapshot.")
    snapshot = _json_safe(dict(value))
    if not str(snapshot.get("storage_uri") or "").strip():
        raise ValidationError("Approved master snapshot requires a durable storage URI.")
    return snapshot


def compile_semantic_video_plan(
    *,
    post_snapshot: Mapping[str, Any],
    batch_snapshot: Mapping[str, Any],
    reference_snapshot: Mapping[str, Any],
    approved_frame_bytes: bytes,
    price_per_provider_second: Decimal | int | float | str = DEFAULT_PRICE_PER_PROVIDER_SECOND_USD,
    base_seed: int = 240713,
    resolution: str = DEFAULT_RESOLUTION,
) -> CompiledSemanticVideoPlan:
    """Compile one immutable free plan without loading or calling provider adapters."""
    post = _required_mapping(post_snapshot, "post")
    batch = _required_mapping(batch_snapshot, "batch")
    reference = _required_mapping(reference_snapshot, "reference")

    post_id = str(post.get("id") or "").strip()
    post_batch_id = str(post.get("batch_id") or "").strip()
    batch_id = str(batch.get("id") or "").strip()
    if not post_id or not batch_id or post_batch_id != batch_id:
        raise ValidationError(
            "Semantic video post and batch snapshots must identify the same batch.",
            {"post_id": post_id or None, "post_batch_id": post_batch_id or None, "batch_id": batch_id or None},
        )
    if str(batch.get("creation_mode") or "").strip() != "semantic_ugc":
        raise ValidationError("Semantic video planning requires a semantic_ugc batch.")

    requested_duration = batch.get("target_duration_seconds")
    duration_contract = build_semantic_duration_contract(requested_duration)
    script, review_status = _resolve_script(post)
    word_count = script_word_count(script)
    if not duration_contract.minimum_words <= word_count <= duration_contract.maximum_words:
        raise ValidationError(
            "Approved semantic script is outside the duration contract word envelope.",
            {
                "word_count": word_count,
                "minimum_words": duration_contract.minimum_words,
                "maximum_words": duration_contract.maximum_words,
            },
        )

    master_snapshot = _resolve_master_snapshot(reference)
    master_hash = str(master_snapshot.get("sha256") or "").strip().lower()
    actual_master_hash = sha256(approved_frame_bytes).hexdigest() if isinstance(approved_frame_bytes, bytes) else ""
    if master_hash != actual_master_hash:
        raise ValidationError(
            "Approved master bytes do not match the snapshotted SHA-256.",
            {"expected_sha256": master_hash or None, "actual_sha256": actual_master_hash or None},
        )
    if master_snapshot.get("byte_length") != len(approved_frame_bytes):
        raise ValidationError(
            "Approved master bytes do not match the snapshotted byte length.",
            {"expected_bytes": master_snapshot.get("byte_length"), "actual_bytes": len(approved_frame_bytes)},
        )

    beats = plan_editorial_beats(script)
    if len(beats) != duration_contract.minimum_take_count:
        raise ValidationError(
            "Approved semantic script does not match the duration contract take count.",
            {
                "planned_take_count": len(beats),
                "minimum_take_count": duration_contract.minimum_take_count,
            },
        )
    shot_deck = derive_shot_deck(
        approved_master_bytes=approved_frame_bytes,
        expected_sha256=master_hash,
        mime_type=str(master_snapshot.get("mime_type") or ""),
        shot_count=len(beats),
    )
    requests = compile_veo_take_requests(beats=beats, shot_deck=shot_deck, base_seed=base_seed)

    price = _resolve_price(price_per_provider_second)
    billable_seconds = sum(request.duration_seconds for request in requests)
    estimated_cost = (price * Decimal(billable_seconds)).quantize(
        _MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    price_text = format(price.quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP), ".2f")
    cost_text = format(estimated_cost, ".2f")

    reference_basis = dict(reference)
    reference_basis.pop("master", None)
    reference_basis.pop("master_snapshot", None)
    reference_basis.pop("reference_hash", None)
    normalized_reference = _json_safe(reference_basis)
    reference_hash = _canonical_hash(normalized_reference)
    actor_snapshot = _json_safe(reference_basis.get("actor") or {})
    script_snapshot = {
        "text": script,
        "review_status": review_status,
        "word_count": word_count,
    }
    script_hash = _canonical_hash(script_snapshot)

    take_payloads: list[dict[str, Any]] = []
    for request in requests:
        shot_transform = {
            "index": request.shot.index,
            "name": request.shot.name,
            "crop_box": list(request.shot.crop_box),
            "width": request.shot.width,
            "height": request.shot.height,
            "mime_type": request.shot.mime_type,
            "source_sha256": request.shot.source_sha256,
            "output_sha256": request.shot.output_sha256,
        }
        request_contract = {
            "take_index": request.index,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "provider_model": request.model,
            "resolution": str(resolution),
            "aspect_ratio": request.aspect_ratio,
            "provider_duration_seconds": request.duration_seconds,
            "seed": request.seed,
            "approved_master_sha256": master_hash,
            "shot_sha256": request.shot.output_sha256,
        }
        take_payloads.append(
            {
                "take_index": request.index,
                "attempt": 1,
                "beat_text": request.beat.text,
                "word_count": request.beat.word_count,
                "estimated_speech_seconds": request.beat.estimated_speech_seconds,
                "provider_duration_seconds": request.duration_seconds,
                "shot_transform": shot_transform,
                "shot_hash": _canonical_hash(shot_transform),
                "prompt_hash": sha256(request.prompt.encode("utf-8")).hexdigest(),
                "negative_prompt_hash": sha256(request.negative_prompt.encode("utf-8")).hexdigest(),
                "provider_model": request.model,
                "seed": request.seed,
                "request_contract": request_contract,
                "request_hash": _canonical_hash(request_contract),
                "submission_state": "planned",
            }
        )

    provider_model = requests[0].model
    plan_basis = {
        "post_id": post_id,
        "batch_id": batch_id,
        "requested_duration_seconds": requested_duration,
        "duration_contract_hash": duration_contract.contract_hash,
        "script_hash": script_hash,
        "reference_hash": reference_hash,
        "master_hash": master_hash,
        "provider_model": provider_model,
        "resolution": str(resolution),
        "price_per_provider_second_usd": price_text,
        "estimated_cost_usd": cost_text,
        "request_hashes": [take["request_hash"] for take in take_payloads],
    }
    plan_hash = _canonical_hash(plan_basis)
    plan_snapshot = {
        **plan_basis,
        "take_count": len(take_payloads),
        "billable_provider_seconds": billable_seconds,
        "quota_units": len(take_payloads),
        "takes": [
            {
                "take_index": take["take_index"],
                "request_hash": take["request_hash"],
                "provider_duration_seconds": take["provider_duration_seconds"],
            }
            for take in take_payloads
        ],
    }
    run_payload = {
        "post_id": post_id,
        "batch_id": batch_id,
        "requested_duration_seconds": requested_duration,
        "duration_contract": duration_contract.as_dict(),
        "duration_contract_hash": duration_contract.contract_hash,
        "script_snapshot": script_snapshot,
        "script_hash": script_hash,
        "actor_identity_id": reference_basis.get("actor_identity_id"),
        "actor_snapshot": actor_snapshot,
        "reference_snapshot": normalized_reference,
        "reference_hash": reference_hash,
        "master_snapshot": master_snapshot,
        "master_hash": master_hash,
        "stage": "awaiting_paid_approval",
        "plan_snapshot": plan_snapshot,
        "plan_hash": plan_hash,
        "provider_model": provider_model,
        "resolution": str(resolution),
        "estimated_cost_usd": cost_text,
        "artifact_prefix": f"semantic-videos/{batch_id}/{post_id}",
    }
    return CompiledSemanticVideoPlan(
        run_payload=_json_safe(run_payload),
        take_payloads=tuple(_json_safe(take) for take in take_payloads),
        plan_hash=plan_hash,
    )


__all__ = [
    "CompiledSemanticVideoPlan",
    "DEFAULT_PRICE_PER_PROVIDER_SECOND_USD",
    "DEFAULT_RESOLUTION",
    "compile_semantic_video_plan",
]
