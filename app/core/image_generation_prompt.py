"""Canonical Raw Camera prompt-writer contract and execution helper."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from app.core.errors import ValidationError


RAW_CAMERA_SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "features"
    / "shot_frames"
    / "raw_camera_casting_system_prompt.txt"
)


@lru_cache(maxsize=1)
def load_raw_camera_system_prompt() -> str:
    """Return the literal long-form Raw Camera Casting Realism prompt."""
    prompt = RAW_CAMERA_SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    if not prompt.strip():
        raise ValidationError("Canonical image-generation system prompt is empty.")
    return prompt


def write_raw_camera_image_prompt(
    *, client: Any, brief: str, timeout_seconds: Optional[float] = None
) -> str:
    """Turn a task-specific brief into the final prompt consumed by an image renderer."""
    normalized_brief = str(brief or "").strip()
    if not normalized_brief:
        raise ValidationError("Raw Camera prompt writer requires a non-empty brief.")
    request = {
        "prompt": normalized_brief,
        "system_prompt": load_raw_camera_system_prompt(),
        "max_tokens": 4096,
        "temperature": 0.2,
        "thinking_budget": 0,
    }
    if timeout_seconds is not None:
        request["timeout_seconds"] = timeout_seconds
        # Deadline-bound queue work owns retries at the state-machine layer.
        request["provider_max_attempts"] = 1
    output = client.generate_gemini_text(**request).strip()
    if not output:
        raise ValidationError("Raw Camera prompt writer returned an empty prompt.")
    if output[-1] not in ".!?":
        raise ValidationError(
            "Raw Camera prompt writer returned an incomplete prompt.",
            {"output_length": len(output), "output_tail": output[-80:]},
        )
    return output


__all__ = [
    "RAW_CAMERA_SYSTEM_PROMPT_PATH",
    "load_raw_camera_system_prompt",
    "write_raw_camera_image_prompt",
]
