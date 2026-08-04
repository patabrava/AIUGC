"""Shared policy for choosing free QA review versus localized paid retry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def acoustic_qa_requires_localized_paid_retry(
    qa_failure: Any,
    pipeline_manifest: Any,
) -> bool:
    """Return true when existing takes cannot satisfy the acoustic contract."""
    if not isinstance(qa_failure, Mapping):
        return False
    if qa_failure.get("stage") != "acoustic_qa":
        return False

    retry_mode = str(qa_failure.get("retry_mode") or "")
    if retry_mode == "qa_only":
        return False
    if retry_mode == "localized_paid_take":
        return True

    manifest = pipeline_manifest if isinstance(pipeline_manifest, Mapping) else {}
    acoustic_plan_failure = manifest.get("acoustic_plan_failure")
    delivery_visual_qa = manifest.get("delivery_visual_qa")
    details = qa_failure.get("details")
    return bool(
        (
            isinstance(acoustic_plan_failure, Mapping)
            and acoustic_plan_failure.get("recommended_retry_take_indexes")
        )
        or (
            isinstance(delivery_visual_qa, Mapping)
            and delivery_visual_qa.get("requires_paid_regeneration") is True
        )
        or (
            isinstance(details, Mapping)
            and details.get("requires_paid_regeneration") is True
        )
    )


__all__ = ["acoustic_qa_requires_localized_paid_retry"]
