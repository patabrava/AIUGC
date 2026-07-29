from __future__ import annotations

import pytest

from app.core.errors import ThirdPartyError
from app.core.video_profiles import script_word_count
from app.features.shot_production.duration import build_semantic_duration_contract
from app.features.shot_production.planner import plan_editorial_beats
from app.features.topics.semantic_scripts import (
    build_semantic_script_prompt,
    generate_semantic_script,
    validate_semantic_script,
    validate_semantic_script_audience_copy,
)


SCREENSHOT_FAILURE = (
    "Wichtig bleibt: „Der Kassenschalter…“ und Wichtig bleibt: „…Niemand…“ und "
    "Wichtig bleibt: „…doch…“ und Wichtig bleibt: „…sein“. Prüfe für deine nächste "
    "wichtige Entscheidung bitte erneut „Kassenschalter“ rechtzeitig und plane deine "
    "sichere Alternative sorgfältig."
)

KASSENSCHALTER_SOURCES = [
    (
        "Der Kassenschalter ist oft wie für eine andere Welt gebaut. Niemand redet "
        "über diese unsichtbare Architektur. Normale Arbeitsräume brauchen drei Meter "
        "Deckenhöhe, doch für Kassen reichen teils nur 2,1 Meter. Während "
        "Steharbeitsplätze 96 Zentimeter hoch sind, müssen barrierefreie Bereiche für "
        "dich maximal 80 Zentimeter erreichen. Seit Juni 2025 müssen zudem neue "
        "Zahlungsterminals endlich barrierefrei sein."
    ),
    (
        "Kassenschalter nehmen baurechtlich eine besondere Position ein und dürfen "
        "unter bestimmten Bedingungen deutlich niedrigere Räume nutzen."
    ),
    (
        "Barrierefreie Schalterbereiche dürfen für Rollstuhlnutzende höchstens achtzig "
        "Zentimeter hoch sein und müssen gut erreichbar bleiben."
    ),
    (
        "Neue Zahlungsterminals und Self-Checkout-Kioske müssen seit Juni 2025 nach "
        "dem BFSG barrierefrei bedienbar sein."
    ),
    (
        "Erreichbare Displays und genügend freie Bewegungsfläche helfen vielen "
        "Menschen beim selbstständigen Bezahlen ohne fremde Unterstützung."
    ),
]


class _UnavailableLLM:
    def generate_gemini_text(self, **_kwargs):
        raise ThirdPartyError("provider unavailable")


def _sentence(index: int, word_count: int) -> str:
    words = [
        "Dieser",
        f"Hinweis{index}",
        "erklärt",
        "dir",
        "einen",
        "konkreten",
        "Zusammenhang",
        "für",
        "deine",
        "nächste",
        "sichere",
        "Entscheidung",
        "im",
        "Alltag",
        "klar",
        "verständlich",
        "und",
        "vollständig",
    ]
    return f"{' '.join(words[:word_count])}."


def _valid_script_for_duration(seconds: int) -> str:
    contract = build_semantic_duration_contract(seconds)
    base_words, extra_words = divmod(
        contract.minimum_words,
        contract.minimum_take_count,
    )
    counts = [
        base_words + (1 if index < extra_words else 0)
        for index in range(contract.minimum_take_count)
    ]
    return " ".join(
        _sentence(index, word_count)
        for index, word_count in enumerate(counts)
    )


def test_screenshot_fragment_collage_is_rejected_as_non_audience_copy():
    with pytest.raises(ValueError, match="recovery scaffolding"):
        validate_semantic_script_audience_copy(SCREENSHOT_FAILURE)


@pytest.mark.parametrize("seconds", [8, 16, 32])
def test_provider_failure_recovers_only_from_complete_kassenschalter_statements(seconds):
    result = generate_semantic_script(
        post_type="value",
        title="Versteckte Höhen am Kassenschalter",
        cta="Prüfe die Schalterhöhe vor dem Einbau.",
        facts=KASSENSCHALTER_SOURCES,
        requested_duration_seconds=seconds,
        llm_client=_UnavailableLLM(),
    )
    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=seconds,
    )

    validate_semantic_script_audience_copy(result.script)
    assert validation.planned_take_count == validation.minimum_take_count
    assert result.provenance["source"] == "fallback"
    assert "Wichtig bleibt" not in result.script
    assert "…" not in result.script
    assert all(
        beat.text.endswith((".", "!", "?"))
        for beat in plan_editorial_beats(result.script)
    )


@pytest.mark.parametrize("seconds", [8, 16, 32])
def test_provider_failure_refuses_short_fragments_instead_of_padding_them(seconds):
    with pytest.raises(ValueError, match="complete sourced statements"):
        generate_semantic_script(
            post_type="value",
            title="Kassenschalter",
            cta="",
            facts=["Achtung.", "Niemand.", "Doch."],
            requested_duration_seconds=seconds,
            llm_client=_UnavailableLLM(),
        )


@pytest.mark.parametrize("seconds", range(8, 61))
def test_generation_accepts_coherent_balanced_scripts_for_every_supported_duration(seconds):
    expected_script = _valid_script_for_duration(seconds)

    class _ValidLLM:
        def generate_gemini_text(self, **_kwargs):
            return expected_script

    result = generate_semantic_script(
        post_type="value",
        title="Sicher entscheiden",
        cta="Speichere dir den Hinweis.",
        facts=["Jeder vollständige Hinweis basiert auf einer belegten Information."],
        requested_duration_seconds=seconds,
        llm_client=_ValidLLM(),
    )
    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=seconds,
    )

    assert result.script == expected_script
    assert validation.word_count == script_word_count(expected_script)
    assert validation.planned_take_count == validation.minimum_take_count
    assert all(
        8 <= script_word_count(beat.text) <= 18
        for beat in plan_editorial_beats(result.script)
    )


@pytest.mark.parametrize("seconds", [8, 16, 32])
def test_duration_prompt_requires_a_logical_arc_and_forbids_fragment_collages(seconds):
    prompt = build_semantic_script_prompt(
        post_type="value",
        title="Kassenschalter",
        cta="Prüfe die Höhe.",
        facts=KASSENSCHALTER_SOURCES,
        requested_duration_seconds=seconds,
    )

    assert "Keine Zitat-Collagen, Auslassungszeichen, Fragmente" in prompt
    assert f"exakt {build_semantic_duration_contract(seconds).minimum_take_count}" in prompt
    assert "logisch" in prompt
