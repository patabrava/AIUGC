"""Stable script provenance contracts shared by plan and worker boundaries."""

from __future__ import annotations

from typing import Any

APP_SCRIPT_SOURCE = "app.features.topics.agents.generate_dialog_scripts"
SEMANTIC_SCRIPT_SOURCE = (
    "app.features.topics.semantic_scripts.generate_semantic_script"
)
MANUAL_SEMANTIC_SCRIPT_SOURCE = "manual_semantic_ugc"


def build_semantic_script_snapshot(
    *,
    text: str,
    review_status: str,
    word_count: int,
    creation_mode: str,
    target_duration_seconds: int,
) -> dict[str, Any]:
    """Build the canonical script snapshot used before and after free planning."""
    normalized_mode = str(creation_mode or "semantic_ugc").strip()
    normalized_review = str(review_status or "").strip().lower()
    source = (
        MANUAL_SEMANTIC_SCRIPT_SOURCE
        if normalized_mode == MANUAL_SEMANTIC_SCRIPT_SOURCE
        else SEMANTIC_SCRIPT_SOURCE
    )
    return {
        "text": " ".join(str(text or "").split()),
        "review_status": normalized_review,
        "word_count": int(word_count),
        "source": source,
        "creation_mode": normalized_mode,
        "script_review_status": normalized_review,
        "target_duration_seconds": int(target_duration_seconds),
    }


__all__ = [
    "APP_SCRIPT_SOURCE",
    "MANUAL_SEMANTIC_SCRIPT_SOURCE",
    "SEMANTIC_SCRIPT_SOURCE",
    "build_semantic_script_snapshot",
]
