"""Compile first-frame-led Veo requests for independent semantic UGC takes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from app.core.errors import ValidationError
from app.features.shot_production.planner import EditorialBeat
from app.features.shot_production.shot_deck import ShotVariant


VEO_MODEL = "veo-3.1-generate-001"
VEO_ASPECT_RATIO = "9:16"
SUPPORTED_DURATIONS = frozenset({4, 6, 8})
_REQUIRED_NEGATIVE_LOCKS = (
    "identity drift",
    "face change",
    "facial distortion",
    "age change",
    "hair change",
    "wardrobe change",
    "room change",
    "lighting change",
    "wheelchair change",
    "wheelchair deformation",
    "standing",
    "walking",
    "extra person",
    "zoom",
    "pan",
    "tilt",
    "dolly",
    "orbit",
    "camera movement",
    "cut",
    "scene transition",
    "wipe transition",
    "end card",
    "push-in",
    "reframe",
    "posture reset",
    "generated text",
    "subtitles",
    "music",
    "background voices",
    "extra speech",
    "hand gesture",
    "hands entering frame",
    "post-dialogue hand movement",
    "door-opening mime",
    "gaze drop",
    "held end pose",
    "lip-sync drift",
    "repeated dialogue",
    "english speech",
    "logos",
    "watermarks",
    "gibberish text",
)
EFFECTIVE_NEGATIVE_PROMPT = (
    "identity drift, face change, facial distortion, age change, hair change, wardrobe change, "
    "room change, lighting change, extra person, wheelchair change, wheelchair deformation, "
    "disconnected wheelchair components, standing, walking, body deformation, malformed hands, "
    "extra fingers, fused fingers, hand gesture, demonstrative hand action, door-opening mime, "
    "hand-position change, hands lifting, hands leaving resting position, hands entering frame, "
    "post-dialogue gesture, post-dialogue hand movement, terminal hand motion, post-dialogue action, "
    "body sway, posture reset, large head movement, exaggerated nodding, concluding nod, farewell "
    "expression, completion smile, downward gaze, gaze drop, looking down, exaggerated mouth closure, "
    "pursed-lip end pose, held end pose, frozen stare, robotic motion, stiff motion, mouth warping, "
    "teeth artifacts, lip-sync drift, camera movement, camera shake, camera pan, camera tilt, camera "
    "zoom, push-in, dolly, orbit, reframe, cut, jump cut, scene transition, wipe transition, end card, "
    "freeze frame, repeated dialogue, extra speech, English speech, music, background voices, subtitles, "
    "captions, generated text, logos, watermarks, gibberish text, plastic skin, waxy skin, over-smoothed "
    "skin, beauty filter, CGI look, 3D render"
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


def _visual_contract_text(visual_contract: Optional[Mapping[str, Any]]) -> str:
    if not visual_contract:
        return (
            "The supplied source frame is the authority for the subject identity, scene and background, "
            "composition, wardrobe, wheelchair, lighting, and visual style. Preserve its exact framing "
            "and visible boundaries throughout the take."
        )
    required = (
        "scene_description",
        "wardrobe_description",
        "wheelchair_description",
        "framing_description",
    )
    normalized = {
        key: " ".join(str(visual_contract.get(key) or "").split()) for key in required
    }
    missing = [key for key, value in normalized.items() if not value]
    if missing:
        raise ValidationError(
            "Veo take prompt requires a complete frozen visual contract.",
            {"missing_fields": missing},
        )
    return (
        "The supplied source frame is the authority for the subject identity, scene and background, "
        "composition, wardrobe, wheelchair, lighting, and visual style. Preserve its exact framing "
        "and visible boundaries throughout the take."
    )


def build_veo_take_prompt(
    beat: EditorialBeat,
    *,
    visual_contract: Optional[Mapping[str, Any]] = None,
) -> str:
    dialogue = str(beat.text or "").strip()
    if not dialogue:
        raise ValidationError("Veo take prompt requires a non-empty editorial beat.")
    delivery_tail_seconds = 1.5 if beat.provider_duration_seconds >= 6 else 1.0
    final_word_target = beat.provider_duration_seconds - delivery_tail_seconds
    return "\n".join(
        (
            f"Cinematography: one continuous, unedited {beat.provider_duration_seconds}-second vertical "
            "smartphone UGC take, animated from the supplied first frame.",
            "Camera: static seated eye-level shot at the source frame's exact angle and composition. "
            "The camera stays completely still.",
            f"Visual continuity: {_visual_contract_text(visual_contract)}",
            "Subject motion: the seated woman speaks directly to the lens with accurate native-German lip and "
            "jaw movement, a restrained conversational expression, irregular natural blinking, quiet breathing, "
            "and minimal speech-coupled head movement. Her shoulders, torso, wheelchair, and visible body remain "
            "stable. Her hands retain the exact visibility and relaxed resting positions established by the "
            "source frame. She communicates through speech and facial expression while her hands remain at rest.",
            "Performance interpretation: the dialogue is spoken advice only. Every reference to hands, support, "
            "wheelchair frames, doors, pulling, pushing, or movement remains speech content rather than a physical "
            "cue. Both hands remain relaxed in their exact source-frame resting positions for the full take.",
            f"Timing: she begins speaking promptly and delivers the dialogue once at a natural conversational pace, "
            f"targeting the final spoken word around {final_word_target:.1f} seconds.",
            "Cut-ready ending: after the final syllable, her speech articulation resolves naturally while she "
            "remains conversationally engaged with the lens, as if the next sentence will follow immediately. "
            "Preserve the same seated posture, shoulder position, wheelchair position, gaze direction, and resting "
            "hand positions through the final frame. Only subtle breathing and natural blinking continue. Maintain "
            "fluid living presence without a concluding gesture, completion expression, or held end pose.",
            f"Dialogue: “{dialogue}”",
            "Audio: one warm adult female voice speaking native German with a natural conversational cadence and "
            "consistent vocal identity. Clean close smartphone-microphone sound with quiet natural ambience "
            "consistent with the source-frame location. The dialogue is the complete spoken performance.",
        )
    )


def compile_veo_take_requests(
    *,
    beats: Sequence[EditorialBeat],
    shot_deck: Sequence[ShotVariant],
    base_seed: int,
    negative_prompt: str = EFFECTIVE_NEGATIVE_PROMPT,
    visual_contract: Optional[Mapping[str, Any]] = None,
) -> Tuple[VeoTakeRequest, ...]:
    """Map ordered editorial beats to matching approved shot variants."""
    if not beats:
        raise ValidationError("Veo request compilation requires at least one editorial beat.")
    if len(beats) != len(shot_deck):
        raise ValidationError(
            "Veo request compilation requires the same number of beats and approved shot variants.",
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
                prompt=build_veo_take_prompt(
                    beat,
                    visual_contract=visual_contract,
                ),
                negative_prompt=effective_negative_prompt,
                model=VEO_MODEL,
                aspect_ratio=VEO_ASPECT_RATIO,
                duration_seconds=beat.provider_duration_seconds,
                seed=base_seed,
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
