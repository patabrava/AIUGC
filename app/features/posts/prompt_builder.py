"""
FLOW-FORGE Video Prompt Assembly
Simple prompt builder that inserts Phase 2 dialogue into video generation template.
Per Canon Phase 3: S4_SCRIPTED → S5_PROMPTS_BUILT
"""

import re
from typing import Dict, Any, Optional

from app.features.posts.schemas import VideoPrompt, AudioSection
from app.core.logging import get_logger
from app.core.errors import ValidationError


__all__ = [
    "STANDARD_AUDIO_BLOCK",
    "SORA_NEGATIVE_CONSTRAINTS",
    "VEO_NEGATIVE_PROMPT",
    "build_video_prompt_from_seed",
    "validate_video_prompt",
    "build_optimized_prompt",
    "split_dialogue_sentences",
    "build_veo_prompt_segment",
]


STANDARD_AUDIO_BLOCK = (
    "Audio: Recorded with a modern smartphone microphone in a quiet indoor room. "
    "The voice is clear, natural, and close to the microphone. No music and no "
    "background voices. Subtle natural room acoustics typical of a small bedroom. "
    "After the final word, the audio gently settles into a quiet room tone for a "
    "brief moment before the clip ends."
)

ENDING_HOLD_DIRECTIVE = (
    "After the final spoken word, speech stops completely. She does not begin a new word or "
    "syllable. Her mouth comes to rest, she holds a gentle smile, and remains still for a brief "
    "moment before the clip ends."
)

SORA_NEGATIVE_CONSTRAINTS = (
    "Universal Negatives (hard constraints): subtitles, captions, watermark, text overlays, "
    "words on screen, logo, branding, poor lighting, blurry footage, low resolution, unwanted "
    "objects, inconsistent character appearance, audio sync issues, amateur quality, cartoon "
    "effects, unrealistic proportions, distorted hands, artificial lighting, oversaturation, "
    "excessive camera shake, no audible audio artifacts, no background voices, no music."
)

VEO_NEGATIVE_PROMPT = (
    "subtitles, captions, watermark, text overlays, words on screen, logos, branding, poor lighting, "
    "blurry footage, low resolution, unwanted objects, character inconsistency, lip-sync drift, "
    "cartoon styling, unrealistic proportions, distorted hands, artificial lighting, oversaturation, "
    "excessive camera shake, background voices, music bed, audio hiss, static, clipping, abrupt cuts, angle changes"
)

OPTIMIZED_PROMPT_TEMPLATE = (
    "Character:\n"
    "38-year-old German woman with shoulder-length light brown hair with subtle blonde highlights, hazel eyes, "
    "and a warm light-medium skin tone. Friendly oval face and natural expression.\n\n"
    "Style:\n"
    "Natural, photorealistic UGC smartphone selfie video with authentic influencer-style delivery, "
    "soft flattering indoor light, and natural skin texture.\n\n"
    "Action:\n"
    "Seated in a wheelchair, she delivers the line directly to camera in one continuous take. "
    "She speaks at a natural conversational pace, using small natural hand gestures and subtle "
    "upper-body nods while speaking.\n\n"
    "Scene:\n"
    "A modern, tidy bedroom with blush-pink walls and minimal decor. Bright soft vanity light "
    "and natural daylight from camera-right create an even, flattering indoor look. The "
    "wheelchair is partially visible in the frame.\n\n"
    "Cinematography:\n"
    "Vertical smartphone video, medium close-up framing, front-facing camera at natural selfie "
    "distance. The camera is handheld but stable, with only minimal natural movement. The "
    "framing remains consistent throughout the shot without noticeable camera drift or "
    "reframing.\n\n"
    "Dialogue:\n"
    "\"{dialogue}\"\n\n"
    "Ending:\n"
    "{ending}\n\n"
    "{audio}{negatives_section}"
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

    script_line = f"{normalized_dialogue} ({ENDING_HOLD_DIRECTIVE})"

    optimized_prompt = build_optimized_prompt(
        normalized_dialogue,
        negative_constraints=SORA_NEGATIVE_CONSTRAINTS,
    )
    veo_prompt = build_optimized_prompt(normalized_dialogue, negative_constraints=None)

    # Keep a single audio block in the final prompt to avoid contradictory synthesis cues.
    audio_section = AudioSection(dialogue=STANDARD_AUDIO_BLOCK, capture="")

    # Assemble complete prompt using template defaults
    base_prompt = VideoPrompt(
        audio=audio_section,
        universal_negatives=SORA_NEGATIVE_CONSTRAINTS,
        post="",
        sound_effects="",
        optimized_prompt=optimized_prompt,
        veo_prompt=veo_prompt,
        veo_negative_prompt=VEO_NEGATIVE_PROMPT,
    )
    action_template = base_prompt.model_fields["action"].default  # type: ignore[attr-defined]
    action_value = action_template.replace("ENTER SCRIPT FROM POST HERE", script_line)

    video_prompt = base_prompt.model_copy(update={"action": action_value})

    # Convert to dict for storage and API submission
    prompt_dict = video_prompt.model_dump()
    prompt_dict["optimized_prompt"] = optimized_prompt
    prompt_dict["veo_prompt"] = veo_prompt
    prompt_dict["veo_negative_prompt"] = VEO_NEGATIVE_PROMPT
    
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


def build_optimized_prompt(dialogue: str, negative_constraints: Optional[str] = SORA_NEGATIVE_CONSTRAINTS) -> str:
    cleaned_dialogue = dialogue.strip()
    return OPTIMIZED_PROMPT_TEMPLATE.format(
        dialogue=cleaned_dialogue,
        ending=ENDING_HOLD_DIRECTIVE,
        audio=STANDARD_AUDIO_BLOCK,
        negatives_section=f"\n\n{negative_constraints}" if negative_constraints else "",
    )


def split_dialogue_sentences(dialogue: str) -> list[str]:
    cleaned = " ".join(dialogue.split()).strip()
    if not cleaned:
        return []
    sentence_matches = re.findall(r"[^.!?]+[.!?]", cleaned)
    remainder_start = sum(len(match) for match in sentence_matches)
    remainder = cleaned[remainder_start:].strip()
    sentences = [match.strip() for match in sentence_matches if match.strip()]
    if remainder:
        if sentences:
            sentences[-1] = f"{sentences[-1].rstrip()} {remainder}".strip()
        else:
            sentences = [remainder]
    return sentences


def build_veo_prompt_segment(dialogue: str, *, include_quotes: bool = False, include_ending: bool = False) -> str:
    cleaned_dialogue = dialogue.strip()
    prompt_dialogue = f"\"{cleaned_dialogue}\"" if include_quotes else cleaned_dialogue
    ending = ENDING_HOLD_DIRECTIVE if include_ending else "Do not end the speech yet; continue into the next segment with no pause."
    template = OPTIMIZED_PROMPT_TEMPLATE
    return template.format(
        dialogue=prompt_dialogue,
        ending=ending,
        audio=STANDARD_AUDIO_BLOCK,
        negatives_section=f"\n\n{VEO_NEGATIVE_PROMPT}" if not include_quotes else f"\n\n{SORA_NEGATIVE_CONSTRAINTS}",
    )
