"""Strict Gemini vision gates that keep original actor references authoritative."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import math
from typing import Any, Mapping, Optional, Sequence, Tuple

from PIL import Image, UnidentifiedImageError

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError
from app.features.semantic_videos.visual_contract import (
    SCENE_IDENTITY_EVALUATOR_CONTRACT_VERSION,
)


VIDEO_IDENTITY_EVALUATOR_CONTRACT_VERSION = "semantic-video-identity-v2"
_SCENE_COMPONENTS = (
    "same_person",
    "facial_geometry_consistent",
    "apparent_age_consistent",
    "hairline_and_hair_consistent",
    "skin_texture_natural",
    "not_beautified_or_stylized",
    "no_face_artifacts",
)
_VIDEO_COMPONENTS = (
    *_SCENE_COMPONENTS,
    "speech_facial_transitions_natural",
)
_REFERENCE_PAIR_COMPONENTS = (
    *_SCENE_COMPONENTS,
    "three_quarter_pose",
)
_COMMON_FIELDS = ("confidence", "blocking_reasons", "observed_differences")
_CONFIDENCE_CONSISTENCY_CORRECTION = """

Consistency correction: A prior report set every identity component true and supplied no identity blocker, but
returned confidence below the required threshold because of excluded non-identity differences. Re-evaluate the
same supplied images from scratch. Any confidence below the required threshold must identify the uncertain
identity component by setting it false and naming the specific identity blocker. Otherwise use a confidence at
or above the required threshold. Return only the same strict JSON shape."""

_SCENE_PROMPT = """Images 1 and 2 are immutable original references of the consented actor, in that order.
Image 3 is a generated Semantic UGC scene-plate candidate.

Compare only the supplied images. Judge whether Image 3 preserves the same person, facial geometry, apparent age,
hairline and hair, ordinary natural skin texture, and realistic unretouched appearance from both original
references. Fail any beautification, face averaging, synthetic smoothing, age drift, identity substitution, or
malformed eyes, mouth, teeth, or facial structure. Treat lighting, expression, pose, wardrobe, wheelchair, and
background changes as non-identity differences unless they obscure or alter the face. The confidence value is
confidence in actor-identity preservation only; do not reduce it for an expected change of room, lighting,
camera distance, expression, seated pose, wardrobe, or wheelchair. When every identity component is true and
the only observed differences are those non-identity changes, return identity confidence of at least 0.90.

Return JSON only with exactly this shape:
{
  "same_person": true,
  "facial_geometry_consistent": true,
  "apparent_age_consistent": true,
  "hairline_and_hair_consistent": true,
  "skin_texture_natural": true,
  "not_beautified_or_stylized": true,
  "no_face_artifacts": true,
  "confidence": 0.0,
  "blocking_reasons": [],
  "observed_differences": []
}
Use booleans for every component, a finite confidence number from 0 through 1, and arrays of specific strings."""

_SCENE_BATCH_PROMPT = """Images 1 and 2 are immutable original references of the consented actor, in that order.
Images 3, 4, and 5 are generated Semantic UGC scene-plate candidates 1, 2, and 3, respectively.

Evaluate each candidate independently against both original references. Judge whether each candidate preserves the
same person, facial geometry, apparent age, hairline and hair, ordinary natural skin texture, and realistic
unretouched appearance. Fail any beautification, face averaging, synthetic smoothing, age drift, identity
substitution, or malformed eyes, mouth, teeth, or facial structure. Treat lighting, expression, pose, wardrobe,
wheelchair, and background changes as non-identity differences unless they obscure or alter the face. The confidence
value is confidence in actor-identity preservation only; do not reduce it for an expected change of room, lighting,
camera distance, expression, seated pose, wardrobe, or wheelchair. When every identity component is true and the
only observed differences are those non-identity changes, return identity confidence of at least 0.90.

