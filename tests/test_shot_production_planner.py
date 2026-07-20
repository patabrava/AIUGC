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

APPROVED_RAMP_SCRIPT = (
    "Jeder, der einen Rollstuhl nutzt, weiß genau: "
    "Normgerechte Rampen sind oft trotzdem eine echte Qual. "
    "Manchmal wird schon eine leichte Steigung zu einem unnötigen Kampf. "
    "Das zehrt an den Kräften."
)

FIFTY_SECOND_SCRIPT = " ".join(
    [
        "Viele Menschen merken im Alltag erst spät, wie viel Kraft kleine Barrieren jeden einzelnen Tag tatsächlich kosten.",
        "Eine zu steile Rampe wirkt auf dem Papier vielleicht harmlos, verlangt aber bei jeder Nutzung volle Konzentration.",
        "Schon beim ersten Anstieg müssen Schultern, Arme und Hände gleichzeitig stabilisieren, lenken und das gesamte Gewicht bewegen.",
        "Wenn dann noch eine enge Kurve folgt, bleibt kaum Raum für einen sicheren und wirklich entspannten Bewegungsablauf.",
        "Das Problem fällt Außenstehenden oft nicht auf, weil wenige Zentimeter Unterschied von weitem völlig unbedeutend erscheinen können.",
        "Für Rollstuhlfahrer summiert sich diese zusätzliche Belastung jedoch über Wege, Termine und viele alltägliche Situationen hinweg.",
        "Darum sollten Rampen nicht nur normgerecht berechnet, sondern gemeinsam mit den Menschen vor Ort praktisch getestet werden.",
    ]
)


def test_plans_minimum_two_eight_second_shots_for_approved_16_second_script():
    beats = plan_editorial_beats(APPROVED_RAMP_SCRIPT)

    assert [beat.index for beat in beats] == [0, 1]
    assert [beat.word_count for beat in beats] == [15, 15]
    assert [beat.provider_duration_seconds for beat in beats] == [8, 8]
    assert " ".join(beat.text for beat in beats) == APPROVED_RAMP_SCRIPT


def test_32_word_16_second_script_forms_two_balanced_full_capacity_takes():
    script = " ".join(f"Wort{index}" for index in range(32))

    beats = plan_editorial_beats(script)

    assert len(beats) == 2
    assert [beat.word_count for beat in beats] == [16, 16]
    assert [beat.provider_duration_seconds for beat in beats] == [8, 8]
    assert " ".join(beat.text for beat in beats) == script


def test_plans_seven_at_most_eight_second_shots_for_fifty_second_script():
    beats = plan_editorial_beats(FIFTY_SECOND_SCRIPT)

    assert len(FIFTY_SECOND_SCRIPT.split()) == 118
    assert len(beats) == 7
    assert all(beat.provider_duration_seconds <= 8 for beat in beats)
    assert all(beat.estimated_speech_seconds <= 7.5 for beat in beats)
    assert " ".join(beat.text for beat in beats) == FIFTY_SECOND_SCRIPT


def test_plans_complete_sentence_beats_for_a_real_16_second_script():
    beats = plan_editorial_beats(SENTENCE_SCRIPT)

    assert [beat.index for beat in beats] == [0, 1]
    assert [beat.text for beat in beats] == [
        "Viele Menschen warten zu lange mit ihrer Entscheidung. Dabei kann frühe Beratung den Alltag deutlich erleichtern.",
        "Ein passender Treppenlift schafft Sicherheit auf jeder Etage. So bleibt dein Zuhause vertraut und selbstständig nutzbar.",
    ]
    assert [beat.word_count for beat in beats] == [16, 16]
    assert all(beat.estimated_speech_seconds <= 7.5 for beat in beats)
    assert {beat.provider_duration_seconds for beat in beats} <= {4, 6, 8}


def test_uses_colon_and_comma_clause_boundaries_when_sentences_are_not_enough():
    beats = plan_editorial_beats(CLAUSE_SCRIPT)

    assert [beat.text for beat in beats] == [
        "Ein Treppenlift verändert mehr als nur deine Wege: er gibt dir jeden Tag ein Stück Freiheit zurück.",
        "Du gewinnst dadurch im ganzen Alltag neue Sicherheit, dein Zuhause bleibt dabei vertraut und frei nutzbar.",
    ]
    assert all(beat.estimated_speech_seconds <= 7.5 for beat in beats)


def test_uses_a_coordinating_boundary_only_when_strong_boundaries_are_not_enough():
    beats = plan_editorial_beats(COORDINATING_SCRIPT)

    assert [beat.text for beat in beats] == [
        "Ein Treppenlift verändert mehr als nur deine Wege: er gibt dir jeden Tag ein Stück Freiheit zurück.",
        "Du gewinnst dadurch im ganzen Alltag neue Sicherheit aber dein Zuhause bleibt dabei vertraut und frei nutzbar.",
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
    beats = plan_editorial_beats(script)
    assert len(beats) == 2
    assert " ".join(beat.text for beat in beats) == script
    assert all("z." not in beat.text or "B." in beat.text for beat in beats)


def test_keeps_a_complete_concise_ending_inside_the_minimum_shot_plan():
    script = (
        "Jeder, der einen Rollstuhl nutzt, weiß genau: "
        "Normgerechte Rampen sind oft trotzdem eine echte Qual. "
        "Manchmal fühlt sich jeder Zentimeter Steigung wie ein unnötiger Kampf an. "
        "Das zehrt an den Kräften."
    )

    beats = plan_editorial_beats(script)

    assert [beat.text for beat in beats] == [
        "Jeder, der einen Rollstuhl nutzt, weiß genau: Normgerechte Rampen sind oft trotzdem eine echte Qual.",
        "Manchmal fühlt sich jeder Zentimeter Steigung wie ein unnötiger Kampf an. Das zehrt an den Kräften.",
    ]
    assert all(beat.estimated_speech_seconds <= 7.5 for beat in beats)
    assert beats[-1].provider_duration_seconds == 8
    assert " ".join(beat.text for beat in beats) == script


def test_preserves_a_three_word_final_sentence_as_a_four_second_semantic_beat():
    script = (
        "Diese Rampe kostet jeden Morgen unnötig viel Kraft und verlangt trotz Planung volle Konzentration bei jeder einzelnen Nutzung. "
        "Schon kleine Steigungen belasten Schultern und Hände deutlich und machen den vertrauten Weg am Ende wieder unnötig anstrengend. "
        "Das kostet Kraft."
    )

    beats = plan_editorial_beats(script)

    assert [beat.word_count for beat in beats] == [18, 18, 3]
    assert [beat.provider_duration_seconds for beat in beats] == [8, 8, 4]
    assert [beat.text for beat in beats] == [
        "Diese Rampe kostet jeden Morgen unnötig viel Kraft und verlangt trotz Planung volle Konzentration bei jeder einzelnen Nutzung.",
        "Schon kleine Steigungen belasten Schultern und Hände deutlich und machen den vertrauten Weg am Ende wieder unnötig anstrengend.",
        "Das kostet Kraft.",
    ]


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


@pytest.mark.parametrize("script", ["", "   ", "Zu kurz."])
def test_rejects_empty_or_too_short_scripts(script):
    with pytest.raises(ValueError):
        plan_editorial_beats(script)
