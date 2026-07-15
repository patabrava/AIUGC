"""Transcript evidence and timing helpers for independent semantic Veo takes."""

from __future__ import annotations

from dataclasses import dataclass
import math
import unicodedata
from typing import Dict, Optional, Sequence, Tuple

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.features.shot_production.planner import EditorialBeat


_GERMAN_STOPWORDS = frozenset(
    {
        "aber",
        "alle",
        "als",
        "am",
        "an",
        "auch",
        "auf",
        "aus",
        "bei",
        "bin",
        "bis",
        "bist",
        "da",
        "dabei",
        "dadurch",
        "daher",
        "darum",
        "das",
        "dass",
        "dein",
        "deine",
        "dem",
        "den",
        "denn",
        "der",
        "des",
        "die",
        "dies",
        "diese",
        "diesem",
        "diesen",
        "dieser",
        "doch",
        "dort",
        "du",
        "durch",
        "ein",
        "eine",
        "einem",
        "einen",
        "einer",
        "eines",
        "er",
        "es",
        "für",
        "gegen",
        "hat",
        "haben",
        "hier",
        "ich",
        "im",
        "in",
        "ist",
        "ja",
        "kann",
        "mit",
        "nach",
        "nicht",
        "noch",
        "nun",
        "oder",
        "ohne",
        "sein",
        "seine",
        "sich",
        "sie",
        "sind",
        "so",
        "über",
        "um",
        "und",
        "uns",
        "unser",
        "vom",
        "von",
        "vor",
        "war",
        "waren",
        "was",
        "weil",
        "wenn",
        "wie",
        "wir",
        "zu",
        "zum",
        "zur",
    }
)

_GERMAN_ASR_NUMERIC_HOMOPHONES = {
    "achte": frozenset({"8"}),
}


@dataclass(frozen=True)
class TakeTranscriptQA:
    beat_index: int
    expected_text: str
    actual_text: str
    expected_words: Tuple[str, ...]
    actual_words: Tuple[str, ...]
    word_error_rate: float
    first_word_present: bool
    last_word_present: bool
    foreign_words: Tuple[str, ...]
    passed: bool
    failure_reasons: Tuple[str, ...]
    first_word_start_seconds: Optional[float]
    final_word_end_seconds: Optional[float]


def normalize_german_words(text: str) -> Tuple[str, ...]:
    """Lowercase German words and remove punctuation without folding Unicode letters."""
    normalized_words = []
    for raw_word in unicodedata.normalize("NFC", str(text or "")).split():
        word = "".join(character for character in raw_word.lower() if character.isalnum())
        if word:
            normalized_words.append(word)
    return tuple(normalized_words)


def _words_match(expected: str, actual: str) -> bool:
    return expected == actual or actual in _GERMAN_ASR_NUMERIC_HOMOPHONES.get(
        expected, frozenset()
    )


def _levenshtein_distance(expected: Sequence[str], actual: Sequence[str]) -> int:
    previous = list(range(len(actual) + 1))
    for expected_index, expected_word in enumerate(expected, start=1):
        current = [expected_index]
        for actual_index, actual_word in enumerate(actual, start=1):
            substitution_cost = 0 if _words_match(expected_word, actual_word) else 1
            current.append(
                min(
                    current[-1] + 1,
                    previous[actual_index] + 1,
                    previous[actual_index - 1] + substitution_cost,
                )
            )
        previous = current
    return previous[-1]


def _transcript_words_with_sources(
    transcript: WordLevelTranscript,
) -> Tuple[Tuple[str, ...], Tuple[Word, ...]]:
    normalized_words = []
    source_words = []
    for source_word in transcript.words or []:
        for normalized_word in normalize_german_words(source_word.word):
            normalized_words.append(normalized_word)
            source_words.append(source_word)
    return tuple(normalized_words), tuple(source_words)


