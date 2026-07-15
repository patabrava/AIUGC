"""Generate reviewable start frames without invoking the video provider."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

from app.adapters.llm_client import get_llm_client
from app.core.errors import ThirdPartyError, ValidationError

RAW_CAMERA_SYSTEM_PROMPT_PATH = Path(__file__).with_name("raw_camera_casting_system_prompt.txt")


@dataclass(frozen=True)
class ShotFrameReference:
    role: str
    mime_type: str
    image_bytes: bytes

    def as_gemini_input(self) -> dict:
        return {"mime_type": self.mime_type, "image_bytes": self.image_bytes}


@dataclass(frozen=True)
class ShotFrameCandidate:
    index: int
    image_bytes: bytes
    mime_type: str
    provider_model: str


@dataclass(frozen=True)
class ShotFrameGenerationResult:
    prompt_writer_output: str
    composition_prompt: str
    candidates: List[ShotFrameCandidate]


def load_raw_camera_system_prompt() -> str:
    return RAW_CAMERA_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _validate_reference(reference: ShotFrameReference, expected_role: str) -> None:
    if reference.role != expected_role:
        raise ValidationError(
            "Shot-frame reference roles must be explicit and ordered.",
            {"expected_role": expected_role, "received_role": reference.role},
        )
    if not reference.mime_type.startswith("image/") or not reference.image_bytes:
        raise ValidationError(
            "Shot-frame references require non-empty image bytes.",
            {"role": reference.role, "mime_type": reference.mime_type},
        )


def _build_prompt_writer_brief(
    *,
    script: str,
    actor_name: str,
    scene_description: str,
    wardrobe_description: str,
) -> str:
    return (
        "Write the final image-generation prompt for a vertical 9:16 AIUGC talking-head start frame. "
        f"The subject is the adult woman represented by the supplied identity references ({actor_name}); "
        "the supplied actor images are the sole visual identity contract. Preserve the visible facial structure, hair, "
        "age, proportions, and ordinary natural appearance shown in those images without supplementing or overriding "
        "them with a written physical description. "
        f"Wardrobe: {wardrobe_description} "
        f"Location: {scene_description} "
        "Use a chest-up or medium close portrait so the face is identity-readable while the supplied room remains "
        "recognizable. She faces the phone camera with a quiet, conversational expression immediately before speaking. "
        "The frame should feel like real creator footage captured at home: direct, slightly imperfect, unpolished, and "
        "physically plausible, with ordinary daylight and enough depth of field to retain the room layout. "
        f"Spoken context for expression only; do not render these words as text: {script}"
    )


def _build_composition_prompt(*, prompt_writer_output: str) -> str:
    return (
        "Create one new vertical image using all three supplied images with these fixed roles. "
        "Image 1 is the PRIMARY ACTOR IDENTITY reference and the cream knit sweater wardrobe reference. "
        "Image 2 is the SAME ACTOR from a three-quarter view and is identity evidence only; ignore and do not copy "
        "the beige blazer visible in Image 2. Image 3 is the ACTOR-FREE LOCATION reference; preserve its room geometry, "
        "warm off-white wall, beige curtain, pale oak floor, narrow light-oak side table, white mug, terracotta rubber "
        "plant, muted palette, and natural daylight. Place exactly one person—the same adult woman from Images 1 and 2—"
        "inside the room from Image 3. Do not average her into a new face, change her apparent age, change her hair, add "
        "another person, invent a wheelchair, redesign the room, add signage, add subtitles, or add readable text. "
        "Images 1 and 2 are the sole and authoritative visual identity evidence. Resolve every visible identity attribute "
        "from those images only; do not infer identity from the actor name or any written physical description. "
        "The output must be a usable opening frame for Veo 3.1: 9:16, chest-up AIUGC talking-head composition, face and "
        "hands anatomically plausible, enough background visible to lock the location, no motion blur, and no beauty polish. "
        "Apply this finished Raw Camera Casting Realism prompt:\n\n"
        f"{prompt_writer_output.strip()}"
    )


def _is_vertex_resource_exhausted(exc: ThirdPartyError) -> bool:
    details = exc.details if isinstance(exc.details, dict) else {}
    return int(details.get("status_code") or 0) == 429 and "RESOURCE_EXHAUSTED" in str(
        details.get("body") or ""
    )


def generate_shot_frame_candidates(
    *,
    script: str,
    actor_name: str,
    scene_description: str,
    wardrobe_description: str,
    actor_references: List[ShotFrameReference],
    location_reference: ShotFrameReference,
    candidate_count: int = 3,
    llm_client: Optional[Any] = None,
    image_model: str = "gemini-3.1-flash-image",
    sleep_fn: Callable[[float], None] = time.sleep,
    quota_retry_delay_seconds: float = 65.0,
) -> ShotFrameGenerationResult:
    """Create still candidates only; explicit approval and Veo submission happen later."""
    if len(actor_references) != 2:
        raise ValidationError(
            "Shot-frame generation requires exactly two actor references.",
            {"actor_reference_count": len(actor_references)},
        )
    if not 1 <= candidate_count <= 4:
        raise ValidationError(
            "Shot-frame candidate count must be between one and four.",
            {"candidate_count": candidate_count},
        )
    try:
        quota_delay = float(quota_retry_delay_seconds)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Shot-frame quota retry delay must be finite and non-negative.") from exc
    if not math.isfinite(quota_delay) or quota_delay < 0:
        raise ValidationError("Shot-frame quota retry delay must be finite and non-negative.")
    _validate_reference(actor_references[0], "actor_front")
    _validate_reference(actor_references[1], "actor_three_quarter")
    _validate_reference(location_reference, "location")

    client = llm_client or get_llm_client()
    prompt_writer_output = client.generate_gemini_text(
        prompt=_build_prompt_writer_brief(
            script=script,
            actor_name=actor_name,
            scene_description=scene_description,
            wardrobe_description=wardrobe_description,
        ),
        system_prompt=load_raw_camera_system_prompt(),
        max_tokens=4096,
        temperature=0.2,
        thinking_budget=0,
    ).strip()
    if not prompt_writer_output:
        raise ValidationError("Raw Camera prompt writer returned an empty prompt.")
    if prompt_writer_output[-1] not in ".!?":
        raise ValidationError(
            "Raw Camera prompt writer returned an incomplete prompt.",
            {"output_length": len(prompt_writer_output), "output_tail": prompt_writer_output[-80:]},
        )

    composition_prompt = _build_composition_prompt(prompt_writer_output=prompt_writer_output)
    ordered_inputs = [
        actor_references[0].as_gemini_input(),
        actor_references[1].as_gemini_input(),
        location_reference.as_gemini_input(),
    ]
    candidates = []
    for index in range(1, candidate_count + 1):
        for attempt in range(2):
            try:
                generated = client.generate_gemini_image(
                    prompt=composition_prompt,
                    model=image_model,
                    temperature=0.7,
                    aspect_ratio="9:16",
                    image_size="2K",
                    input_images=ordered_inputs,
                )
                break
            except ThirdPartyError as exc:
                if attempt > 0 or not _is_vertex_resource_exhausted(exc):
                    raise
                sleep_fn(quota_delay)
        candidates.append(
            ShotFrameCandidate(
                index=index,
                image_bytes=generated["image_bytes"],
                mime_type=generated["mime_type"],
                provider_model=generated["model"],
            )
        )

    return ShotFrameGenerationResult(
        prompt_writer_output=prompt_writer_output,
        composition_prompt=composition_prompt,
        candidates=candidates,
    )
