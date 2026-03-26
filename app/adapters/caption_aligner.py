"""Align Deepgram word-level transcription to the known-correct script.

Deepgram provides accurate timing but can misspell German compound words
or split them incorrectly. This module replaces Deepgram's text with the
original script while preserving Deepgram's word-level timestamps.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.core.logging import get_logger

logger = get_logger(__name__)


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _strip_trailing_punctuation(word: str) -> str:
    return re.sub(r"[.,!?;:]+$", "", word)


def align_transcript_to_script(
    *,
    transcript: WordLevelTranscript,
    script: str,
    similarity_threshold: float = 0.4,
) -> WordLevelTranscript:
    if not transcript.words or not script.strip():
        return WordLevelTranscript(words=[], full_text="")

    script_words = script.split()
    dg_words = transcript.words
    aligned: list[Word] = []
    dg_idx = 0

    for script_word in script_words:
        clean_script = _strip_trailing_punctuation(script_word)

        if dg_idx >= len(dg_words):
            if aligned:
                aligned.append(Word(
                    word=clean_script,
                    start=aligned[-1].end,
                    end=aligned[-1].end + 0.3,
                ))
            continue

        single_score = _similarity(dg_words[dg_idx].word, clean_script)

        # Check lookahead for a better single-word match (handles filler words like "uh")
        best_single_score = single_score
        best_single_idx = dg_idx
        for lookahead in range(1, min(4, len(dg_words) - dg_idx)):
            score = _similarity(dg_words[dg_idx + lookahead].word, clean_script)
            if score > best_single_score:
                best_single_score = score
                best_single_idx = dg_idx + lookahead

        # Try merging consecutive Deepgram words starting at dg_idx
        best_merge_score = single_score
        best_merge_count = 1
        for merge_count in range(2, min(5, len(dg_words) - dg_idx + 1)):
            merged_text = "".join(
                dg_words[dg_idx + j].word for j in range(merge_count)
            )
            score = _similarity(merged_text, clean_script)
            if score > best_merge_score:
                best_merge_score = score
                best_merge_count = merge_count

        # Prefer merge if it's clearly better than the best single match
        use_merge = (
            best_merge_count > 1
            and best_merge_score >= similarity_threshold
            and best_merge_score > best_single_score + 0.1
        )

        if use_merge:
            aligned.append(Word(
                word=clean_script,
                start=dg_words[dg_idx].start,
                end=dg_words[dg_idx + best_merge_count - 1].end,
            ))
            dg_idx += best_merge_count
        elif best_single_score >= similarity_threshold:
            aligned.append(Word(
                word=clean_script,
                start=dg_words[best_single_idx].start,
                end=dg_words[best_single_idx].end,
            ))
            dg_idx = best_single_idx + 1
        else:
            if aligned:
                gap_start = aligned[-1].end
            else:
                gap_start = dg_words[dg_idx].start
            gap_end = dg_words[dg_idx].start if dg_idx < len(dg_words) else gap_start + 0.3
            if gap_end <= gap_start:
                gap_end = gap_start + 0.3
            aligned.append(Word(
                word=clean_script,
                start=gap_start,
                end=gap_end,
            ))

    full_text = " ".join(w.word for w in aligned)

    logger.info(
        "script_alignment_complete",
        script_words=len(script_words),
        deepgram_words=len(dg_words),
        aligned_words=len(aligned),
        consumed_dg_words=dg_idx,
    )

    return WordLevelTranscript(words=aligned, full_text=full_text)
