from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
import math

import pytest

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.features.shot_production.composer import (
    TakeTranscriptQA,
    build_take_trim_window,
    evaluate_seam_gaps,
    evaluate_take_transcript,
    merge_take_transcripts,
    normalize_german_words,
    offset_transcript,
)
from app.features.shot_production.planner import EditorialBeat


def _beat(index: int, text: str) -> EditorialBeat:
    return EditorialBeat(
        index=index,
        text=text,
        word_count=len(text.split()),
        estimated_speech_seconds=3.5,
        provider_duration_seconds=4,
    )


def _transcript(*timed_words: tuple[str, float, float]) -> WordLevelTranscript:
    return WordLevelTranscript(
        words=[Word(word=word, start=start, end=end) for word, start, end in timed_words],
        full_text=" ".join(word for word, _, _ in timed_words),
    )


def test_exact_transcript_returns_complete_immutable_qa_evidence():
    beat = _beat(2, "Viele Menschen warten heute.")
    transcript = _transcript(
        ("Viele", 0.10, 0.35),
        ("Menschen", 0.40, 0.80),
        ("warten", 0.85, 1.20),
        ("heute", 1.25, 1.60),
    )

    qa = evaluate_take_transcript(beat, transcript, other_beats=[])

    assert [field.name for field in fields(TakeTranscriptQA)] == [
        "beat_index",
        "expected_text",
        "actual_text",
        "expected_words",
        "actual_words",
        "word_error_rate",
        "first_word_present",
        "last_word_present",
        "foreign_words",
        "passed",
        "failure_reasons",
        "first_word_start_seconds",
        "final_word_end_seconds",
    ]
    assert qa == TakeTranscriptQA(
        beat_index=2,
        expected_text="Viele Menschen warten heute.",
        actual_text="Viele Menschen warten heute",
        expected_words=("viele", "menschen", "warten", "heute"),
        actual_words=("viele", "menschen", "warten", "heute"),
        word_error_rate=0.0,
        first_word_present=True,
        last_word_present=True,
        foreign_words=(),
        passed=True,
        failure_reasons=(),
        first_word_start_seconds=0.10,
        final_word_end_seconds=1.60,
    )
    with pytest.raises(FrozenInstanceError):
        qa.passed = False  # type: ignore[misc]


def test_normalizes_german_case_punctuation_umlauts_and_eszett():
    assert normalize_german_words("ÄPFEL, Grüße! Fußgänger-Straße; groß?") == (
        "äpfel",
        "grüße",
        "fußgängerstraße",
        "groß",
    )

    beat = _beat(0, "Äpfel, Grüße und die Straße!")
    transcript = _transcript(
        ("ÄPFEL", 0.0, 0.2),
        ("Grüße!", 0.3, 0.5),
        ("UND", 0.6, 0.7),
        ("die", 0.8, 0.9),
        ("Straße", 1.0, 1.3),
    )

    qa = evaluate_take_transcript(beat, transcript, other_beats=[])

    assert qa.passed is True
    assert qa.word_error_rate == 0.0
    assert qa.first_word_start_seconds == 0.0
    assert qa.final_word_end_seconds == 1.3


def test_one_substitution_contributes_one_word_error():
    beat = _beat(0, "Viele Menschen warten heute.")
    transcript = _transcript(
        ("Viele", 0.0, 0.2),
        ("Leute", 0.3, 0.5),
        ("warten", 0.6, 0.8),
        ("heute", 0.9, 1.1),
    )

    qa = evaluate_take_transcript(beat, transcript, other_beats=[])

    assert qa.word_error_rate == 0.25
    assert qa.passed is False
    assert qa.failure_reasons == ("word_error_rate_exceeded",)
    assert evaluate_take_transcript(beat, transcript, other_beats=[], max_wer=0.25).passed


@pytest.mark.parametrize(
    ("words", "missing_reason", "first_present", "last_present"),
    [
        (
            (("Menschen", 0.0, 0.3), ("warten", 0.4, 0.7), ("heute", 0.8, 1.1)),
            "missing_first_word",
            False,
            True,
        ),
        (
            (("Viele", 0.0, 0.2), ("Menschen", 0.3, 0.6), ("warten", 0.7, 1.0)),
            "missing_last_word",
            True,
            False,
        ),
    ],
)
def test_missing_boundary_word_fails_closed(
    words, missing_reason, first_present, last_present
):
    beat = _beat(0, "Viele Menschen warten heute.")

    qa = evaluate_take_transcript(
        beat,
        _transcript(*words),
        other_beats=[],
        max_wer=1.0,
    )

    assert qa.passed is False
    assert qa.first_word_present is first_present
    assert qa.last_word_present is last_present
    assert missing_reason in qa.failure_reasons
    if not last_present:
        assert qa.final_word_end_seconds is None


