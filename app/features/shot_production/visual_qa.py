"""Gemini vision gate for scene continuity across independent semantic takes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Dict, Optional, Tuple

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError


_COMPONENT_FIELDS = (
    "wardrobe_consistent",
    "room_consistent",
    "wheelchair_consistent",
    "framing_stable",
    "no_artifacts",
)
_REQUIRED_FIELDS = frozenset(
    (*_COMPONENT_FIELDS, "confidence", "blocking_reasons", "observed_differences")
)
_ALLOWED_FIELDS = _REQUIRED_FIELDS | {"passed"}

_VISUAL_QA_PROMPT = """Image 1 is the approved scene-plate master reference.
Image 2 is the labeled multi-frame contact sheet extracted from the generated takes.

Compare only the supplied references and judge whether they preserve the exact wardrobe and location/background
shown in the approved scene plate, the same manual wheelchair, stable UGC framing, and no visual artifacts.
Actor identity is evaluated separately against the immutable original actor references. These takes use a tight
arm's-length selfie crop, so the manual wheelchair is often partly or entirely outside the frame; that is expected
and is not a defect. Set wheelchair_consistent=false only if the wheelchair is visible and its design changes
between frames, or if the actor appears standing or walking. Do not set it false merely because the wheelchair is
cropped out or not visible. Inspect every labeled frame carefully, especially the lower third. Set no_artifacts=false if any
raw frame contains baked-in captions, subtitles, logos, watermarks, letters, symbols, malformed glyphs, or gibberish
text; identify the take and frame label in blocking_reasons. The contact-sheet labels above each frame are QA metadata
and are not artifacts inside the video.

For framing_stable, allow small fixed crop differences between takes plus natural speaking head movement, blinking,
and expression changes. These takes are shot handheld at arm's length on purpose, so also allow continuous
low-amplitude handheld micro-motion: small irregular drift, slight roll, and tiny arm corrections that shift the
frame by a couple of percent without changing the composition. Treat each delivered-tail frame as the final eligible
frame that can appear in the delivery and compare it closely with the preceding final-word frame. Fail framing only
for a material composition change or a deliberate camera move: a sustained directional pan, tilt, dolly, orbit,
push-in, pull-back, continuous zoom, or reframe that accumulates in one direction rather than drifting and
returning. Report every observed difference, including natural expression or pose changes.
This is scene-continuity comparison only.

Return JSON only, without Markdown or commentary, using exactly this shape:
{
  "wardrobe_consistent": true,
  "room_consistent": true,
  "wheelchair_consistent": true,
  "framing_stable": true,
  "no_artifacts": true,
  "confidence": 0.0,
  "blocking_reasons": ["specific blocking mismatch"],
  "observed_differences": ["specific visible difference"]
}
Use booleans for every component, a confidence number from 0 through 1, and arrays of strings for both lists."""


@dataclass(frozen=True)
class SceneContinuityQAReport:
    wardrobe_consistent: bool
    room_consistent: bool
    wheelchair_consistent: bool
    framing_stable: bool
    no_artifacts: bool
    confidence: float
    blocking_reasons: Tuple[str, ...]
    observed_differences: Tuple[str, ...]
    passed: bool


def _validate_image_input(image: Any, *, label: str) -> None:
    if not isinstance(image, dict):
        raise ValidationError(
            "Visual QA image inputs must be mappings with MIME type and bytes.",
            {"image": label},
        )
    mime_type = str(image.get("mime_type") or "").strip()
    image_bytes = image.get("image_bytes")
    if not mime_type.startswith("image/") or not isinstance(image_bytes, bytes) or not image_bytes:
        raise ValidationError(
            "Visual QA image inputs require non-empty bytes and an image MIME type.",
            {"image": label, "mime_type": mime_type},
        )


def evaluate_visual_consistency(
    master_image: Dict[str, Any],
    contact_sheet: Dict[str, Any],
    llm_client: Optional[Any] = None,
    model: Optional[str] = None,
) -> SceneContinuityQAReport:
    """Compare a generated take contact sheet with its approved scene plate."""
    _validate_image_input(master_image, label="approved_master")
    _validate_image_input(contact_sheet, label="contact_sheet")
    client = llm_client or get_llm_client()
    raw_response = client.generate_gemini_text(
        prompt=_VISUAL_QA_PROMPT,
        model=model,
        temperature=0,
        input_images=[master_image, contact_sheet],
    )
    normalized_response = str(raw_response or "").strip()
    response_lines = normalized_response.splitlines()
    if (
        len(response_lines) >= 3
        and response_lines[0].strip().lower() in {"```", "```json"}
        and response_lines[-1].strip() == "```"
    ):
        normalized_response = "\n".join(response_lines[1:-1]).strip()
    try:
        payload = json.loads(normalized_response)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationError(
            "Visual QA response must contain valid JSON.",
            {"error": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise ValidationError("Visual QA JSON must be an object.")
    missing_fields = sorted(_REQUIRED_FIELDS - payload.keys())
    if missing_fields:
        raise ValidationError(
            "Visual QA response does not match the required schema.",
            {"missing_fields": missing_fields},
        )
    unexpected_fields = sorted(payload.keys() - _ALLOWED_FIELDS)
    if unexpected_fields:
        raise ValidationError(
            "Visual QA response does not match the required schema.",
            {"unexpected_fields": unexpected_fields},
        )
    invalid_boolean_fields = [
        field for field in _COMPONENT_FIELDS if not isinstance(payload[field], bool)
    ]
    if "passed" in payload and not isinstance(payload["passed"], bool):
        invalid_boolean_fields.append("passed")
    if invalid_boolean_fields:
        raise ValidationError(
            "Visual QA boolean fields must contain boolean values.",
            {"invalid_fields": invalid_boolean_fields},
        )
    raw_confidence = payload["confidence"]
    if not isinstance(raw_confidence, (int, float)) or isinstance(raw_confidence, bool):
        raise ValidationError("Visual QA confidence must be a finite number from 0 through 1.")
    confidence = float(raw_confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValidationError(
            "Visual QA confidence must be a finite number from 0 through 1.",
            {"confidence": raw_confidence},
        )
    invalid_list_fields = [
        field
        for field in ("blocking_reasons", "observed_differences")
        if not isinstance(payload[field], list)
        or any(not isinstance(item, str) for item in payload[field])
    ]
    if invalid_list_fields:
        raise ValidationError(
            "Visual QA blocking reasons and observed differences must be lists of strings.",
            {"invalid_fields": invalid_list_fields},
        )
    blocking_reasons = tuple(payload["blocking_reasons"])
    observed_differences = tuple(payload["observed_differences"])
    passed = (
        all(payload[field] for field in _COMPONENT_FIELDS)
        and confidence >= 0.75
        and not blocking_reasons
    )
    return SceneContinuityQAReport(
        wardrobe_consistent=payload["wardrobe_consistent"],
        room_consistent=payload["room_consistent"],
        wheelchair_consistent=payload["wheelchair_consistent"],
        framing_stable=payload["framing_stable"],
        no_artifacts=payload["no_artifacts"],
        confidence=confidence,
        blocking_reasons=blocking_reasons,
        observed_differences=observed_differences,
        passed=passed,
    )


VisualQAReport = SceneContinuityQAReport
evaluate_scene_continuity = evaluate_visual_consistency


__all__ = [
    "SceneContinuityQAReport",
    "VisualQAReport",
    "evaluate_scene_continuity",
    "evaluate_visual_consistency",
]