def _finite_non_negative_seconds(value: object) -> Optional[float]:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def evaluate_take_transcript(
    beat: EditorialBeat,
    transcript: WordLevelTranscript,
    other_beats: Sequence[EditorialBeat],
    max_wer: float = 0.10,
) -> TakeTranscriptQA:
    """Evaluate one raw take against its exact editorial beat and neighboring beats."""
    try:
        threshold = float(max_wer)
    except (TypeError, ValueError) as exc:
        raise ValueError("Maximum word-error rate must be a finite non-negative number.") from exc
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("Maximum word-error rate must be a finite non-negative number.")

    expected_words = normalize_german_words(beat.text)
    actual_words, source_words = _transcript_words_with_sources(transcript)
    edit_distance = _levenshtein_distance(expected_words, actual_words)
    word_error_rate = edit_distance / max(len(expected_words), 1)

    first_expected = expected_words[0] if expected_words else None
    last_expected = expected_words[-1] if expected_words else None
    first_indexes = [
        index
        for index, word in enumerate(actual_words)
        if first_expected is not None and _words_match(first_expected, word)
    ]
    last_indexes = [
        index
        for index, word in enumerate(actual_words)
        if last_expected is not None and _words_match(last_expected, word)
    ]
    first_word_present = bool(first_indexes)
    last_word_present = bool(last_indexes)

    boundary_words_in_order = False
    if first_word_present and last_word_present:
        if len(expected_words) == 1:
            boundary_words_in_order = True
        else:
            boundary_words_in_order = any(
                first_index < last_index
                for first_index in first_indexes
                for last_index in last_indexes
            )

    first_word_start_seconds = None
    if first_expected is not None:
        for actual_word, source_word in zip(actual_words, source_words):
            if _words_match(first_expected, actual_word):
                first_word_start_seconds = _finite_non_negative_seconds(source_word.start)
                break

    final_word_end_seconds = None
    if last_expected is not None:
        for actual_word, source_word in zip(reversed(actual_words), reversed(source_words)):
            if _words_match(last_expected, actual_word):
                final_word_end_seconds = _finite_non_negative_seconds(source_word.end)
                break

    expected_word_set = set(expected_words)
    other_beat_words = {
        word
        for other_beat in other_beats
        for word in normalize_german_words(other_beat.text)
    }
    foreign_candidates = other_beat_words - expected_word_set - _GERMAN_STOPWORDS
    foreign_words = tuple(
        dict.fromkeys(word for word in actual_words if word in foreign_candidates)
    )

    failure_reasons = []
    if not expected_words:
        failure_reasons.append("missing_expected_words")
    else:
        if not first_word_present:
            failure_reasons.append("missing_first_word")
        if not last_word_present:
            failure_reasons.append("missing_last_word")
        if first_word_present and last_word_present and not boundary_words_in_order:
            failure_reasons.append("boundary_words_out_of_order")
    if word_error_rate > threshold:
        failure_reasons.append("word_error_rate_exceeded")
    if foreign_words:
        failure_reasons.append("cross_beat_leakage")

    reasons = tuple(failure_reasons)
    return TakeTranscriptQA(
        beat_index=beat.index,
        expected_text=beat.text,
        actual_text=str(transcript.full_text or ""),
        expected_words=expected_words,
        actual_words=actual_words,
        word_error_rate=word_error_rate,
        first_word_present=first_word_present,
        last_word_present=last_word_present,
        foreign_words=foreign_words,
        passed=not reasons,
        failure_reasons=reasons,
        first_word_start_seconds=first_word_start_seconds,
        final_word_end_seconds=final_word_end_seconds,
    )


def build_take_trim_window(
    qa: TakeTranscriptQA,
    provider_duration_seconds: float,
    head_pad_seconds: float = 0.25,
    tail_pad_seconds: float = 0.25,
    trim_head: bool = True,
) -> Dict[str, object]:
    """Build a speech-window trim with 0.1 seconds of encoder/timestamp seam headroom."""
    if not qa.passed:
        raise ValueError("Cannot trim a take that failed transcript QA.")

    provider_duration = _finite_non_negative_seconds(provider_duration_seconds)
    if provider_duration is None or provider_duration <= 0:
        raise ValueError("Provider duration must be a finite positive number of seconds.")
    tail_pad = _finite_non_negative_seconds(tail_pad_seconds)
    if tail_pad is None:
        raise ValueError("Tail padding must be a finite non-negative number of seconds.")
    head_pad = _finite_non_negative_seconds(head_pad_seconds)
    if head_pad is None:
        raise ValueError("Head padding must be a finite non-negative number of seconds.")
    if head_pad + tail_pad > 0.6 + 1e-9:
        raise ValueError("Combined head and tail padding must not exceed 0.6 seconds.")
    first_word_start = _finite_non_negative_seconds(qa.first_word_start_seconds)
    if first_word_start is None:
        raise ValueError("Accepted transcript QA requires a real Deepgram first-word timestamp.")
    final_word_end = _finite_non_negative_seconds(qa.final_word_end_seconds)
    if final_word_end is None:
        raise ValueError("Accepted transcript QA requires a real Deepgram final-word timestamp.")

    return {
        "start_seconds": max(0.0, first_word_start - head_pad) if trim_head else 0.0,
        "end_seconds": min(provider_duration, final_word_end + tail_pad),
        "source": "deepgram_word_window",
    }