Return JSON only with exactly this shape and exactly three ordered candidate objects:
{
  "candidates": [
    {
      "candidate_index": 1,
      "same_person": true,
      "facial_geometry_consistent": true,
      "apparent_age_consistent": true,
      "hairline_and_hair_consistent": true,
      "skin_texture_natural": true,
      "not_beautified_or_stylized": true,
      "no_face_artifacts": true,
      "confidence": 0.0,
      "blocking_reasons": [],
      "observed_differences": []
    },
    {
      "candidate_index": 2,
      "same_person": true,
      "facial_geometry_consistent": true,
      "apparent_age_consistent": true,
      "hairline_and_hair_consistent": true,
      "skin_texture_natural": true,
      "not_beautified_or_stylized": true,
      "no_face_artifacts": true,
      "confidence": 0.0,
      "blocking_reasons": [],
      "observed_differences": []
    },
    {
      "candidate_index": 3,
      "same_person": true,
      "facial_geometry_consistent": true,
      "apparent_age_consistent": true,
      "hairline_and_hair_consistent": true,
      "skin_texture_natural": true,
      "not_beautified_or_stylized": true,
      "no_face_artifacts": true,
      "confidence": 0.0,
      "blocking_reasons": [],
      "observed_differences": []
    }
  ]
}
Use booleans for every component, a finite confidence number from 0 through 1, and arrays of specific strings."""

_VIDEO_PROMPT = """Images 1 and 2 are immutable original references of the consented actor, in that order.
Image 3 is a labeled contact sheet sampled from every generated take.

Compare every labeled video frame with both original references. Judge whether every frame preserves the same
person, facial geometry, apparent age, hairline and hair, ordinary natural skin texture, and realistic unretouched
appearance. Inspect speech transitions for face replacement, synthetic smoothing, beautification, and malformed
eyes, mouth, teeth, or facial motion. Contact-sheet labels are QA metadata, not video artifacts.
The confidence value is confidence in actor-identity preservation only; do not reduce it for expected changes
of room, lighting, camera distance, expression, seated pose, wardrobe, wheelchair, or speech pose. When every
identity component is true and the only observed differences are those non-identity changes, return identity
confidence of at least 0.90.
Use this confidence rubric consistently:
- 0.90 through 1.00: every identity component is visibly supported and there are no identity blockers;
- 0.75 through 0.89: at least one identity component is uncertain or obscured, so set that component false and
  name the specific identity uncertainty in blocking_reasons;
- below 0.75: identity is contradicted or cannot be evaluated, with false components and specific blockers.
Do not list room, lighting, camera distance, pose, wardrobe, or wheelchair changes in observed_differences because
they are outside this actor-identity evaluation.

Return JSON only with exactly this shape:
{
  "same_person": true,
  "facial_geometry_consistent": true,
  "apparent_age_consistent": true,
  "hairline_and_hair_consistent": true,
  "skin_texture_natural": true,
  "not_beautified_or_stylized": true,
  "no_face_artifacts": true,
  "speech_facial_transitions_natural": true,
  "confidence": 0.0,
  "blocking_reasons": [],
  "observed_differences": []
}
Use booleans for every component, a finite confidence number from 0 through 1, and arrays of specific strings."""

_REFERENCE_PAIR_PROMPT = """Image 1 is the canonical frontal identity authority for the consented actor.
Image 2 is a generated three-quarter reference that must depict that exact same person.

Compare only the supplied images. Fail any change to facial geometry, skull or jaw proportions, eyes, eyebrows,
nose, lips, ears, hairline, hair, apparent age, skin tone, natural skin texture, asymmetry, or body proportions.
Fail beautification, face averaging, synthetic smoothing, identity substitution, or facial artifacts. Require
Image 2 to show a clear approximately 30-degree three-quarter head view while retaining an identity-readable face.
Treat the intended viewpoint change as non-identity variation.

