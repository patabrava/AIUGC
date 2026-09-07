"""Canonical duration contract for Semantic UGC video production."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from typing import Any, Mapping, Optional


MINIMUM_SEMANTIC_UGC_DURATION_SECONDS = 8
DEFAULT_MAXIMUM_SEMANTIC_UGC_DURATION_SECONDS = 60
SAFE_WORDS_PER_TAKE = 18
EXACT_SHORT_FORM_DURATION_SECONDS = 16
DELIVERY_CONTRACT_FPS = 24.0
SEMANTIC_END_PAN_TAIL_EXCLUSION_SECONDS = 0.5
# One 48 kHz AAC frame preserves the final phoneme while keeping the last visible
# 24 fps frame inside the actor's active speech articulation.
SEMANTIC_TERMINAL_SPEECH_GUARD_SECONDS = 1024.0 / 48000.0


def semantic_terminal_speech_cut_floor(final_word_end_seconds: object) -> float:
    """Return the earliest safe source cut after the final spoken syllable."""
    try:
        final_word_end = float(final_word_end_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Terminal protection requires a finite final-word timestamp."
        ) from exc
    if not math.isfinite(final_word_end) or final_word_end < 0:
        raise ValueError(
            "Terminal protection requires a finite non-negative final-word timestamp."
        )
    return final_word_end + SEMANTIC_TERMINAL_SPEECH_GUARD_SECONDS


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
    duration_mode: str = "fixed"
    estimated_speech_seconds: float | None = None

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            **({"duration_mode": self.duration_mode} if self.duration_mode != "fixed" else {}),
            **({"estimated_speech_seconds": self.estimated_speech_seconds} if self.estimated_speech_seconds is not None else {}),
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


ADAPTIVE_MANUAL_DURATION = "manual_script_v1"


def build_manual_duration_contract(script: str) -> SemanticDurationContract:
    """Size provider capacity from verbatim copy; delivery follows verified speech."""
    from app.core.video_profiles import script_word_count
    from app.features.shot_production.planner import plan_manual_editorial_beats

    if len(script) > 4000:
        raise ValueError("Manual scripts support up to 4000 characters.")
    beats = plan_manual_editorial_beats(script)
    words = script_word_count(script)
    estimate = sum(beat.estimated_speech_seconds for beat in beats)
    capacity = sum(beat.provider_duration_seconds for beat in beats)
    # The run-table integer remains an estimate >=8 for rolling compatibility.
    # The explicit mode and speech envelope govern actual delivered duration.
    return SemanticDurationContract(
        requested_duration_seconds=max(8, math.ceil(estimate)),
        delivery_min_seconds=0.5,
        delivery_max_seconds=float(capacity),
        minimum_take_count=len(beats),
        minimum_words=words,
        maximum_words=words,
        minimum_semantic_blocks=1,
        maximum_semantic_blocks=len(beats),
        maximum_duration_seconds=max(8, capacity),
        duration_mode=ADAPTIVE_MANUAL_DURATION,
        estimated_speech_seconds=round(estimate, 2),
    )


def resolve_post_duration_contract(post: Mapping[str, Any], batch: Mapping[str, Any]) -> SemanticDurationContract:
    """Keep historical approvals fixed until the operator saves/reapproves them."""
    seed = post.get("seed_data") or {}
    if (batch.get("creation_mode") == "manual_semantic_ugc"
            and seed.get("semantic_duration_mode") == ADAPTIVE_MANUAL_DURATION):
        return build_manual_duration_contract(str(seed.get("script") or post.get("script") or ""))
    return build_semantic_duration_contract(batch.get("target_duration_seconds"))


def resolve_run_duration_contract(run: Mapping[str, Any]) -> SemanticDurationContract:
    stored = run.get("duration_contract") or {}
    if stored.get("duration_mode") == ADAPTIVE_MANUAL_DURATION:
        script = run.get("script_snapshot") or {}
        if script.get("creation_mode") != "manual_semantic_ugc":
            raise ValueError("Adaptive duration requires manual script provenance.")
        contract = build_manual_duration_contract(str(script.get("text") or ""))
        if contract.as_dict() != stored or contract.contract_hash != run.get("duration_contract_hash"):
            raise ValueError("Manual duration contract changed after approval.")
        return contract
    return build_semantic_duration_contract(int(run.get("requested_duration_seconds") or 0))


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

    if value == EXACT_SHORT_FORM_DURATION_SECONDS:
        frame_seconds = 1.0 / DELIVERY_CONTRACT_FPS
        delivery_min_seconds = value - frame_seconds
        delivery_max_seconds = value + frame_seconds
    else:
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
    "DELIVERY_CONTRACT_FPS",
    "EXACT_SHORT_FORM_DURATION_SECONDS",
    "MINIMUM_SEMANTIC_UGC_DURATION_SECONDS",
    "SAFE_WORDS_PER_TAKE",
    "SEMANTIC_END_PAN_TAIL_EXCLUSION_SECONDS",
    "SEMANTIC_TERMINAL_SPEECH_GUARD_SECONDS",
    "SemanticDurationContract",
    "build_semantic_duration_contract",
    "semantic_terminal_speech_cut_floor",
]
