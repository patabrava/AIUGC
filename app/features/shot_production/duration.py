"""Canonical duration contract for Semantic UGC video production."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from typing import Optional


MINIMUM_SEMANTIC_UGC_DURATION_SECONDS = 8
DEFAULT_MAXIMUM_SEMANTIC_UGC_DURATION_SECONDS = 60
SAFE_WORDS_PER_TAKE = 18


@dataclass(frozen=True)
class SemanticDurationContract:
    requested_duration_seconds: int
    delivery_min_seconds: float
    delivery_max_seconds: float
    minimum_take_count: int
    minimum_words: int
    maximum_words: int
    minimum_semantic_blocks: int
    maximum_semantic_blocks: int
    maximum_duration_seconds: int

    def as_dict(self) -> dict[str, int | float]:
        return {
            "requested_duration_seconds": self.requested_duration_seconds,
            "delivery_min_seconds": self.delivery_min_seconds,
            "delivery_max_seconds": self.delivery_max_seconds,
            "minimum_take_count": self.minimum_take_count,
            "minimum_words": self.minimum_words,
            "maximum_words": self.maximum_words,
            "minimum_semantic_blocks": self.minimum_semantic_blocks,
            "maximum_semantic_blocks": self.maximum_semantic_blocks,
            "maximum_duration_seconds": self.maximum_duration_seconds,
        }

    @property
    def contract_hash(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_maximum_seconds(maximum_seconds: Optional[int]) -> int:
    value: object = maximum_seconds
    if value is None:
        raw_value = os.getenv(
            "SEMANTIC_UGC_MAX_DURATION_SECONDS",
            str(DEFAULT_MAXIMUM_SEMANTIC_UGC_DURATION_SECONDS),
        )
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "SEMANTIC_UGC_MAX_DURATION_SECONDS must be an integer."
            ) from exc

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Semantic UGC maximum duration must be an integer.")
    if value < MINIMUM_SEMANTIC_UGC_DURATION_SECONDS:
        raise ValueError(
            "Semantic UGC maximum duration must be at least "
            f"{MINIMUM_SEMANTIC_UGC_DURATION_SECONDS} seconds."
        )
    return value


def build_semantic_duration_contract(
    value: int,
    maximum_seconds: Optional[int] = None,
) -> SemanticDurationContract:
    """Build the immutable duration, take-count, and word-envelope contract."""
    configured_maximum = _resolve_maximum_seconds(maximum_seconds)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Semantic UGC duration must be an integer.")
    if not MINIMUM_SEMANTIC_UGC_DURATION_SECONDS <= value <= configured_maximum:
        raise ValueError(
            "Semantic UGC duration must be between "
            f"{MINIMUM_SEMANTIC_UGC_DURATION_SECONDS} and "
            f"{configured_maximum} seconds."
        )

    delivery_min_seconds = value - 1.5
    delivery_max_seconds = value + 0.5
    minimum_take_count = math.ceil(max(4, delivery_min_seconds) / 8)
    minimum_words = max(
        14,
        math.ceil(2.0 * delivery_min_seconds),
        SAFE_WORDS_PER_TAKE * (minimum_take_count - 1) + 1,
    )
    maximum_words = min(
        SAFE_WORDS_PER_TAKE * minimum_take_count,
        math.floor(2.4 * (value - 0.5)),
    )
    if minimum_words > maximum_words:
        raise ValueError(
            "Semantic UGC duration produces an impossible script word envelope."
        )

    return SemanticDurationContract(
        requested_duration_seconds=value,
        delivery_min_seconds=delivery_min_seconds,
        delivery_max_seconds=delivery_max_seconds,
        minimum_take_count=minimum_take_count,
        minimum_words=minimum_words,
        maximum_words=maximum_words,
        minimum_semantic_blocks=minimum_take_count,
        maximum_semantic_blocks=minimum_take_count * 2,
        maximum_duration_seconds=configured_maximum,
    )


__all__ = [
    "DEFAULT_MAXIMUM_SEMANTIC_UGC_DURATION_SECONDS",
    "MINIMUM_SEMANTIC_UGC_DURATION_SECONDS",
    "SAFE_WORDS_PER_TAKE",
    "SemanticDurationContract",
    "build_semantic_duration_contract",
]
