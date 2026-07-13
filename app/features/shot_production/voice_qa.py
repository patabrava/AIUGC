"""Gemini audio gate for voice consistency across independent semantic takes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Dict, Optional, Sequence, Tuple

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError


VOICE_QA_RUBRIC_VERSION = "voice-continuity-v1"
DEFAULT_VOICE_QA_MODEL = "gemini-2.5-flash"

_BLOCKING_COMPONENT_FIELDS = (
    "same_speaker_across_takes",
    "vocal_timbre_consistent",
    "apparent_vocal_age_consistent",
    "german_accent_consistent",
    "evidence_sufficient",
    "single_speaker_each_clip",
    "no_music",
    "no_background_voices",
)
_BOOLEAN_FIELDS = (*_BLOCKING_COMPONENT_FIELDS, "delivery_style_consistent")
_REQUIRED_FIELDS = frozenset(
    (
        *_BOOLEAN_FIELDS,
        "outlier_take_indexes",
        "confidence",
        "blocking_reasons",
        "observed_differences",
    )
)
_ALLOWED_FIELDS = _REQUIRED_FIELDS | {"passed"}

_VOICE_QA_PROMPT = """Four complete raw-take audio clips follow this text in take order 0, 1, 2, 3.
They are consecutive semantic beats from one synthetic German AIUGC performance.

Compare only the supplied clips. Decide whether the relative voice characteristics are consistent with the same
synthetic speaker across all takes. Judge vocal timbre, apparent vocal age, and German accent. Confirm that the audio
is sufficient for that judgment and that every complete clip contains only one foreground speaker, no music, and no
background voices. Different wording, emphasis, pace, loudness, cadence, and emotion are expected between semantic
beats; record material delivery differences, but do not make ordinary variation blocking. If one or more clips are
voice outliers, list their zero-based take indexes. Do not identify or infer any real person; this is continuity QA only.

