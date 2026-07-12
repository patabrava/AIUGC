"""Gemini gate for acoustic continuity at native-voice jump cuts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Dict, Optional, Sequence, Tuple

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError


ACOUSTIC_QA_RUBRIC_VERSION = "acoustic-seams-v1"
DEFAULT_ACOUSTIC_QA_MODEL = "gemini-2.5-flash"
_BOOLEAN_FIELDS = (
    "no_breath_restart",
    "no_duplicated_breath",
    "no_click",
    "no_room_tone_reset",
    "cadence_continuous",
    "speaker_continuous",
    "evidence_sufficient",
)
_REQUIRED_FIELDS = frozenset((*_BOOLEAN_FIELDS, "confidence", "blocking_reasons", "observed_differences"))

_PROMPT = """Three 1.5-second audio clips follow, ordered by jump-cut seam 0, 1, 2. Each clip is centered on one visual cut in a synthetic German AIUGC performance. Judge only the acoustic transition at the center of each clip. Fail any audible breath restart, duplicated inhale or exhale, click/pop, abrupt room-tone reset, broken speech cadence, speaker discontinuity, or insufficient evidence. Natural continuous breathing and intentional UGC jump-cut timing are acceptable. Do not identify a real person.

Return JSON only with exactly this shape:
{
  "no_breath_restart": true,
  "no_duplicated_breath": true,
  "no_click": true,
  "no_room_tone_reset": true,
  "cadence_continuous": true,
  "speaker_continuous": true,
  "evidence_sufficient": true,
  "confidence": 0.0,
  "blocking_reasons": [],
  "observed_differences": []
}
Use booleans, confidence from 0 through 1, and arrays of specific strings."""


@dataclass(frozen=True)
class AcousticQAReport:
    no_breath_restart: bool
    no_duplicated_breath: bool
    no_click: bool
    no_room_tone_reset: bool
    cadence_continuous: bool
    speaker_continuous: bool
    evidence_sufficient: bool
    confidence: float
    blocking_reasons: Tuple[str, ...]
    observed_differences: Tuple[str, ...]
    passed: bool


def _validate_clips(clips: Sequence[Dict[str, Any]]) -> None:
    if not isinstance(clips, (list, tuple)) or len(clips) != 3:
        raise ValidationError("Acoustic seam QA requires exactly three ordered audio clips.")
    for index, clip in enumerate(clips):
        if not isinstance(clip, dict) or not str(clip.get("mime_type") or "").startswith("audio/"):
            raise ValidationError("Acoustic seam QA requires audio MIME types.", {"seam_index": index})
        if not isinstance(clip.get("media_bytes"), bytes) or not clip["media_bytes"]:
            raise ValidationError("Acoustic seam QA requires non-empty audio bytes.", {"seam_index": index})


def evaluate_acoustic_seam_continuity(
    clips: Sequence[Dict[str, Any]],
    *,
    llm_client: Optional[Any] = None,
    model: Optional[str] = DEFAULT_ACOUSTIC_QA_MODEL,
) -> AcousticQAReport:
    _validate_clips(clips)
    raw = (llm_client or get_llm_client()).generate_gemini_text(
        prompt=_PROMPT,
        model=model,
        temperature=0,
        input_media=list(clips),
    )
    normalized = str(raw or "").strip()
    if normalized.startswith("```json") and normalized.endswith("```"):
        normalized = normalized[7:-3].strip()
    try:
        payload = json.loads(normalized)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("Acoustic seam QA response must contain valid JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != _REQUIRED_FIELDS:
        raise ValidationError("Acoustic seam QA response does not match the required schema.")
    if any(not isinstance(payload[field], bool) for field in _BOOLEAN_FIELDS):
        raise ValidationError("Acoustic seam QA boolean fields must be booleans.")
    confidence = payload["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1:
        raise ValidationError("Acoustic seam QA confidence must be from zero through one.")
    for field in ("blocking_reasons", "observed_differences"):
        if not isinstance(payload[field], list) or any(not isinstance(item, str) for item in payload[field]):
            raise ValidationError("Acoustic seam QA reason fields must be lists of strings.")
    blocking = tuple(payload["blocking_reasons"])
    passed = all(payload[field] for field in _BOOLEAN_FIELDS) and float(confidence) >= 0.85 and not blocking
    return AcousticQAReport(
        **{field: payload[field] for field in _BOOLEAN_FIELDS},
        confidence=float(confidence),
        blocking_reasons=blocking,
        observed_differences=tuple(payload["observed_differences"]),
        passed=passed,
    )


__all__ = [
    "ACOUSTIC_QA_RUBRIC_VERSION",
    "DEFAULT_ACOUSTIC_QA_MODEL",
    "AcousticQAReport",
    "evaluate_acoustic_seam_continuity",
]