def evaluate_seam_gaps(
    transcript: WordLevelTranscript,
    *,
    beat_word_counts: Sequence[int],
    max_gap_seconds: float = 0.6,
) -> Dict[str, object]:
    """Measure silence between semantic beats in the final stitched transcript."""
    threshold = _finite_non_negative_seconds(max_gap_seconds)
    if threshold is None:
        raise ValueError("Maximum seam gap must be a finite non-negative number.")
    counts = tuple(int(count) for count in beat_word_counts)
    if len(counts) < 2 or any(count <= 0 for count in counts):
        raise ValueError("Seam QA requires at least two positive beat word counts.")
    timed_words = _validated_word_timings(transcript)
    if len(timed_words) != sum(counts):
        raise ValueError("Final transcript word count must match semantic beat word counts.")
    boundary_indexes = []
    cumulative = 0
    for count in counts[:-1]:
        cumulative += count
        boundary_indexes.append(cumulative)
    gaps = []
    for boundary in boundary_indexes:
        previous_end = timed_words[boundary - 1][2]
        next_start = timed_words[boundary][1]
        gaps.append(round(max(0.0, next_start - previous_end), 3))
    failed = [index for index, gap in enumerate(gaps) if gap > threshold + 1e-9]
    return {
        "max_allowed_seconds": threshold,
        "gaps_seconds": gaps,
        "max_observed_seconds": max(gaps, default=0.0),
        "failed_seam_indexes": failed,
        "passed": not failed,
    }


def _validated_word_timings(transcript: WordLevelTranscript) -> Tuple[Tuple[Word, float, float], ...]:
    timed_words = []
    previous_end = 0.0
    for index, word in enumerate(transcript.words or []):
        start = _finite_non_negative_seconds(word.start)
        end = _finite_non_negative_seconds(word.end)
        if start is None or end is None or end < start:
            raise ValueError("Transcript word timings must be finite, non-negative, and monotonic.")
        if index and start < previous_end:
            raise ValueError("Transcript word timings must be monotonic and non-overlapping.")
        timed_words.append((word, start, end))
        previous_end = end
    return tuple(timed_words)


def offset_transcript(
    transcript: WordLevelTranscript,
    offset_seconds: float,
) -> WordLevelTranscript:
    """Copy a transcript with every real word timestamp shifted by one non-negative offset."""
    offset = _finite_non_negative_seconds(offset_seconds)
    if offset is None:
        raise ValueError("Transcript offset must be a finite non-negative number of seconds.")

    words = [
        Word(word=word.word, start=round(start + offset, 6), end=round(end + offset, 6))
        for word, start, end in _validated_word_timings(transcript)
    ]
    return WordLevelTranscript(words=words, full_text=str(transcript.full_text or ""))


def merge_take_transcripts(
    transcripts: Sequence[WordLevelTranscript],
    effective_take_durations: Sequence[float],
) -> WordLevelTranscript:
    """Merge take-level audit transcripts using cumulative effective stitch durations."""
    ordered_transcripts = tuple(transcripts)
    raw_durations = tuple(effective_take_durations)
    if len(ordered_transcripts) != len(raw_durations):
        raise ValueError("Transcript count must match effective take duration count.")

    durations = []
    for duration in raw_durations:
        resolved = _finite_non_negative_seconds(duration)
        if resolved is None:
            try:
                numeric = float(duration)
            except (TypeError, ValueError):
                numeric = math.nan
            if math.isfinite(numeric) and numeric < 0:
                raise ValueError("Effective take durations must be non-negative.")
            raise ValueError("Effective take durations must be finite.")
        durations.append(resolved)

    merged_words = []
    merged_texts = []
    cumulative_offset = 0.0
    for transcript, duration in zip(ordered_transcripts, durations):
        shifted = offset_transcript(transcript, cumulative_offset)
        if merged_words and shifted.words and shifted.words[0].start < merged_words[-1].end:
            raise ValueError("Merged transcript word timings must be monotonic and non-overlapping.")
        merged_words.extend(shifted.words)
        if shifted.full_text.strip():
            merged_texts.append(shifted.full_text.strip())
        cumulative_offset += duration

    return WordLevelTranscript(words=merged_words, full_text=" ".join(merged_texts))


__all__ = [
    "TakeTranscriptQA",
    "build_take_trim_window",
    "evaluate_seam_gaps",
    "evaluate_take_transcript",
    "merge_take_transcripts",
    "normalize_german_words",
    "offset_transcript",
]
