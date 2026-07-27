"""Structured visual contract shared by Semantic Manual and Semantic UGC."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import math
from typing import Any, Mapping

from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.features.shot_frames.wheelchair_scene_plate import (
    FRAMING_CONTRACT,
    WHEELCHAIR_VISUAL_CONTRACT,
)


VISUAL_CONTRACT_VERSION = "semantic_visual_contract_v1"
SCENE_PLATE_REFERENCE_ROLE_CONTRACT = (
    "actor_front",
    "actor_three_quarter",
    "actor_free_location",
)
SCENE_PLATE_ASPECT_RATIO = "9:16"
SCENE_IDENTITY_EVALUATOR_CONTRACT_VERSION = "semantic-scene-identity-v2"
SCENE_IDENTITY_ATTESTATION_VERSION = "semantic-actor-identity-v1"
SCENE_IDENTITY_COMPONENT_FIELDS = (
    "same_person",
    "facial_geometry_consistent",
    "apparent_age_consistent",
    "hairline_and_hair_consistent",
    "skin_texture_natural",
    "not_beautified_or_stylized",
    "no_face_artifacts",
)
SEMANTIC_WARDROBES = {
    "cream_sweater": "cream crewneck knit sweater",
    "grey_cardigan": "light-grey cardigan over a plain white top",
    "beige_blazer": "soft-beige blazer over a plain white top",
}
SEMANTIC_LOCATION_ROTATION = (
    "bathroom_accessibility_a",
    "garden_patio_a",
    "home_office_advice_a",
)


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def build_actor_reference_fingerprint(actor_references: Any) -> str:
    """Hash ordered, byte-verified actor anchors for one canonical scene plate."""
    if not isinstance(actor_references, (list, tuple)) or len(actor_references) != 2:
        raise ValidationError(
            "Semantic actor fingerprint requires exactly two ordered references."
        )
    normalized = []
    for reference in actor_references:
        if not isinstance(reference, Mapping):
            raise ValidationError("Semantic actor fingerprint references must be mappings.")
        row = {
            "role": str(reference.get("role") or "").strip(),
            "storage_uri": str(reference.get("storage_uri") or "").strip(),
            "mime_type": str(reference.get("mime_type") or "").strip().lower(),
            "byte_length": int(reference.get("byte_length") or 0),
            "sha256": str(reference.get("sha256") or "").strip().lower(),
        }
        if (
            not row["role"]
            or not row["storage_uri"]
            or not row["mime_type"].startswith("image/")
            or row["byte_length"] <= 0
            or len(row["sha256"]) != 64
        ):
            raise ValidationError("Semantic actor fingerprint reference is incomplete.")
        normalized.append(row)
    return _canonical_hash({"ordered_actor_references": normalized})


def build_scene_plate_generation_contract(
    *,
    actor_reference_fingerprint: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    resolved = settings or get_settings()
    fingerprint = str(actor_reference_fingerprint or "").strip().lower()
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint
    ):
        raise ValidationError(
            "Semantic scene-plate generation contract requires an actor fingerprint."
        )
    fields = {
        "version": str(resolved.semantic_scene_plate_contract_version).strip(),
        "model": str(resolved.semantic_scene_plate_model).strip(),
        "prompt_contract_version": str(
            resolved.semantic_scene_plate_contract_version
        ).strip(),
        "reference_roles": list(SCENE_PLATE_REFERENCE_ROLE_CONTRACT),
        "aspect_ratio": SCENE_PLATE_ASPECT_RATIO,
        "image_size": str(resolved.semantic_scene_plate_image_size),
        "identity_evaluator_model": str(
            resolved.semantic_scene_identity_gate_model
        ).strip(),
        "identity_evaluator_contract_version": (
            SCENE_IDENTITY_EVALUATOR_CONTRACT_VERSION
        ),
        "minimum_identity_confidence": float(
            resolved.semantic_scene_identity_min_confidence
        ),
        "actor_reference_fingerprint": fingerprint,
    }
    if not fields["version"] or not fields["model"] or not fields[
        "identity_evaluator_model"
    ]:
        raise ValidationError("Semantic scene-plate generation contract is incomplete.")
    return {**fields, "contract_hash": _canonical_hash(fields)}


def validate_scene_plate_generation_contract(
    value: Mapping[str, Any] | None,
    *,
    actor_reference_fingerprint: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(
            "Semantic scene-plate generation contract is unavailable."
        )
    expected = build_scene_plate_generation_contract(
        actor_reference_fingerprint=actor_reference_fingerprint,
        settings=settings,
    )
    if dict(value) != expected:
        raise ValidationError(
            "Semantic scene-plate generation contract is stale.",
            {
                "expected_contract_hash": expected["contract_hash"],
                "actual_contract_hash": value.get("contract_hash"),
            },
        )
    return expected


def validate_scene_identity_gate(
    candidate: Mapping[str, Any],
    *,
    actor_reference_fingerprint: str,
    generation_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate server-computed scene identity evidence against current inputs."""
    gate = candidate.get("identity_gate_result")
    candidate_hash = str(candidate.get("sha256") or "").strip().lower()
    if not isinstance(gate, Mapping):
        raise ValidationError("Semantic scene-plate candidate has no identity result.")
    components = gate.get("component_results")
    confidence = gate.get("confidence")
    if (
        not isinstance(components, Mapping)
        or set(components) != set(SCENE_IDENTITY_COMPONENT_FIELDS)
        or any(components.get(field) is not True for field in SCENE_IDENTITY_COMPONENT_FIELDS)
        or not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not math.isfinite(float(confidence))
        or float(confidence)
        < float(generation_contract.get("minimum_identity_confidence") or 0.0)
        or gate.get("passed") is not True
        or str(gate.get("status") or "") != "passed"
        or str(gate.get("candidate_sha256") or "").lower() != candidate_hash
        or str(gate.get("evaluated_actor_reference_fingerprint") or "").lower()
        != actor_reference_fingerprint
        or str(gate.get("evaluator_model") or "")
        != str(generation_contract.get("identity_evaluator_model") or "")
        or str(gate.get("evaluator_contract_version") or "")
        != SCENE_IDENTITY_EVALUATOR_CONTRACT_VERSION
        or list(gate.get("blocking_reasons") or [])
    ):
        raise ValidationError(
            "Semantic scene-plate candidate did not pass the current original-actor identity gate."
        )
    return dict(gate)


