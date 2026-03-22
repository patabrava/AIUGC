"""
Pure content helpers for topic scripts and captions.
"""

from __future__ import annotations

import re
from typing import Optional

from app.core.errors import ValidationError


def extract_soft_cta(script: str) -> str:
    script = script.strip()
    if not script:
        raise ValidationError(message="Script is empty", details={})

    sentences = re.findall(r"[^.!?]*[.!?]", script)
    for sentence in reversed(sentences):
        candidate = sentence.strip()
        if candidate:
            return candidate

    words = script.split()
    slice_length = min(4, len(words))
    return " ".join(words[-slice_length:])


def strip_cta_from_script(script: str, cta: str) -> str:
    script = script.strip()
    if not cta:
        return script
    if script.endswith(cta):
        trimmed = script[: -len(cta)].rstrip()
        return trimmed.rstrip("-–—,:;")
    return script


def build_social_description(script: str, source_summary: Optional[str]) -> str:
    if source_summary:
        stripped_summary = source_summary.strip()
        if stripped_summary:
            return re.sub(r"\s+", " ", stripped_summary).strip()
    return script.strip()
