"""
FLOW-FORGE Video Prompt Assembly
Simple prompt builder that inserts Phase 2 dialogue into video generation template.
Per Canon Phase 3: S4_SCRIPTED → S5_PROMPTS_BUILT
"""

from typing import Dict, Any
from app.features.posts.schemas import VideoPrompt, AudioSection
from app.core.logging import get_logger
from app.core.errors import ValidationError

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
    dialogue = seed_data.get("dialog_script")
    if not dialogue:
        # Fallback to script field if dialog_script not present
        dialogue = seed_data.get("script")
    
    if not dialogue:
        raise ValidationError(
            message="Missing dialogue in seed_data. Post must have dialog_script or script.",
            details={"seed_data_keys": list(seed_data.keys())}
        )
    
    # Normalize dialogue to avoid duplicate ending markers
    normalized_dialogue = dialogue.strip()
    suffix_variants = ["(stiller Halt)", "( stiller Halt)"]
    for suffix in suffix_variants:
        if normalized_dialogue.endswith(suffix):
            normalized_dialogue = normalized_dialogue[: -len(suffix)].rstrip()
            break

    formatted_dialogue = f"Dialogue; {normalized_dialogue} ( stiller Halt)"

    # Build audio config with dialogue
    audio_section = AudioSection(dialogue=formatted_dialogue)

    # Assemble complete prompt using template defaults
    video_prompt = VideoPrompt(audio=audio_section)
    
    # Convert to dict for storage and API submission
    prompt_dict = video_prompt.model_dump()
    
    logger.info(
        "video_prompt_assembled",
        dialogue_length=len(dialogue),
        dialogue_preview=dialogue[:50] + "..." if len(dialogue) > 50 else dialogue
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
