"""
FLOW-FORGE Video Prompt Assembly
Simple prompt builder that inserts Phase 2 dialogue into video generation template.
Per Canon Phase 3: S4_SCRIPTED → S5_PROMPTS_BUILT
"""

from typing import Dict, Any

from app.features.posts.schemas import VideoPrompt, AudioSection
from app.core.logging import get_logger
from app.core.errors import ValidationError


__all__ = ["build_video_prompt_from_seed", "validate_video_prompt", "build_optimized_prompt"]


AUDIO_DIALOGUE_DIRECTIVE = (
    "Audio: Recorded through modern smartphone mic — clear, front-facing voice with intimate presence and a soft, short living-room bloom (RT60 ≈ 0.3–0.4 s). Camera 20–30 cm from mouth, mic unobstructed. HVAC/appliances off; noise floor ≤ –55 dBFS with zero room-tone bed. After the final word, capture a dead-silent tail — no reverb, breaths, or background noise — for the closing pause. No music, one-take natural pacing."
)

OPTIMIZED_PROMPT_TEMPLATE = (
    "Subject & Look:\n"
    "A 38-year-old German woman with long, slightly damp light-brown hair with natural blonde highlights; hazel almond-shaped eyes with faint crow’s feet; friendly oval face with soft expression lines; warm light-medium skin with neutral undertones. She faces camera with a neutral, friendly expression that softens into a gentle smile.\n\n"
    "Setting:\n"
    "A modern, tidy bedroom with blush-pink walls and minimal décor. Vertical frame.\n\n"
    "Cinematography:\n"
    "Camera shot: medium close-up, slightly high angle, centered; one continuous take, no cuts. Lens & DOF: smartphone front camera (~24 mm equiv.), deep DOF with subtle natural falloff. Camera motion: subtle handheld sway and micro-jitter consistent with selfie grip.\n\n"
    "Lighting & Palette:\n"
    "Key: soft vanity light frontal; Fill: window daylight camera-right; Rim: gentle ambient wrap. Palette anchors: blush pink, soft white, warm oak, brushed nickel.\n\n"
    "Action (8 s):\n"
    "0–2 s: seated in a wheelchair, steady head-and-shoulders, direct eye contact, neutral expression.\n"
    "2–5 s: small natural hand gesture; slight upper-body nods.\n"
    "5–8 s: expression warms into a gentle smile; brief 0.5 s pause after speaking.\n\n"
    "Dialogue:\n"
    "\"{dialogue}\"\n\n"
    "Audio:\n"
    "Clean smartphone voice, intimate presence, zero room-tone bed; after the final word the space stays dead quiet with no reverb, breaths, or environmental sounds; no music; no background HVAC.\n\n"
    "Constraints — Avoid:\n"
    "text overlays/subtitles, logos/branding, poor lighting, heavy compression, excessive shake, off-sync audio, changes to character identity, cuts or angle changes."
)

logger = get_logger(__name__)


def build_video_prompt_from_seed(seed_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Assemble video generation prompt by inserting dialogue from Phase 2 seed data.
    
    Args:
        seed_data: Post seed_data containing dialog_script from Phase 2
        
    Returns:
        Complete video prompt JSON ready for video generation API
        
    Raises:
        ValidationError: If seed_data is missing required dialogue
        
    Per Constitution § XII: Schema-first validation
    """
    # Extract dialogue from seed_data
    dialogue = seed_data.get("script")
    dialogue_source = "seed_script"

    if not dialogue:
        # Fallback to dialog_script (PROMPT_2) if PROMPT_1 script missing
        dialogue = seed_data.get("dialog_script")
        dialogue_source = "dialog_script" if dialogue else None

    if not dialogue:
        raise ValidationError(
            message="Missing dialogue in seed_data. Post must have dialog_script or script.",
            details={"seed_data_keys": list(seed_data.keys())}
        )
    
    # Normalize dialogue to avoid duplicate ending markers
    normalized_dialogue = dialogue.strip()
    suffix_variants = ["(After delivering the dialogue, the character maintains a still, gentle smile with no further facial or mouth movements)", "( After delivering the dialogue, the character maintains a still, gentle smile with no further facial or mouth movements)"]
    for suffix in suffix_variants:
        if normalized_dialogue.endswith(suffix):
            normalized_dialogue = normalized_dialogue[: -len(suffix)].rstrip()
            break

    script_line = f"{normalized_dialogue} (After delivering the dialogue, the character maintains a still, gentle smile with no further facial or mouth movements)"

    optimized_prompt = build_optimized_prompt(normalized_dialogue)

    # Build audio config with distinct dialogue guidance to avoid duplication with capture notes
    audio_section = AudioSection(dialogue=AUDIO_DIALOGUE_DIRECTIVE)

    # Assemble complete prompt using template defaults
    base_prompt = VideoPrompt(audio=audio_section)
    action_template = base_prompt.model_fields["action"].default  # type: ignore[attr-defined]
    action_value = action_template.replace("ENTER SCRIPT FROM POST HERE", script_line)

    video_prompt = base_prompt.model_copy(update={"action": action_value})

    # Convert to dict for storage and API submission
    prompt_dict = video_prompt.model_dump()
    prompt_dict["optimized_prompt"] = optimized_prompt
    
    logger.info(
        "video_prompt_assembled",
        dialogue_length=len(dialogue),
        dialogue_preview=dialogue[:50] + "..." if len(dialogue) > 50 else dialogue,
        dialogue_source=dialogue_source,
        optimized_prompt_length=len(optimized_prompt),
    )
    
    return prompt_dict


def validate_video_prompt(prompt_data: Dict[str, Any]) -> bool:
    """
    Validate that prompt data conforms to VideoPrompt schema.
    
    Args:
        prompt_data: Prompt dictionary to validate
        
    Returns:
        True if valid
        
    Raises:
        ValidationError: If prompt is invalid
    """
    try:
        VideoPrompt.model_validate(prompt_data)
        return True
    except Exception as e:
        raise ValidationError(
            message=f"Video prompt validation failed: {str(e)}",
            details={"prompt_keys": list(prompt_data.keys())}
        )


def build_optimized_prompt(dialogue: str) -> str:
    cleaned_dialogue = dialogue.strip()
    return OPTIMIZED_PROMPT_TEMPLATE.format(dialogue=cleaned_dialogue)