Return JSON only, without Markdown or commentary, using exactly this shape:
{
  "same_speaker_across_takes": true,
  "vocal_timbre_consistent": true,
  "apparent_vocal_age_consistent": true,
  "german_accent_consistent": true,
  "evidence_sufficient": true,
  "delivery_style_consistent": true,
  "single_speaker_each_clip": true,
  "no_music": true,
  "no_background_voices": true,
  "outlier_take_indexes": [],
  "confidence": 0.0,
  "blocking_reasons": ["specific blocking mismatch"],
  "observed_differences": ["specific non-blocking difference"]
}
Use booleans for every component, unique zero-based integer indexes from 0 through 3 for outliers, a confidence
number from 0 through 1, and arrays of strings for both reason lists."""


@dataclass(frozen=True)
class VoiceQAReport:
    same_speaker_across_takes: bool
    vocal_timbre_consistent: bool
    apparent_vocal_age_consistent: bool
    german_accent_consistent: bool
    evidence_sufficient: bool
    delivery_style_consistent: bool
    single_speaker_each_clip: bool
    no_music: bool
    no_background_voices: bool
    outlier_take_indexes: Tuple[int, ...]
    confidence: float
    blocking_reasons: Tuple[str, ...]
    observed_differences: Tuple[str, ...]
    passed: bool
    status: str = "evaluated"


def _validate_audio_clips(audio_clips: Sequence[Dict[str, Any]]) -> None:
    if not isinstance(audio_clips, (list, tuple)) or len(audio_clips) < 1:
        raise ValidationError(
            "Voice QA requires at least one ordered audio clip.",
            {"clip_count": len(audio_clips) if isinstance(audio_clips, (list, tuple)) else None},
        )
    for index, clip in enumerate(audio_clips):
        if not isinstance(clip, dict):
            raise ValidationError("Voice QA audio clips must be mappings.", {"take_index": index})
        mime_type = str(clip.get("mime_type") or "").strip()
        media_bytes = clip.get("media_bytes")
        if not mime_type.startswith("audio/") or not isinstance(media_bytes, bytes) or not media_bytes:
            raise ValidationError(
                "Voice QA requires non-empty audio bytes and an audio MIME type.",
                {"take_index": index, "mime_type": mime_type},
            )


def _parse_json_response(raw_response: Any) -> Dict[str, Any]:
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
        raise ValidationError(
            "Voice QA response must contain valid JSON.",
            {"error": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise ValidationError("Voice QA JSON must be an object.")
    return payload


def evaluate_voice_consistency(
    audio_clips: Sequence[Dict[str, Any]],
    *,
    llm_client: Optional[Any] = None,
    model: Optional[str] = DEFAULT_VOICE_QA_MODEL,
) -> VoiceQAReport:
    """Evaluate four ordered full-take clips as one continuous UGC voice performance."""
    _validate_audio_clips(audio_clips)
    clip_count = len(audio_clips)
    if clip_count == 1:
        return VoiceQAReport(
            same_speaker_across_takes=True,
            vocal_timbre_consistent=True,
            apparent_vocal_age_consistent=True,
            german_accent_consistent=True,
            evidence_sufficient=True,
            delivery_style_consistent=True,
            single_speaker_each_clip=True,
            no_music=True,
            no_background_voices=True,
            outlier_take_indexes=(),
            confidence=1.0,
            blocking_reasons=(),
            observed_differences=(),
            passed=True,
            status="not_applicable",
        )
    take_order = ", ".join(str(index) for index in range(clip_count))
    prompt = _VOICE_QA_PROMPT.replace(
        "Four complete raw-take audio clips follow this text in take order 0, 1, 2, 3.",
        f"{clip_count} complete raw-take audio clips follow this text in take order {take_order}.",
    ).replace(
        "unique zero-based integer indexes from 0 through 3 for outliers",
        f"unique zero-based take indexes from 0 through {clip_count - 1} for outliers",
    )
    client = llm_client or get_llm_client()
    payload = _parse_json_response(
        client.generate_gemini_text(
            prompt=prompt,
            model=model,
            temperature=0,
            input_media=list(audio_clips),
        )
    )
    missing_fields = sorted(_REQUIRED_FIELDS - payload.keys())
    unexpected_fields = sorted(payload.keys() - _ALLOWED_FIELDS)
    if missing_fields or unexpected_fields:
        raise ValidationError(
            "Voice QA response does not match the required schema.",
            {"missing_fields": missing_fields, "unexpected_fields": unexpected_fields},
        )
    invalid_boolean_fields = [
        field for field in _BOOLEAN_FIELDS if not isinstance(payload[field], bool)
    ]
    if "passed" in payload and not isinstance(payload["passed"], bool):
        invalid_boolean_fields.append("passed")
    if invalid_boolean_fields:
        raise ValidationError(
            "Voice QA boolean fields must contain boolean values.",
            {"invalid_fields": invalid_boolean_fields},
        )
    raw_confidence = payload["confidence"]
    if not isinstance(raw_confidence, (int, float)) or isinstance(raw_confidence, bool):
        raise ValidationError("Voice QA confidence must be a finite number from 0 through 1.")
    confidence = float(raw_confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValidationError(
            "Voice QA confidence must be a finite number from 0 through 1.",
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
            "Voice QA reasons and differences must be lists of strings.",
            {"invalid_fields": invalid_list_fields},
        )
    raw_outliers = payload["outlier_take_indexes"]
    if (
        not isinstance(raw_outliers, list)
        or any(not isinstance(index, int) or isinstance(index, bool) for index in raw_outliers)
        or any(index < 0 or index >= clip_count for index in raw_outliers)
        or raw_outliers != sorted(set(raw_outliers))
    ):
        raise ValidationError(
            f"Voice QA outlier take indexes must be unique ordered integers from 0 through {clip_count - 1}.",
            {"outlier_take_indexes": raw_outliers},
        )
    blocking_reasons = tuple(payload["blocking_reasons"])
    observed_differences = tuple(payload["observed_differences"])
    outlier_take_indexes = tuple(raw_outliers)
    passed = (
        all(payload[field] for field in _BLOCKING_COMPONENT_FIELDS)
        and confidence >= 0.85
        and not blocking_reasons
        and not outlier_take_indexes
    )
    return VoiceQAReport(
        same_speaker_across_takes=payload["same_speaker_across_takes"],
        vocal_timbre_consistent=payload["vocal_timbre_consistent"],
        apparent_vocal_age_consistent=payload["apparent_vocal_age_consistent"],
        german_accent_consistent=payload["german_accent_consistent"],
        evidence_sufficient=payload["evidence_sufficient"],
        delivery_style_consistent=payload["delivery_style_consistent"],
        single_speaker_each_clip=payload["single_speaker_each_clip"],
        no_music=payload["no_music"],
        no_background_voices=payload["no_background_voices"],
        outlier_take_indexes=outlier_take_indexes,
        confidence=confidence,
        blocking_reasons=blocking_reasons,
        observed_differences=observed_differences,
        passed=passed,
    )


__all__ = [
    "DEFAULT_VOICE_QA_MODEL",
    "VOICE_QA_RUBRIC_VERSION",
    "VoiceQAReport",
    "evaluate_voice_consistency",
]
