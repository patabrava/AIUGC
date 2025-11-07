"""
Prompt text assembly utilities shared across providers.
Per Constitution § V: Co-locate feature logic.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.core.logging import get_logger

logger = get_logger(__name__)

PROMPT_FIELD_ORDER: List[str] = [
    "character",
    "action",
    "style",
    "scene",
    "cinematography",
    "lighting",
    "color_and_grade",
    "camera_positioning_and_motion",
    "composition",
    "focus_and_lens_effects",
    "atmosphere",
    "authenticity_modifiers",
    "universal_negatives",
    "post",
    "sound_effects",
]


def _compose_prompt_sections(video_prompt: Dict[str, Any]) -> List[str]:
    sections: List[str] = []

    for field_name in PROMPT_FIELD_ORDER:
        value = video_prompt.get(field_name)
        if not value:
            continue
        sections.append(str(value).strip())

    audio_payload = video_prompt.get("audio")
    if audio_payload:
        if isinstance(audio_payload, dict):
            dialogue = audio_payload.get("dialogue")
            capture = audio_payload.get("capture")
        else:
            dialogue = getattr(audio_payload, "dialogue", None)
            capture = getattr(audio_payload, "capture", None)

        if dialogue:
            sections.append(str(dialogue).strip())
        if capture and capture != dialogue:
            sections.append(str(capture).strip())

    return [section for section in sections if section]


def build_full_prompt_text(video_prompt: Dict[str, Any]) -> str:
    """Assemble canonical provider prompt text from Phase 3 prompt structure."""
    sections = _compose_prompt_sections(video_prompt)
    prompt_text = "\n\n".join(sections)
    logger.debug(
        "composed_prompt_text",
        prompt_length=len(prompt_text),
        sections=len(sections)
    )
    return prompt_text
