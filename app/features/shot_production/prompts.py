"""Compile first-frame-led Veo requests for independent semantic UGC takes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Sequence, Tuple

from app.core.errors import ValidationError
from app.features.shot_production.planner import EditorialBeat
from app.features.shot_production.shot_deck import ShotVariant


VEO_MODEL = "veo-3.1-generate-001"
VEO_ASPECT_RATIO = "9:16"
SUPPORTED_DURATIONS = frozenset({4, 6, 8})
_REQUIRED_NEGATIVE_LOCKS = (
    "face change",
    "age change",
    "hair change",
    "wardrobe change",
    "room change",
    "extra person",
    "zoom",
    "push-in",
    "reframe",
    "posture reset",
    "generated text",
    "subtitles",
    "music",
    "background voices",
    "extra speech",
    "hands entering frame",
    "repeated dialogue",
    "english speech",
    "logos",
    "watermarks",
    "gibberish text",
)
EFFECTIVE_NEGATIVE_PROMPT = (
    "face change, age change, hair change, wardrobe change, room change, extra person, "
    "camera zoom, push-in, reframe, posture reset, generated text, subtitles, music, "
    "background voices, extra speech, hands entering frame, repeated dialogue, English speech, "
    "logos, watermarks, gibberish text"
)


@dataclass(frozen=True)
class VeoTakeRequest:
    index: int
    beat: EditorialBeat
    shot: ShotVariant
    prompt: str
    negative_prompt: str
    model: str
    aspect_ratio: str
    duration_seconds: int
    seed: int

    def as_vertex_submit_kwargs(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "image_bytes": self.shot.image_bytes,
            "mime_type": self.shot.mime_type,
            "aspect_ratio": self.aspect_ratio,
            "duration_seconds": self.duration_seconds,
            "model": self.model,
            "negative_prompt": self.negative_prompt,
            "seed": self.seed,
        }


def build_veo_take_prompt(beat: EditorialBeat) -> str:
    dialogue = str(beat.text or "").strip()
    if not dialogue:
        raise ValidationError("Veo take prompt requires a non-empty editorial beat.")
    return (
        "Treat the supplied first frame as the sole visual truth. Keep the same adult woman's identity and hair, "
        "cream knit sweater, room, posture, camera position, and framing exactly as shown. Continue as "
        "restrained, natural phone-camera UGC with a subtle conversational expression, subtle blinking, and "
        "minimal head movement. Use the same warm adult German female voice across every take, speaking native German "
        "with natural conversational pacing and close smartphone microphone sound. She says exactly this German beat once: "
        f"“{dialogue}” Do not speak any other words or any English. After the final word, naturally stop speaking, "
        "close her mouth, and keep quiet eye contact. Do not freeze or perform an artificial end pose. Keep every frame "
        "completely free of on-screen text: no captions, subtitles, logos, watermarks, letters, symbols, or gibberish glyphs."
    )


def compile_veo_take_requests(
    *,
    beats: Sequence[EditorialBeat],
    shot_deck: Sequence[ShotVariant],
    base_seed: int,
    negative_prompt: str = EFFECTIVE_NEGATIVE_PROMPT,
) -> Tuple[VeoTakeRequest, ...]:
    """Map ordered editorial beats to matching approved shot variants."""
    if not beats:
        raise ValidationError("Veo request compilation requires at least one editorial beat.")
    if len(shot_deck) != 4:
        raise ValidationError(
            "Veo request compilation requires exactly four approved shot variants.",
            {"shot_variant_count": len(shot_deck)},
        )
    if len(beats) > len(shot_deck):
        raise ValidationError(
            "Veo request compilation has more beats than approved shot variants.",
            {"beat_count": len(beats), "shot_variant_count": len(shot_deck)},
        )
    if isinstance(base_seed, bool) or not isinstance(base_seed, int) or base_seed < 0:
        raise ValidationError("Veo request compilation requires a non-negative integer base seed.")

    effective_negative_prompt = str(negative_prompt or "").strip()
    if not effective_negative_prompt:
        raise ValidationError("Veo request compilation requires a non-empty negative prompt.")
    missing_negative_locks = [
        lock for lock in _REQUIRED_NEGATIVE_LOCKS if lock not in effective_negative_prompt.lower()
    ]
    if missing_negative_locks:
        raise ValidationError(
            "Veo request negative prompt is missing required continuity locks.",
            {"missing_locks": missing_negative_locks},
        )

    requests = []
    for expected_index, beat in enumerate(beats):
        if not isinstance(beat, EditorialBeat) or beat.index != expected_index:
            raise ValidationError(
                "Editorial beats must be ordered with contiguous zero-based indexes.",
                {"expected_index": expected_index, "received_index": getattr(beat, "index", None)},
            )
        shot = shot_deck[beat.index]
        if not isinstance(shot, ShotVariant) or shot.index != beat.index:
            raise ValidationError(
                "Each editorial beat requires the matching approved shot variant.",
                {"beat_index": beat.index, "shot_index": getattr(shot, "index", None)},
            )
        if beat.provider_duration_seconds not in SUPPORTED_DURATIONS:
            raise ValidationError(
                "Editorial beat provider duration must be 4, 6, or 8 seconds.",
                {"beat_index": beat.index, "duration_seconds": beat.provider_duration_seconds},
            )

        requests.append(
            VeoTakeRequest(
                index=beat.index,
                beat=beat,
                shot=shot,
                prompt=build_veo_take_prompt(beat),
                negative_prompt=effective_negative_prompt,
                model=VEO_MODEL,
                aspect_ratio=VEO_ASPECT_RATIO,
                duration_seconds=beat.provider_duration_seconds,
                seed=base_seed + beat.index,
            )
        )
    return tuple(requests)


__all__ = [
    "EFFECTIVE_NEGATIVE_PROMPT",
    "SUPPORTED_DURATIONS",
    "VEO_ASPECT_RATIO",
    "VEO_MODEL",
    "VeoTakeRequest",
    "build_veo_take_prompt",
    "compile_veo_take_requests",
]
