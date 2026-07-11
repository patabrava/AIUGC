from dataclasses import FrozenInstanceError

import pytest

from app.features.shot_production.planner import (
    plan_editorial_beats,
    provider_duration_for_estimate,
)


SENTENCE_SCRIPT = (
    "Viele Menschen warten zu lange mit ihrer Entscheidung. "
    "Dabei kann frühe Beratung den Alltag deutlich erleichtern. "
    "Ein passender Treppenlift schafft Sicherheit auf jeder Etage. "
    "So bleibt dein Zuhause vertraut und selbstständig nutzbar."
)

CLAUSE_SCRIPT = (
    "Ein Treppenlift verändert mehr als nur deine Wege: "
    "er gibt dir jeden Tag ein Stück Freiheit zurück. "
    "Du gewinnst dadurch im ganzen Alltag neue Sicherheit, "
    "dein Zuhause bleibt dabei vertraut und frei nutzbar."
)

COORDINATING_SCRIPT = (
    "Ein Treppenlift verändert mehr als nur deine Wege: "
    "er gibt dir jeden Tag ein Stück Freiheit zurück. "
    "Du gewinnst dadurch im ganzen Alltag neue Sicherheit "
    "aber dein Zuhause bleibt dabei vertraut und frei nutzbar."
)


def test_plans_complete_sentence_beats_for_a_real_16_second_script():
    beats = plan_editorial_beats(SENTENCE_SCRIPT)

    assert [beat.index for beat in beats] == [0, 1, 2, 3]
    assert [beat.text for beat in beats] == [
        "Viele Menschen warten zu lange mit ihrer Entscheidung.",
        "Dabei kann frühe Beratung den Alltag deutlich erleichtern.",
        "Ein passender Treppenlift schafft Sicherheit auf jeder Etage.",
        "So bleibt dein Zuhause vertraut und selbstständig nutzbar.",
    ]
    assert [beat.word_count for beat in beats] == [8, 8, 8, 8]
    assert all(3.0 <= beat.estimated_speech_seconds <= 5.0 for beat in beats)
    assert {beat.provider_duration_seconds for beat in beats} <= {4, 6, 8}


def test_uses_colon_and_comma_clause_boundaries_when_sentences_are_not_enough():
    beats = plan_editorial_beats(CLAUSE_SCRIPT)

    assert [beat.text for beat in beats] == [
        "Ein Treppenlift verändert mehr als nur deine Wege:",
        "er gibt dir jeden Tag ein Stück Freiheit zurück.",
        "Du gewinnst dadurch im ganzen Alltag neue Sicherheit,",
        "dein Zuhause bleibt dabei vertraut und frei nutzbar.",
    ]
    assert all(3.0 <= beat.estimated_speech_seconds <= 5.0 for beat in beats)


def test_uses_a_coordinating_boundary_only_when_strong_boundaries_are_not_enough():
    beats = plan_editorial_beats(COORDINATING_SCRIPT)

    assert [beat.text for beat in beats] == [
        "Ein Treppenlift verändert mehr als nur deine Wege:",
        "er gibt dir jeden Tag ein Stück Freiheit zurück.",
        "Du gewinnst dadurch im ganzen Alltag neue Sicherheit",
        "aber dein Zuhause bleibt dabei vertraut und frei nutzbar.",
    ]


@pytest.mark.parametrize(
    "example_sentence",
    [
        "Fachleute nennen verschiedene praktische Hilfen wie z. B. Treppenlifte.",
        "Fachleute nennen heute verschiedene praktische Hilfen wie z.B. Treppenlifte.",
    ],
)
def test_does_not_treat_german_example_abbreviations_as_terminal_boundaries(example_sentence):
    sentences = [
        "Viele Menschen warten lange mit ihrer Entscheidung.",
        "Frühe Beratung kann den Alltag anschließend deutlich erleichtern.",
        example_sentence,
        "So bleibt dein Zuhause langfristig sicher nutzbar.",
    ]
    script = " ".join(sentences)

    assert len(script.split()) == 31
    assert [beat.text for beat in plan_editorial_beats(script)] == sentences


@pytest.mark.parametrize("script", [SENTENCE_SCRIPT, CLAUSE_SCRIPT])
def test_preserves_every_word_once_and_in_order(script):
    beats = plan_editorial_beats(script)

    assert " ".join(beat.text for beat in beats) == " ".join(script.split())
    assert sum(beat.word_count for beat in beats) == len(script.split())


@pytest.mark.parametrize(
    "estimate,expected",
    [
        (3.25, 4),
        (3.26, 6),
        (5.25, 6),
        (5.26, 8),
    ],
)
def test_provider_duration_thresholds(estimate, expected):
    assert provider_duration_for_estimate(estimate) == expected


def test_editorial_beats_are_immutable():
    beat = plan_editorial_beats(SENTENCE_SCRIPT)[0]

    with pytest.raises(FrozenInstanceError):
        beat.text = "Geändert."


@pytest.mark.parametrize("script", ["", "   ", "Treppenlifte schaffen Sicherheit im Alltag."])
def test_rejects_empty_or_too_short_scripts(script):
    with pytest.raises(ValueError):
        plan_editorial_beats(script)
