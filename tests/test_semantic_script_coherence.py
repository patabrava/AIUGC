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

PRODUCTION_SAFE_SOURCE = (
    "Ein Plattformlift macht tägliche Wege zuhause zuverlässig planbar. "
    "Die Bedienung bleibt auch bei häufiger Nutzung gut verständlich. "
    "Breite Plattformen geben Rollstühlen beim Auffahren genügend Platz. "
    "Klare Haltepunkte erleichtern das sichere Einsteigen im eigenen Zuhause. "
    "Vor dem Einbau prüfst du gemeinsam Platz, Bedienung, Fahrweg und die passende "
    "Position für deine täglichen Wege. "
    "So bleibt die gewählte Lösung langfristig verlässlich, ruhig nutzbar und auf "
    "deine konkrete Wohnsituation sinnvoll abgestimmt."
)

INCOMPATIBLE_LIFESTYLE_SOURCE = (
    "Spontane Freizeit braucht im Rollstuhl oft mehr Planung als man von außen sieht. "
    "Mit einer klaren Routine bleibst du im Alltag trotzdem deutlich entspannter. "
    "Genau solche Kleinigkeiten entscheiden oft darüber, ob sich ein Weg leicht oder "
    "unnötig anstrengend anfühlt. "
    "Darüber wird selten gesprochen, obwohl es im Rollstuhl-Alltag ständig wieder "
    "passiert. "
    "Wenn du das einmal sauber gelöst hast, sparst du dir später Zeit, Kraft und Nerven."
)

LIFESTYLE_RECOVERY_SOURCE = (
    "Prüfe unbekannte Wege vorab auf Zugänge, erreichbare Toiletten und mögliche "
    "Umwege, bevor du deinen Ausflug startest. "
    "Plane eine kurze Pause und eine erreichbare Alternative ein, falls der direkte "
    "Weg unterwegs plötzlich blockiert ist. "
    "Speichere wichtige Adressen und Telefonnummern vorher griffbereit, damit du bei "
    "Änderungen nicht lange suchen musst. "
    "So bleibt mehr Energie für dein eigentliches Ziel, und kleine Barrieren "
    "bestimmen nicht deinen gesamten Tagesablauf."
)

VALUE_RECOVERY_SOURCE = (
    "Prüfe öffentliche Wege heute vorab auf abgesenkte Bordsteine, sichere Querungen "
    "und erreichbare Alternativen für unerwartete Sperrungen. "
    "Dokumentiere konkrete Barrieren mit Ort, Zeitpunkt und Foto, damit zuständige "
    "Stellen den Hinweis nachvollziehen können. "
    "Frage bei Veranstaltern oder Behörden früh nach Zugängen, Begleitung und einer "
    "verlässlichen Ausweichroute für deinen Termin. "
    "So sparst du unnötige Umwege und kannst Entscheidungen auf klare, überprüfbare "
    "Informationen statt Vermutungen stützen."
)

PRODUCT_RECOVERY_SOURCE = (
    "Ein Plattformlift kann gerade, kurvige, steile oder enge Treppen für deinen "
    "Alltag wieder sicher nutzbar machen. "
    "Vor dem Einbau werden Fahrweg, Platz, Tragkraft und Bedienung gemeinsam an deine "
    "konkrete Wohnsituation angepasst. "
    "Eine verständliche Steuerung und klar erreichbare Haltepunkte erleichtern dir "
    "die regelmäßige Nutzung ohne unnötige Umwege. "
    "Kläre Wartung, mögliche Nachrüstung und die gewünschte Ausstattung früh, damit "
    "die Lösung langfristig zu dir passt."
)


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


def test_raw_source_url_is_rejected_as_non_audience_copy():
    script = (
        "Die passende Antwort findest du bei BUNTE: "
        "https://www.bunte.de/family/leben-und-erziehen/."
    )

    with pytest.raises(ValueError, match="external reference"):
        validate_semantic_script_audience_copy(script)


def test_acronym_source_heading_is_rejected_as_non_audience_copy():
    script = (
        "Halte für wichtige Geräte immer einen geladenen Reserve-Akku bereit. "
        "Implantierte Defibrillatoren (ICD): Eine schnelle Batterieentleerung kann "
        "die lebensrettende Schockfunktion beeinträchtigen."
    )

    with pytest.raises(ValueError, match="external reference"):
        validate_semantic_script_audience_copy(script)


def test_audited_32_second_source_with_raw_url_uses_family_recovery():
    source = (
        "Als Rollstuhlnutzerin kennst du das: Ständig diese übergriffigen Blicke und "
        "dummen Fragen im Alltag. "
        "Netzseiten zeigen, wie du ruhig reagieren und dich gegen aufdringliche "
        "Bemerkungen wehren kannst. "
        "Dieses Phänomen ist tief in gesellschaftlichen Normen und Erwartungen "
        "verwurzelt und betrifft Menschen in verschiedensten Lebenssituationen. "
        "Die passende Antwort steht hier: "
        "https://www.bunte.de/family/leben-und-erziehen/psycho-fallen.html."
    )

    result = generate_semantic_script(
        post_type="value",
        title="Die unsichtbare Last",
        cta="Speichere dir die Hinweise.",
        facts=[source],
        recovery_facts=[VALUE_RECOVERY_SOURCE],
        requested_duration_seconds=32,
        llm_client=_UnavailableLLM(),
    )

    validate_semantic_script_audience_copy(result.script)
    assert result.provenance["source"] == "deterministic_recovery"
    assert "http" not in result.script.casefold()