def test_boundary_words_must_appear_in_order():
    beat = _beat(0, "Anfang bleibt bis Ende.")
    transcript = _transcript(
        ("Ende", 0.0, 0.2),
        ("bleibt", 0.3, 0.5),
        ("Anfang", 0.6, 0.9),
    )

    qa = evaluate_take_transcript(beat, transcript, other_beats=[], max_wer=1.0)

    assert qa.first_word_present is True
    assert qa.last_word_present is True
    assert qa.passed is False
    assert "boundary_words_out_of_order" in qa.failure_reasons


def test_unique_cross_beat_word_blocks_take_even_with_acceptable_wer():
    beat = _beat(
        0,
        "Viele Menschen gewinnen mit Beratung zuhause wieder täglich mehr Sicherheit.",
    )
    other = _beat(1, "Und der Aufzug bringt neue Freiheit.")
    transcript = _transcript(
        ("Viele", 0.0, 0.2),
        ("Menschen", 0.2, 0.4),
        ("gewinnen", 0.4, 0.6),
        ("mit", 0.6, 0.8),
        ("Beratung", 0.8, 1.0),
        ("zuhause", 1.0, 1.2),
        ("wieder", 1.2, 1.4),
        ("täglich", 1.4, 1.6),
        ("mehr", 1.6, 1.8),
        ("Aufzug", 1.8, 2.0),
        ("Sicherheit", 2.0, 2.2),
    )

    qa = evaluate_take_transcript(beat, transcript, other_beats=[other])

    assert qa.word_error_rate == 0.1
    assert qa.foreign_words == ("aufzug",)
    assert qa.passed is False
    assert qa.failure_reasons == ("cross_beat_leakage",)


def test_common_stopwords_from_other_beats_do_not_count_as_leakage():
    beat = _beat(
        0,
        "Viele Menschen gewinnen mit Beratung zuhause wieder täglich mehr Sicherheit.",
    )
    other = _beat(1, "Und der Aufzug bringt neue Freiheit.")
    transcript = _transcript(
        ("Viele", 0.0, 0.2),
        ("Menschen", 0.2, 0.4),
        ("gewinnen", 0.4, 0.6),
        ("mit", 0.6, 0.8),
        ("Beratung", 0.8, 1.0),
        ("zuhause", 1.0, 1.2),
        ("wieder", 1.2, 1.4),
        ("täglich", 1.4, 1.6),
        ("mehr", 1.6, 1.8),
        ("und", 1.8, 2.0),
        ("Sicherheit", 2.0, 2.2),
    )

    qa = evaluate_take_transcript(beat, transcript, other_beats=[other])

    assert qa.word_error_rate == 0.1
    assert qa.foreign_words == ()
    assert qa.passed is True


def test_trim_window_uses_real_final_word_timestamp_plus_tail_pad_and_clamps():
    qa = evaluate_take_transcript(
        _beat(0, "Viele Menschen warten heute."),
        _transcript(
            ("Viele", 0.1, 0.3),
            ("Menschen", 0.4, 0.7),
            ("warten", 0.8, 1.1),
            ("heute", 1.2, 1.8),
        ),
        other_beats=[],
    )

    assert build_take_trim_window(qa, provider_duration_seconds=4.0) == {
        "start_seconds": 0.0,
        "end_seconds": 2.05,
        "source": "deepgram_word_window",
    }
    assert build_take_trim_window(qa, provider_duration_seconds=2.0) == {
        "start_seconds": 0.0,
        "end_seconds": 2.0,
        "source": "deepgram_word_window",
    }

    late_start = evaluate_take_transcript(
        _beat(1, "Viele Menschen warten heute."),
        _transcript(
            ("Viele", 0.8, 1.0),
            ("Menschen", 1.1, 1.4),
            ("warten", 1.5, 1.8),
            ("heute", 1.9, 2.2),
        ),
        other_beats=[],
    )
    assert build_take_trim_window(late_start, provider_duration_seconds=4.0) == {
        "start_seconds": 0.55,
        "end_seconds": 2.45,
        "source": "deepgram_word_window",
    }
    assert build_take_trim_window(
        late_start,
        provider_duration_seconds=4.0,
        trim_head=False,
    )["start_seconds"] == 0.0


