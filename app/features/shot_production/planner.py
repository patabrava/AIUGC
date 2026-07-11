"""Compile one German 16-second-tier script into semantic Veo takes.

Speech estimates use 2.5 spoken words per second plus a 0.25-second allowance
for natural beat-level pacing. Provider capacity is deliberately compiled
separately from the estimated spoken duration.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.core.video_profiles import script_word_count


SPEECH_WORDS_PER_SECOND = 2.5
PACING_ALLOWANCE_SECONDS = 0.25
TARGET_MIN_SECONDS = 3.0
TARGET_MAX_SECONDS = 5.0
CONCISE_FINAL_MIN_SECONDS = 2.0
MIN_BEAT_WORDS = 7
MIN_BEAT_COUNT = 3
MAX_BEAT_COUNT = 4
MIN_SCRIPT_WORDS = MIN_BEAT_WORDS * MIN_BEAT_COUNT

_TRAILING_CLOSERS = "\"'»”’)]}"
_COORDINATING_WORDS = frozenset({"aber", "denn", "doch", "oder", "sondern", "und"})


@dataclass(frozen=True)
class EditorialBeat:
    index: int
    text: str
    word_count: int
    estimated_speech_seconds: float
    provider_duration_seconds: int


def estimate_speech_seconds(word_count: int) -> float:
    """Estimate natural dialogue time from the documented speech-rate contract."""
    if word_count <= 0:
        raise ValueError("Speech estimation requires at least one word.")
    return round((word_count / SPEECH_WORDS_PER_SECOND) + PACING_ALLOWANCE_SECONDS, 2)


def provider_duration_for_estimate(estimated_speech_seconds: float) -> int:
    """Map spoken time to the smallest supported Veo duration bucket."""
    if not math.isfinite(estimated_speech_seconds) or estimated_speech_seconds < 0:
        raise ValueError("Estimated speech seconds must be a finite non-negative number.")
    if estimated_speech_seconds <= 3.25:
        return 4
    if estimated_speech_seconds <= 5.25:
        return 6
    return 8


def _terminal_mark(token: str) -> str:
    return token.rstrip(_TRAILING_CLOSERS)[-1:]


def _is_example_abbreviation(tokens: Sequence[str], index: int) -> bool:
    token = tokens[index].rstrip(_TRAILING_CLOSERS).lower()
    if token == "z.b.":
        return True
    if token == "z." and index + 1 < len(tokens):
        return tokens[index + 1].rstrip(_TRAILING_CLOSERS).lower() == "b."
    if token == "b." and index > 0:
        return tokens[index - 1].rstrip(_TRAILING_CLOSERS).lower() == "z."
    return False


def _boundary_groups(tokens: Sequence[str]) -> Tuple[Dict[int, float], List[int], List[int]]:
    """Return strong boundary costs, comma positions, and conjunction positions."""
    strong: Dict[int, float] = {}
    commas: List[int] = []
    coordinating: List[int] = []

    for index, token in enumerate(tokens[:-1]):
        position = index + 1
        mark = _terminal_mark(token)
        if mark in ".!?" and not (mark == "." and _is_example_abbreviation(tokens, index)):
            strong[position] = 0.0
        elif mark in ":;":
            strong[position] = 0.25
        elif mark == ",":
            commas.append(position)

    for position, token in enumerate(tokens[1:], start=1):
        normalized = re.sub(r"^[^\wÄÖÜäöüß]+|[^\wÄÖÜäöüß]+$", "", token).lower()
        if normalized in _COORDINATING_WORDS and position not in strong and position not in commas:
            coordinating.append(position)

    return strong, commas, coordinating


def _partition_text(tokens: Sequence[str], boundaries: Iterable[int]) -> List[str]:
    parts: List[str] = []
    start = 0
    for end in (*boundaries, len(tokens)):
        parts.append(" ".join(tokens[start:end]))
        start = end
    return parts


def _best_partition(
    tokens: Sequence[str],
    boundary_costs: Dict[int, float],
    *,
    allow_concise_final: bool = False,
) -> Optional[List[str]]:
    positions = sorted(boundary_costs)
    for beat_count in range(MAX_BEAT_COUNT, MIN_BEAT_COUNT - 1, -1):
        if len(positions) < beat_count - 1:
            continue

        candidates = []
        for selected in combinations(positions, beat_count - 1):
            parts = _partition_text(tokens, selected)
            counts = [script_word_count(part) for part in parts]
            estimates = [estimate_speech_seconds(count) for count in counts]
            all_on_target = all(
                TARGET_MIN_SECONDS <= estimate <= TARGET_MAX_SECONDS for estimate in estimates
            )
            concise_final = (
                allow_concise_final
                and all(
                    TARGET_MIN_SECONDS <= estimate <= TARGET_MAX_SECONDS
                    for estimate in estimates[:-1]
                )
                and CONCISE_FINAL_MIN_SECONDS <= estimates[-1] < TARGET_MIN_SECONDS
                and _terminal_mark(parts[-1].split()[-1]) in ".!?"
            )
            if not (all_on_target or concise_final):
                continue

            boundary_penalty = sum(boundary_costs[position] for position in selected)
            pacing_penalty = sum((estimate - 4.0) ** 2 for estimate in estimates)
            candidates.append(((boundary_penalty, pacing_penalty, selected), parts))

        if candidates:
            return min(candidates, key=lambda item: item[0])[1]
    return None


def _semantic_parts(tokens: Sequence[str]) -> Optional[List[str]]:
    strong, commas, coordinating = _boundary_groups(tokens)

    for allow_concise_final in (False, True):
        boundary_costs = dict(strong)
        partition = _best_partition(
            tokens,
            boundary_costs,
            allow_concise_final=allow_concise_final,
        )
        if partition is not None:
            return partition

        boundary_costs.update({position: 1.0 for position in commas})
        partition = _best_partition(
            tokens,
            boundary_costs,
            allow_concise_final=allow_concise_final,
        )
        if partition is not None:
            return partition

        boundary_costs.update({position: 2.0 for position in coordinating})
        partition = _best_partition(
            tokens,
            boundary_costs,
            allow_concise_final=allow_concise_final,
        )
        if partition is not None:
            return partition
    return None


def plan_editorial_beats(script: str) -> List[EditorialBeat]:
    """Plan three or four ordered, complete semantic beats from a spoken script."""
    cleaned = " ".join(str(script or "").split())
    if not cleaned:
        raise ValueError("Editorial beat planning requires a non-empty script.")

    total_words = script_word_count(cleaned)
    if total_words < MIN_SCRIPT_WORDS:
        raise ValueError(
            f"Editorial beat planning requires at least {MIN_SCRIPT_WORDS} words; got {total_words}."
        )

    tokens = cleaned.split()
    parts = _semantic_parts(tokens)
    if parts is None:
        raise ValueError(
            "Script cannot form three or four complete 3-5 second beats at semantic boundaries."
        )

    beats = []
    for index, text in enumerate(parts):
        word_count = script_word_count(text)
        estimate = estimate_speech_seconds(word_count)
        beats.append(
            EditorialBeat(
                index=index,
                text=text,
                word_count=word_count,
                estimated_speech_seconds=estimate,
                provider_duration_seconds=provider_duration_for_estimate(estimate),
            )
        )
    return beats


__all__ = [
    "EditorialBeat",
    "estimate_speech_seconds",
    "plan_editorial_beats",
    "provider_duration_for_estimate",
]