@pytest.mark.parametrize(
    ("script", "message"),
    [
        (
            "Sie führen zu falschen Behandlungen und vermeidbaren "
            "Krankenhauseinweisungen.",
            "unresolved source reference",
        ),
        (
            "Als Rollstuhlnutzerin suchst du oft lange nach passenden Orten. "
            "Doch es gibt eine Lösung!",
            "generic padding",
        ),
        (
            "Dieser individuell gefertigte Lift passt sich jeder Treppe an. "
            "So ist man immer gut unterwegs.",
            "generic padding",
        ),
        (
            "Ein individuell gefertigter Treppenlift gibt dir Freiheit, jede "
            "Treppe mühelos zu überwinden, und das ist toll.",
            "generic padding",
        ),
        (
            "Der 5-Sekunden-Check für barrierefreie Toiletten Dieses Dossier "
            "sammelt Fakten und zur schnellen Bewertung von WC-Anlagen.",
            "recovery scaffolding",
        ),
        (
            "(TAKE 1) Eine Treppe schien mir oft unüberwindbar. "
            "(TAKE 2) Mit einem passenden Lift wird sie wieder nutzbar.",
            "recovery scaffolding",
        ),
    ],
)
def test_non_substantive_source_sentences_are_rejected(script, message):
    with pytest.raises(ValueError, match=message):
        validate_semantic_script_audience_copy(script)


@pytest.mark.parametrize(
    "source",
    [
        (
            "Sie führen zu falschen Behandlungen und vermeidbaren "
            "Krankenhauseinweisungen. Die korrekte Verwaltung deiner Unterlagen ist "
            "entscheidend für deine Gesundheit."
        ),
        (
            "Dieser individuell gefertigte Lift passt sich jeder Treppe an. "
            "So ist man immer gut unterwegs."
        ),
    ],
)
def test_eight_second_source_fragment_or_padding_uses_family_recovery(source):
    result = generate_semantic_script(
        post_type="value",
        title="Planbare Entscheidungen",
        cta="Speichere dir die Hinweise.",
        facts=[source],
        recovery_facts=[VALUE_RECOVERY_SOURCE],
        requested_duration_seconds=8,
        llm_client=_UnavailableLLM(),
    )

    validate_semantic_script_audience_copy(result.script)
    assert result.provenance["source"] == "deterministic_recovery"


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
    assert result.provenance["source"] == (
        "audited_source" if seconds == 32 else "fallback"
    )
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


@pytest.mark.parametrize("post_type", ["value", "lifestyle", "product"])
@pytest.mark.parametrize("seconds", [8, 16, 32])
def test_full_family_duration_matrix_recovers_without_fragments_or_padding(
    post_type,
    seconds,
):
    client = _UnavailableLLM()
    result = generate_semantic_script(
        post_type=post_type,
        title="Planbare Wege zuhause",
        cta="Prüfe die passende Lösung für deinen Alltag.",
        facts=[PRODUCTION_SAFE_SOURCE],
        requested_duration_seconds=seconds,
        llm_client=client,
    )
    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=seconds,
    )
    beats = plan_editorial_beats(result.script)

    validate_semantic_script_audience_copy(result.script)
    assert validation.planned_take_count == validation.minimum_take_count
    assert len({beat.text.casefold() for beat in beats}) == len(beats)
    assert "außerdem gilt" not in result.script.casefold()
    assert "…" not in result.script
    assert all(8 <= script_word_count(beat.text) <= 18 for beat in beats)
    assert result.provenance["source"] == (
        "audited_source" if seconds == 32 else "fallback"
    )


@pytest.mark.parametrize("post_type", ["value", "lifestyle", "product"])
def test_valid_32_second_audited_source_bypasses_provider_regeneration(post_type):
    class _MustNotRun:
        def generate_gemini_text(self, **_kwargs):
            raise AssertionError("validated audited source should bypass Gemini")

    result = generate_semantic_script(
        post_type=post_type,
        title="Planbare Wege zuhause",
        cta="Prüfe die passende Lösung für deinen Alltag.",
        facts=[
            PRODUCTION_SAFE_SOURCE,
            PRODUCTION_SAFE_SOURCE.replace("Plattformlift", "Plattform-Lift"),
        ],
        requested_duration_seconds=32,
        llm_client=_MustNotRun(),
    )

    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=32,
    )
    validate_semantic_script_audience_copy(result.script)
    assert result.provenance["source"] == "audited_source"
    assert validation.planned_take_count == 4
    assert result.script.count("Plattformlift") == 1
    assert "außerdem gilt" not in result.script.casefold()


@pytest.mark.parametrize(
    ("post_type", "recovery_source"),
    [
        ("value", VALUE_RECOVERY_SOURCE),
        ("lifestyle", LIFESTYLE_RECOVERY_SOURCE),
        ("product", PRODUCT_RECOVERY_SOURCE),
    ],
)
@pytest.mark.parametrize("seconds", [8, 16, 32])
def test_provider_exhaustion_uses_complete_family_recovery_source(
    post_type,
    recovery_source,
    seconds,
):
    result = generate_semantic_script(
        post_type=post_type,
        title="Spontane Freizeit planbar machen",
        cta="Speichere dir diese Planung.",
        facts=["Kurzer Hinweis."],
        recovery_facts=[recovery_source],
        requested_duration_seconds=seconds,
        llm_client=_UnavailableLLM(),
    )
    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=seconds,
    )

    validate_semantic_script_audience_copy(result.script)
    assert validation.planned_take_count == validation.minimum_take_count
    assert result.provenance["source"] == "deterministic_recovery"
    assert "außerdem gilt" not in result.script.casefold()
    assert "…" not in result.script


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