Return JSON only with exactly this shape:
{
  "same_person": true,
  "facial_geometry_consistent": true,
  "apparent_age_consistent": true,
  "hairline_and_hair_consistent": true,
  "skin_texture_natural": true,
  "not_beautified_or_stylized": true,
  "no_face_artifacts": true,
  "three_quarter_pose": true,
  "confidence": 0.0,
  "blocking_reasons": [],
  "observed_differences": []
}
Use booleans for every component, a finite confidence number from 0 through 1, and arrays of specific strings."""


@dataclass(frozen=True)
class SceneIdentityQAReport:
    same_person: bool
    facial_geometry_consistent: bool
    apparent_age_consistent: bool
    hairline_and_hair_consistent: bool
    skin_texture_natural: bool
    not_beautified_or_stylized: bool
    no_face_artifacts: bool
    confidence: float
    blocking_reasons: Tuple[str, ...]
    observed_differences: Tuple[str, ...]
    passed: bool


@dataclass(frozen=True)
class VideoIdentityQAReport:
    same_person: bool
    facial_geometry_consistent: bool
    apparent_age_consistent: bool
    hairline_and_hair_consistent: bool
    skin_texture_natural: bool
    not_beautified_or_stylized: bool
    no_face_artifacts: bool
    speech_facial_transitions_natural: bool
    confidence: float
    blocking_reasons: Tuple[str, ...]
    observed_differences: Tuple[str, ...]
    passed: bool


@dataclass(frozen=True)
class ActorReferencePairQAReport:
    same_person: bool
    facial_geometry_consistent: bool
    apparent_age_consistent: bool
    hairline_and_hair_consistent: bool
    skin_texture_natural: bool
    not_beautified_or_stylized: bool
    no_face_artifacts: bool
    three_quarter_pose: bool
    confidence: float
    blocking_reasons: Tuple[str, ...]
    observed_differences: Tuple[str, ...]
    passed: bool


def _validated_image(image: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    if not isinstance(image, Mapping):
        raise ValidationError("Actor identity QA image inputs must be mappings.", {"image": label})
    mime_type = str(image.get("mime_type") or "").strip().lower()
    image_bytes = image.get("image_bytes")
    if not mime_type.startswith("image/") or not isinstance(image_bytes, bytes) or not image_bytes:
        raise ValidationError(
            "Actor identity QA requires non-empty image bytes and an image MIME type.",
            {"image": label, "mime_type": mime_type},
        )
    expected_length = image.get("byte_length")
    if expected_length is not None and int(expected_length) != len(image_bytes):
        raise ValidationError(
            "Actor identity QA image byte length changed.",
            {"image": label, "expected": expected_length, "actual": len(image_bytes)},
        )
    actual_hash = sha256(image_bytes).hexdigest()
    expected_hash = str(image.get("sha256") or "").strip().lower()
    if expected_hash and expected_hash != actual_hash:
        raise ValidationError(
            "Actor identity QA image hash changed.",
            {"image": label, "expected": expected_hash, "actual": actual_hash},
        )
    return {"mime_type": mime_type, "image_bytes": image_bytes}


def _compact_batch_identity_image(image: dict[str, Any]) -> dict[str, Any]:
    """Bound five-image QA payloads while retaining identity-readable detail."""
    try:
        with Image.open(BytesIO(image["image_bytes"])) as source:
            normalized = source.convert("RGB")
            normalized.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            output = BytesIO()
            normalized.save(output, format="JPEG", quality=92, optimize=True)
    except (UnidentifiedImageError, OSError):
        # Validation fixtures may use opaque bytes; production images are
        # decoded and compacted. The original boundary checks still apply.
        return image
    return {"mime_type": "image/jpeg", "image_bytes": output.getvalue()}


def _parse_report(
    raw_response: Any,
    *,
    component_fields: Sequence[str],
    minimum_confidence: float,
) -> dict[str, Any]:
    if not isinstance(minimum_confidence, (int, float)) or isinstance(
        minimum_confidence, bool
    ):
        raise ValidationError("Actor identity confidence threshold must be numeric.")
    threshold = float(minimum_confidence)
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValidationError("Actor identity confidence threshold must be finite from 0 through 1.")
    normalized = str(raw_response or "").strip()
    lines = normalized.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() in {"```", "```json"}
        and lines[-1].strip() == "```"
    ):
        normalized = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(normalized)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("Actor identity QA response must contain valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValidationError("Actor identity QA JSON must be an object.")
    required = frozenset((*component_fields, *_COMMON_FIELDS))
    if set(payload) != required:
        raise ValidationError(
            "Actor identity QA response does not match the strict schema.",
            {
                "missing_fields": sorted(required - payload.keys()),
                "unexpected_fields": sorted(payload.keys() - required),
            },
        )
    invalid_booleans = [
        field for field in component_fields if not isinstance(payload[field], bool)
    ]
    if invalid_booleans:
        raise ValidationError(
            "Actor identity QA component fields must be booleans.",
            {"invalid_fields": invalid_booleans},
        )
    raw_confidence = payload["confidence"]
    if not isinstance(raw_confidence, (int, float)) or isinstance(raw_confidence, bool):
        raise ValidationError("Actor identity QA confidence must be numeric.")
    confidence = float(raw_confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValidationError("Actor identity QA confidence must be finite from 0 through 1.")
    for field in ("blocking_reasons", "observed_differences"):
        if not isinstance(payload[field], list) or any(
            not isinstance(item, str) for item in payload[field]
        ):
            raise ValidationError(
                "Actor identity QA reasons and differences must be lists of strings.",
                {"invalid_field": field},
            )
    blocking_reasons = tuple(payload["blocking_reasons"])
    return {
        **{field: payload[field] for field in component_fields},
        "confidence": confidence,
        "blocking_reasons": blocking_reasons,
        "observed_differences": tuple(payload["observed_differences"]),
        "passed": bool(
            all(payload[field] for field in component_fields)
            and confidence >= threshold
            and not blocking_reasons
        ),
    }


def _evaluate_report_with_consistency_retry(
    *,
    llm_client: Any,
    prompt: str,
    model: str,
    images: Sequence[Mapping[str, Any]],
    component_fields: Sequence[str],
    minimum_confidence: float,
    location: Optional[str] = None,
) -> dict[str, Any]:
    raw = llm_client.generate_gemini_text(
        prompt=prompt,
        model=model,
        temperature=0,
        input_images=images,
        location=location,
    )
    parsed = _parse_report(
        raw,
        component_fields=component_fields,
        minimum_confidence=minimum_confidence,
    )
    internally_inconsistent_low_confidence = bool(
        all(parsed[field] for field in component_fields)
        and not parsed["blocking_reasons"]
        and parsed["confidence"] < float(minimum_confidence)
    )
    if not internally_inconsistent_low_confidence:
        return parsed
    corrected_raw = llm_client.generate_gemini_text(
        prompt=prompt + _CONFIDENCE_CONSISTENCY_CORRECTION,
        model=model,
        temperature=0,
        input_images=images,
        location=location,
    )
    return _parse_report(
        corrected_raw,
        component_fields=component_fields,
        minimum_confidence=minimum_confidence,
    )


def _parse_scene_batch_reports(
    raw_response: Any,
    *,
    candidate_indexes: Sequence[int],
    minimum_confidence: float,
) -> tuple[dict[str, Any], ...]:
    normalized = str(raw_response or "").strip()
    lines = normalized.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() in {"```", "```json"}
        and lines[-1].strip() == "```"
    ):
        normalized = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(normalized)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("Actor identity QA response must contain valid JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != {"candidates"}:
        raise ValidationError("Batched actor identity QA JSON does not match the strict schema.")
    rows = payload["candidates"]
    if not isinstance(rows, list) or len(rows) != len(candidate_indexes):
        raise ValidationError(
            "Batched actor identity QA returned an unexpected candidate count.",
            {"expected": len(candidate_indexes)},
        )
    parsed_reports = []
    for expected_index, row in zip(candidate_indexes, rows):
        if not isinstance(row, dict) or row.get("candidate_index") != expected_index:
            raise ValidationError(
                "Batched actor identity QA returned candidates out of order.",
                {"expected_candidate_index": expected_index},
            )
        report_payload = {key: value for key, value in row.items() if key != "candidate_index"}
        parsed_reports.append(
            _parse_report(
                json.dumps(report_payload),
                component_fields=_SCENE_COMPONENTS,
                minimum_confidence=minimum_confidence,
            )
        )
    return tuple(parsed_reports)


def evaluate_scene_plate_identities(
    actor_front: Mapping[str, Any],
    actor_three_quarter: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    llm_client: Optional[Any] = None,
    model: str,
    minimum_confidence: float = 0.90,
    location: Optional[str] = None,
) -> tuple[SceneIdentityQAReport, ...]:
    """Evaluate a complete three-candidate set in one multimodal provider call."""
    if len(candidates) != 3:
        raise ValidationError("Batched scene identity QA requires exactly three candidates.")
    images = [
        _compact_batch_identity_image(
            _validated_image(actor_front, label="actor_front")
        ),
        _compact_batch_identity_image(
            _validated_image(actor_three_quarter, label="actor_three_quarter")
        ),
        *(
            _compact_batch_identity_image(
                _validated_image(candidate, label=f"candidate_{index}")
            )
            for index, candidate in enumerate(candidates, start=1)
        ),
    ]
    client = llm_client or get_llm_client()
    raw = client.generate_gemini_text(
        prompt=_SCENE_BATCH_PROMPT,
        model=model,
        temperature=0,
        input_images=images,
        location=location,
    )
    parsed = _parse_scene_batch_reports(
        raw,
        candidate_indexes=(1, 2, 3),
        minimum_confidence=minimum_confidence,
    )
    if any(
        all(report[field] for field in _SCENE_COMPONENTS)
        and not report["blocking_reasons"]
        and report["confidence"] < float(minimum_confidence)
        for report in parsed
    ):
        corrected_raw = client.generate_gemini_text(
            prompt=_SCENE_BATCH_PROMPT + _CONFIDENCE_CONSISTENCY_CORRECTION,
            model=model,
            temperature=0,
            input_images=images,
            location=location,
        )
        parsed = _parse_scene_batch_reports(
            corrected_raw,
            candidate_indexes=(1, 2, 3),
            minimum_confidence=minimum_confidence,
        )
    return tuple(SceneIdentityQAReport(**report) for report in parsed)


def evaluate_scene_plate_identity(
    actor_front: Mapping[str, Any],
    actor_three_quarter: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    llm_client: Optional[Any] = None,
    model: str,
    minimum_confidence: float = 0.90,
    location: Optional[str] = None,
) -> SceneIdentityQAReport:
    images = [
        _validated_image(actor_front, label="actor_front"),
        _validated_image(actor_three_quarter, label="actor_three_quarter"),
        _validated_image(candidate, label="candidate"),
    ]
    return SceneIdentityQAReport(
        **_evaluate_report_with_consistency_retry(
            llm_client=llm_client or get_llm_client(),
            prompt=_SCENE_PROMPT,
            model=model,
            images=images,
            component_fields=_SCENE_COMPONENTS,
            minimum_confidence=minimum_confidence,
            location=location,
        )
    )


def evaluate_video_actor_identity(
    actor_front: Mapping[str, Any],
    actor_three_quarter: Mapping[str, Any],
    contact_sheet: Mapping[str, Any],
    *,
    llm_client: Optional[Any] = None,
    model: str,
    minimum_confidence: float = 0.90,
) -> VideoIdentityQAReport:
    images = [
        _validated_image(actor_front, label="actor_front"),
        _validated_image(actor_three_quarter, label="actor_three_quarter"),
        _validated_image(contact_sheet, label="contact_sheet"),
    ]
    return VideoIdentityQAReport(
        **_evaluate_report_with_consistency_retry(
            llm_client=llm_client or get_llm_client(),
            prompt=_VIDEO_PROMPT,
            model=model,
            images=images,
            component_fields=_VIDEO_COMPONENTS,
            minimum_confidence=minimum_confidence,
        )
    )


def evaluate_actor_reference_pair(
    actor_front: Mapping[str, Any],
    actor_three_quarter: Mapping[str, Any],
    *,
    llm_client: Optional[Any] = None,
    model: str,
    minimum_confidence: float = 0.90,
) -> ActorReferencePairQAReport:
    images = [
        _validated_image(actor_front, label="actor_front"),
        _validated_image(actor_three_quarter, label="actor_three_quarter"),
    ]
    return ActorReferencePairQAReport(
        **_evaluate_report_with_consistency_retry(
            llm_client=llm_client or get_llm_client(),
            prompt=_REFERENCE_PAIR_PROMPT,
            model=model,
            images=images,
            component_fields=_REFERENCE_PAIR_COMPONENTS,
            minimum_confidence=minimum_confidence,
        )
    )


def failed_scene_identity_result(
    *,
    evaluator_model: str,
    actor_reference_fingerprint: str,
    candidate_sha256: str,
    reason_code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "passed": False,
        "evaluator_model": evaluator_model,
        "evaluator_contract_version": SCENE_IDENTITY_EVALUATOR_CONTRACT_VERSION,
        "evaluated_actor_reference_fingerprint": actor_reference_fingerprint,
        "candidate_sha256": candidate_sha256,
        "component_results": {},
        "confidence": 0.0,
        "blocking_reasons": [reason_code],
        "observed_differences": [str(message)[:300]],
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


def scene_identity_result_metadata(
    report: SceneIdentityQAReport,
    *,
    evaluator_model: str,
    actor_reference_fingerprint: str,
    candidate_sha256: str,
) -> dict[str, Any]:
    values = asdict(report)
    return {
        "status": "passed" if report.passed else "failed",
        "passed": report.passed,
        "evaluator_model": evaluator_model,
        "evaluator_contract_version": SCENE_IDENTITY_EVALUATOR_CONTRACT_VERSION,
        "evaluated_actor_reference_fingerprint": actor_reference_fingerprint,
        "candidate_sha256": candidate_sha256,
        "component_results": {
            field: values[field] for field in _SCENE_COMPONENTS
        },
        "confidence": report.confidence,
        "blocking_reasons": list(report.blocking_reasons),
        "observed_differences": list(report.observed_differences),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "ActorReferencePairQAReport",
    "SceneIdentityQAReport",
    "VIDEO_IDENTITY_EVALUATOR_CONTRACT_VERSION",
    "VideoIdentityQAReport",
    "evaluate_scene_plate_identity",
    "evaluate_actor_reference_pair",
    "evaluate_video_actor_identity",
    "failed_scene_identity_result",
    "scene_identity_result_metadata",
]