def validate_approved_scene_plate_identity(
    master: Mapping[str, Any],
    *,
    actor_reference_fingerprint: str,
    generation_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Require current machine evidence plus an explicit attributable human attestation."""
    gate = validate_scene_identity_gate(
        master,
        actor_reference_fingerprint=actor_reference_fingerprint,
        generation_contract=generation_contract,
    )
    approved_by = str(master.get("approved_by") or "").strip()
    approved_at = str(master.get("approved_at") or "").strip()
    try:
        parsed_approved_at = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            "Semantic scene-plate approval timestamp is invalid."
        ) from exc
    if (
        master.get("identity_attestation") is not True
        or str(master.get("attestation_version") or "")
        != SCENE_IDENTITY_ATTESTATION_VERSION
        or not approved_by
        or parsed_approved_at.tzinfo is None
    ):
        raise ValidationError(
            "Semantic scene plate requires an attributable human identity attestation."
        )
    return gate


def select_semantic_wardrobe(
    *,
    post_id: str,
    rotation_index: int | None = None,
    wardrobe_key: str | None = None,
    wardrobe_description: str | None = None,
) -> tuple[str, str]:
    explicit_description = " ".join(str(wardrobe_description or "").split())
    explicit_key = str(wardrobe_key or "").strip()
    if explicit_description:
        return explicit_key or "custom", explicit_description
    if explicit_key in SEMANTIC_WARDROBES:
        return explicit_key, SEMANTIC_WARDROBES[explicit_key]
    keys = tuple(SEMANTIC_WARDROBES)
    if isinstance(rotation_index, int) and not isinstance(rotation_index, bool):
        selected = keys[max(0, rotation_index) % len(keys)]
        return selected, SEMANTIC_WARDROBES[selected]
    digest = sha256(str(post_id or "semantic-video").encode("utf-8")).hexdigest()
    selected = keys[int(digest, 16) % len(keys)]
    return selected, SEMANTIC_WARDROBES[selected]


def build_visual_contract(reference: Mapping[str, Any]) -> dict[str, Any]:
    location = reference.get("location_reference")
    if not isinstance(location, Mapping):
        raise ValidationError("Semantic visual contract requires a location reference.")
    fields = {
        "version": VISUAL_CONTRACT_VERSION,
        "scene_key": str(reference.get("scene_key") or location.get("scene_key") or "").strip(),
        "scene_description": " ".join(str(reference.get("scene_description") or "").split()),
        "wardrobe_key": str(reference.get("wardrobe_key") or "").strip(),
        "wardrobe_description": " ".join(
            str(reference.get("wardrobe_description") or "").split()
        ),
        "wheelchair_description": WHEELCHAIR_VISUAL_CONTRACT,
        "framing_description": FRAMING_CONTRACT,
        "location_reference_sha256": str(location.get("sha256") or "").strip().lower(),
    }
    missing = [
        key
        for key in (
            "scene_key",
            "scene_description",
            "wardrobe_key",
            "wardrobe_description",
            "location_reference_sha256",
        )
        if not fields[key]
    ]
    if missing:
        raise ValidationError(
            "Semantic visual contract is incomplete.",
            {"missing_fields": missing},
        )
    if len(fields["location_reference_sha256"]) != 64:
        raise ValidationError("Semantic visual contract requires a SHA-256 location hash.")
    return {**fields, "contract_hash": _canonical_hash(fields)}


def validate_visual_contract(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("Semantic video planning requires a frozen visual contract.")
    payload = dict(value)
    supplied_hash = str(payload.pop("contract_hash", "")).strip().lower()
    expected = build_visual_contract(
        {
            **payload,
            "location_reference": {
                "scene_key": payload.get("scene_key"),
                "sha256": payload.get("location_reference_sha256"),
            },
        }
    )
    if supplied_hash and supplied_hash != expected["contract_hash"]:
        raise ValidationError("Semantic visual contract hash does not match its contents.")
    return expected


__all__ = [
    "SEMANTIC_WARDROBES",
    "SEMANTIC_LOCATION_ROTATION",
    "SCENE_IDENTITY_EVALUATOR_CONTRACT_VERSION",
    "SCENE_IDENTITY_ATTESTATION_VERSION",
    "SCENE_IDENTITY_COMPONENT_FIELDS",
    "SCENE_PLATE_ASPECT_RATIO",
    "SCENE_PLATE_REFERENCE_ROLE_CONTRACT",
    "VISUAL_CONTRACT_VERSION",
    "build_actor_reference_fingerprint",
    "build_scene_plate_generation_contract",
    "build_visual_contract",
    "select_semantic_wardrobe",
    "validate_scene_plate_generation_contract",
    "validate_scene_identity_gate",
    "validate_approved_scene_plate_identity",
    "validate_visual_contract",
]
