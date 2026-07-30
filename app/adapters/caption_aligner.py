"""Align Deepgram word-level transcription to the known-correct script.

Deepgram provides accurate timing but can misspell German compound words
or split them incorrectly. This module replaces Deepgram's text with the
original script while preserving Deepgram's word-level timestamps.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.core.logging import get_logger

logger = get_logger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _effective_threshold(a: str, b: str, base_threshold: float) -> float:
    a_norm = _normalize(a)
    b_norm = _normalize(b)
    if min(len(a_norm), len(b_norm)) <= 4:
        return max(base_threshold, 0.75)
    return max(base_threshold, 0.6)


def _strip_trailing_punctuation(word: str) -> str:
    return re.sub(r"[.,!?;:]+$", "", word)


def _clean_script_words(script: str) -> list[str]:
    return [
        cleaned
        for raw in script.split()
        if (cleaned := _strip_trailing_punctuation(raw).strip())
    ]


def _best_match(
    *,
    script_word: str,
    dg_words: list[Word],
    dg_idx: int,
    similarity_threshold: float,
) -> tuple[str, int, float] | None:
    """Return ("single"|"merge", consumed-or-index, score) for the best anchor."""
    if dg_idx >= len(dg_words):
        return None

    single_score = _similarity(dg_words[dg_idx].word, script_word)
    best_single_score = single_score
    best_single_idx = dg_idx
    for lookahead in range(1, min(4, len(dg_words) - dg_idx)):
        score = _similarity(dg_words[dg_idx + lookahead].word, script_word)
        if score > best_single_score:
            best_single_score = score
            best_single_idx = dg_idx + lookahead

    best_merge_score = single_score
    best_merge_count = 1
    for merge_count in range(2, min(5, len(dg_words) - dg_idx + 1)):
        merged_text = "".join(dg_words[dg_idx + j].word for j in range(merge_count))
        score = _similarity(merged_text, script_word)
        if score > best_merge_score:
            best_merge_score = score
            best_merge_count = merge_count

    merge_threshold = _effective_threshold(
        "".join(dg_words[dg_idx + j].word for j in range(best_merge_count)),
        script_word,
        similarity_threshold,
    )
    single_threshold = _effective_threshold(
        dg_words[best_single_idx].word,
        script_word,
        similarity_threshold,
    )

    if (
        best_merge_count > 1
        and best_merge_score >= merge_threshold
        and best_merge_score > best_single_score + 0.1
    ):
        return "merge", best_merge_count, best_merge_score
    if best_single_score >= single_threshold:
        return "single", best_single_idx, best_single_score
    return None


def _match_start_end(match: tuple[str, int, float], dg_words: list[Word], dg_idx: int) -> tuple[float, float, int]:
    kind, value, _score = match
    if kind == "merge":
        merge_count = value
        return (
            float(dg_words[dg_idx].start),
            float(dg_words[dg_idx + merge_count - 1].end),
            dg_idx + merge_count,
        )
    matched_idx = value
    return float(dg_words[matched_idx].start), float(dg_words[matched_idx].end), matched_idx + 1


def _find_future_anchor(
    *,
    script_words: list[str],
    script_idx: int,
    dg_words: list[Word],
    dg_idx: int,
    similarity_threshold: float,
) -> tuple[int, tuple[str, int, float], float] | None:
    """Find the next reliable script/transcript anchor after a mismatch run."""
    search_script_until = min(len(script_words), script_idx + 7)
    for future_script_idx in range(script_idx + 1, search_script_until):
        match = _best_match(
            script_word=script_words[future_script_idx],
            dg_words=dg_words,
            dg_idx=dg_idx,
            similarity_threshold=similarity_threshold,
        )
        if match is None:
            continue
        anchor_start, _anchor_end, _next_dg_idx = _match_start_end(match, dg_words, dg_idx)
        return future_script_idx, match, anchor_start
    return None


def _interpolate_missing_words(
    *,
    words: list[str],
    start: float,
    end: float,
    fallback_step_seconds: float = 0.05,
) -> list[Word]:
    if not words:
        return []
    start = max(float(start), 0.0)
    end = max(float(end), start)
    gap = end - start
    if gap <= 1e-9:
        gap = fallback_step_seconds * len(words)
        end = start + gap
    step = gap / len(words)
    interpolated = []
    cursor = start
    for word in words:
        next_cursor = cursor + step
        interpolated.append(
            Word(
                word=word,
                start=round(cursor, 6),
                end=round(next_cursor, 6),
            )
        )
        cursor = next_cursor
    return interpolated


def align_transcript_to_script(
    *,
    transcript: WordLevelTranscript,
    script: str,
    similarity_threshold: float = 0.4,
) -> WordLevelTranscript:
    if not transcript.words or not script.strip():
        return WordLevelTranscript(words=[], full_text="")

    script_words = _clean_script_words(script)
    dg_words = transcript.words
    aligned: list[Word] = []
    dg_idx = 0
    script_idx = 0
    synthetic_words = 0

    while script_idx < len(script_words):
        clean_script = script_words[script_idx]
        if dg_idx >= len(dg_words):
            tail_words = script_words[script_idx:]
            if aligned:
                tail_start = float(aligned[-1].end)
            else:
                tail_start = float(dg_words[-1].end)
            tail_end = tail_start + min(0.5, max(0.05, 0.05 * len(tail_words)))
            aligned.extend(
                _interpolate_missing_words(
                    words=tail_words,
                    start=tail_start,
                    end=tail_end,
                )
            )
            synthetic_words += len(tail_words)
            break

        match = _best_match(
            script_word=clean_script,
            dg_words=dg_words,
            dg_idx=dg_idx,
            similarity_threshold=similarity_threshold,
        )
        if match is not None:
            start, end, next_dg_idx = _match_start_end(match, dg_words, dg_idx)
            aligned.append(Word(word=clean_script, start=start, end=end))
            dg_idx = next_dg_idx
            script_idx += 1
            continue

        future_anchor = _find_future_anchor(
            script_words=script_words,
            script_idx=script_idx,
            dg_words=dg_words,
            dg_idx=dg_idx,
            similarity_threshold=similarity_threshold,
        )
        if future_anchor is not None:
            anchor_script_idx, _anchor_match, anchor_start = future_anchor
            gap_start = float(aligned[-1].end) if aligned else float(dg_words[dg_idx].start)
            missing = script_words[script_idx:anchor_script_idx]
            aligned.extend(
                _interpolate_missing_words(
                    words=missing,
                    start=gap_start,
                    end=anchor_start,
                )
            )
            synthetic_words += len(missing)
            script_idx = anchor_script_idx
            continue

        gap_start = float(aligned[-1].end) if aligned else float(dg_words[dg_idx].start)
        next_start = float(dg_words[dg_idx].start)
        aligned.extend(
            _interpolate_missing_words(
                words=[clean_script],
                start=gap_start,
                end=next_start,
            )
        )
        synthetic_words += 1
        script_idx += 1

    full_text = " ".join(w.word for w in aligned)

    logger.info(
        "script_alignment_complete",
        script_words=len(script_words),
        deepgram_words=len(dg_words),
        aligned_words=len(aligned),
        consumed_dg_words=dg_idx,
        synthetic_words=synthetic_words,
    )

    return WordLevelTranscript(words=aligned, full_text=full_text)
