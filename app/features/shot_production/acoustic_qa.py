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
_REQUIRED_FIELDS = frozenset(
    (*_BOOLEAN_FIELDS, "confidence", "blocking_reasons", "observed_differences", "seam_verdicts")
)

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
  "observed_differences": [],
  "seam_verdicts": [
    {"seam_index": 0, "passed": true, "blocking_reasons": []}
  ]
}
Use booleans, confidence from 0 through 1, and arrays of specific strings. Return exactly one seam_verdicts item for every supplied seam index, in order. A seam verdict passes only when its blocking_reasons is empty."""


@dataclass(frozen=True)
class AcousticSeamVerdict:
    seam_index: int
    passed: bool
    blocking_reasons: Tuple[str, ...]


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
    seam_verdicts: Tuple[AcousticSeamVerdict, ...] = ()
    status: str = "evaluated"


def _validate_clips(clips: Sequence[Dict[str, Any]]) -> None:
    if not isinstance(clips, (list, tuple)):
        raise ValidationError("Acoustic seam QA clips must be an ordered sequence.")
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
    seam_count = len(clips)
    if seam_count == 0:
        return AcousticQAReport(
            no_breath_restart=True,
            no_duplicated_breath=True,
            no_click=True,
            no_room_tone_reset=True,
            cadence_continuous=True,
            speaker_continuous=True,
            evidence_sufficient=True,
            confidence=1.0,
            blocking_reasons=(),
            observed_differences=(),
            passed=True,
            seam_verdicts=(),
            status="not_applicable",
        )
    prompt = _PROMPT.replace(
        "Three 1.5-second audio clips follow, ordered by jump-cut seam 0, 1, 2.",
        f"{seam_count} 1.5-second audio clips follow, ordered by jump-cut seam 0 through {seam_count - 1}.",
    )
    raw = (llm_client or get_llm_client()).generate_gemini_text(
        prompt=prompt,
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
    raw_verdicts = payload["seam_verdicts"]
    if not isinstance(raw_verdicts, list) or len(raw_verdicts) != seam_count:
        raise ValidationError("Acoustic seam QA requires one seam verdict per supplied clip.")
    verdicts = []
    for expected_index, raw_verdict in enumerate(raw_verdicts):
        if not isinstance(raw_verdict, dict) or set(raw_verdict) != {
            "seam_index",
            "passed",
            "blocking_reasons",
        }:
            raise ValidationError("Acoustic seam QA seam verdict does not match the required schema.")
        reasons = raw_verdict["blocking_reasons"]
        if (
            isinstance(raw_verdict["seam_index"], bool)
            or raw_verdict["seam_index"] != expected_index
            or not isinstance(raw_verdict["passed"], bool)
            or not isinstance(reasons, list)
            or any(not isinstance(reason, str) for reason in reasons)
            or raw_verdict["passed"] != (not reasons)
        ):
            raise ValidationError("Acoustic seam QA seam verdict is invalid or out of order.")
        verdicts.append(
            AcousticSeamVerdict(
                seam_index=expected_index,
                passed=raw_verdict["passed"],
                blocking_reasons=tuple(reasons),
            )
        )
    blocking = tuple(payload["blocking_reasons"])
    passed = (
        all(payload[field] for field in _BOOLEAN_FIELDS)
        and float(confidence) >= 0.85
        and not blocking
        and all(verdict.passed for verdict in verdicts)
    )
    return AcousticQAReport(
        **{field: payload[field] for field in _BOOLEAN_FIELDS},
        confidence=float(confidence),
        blocking_reasons=blocking,
        observed_differences=tuple(payload["observed_differences"]),
        passed=passed,
        seam_verdicts=tuple(verdicts),
    )


__all__ = [
    "ACOUSTIC_QA_RUBRIC_VERSION",
    "DEFAULT_ACOUSTIC_QA_MODEL",
    "AcousticQAReport",
    "AcousticSeamVerdict",
    "evaluate_acoustic_seam_continuity",
]
