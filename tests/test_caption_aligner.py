"""Tests for caption script alignment."""

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.adapters.caption_aligner import align_transcript_to_script


def test_perfect_match_preserves_timing():
    transcript = WordLevelTranscript(
        words=[
            Word(word="Ab", start=0.5, end=0.7),
            Word(word="Juli", start=0.8, end=1.1),
            Word(word="wird", start=1.2, end=1.4),
        ],
        full_text="Ab Juli wird",
    )
    script = "Ab Juli wird"
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert [w.word for w in result.words] == ["Ab", "Juli", "wird"]
    assert result.words[0].start == 0.5
    assert result.words[2].end == 1.4


def test_misspelled_word_gets_corrected():
    transcript = WordLevelTranscript(
        words=[
            Word(word="Entlastungs", start=1.0, end=1.5),
            Word(word="budget", start=1.5, end=2.0),
        ],
        full_text="Entlastungs budget",
    )
    script = "Entlastungsbudget"
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert len(result.words) == 1
    assert result.words[0].word == "Entlastungsbudget"
    assert result.words[0].start == 1.0
    assert result.words[0].end == 2.0


def test_extra_deepgram_word_is_dropped():
    transcript = WordLevelTranscript(
        words=[
            Word(word="Ab", start=0.5, end=0.7),
            Word(word="uh", start=0.8, end=0.9),
            Word(word="Juli", start=1.0, end=1.3),
        ],
        full_text="Ab uh Juli",
    )
    script = "Ab Juli"
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert [w.word for w in result.words] == ["Ab", "Juli"]
    assert result.words[0].start == 0.5
    assert result.words[1].start == 1.0


def test_missing_deepgram_word_gets_interpolated():
    transcript = WordLevelTranscript(
        words=[
            Word(word="Ab", start=0.5, end=0.7),
            Word(word="wird", start=1.2, end=1.4),
        ],
        full_text="Ab wird",
    )
    script = "Ab Juli wird"
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert [w.word for w in result.words] == ["Ab", "Juli", "wird"]
    assert result.words[1].start == 0.7
    assert result.words[1].end == 1.2


def test_empty_transcript_returns_empty():
    transcript = WordLevelTranscript(words=[], full_text="")
    script = "Ab Juli wird"
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert result.words == []


def test_empty_script_returns_empty():
    transcript = WordLevelTranscript(
        words=[Word(word="Ab", start=0.5, end=0.7)],
        full_text="Ab",
    )
    script = ""
    result = align_transcript_to_script(transcript=transcript, script=script)
    assert result.words == []


def test_real_german_sentence_alignment():
    transcript = WordLevelTranscript(
        words=[
            Word(word="Ab", start=0.5, end=0.7),
            Word(word="Juli", start=0.8, end=1.1),
            Word(word="2026", start=1.2, end=1.8),
            Word(word="wird", start=1.9, end=2.1),
            Word(word="deine", start=2.2, end=2.5),
            Word(word="häusliche", start=2.6, end=3.1),
            Word(word="Pflege", start=3.2, end=3.6),
            Word(word="mit", start=3.7, end=3.8),
            Word(word="dem", start=3.9, end=4.0),
            Word(word="neuen", start=4.1, end=4.4),
            Word(word="Entlastungs", start=4.5, end=5.0),
            Word(word="budget", start=5.0, end=5.4),
            Word(word="flexibler", start=5.5, end=6.0),
        ],
        full_text="Ab Juli 2026 wird deine häusliche Pflege mit dem neuen Entlastungs budget flexibler",
    )
    script = "Ab Juli 2026 wird deine häusliche Pflege mit dem neuen Entlastungsbudget flexibler."
    result = align_transcript_to_script(transcript=transcript, script=script)
    words = [w.word for w in result.words]
    assert "Entlastungsbudget" in words
    assert "Entlastungs" not in words
    assert "budget" not in words
    assert words[-1] == "flexibler"
    assert len(result.words) == 12