@pytest.mark.parametrize(
    "qa_mutation",
    [
        {"passed": False},
        {"final_word_end_seconds": None},
        {"final_word_end_seconds": math.nan},
    ],
)
def test_trim_window_rejects_failed_or_timestamp_less_qa(qa_mutation):
    valid = evaluate_take_transcript(
        _beat(0, "Viele warten heute."),
        _transcript(
            ("Viele", 0.0, 0.2),
            ("warten", 0.3, 0.5),
            ("heute", 0.6, 0.9),
        ),
        other_beats=[],
    )

    with pytest.raises(ValueError):
        build_take_trim_window(replace(valid, **qa_mutation), provider_duration_seconds=4.0)


@pytest.mark.parametrize("duration", [0.0, -1.0, math.inf, math.nan])
def test_trim_window_rejects_invalid_provider_duration(duration):
    qa = evaluate_take_transcript(
        _beat(0, "Viele warten heute."),
        _transcript(
            ("Viele", 0.0, 0.2),
            ("warten", 0.3, 0.5),
            ("heute", 0.6, 0.9),
        ),
        other_beats=[],
    )

    with pytest.raises(ValueError):
        build_take_trim_window(qa, provider_duration_seconds=duration)


def test_offset_transcript_returns_new_words_at_requested_offset():
    original = _transcript(
        ("Viele", 0.1, 0.3),
        ("Menschen", 0.4, 0.8),
    )

    shifted = offset_transcript(original, 2.0)

    assert shifted.full_text == original.full_text
    assert [(word.word, word.start, word.end) for word in shifted.words] == [
        ("Viele", 2.1, 2.3),
        ("Menschen", 2.4, 2.8),
    ]
    assert [(word.start, word.end) for word in original.words] == [(0.1, 0.3), (0.4, 0.8)]
    assert shifted.words[0] is not original.words[0]


def test_merge_take_transcripts_uses_cumulative_effective_durations_monotonically():
    first = _transcript(
        ("Viele", 0.0, 0.3),
        ("Menschen", 0.4, 1.0),
    )
    second = _transcript(
        ("warten", 0.1, 0.4),
        ("heute", 0.5, 0.8),
    )

    merged = merge_take_transcripts(
        [first, second],
        effective_take_durations=[1.35, 0.9],
    )

    assert merged.full_text == "Viele Menschen warten heute"
    assert [(word.word, word.start, word.end) for word in merged.words] == [
        ("Viele", 0.0, 0.3),
        ("Menschen", 0.4, 1.0),
        ("warten", 1.45, 1.75),
        ("heute", 1.85, 2.15),
    ]
    assert all(
        current.end <= following.start
        for current, following in zip(merged.words, merged.words[1:])
    )


def test_merge_take_transcripts_rejects_count_mismatch_and_negative_durations():
    transcript = _transcript(("Hallo", 0.0, 0.4))

    with pytest.raises(ValueError, match="count"):
        merge_take_transcripts([transcript], effective_take_durations=[])
    with pytest.raises(ValueError, match="non-negative"):
        merge_take_transcripts([transcript], effective_take_durations=[-0.1])
    with pytest.raises(ValueError, match="finite"):
        merge_take_transcripts([transcript], effective_take_durations=[math.inf])


def test_seam_gap_qa_enforces_each_semantic_cut_at_six_tenths_or_less():
    transcript = _transcript(
        ("eins", 0.0, 0.3),
        ("zwei", 0.4, 0.8),
        ("drei", 1.4, 1.8),
        ("vier", 1.9, 2.2),
        ("fünf", 2.81, 3.1),
    )

    report = evaluate_seam_gaps(transcript, beat_word_counts=[2, 2, 1], max_gap_seconds=0.6)

    assert report["gaps_seconds"] == [0.6, 0.61]
    assert report["passed"] is False
    assert report["failed_seam_indexes"] == [1]


def test_offset_and_merge_reject_non_monotonic_word_timings():
    non_monotonic = _transcript(
        ("später", 0.8, 1.0),
        ("früher", 0.2, 0.4),
    )

    with pytest.raises(ValueError, match="monotonic"):
        offset_transcript(non_monotonic, 0.0)
    with pytest.raises(ValueError, match="monotonic"):
        merge_take_transcripts([non_monotonic], effective_take_durations=[1.0])
