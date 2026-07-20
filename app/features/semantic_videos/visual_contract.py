"""Structured visual contract shared by Semantic Manual and Semantic UGC."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from app.core.errors import ValidationError
from app.features.shot_frames.wheelchair_scene_plate import (
    FRAMING_CONTRACT,
    WHEELCHAIR_VISUAL_CONTRACT,
)


VISUAL_CONTRACT_VERSION = "semantic_visual_contract_v1"
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
    "VISUAL_CONTRACT_VERSION",
    "build_actor_reference_fingerprint",
    "build_visual_contract",
    "select_semantic_wardrobe",
    "validate_visual_contract",
]
