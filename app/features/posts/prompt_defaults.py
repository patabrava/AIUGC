"""
Shared prompt defaults for the posts feature.
Keep the stored prompt JSON and generated prompt text aligned.
"""

from __future__ import annotations

DEFAULT_SCENE_BODY = (
    "A modern, tidy bedroom with blush-pink walls and minimal decor. Bright soft vanity light "
    "and natural daylight from camera-right create an even, flattering indoor look. The "
    "wheelchair is partially visible in the frame."
)

DEFAULT_SCENE = f"Scene: {DEFAULT_SCENE_BODY}"

LEGACY_SCENE = (
    "Scene: The woman is sitting on a wheelchair in a brightly lit modern bedroom with pink walls. "
    "Clean, minimal décor. Natural daylight streams through an unseen window camera-right, "
    "supplemented by soft ambient lighting creating even, flattering illumination across the space. "
    "The wheelchair is partially visible in the frame."
)

LEGACY_SCENE_BODY = LEGACY_SCENE[len("Scene: "):]
