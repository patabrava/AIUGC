"""Generate identity-locked wheelchair scene plates before any Veo request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

from app.adapters.llm_client import get_llm_client
from app.core.errors import ValidationError
from app.features.shot_frames.service import ShotFrameReference


WHEELCHAIR_VISUAL_CONTRACT = (
    "The same lightweight manual wheelchair in every image: matte dark-graphite frame, "
    "slim black armrests, black seat and back cushion, and silver hand rims."
)
FRAMING_CONTRACT = (
    "Use a static vertical 9:16 medium close-up from head to mid-torso at seated eye-level. "
    "Keep her face large and identity-readable while at least one armrest and part of a large "
    "rear wheel or silver hand rim remain clearly visible."
)
_REFERENCE_ROLES = ("identity_primary", "identity_support", "location")


@dataclass(frozen=True)
class ScenePlateCandidate:
    index: int
    image_bytes: bytes
    mime_type: str
    provider_model: str
    prompt: str


@dataclass(frozen=True)
class ScenePlateGenerationResult:
    candidates: Tuple[ScenePlateCandidate, ...]
    prompts: Tuple[str, ...]
    derivation_mode: str


def build_canonical_scene_plate_prompt(*, scene: str, wardrobe: str) -> str:
    return (
        "Create one photorealistic vertical start image using all three supplied images with fixed roles. "
        "Image 1 is the PRIMARY ACTOR IDENTITY reference. Image 2 is the SAME ACTOR from another view and "
        "is supporting identity evidence only. Image 3 is the ACTOR-FREE LOCATION reference. Place exactly "
        "the same adult woman from Images 1 and 2 inside Image 3. Preserve her exact facial geometry, "
        "hairline, hair, apparent age, body proportions, and ordinary natural skin texture; do not average "
        "her into a new face. She is seated upright in a manual wheelchair. "
        f"{WHEELCHAIR_VISUAL_CONTRACT} {FRAMING_CONTRACT} "
        f"Her upper-body outfit is exactly: {wardrobe}. The location is exactly: {scene}. "
        "Her hands and wheelchair geometry are physically plausible. Use natural available light and a "
        "quiet conversational expression immediately before speaking, with her mouth closed. Render no "
        "other person, text, logo, watermark, mobility device, standing pose, walking pose, beauty polish, "
        "camera tilt, wide shot, full-body shot, or cropped-out wheelchair."
    )


def build_derived_scene_plate_prompt(*, scene: str, wardrobe: str) -> str:
    return (
        "Create one photorealistic vertical start image using all three supplied images with fixed roles. "
        "Image 1 is the canonical scene plate and is the authoritative source for the exact woman, exact "
        "manual wheelchair, seated posture, camera height, camera distance, facial geometry, and scale. "
        "Image 2 is the unchanged front identity reference for the same woman and exists only to prevent "
        "facial drift. Image 3 is the ACTOR-FREE LOCATION reference. Preserve the exact woman from Images 1 "
        "and 2 and preserve the exact manual wheelchair, seated pose, face size, and framing from Image 1. "
        f"{WHEELCHAIR_VISUAL_CONTRACT} {FRAMING_CONTRACT} "
        f"Keep the actor-free location exactly: {scene}; and the upper-body outfit exactly: {wardrobe}. "
        "Keep her mouth closed with a quiet conversational expression. Keep hands, wheelchair, and room "
        "perspective physically plausible. Render no other person, text, logo, watermark, standing pose, "
        "walking pose, wide shot, full-body shot, beauty polish, camera movement, or cropped-out wheelchair."
    )


def generate_scene_plate(
    *,
    references: Sequence[ShotFrameReference],
    prompt: str,
    llm_client: Optional[Any] = None,
    image_model: str = "gemini-3.1-flash-image",
) -> dict[str, Any]:
    if len(references) != 3 or tuple(item.role for item in references) != _REFERENCE_ROLES:
        raise ValidationError(
            "Scene-plate references must be explicit and ordered.",
            {
                "expected_roles": list(_REFERENCE_ROLES),
                "received_roles": [item.role for item in references],
            },
        )
    for reference in references:
        if not reference.mime_type.startswith("image/") or not reference.image_bytes:
            raise ValidationError(
                "Scene-plate references require non-empty image bytes.",
                {"role": reference.role, "mime_type": reference.mime_type},
            )
    normalized_prompt = " ".join(str(prompt or "").split())
    if not normalized_prompt:
        raise ValidationError("Scene-plate generation requires a prompt.")

    client = llm_client or get_llm_client()
    return client.generate_gemini_image(
        prompt=normalized_prompt,
        model=image_model,
        temperature=0.2,
        aspect_ratio="9:16",
        image_size="2K",
        input_images=[item.as_gemini_input() for item in references],
    )


def _as_role(reference: ShotFrameReference, role: str) -> ShotFrameReference:
    return ShotFrameReference(
        role=role,
        mime_type=reference.mime_type,
        image_bytes=reference.image_bytes,
    )


def generate_scene_plate_candidates(
    *,
    actor_references: Sequence[ShotFrameReference],
    location_reference: ShotFrameReference,
    canonical_scene_plate: Optional[ShotFrameReference] = None,
    scene: str,
    wardrobe: str,
    candidate_count: int = 3,
    llm_client: Optional[Any] = None,
    image_model: str = "gemini-3.1-flash-image",
) -> ScenePlateGenerationResult:
    """Generate one canonical plate, then identity-locked alternatives from it."""
    if len(actor_references) != 2:
        raise ValidationError(
            "Scene-plate generation requires exactly two immutable actor references.",
            {"actor_reference_count": len(actor_references)},
        )
    if candidate_count != 3:
        raise ValidationError("Semantic scene-plate generation requires exactly three candidates.")
    expected_roles = ("actor_front", "actor_three_quarter")
    if tuple(reference.role for reference in actor_references) != expected_roles:
        raise ValidationError(
            "Scene-plate actor references must remain actor_front then actor_three_quarter."
        )
    if location_reference.role != "location":
        raise ValidationError("Scene-plate location reference must use the location role.")

    client = llm_client or get_llm_client()
    derived_prompt = build_derived_scene_plate_prompt(scene=scene, wardrobe=wardrobe)
    if canonical_scene_plate is not None:
        if canonical_scene_plate.role != "canonical_scene_plate":
            raise ValidationError(
                "Established semantic scene plate must use the canonical_scene_plate role."
            )
        candidates = []
        canonical_reference = _as_role(canonical_scene_plate, "identity_primary")
        start_index = 1
        derivation_mode = "canonical_anchor"
    else:
        canonical_prompt = build_canonical_scene_plate_prompt(
            scene=scene,
            wardrobe=wardrobe,
        )
        canonical = generate_scene_plate(
            references=(
                _as_role(actor_references[0], "identity_primary"),
                _as_role(actor_references[1], "identity_support"),
                _as_role(location_reference, "location"),
            ),
            prompt=canonical_prompt,
            llm_client=client,
            image_model=image_model,
        )
        candidates = [
            ScenePlateCandidate(
                index=1,
                image_bytes=canonical["image_bytes"],
                mime_type=str(canonical["mime_type"]),
                provider_model=str(canonical["model"]),
                prompt=canonical_prompt,
            )
        ]
        canonical_reference = ShotFrameReference(
            role="identity_primary",
            mime_type=str(canonical["mime_type"]),
            image_bytes=canonical["image_bytes"],
        )
        start_index = 2
        derivation_mode = "bootstrap"
    for index in range(start_index, candidate_count + 1):
        generated = generate_scene_plate(
            references=(
                canonical_reference,
                _as_role(actor_references[0], "identity_support"),
                _as_role(location_reference, "location"),
            ),
            prompt=derived_prompt,
            llm_client=client,
            image_model=image_model,
        )
        candidates.append(
            ScenePlateCandidate(
                index=index,
                image_bytes=generated["image_bytes"],
                mime_type=str(generated["mime_type"]),
                provider_model=str(generated["model"]),
                prompt=derived_prompt,
            )
        )
    return ScenePlateGenerationResult(
        candidates=tuple(candidates),
        prompts=tuple(candidate.prompt for candidate in candidates),
        derivation_mode=derivation_mode,
    )


__all__ = [
    "FRAMING_CONTRACT",
    "WHEELCHAIR_VISUAL_CONTRACT",
    "ScenePlateCandidate",
    "ScenePlateGenerationResult",
    "build_canonical_scene_plate_prompt",
    "build_derived_scene_plate_prompt",
    "generate_scene_plate",
    "generate_scene_plate_candidates",
]
