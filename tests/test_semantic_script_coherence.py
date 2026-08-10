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
    "bestimmen nicht deinen gesamten Tagesablauf. "
    "Prüfe Wetter, Untergrund und Ruheplätze vorab, damit spontane Änderungen nicht "
    "deine gesamte Kraft für den Tag verbrauchen. "
    "Sprich Begleitung und Treffpunkte vorher ab, damit alle Beteiligten bei "
    "Verspätungen denselben einfachen Ausweichplan kennen. "
    "Plane Rückweg und Akkureserve gemeinsam, damit ein längerer Ausflug nicht durch "
    "vermeidbare Unsicherheit vorzeitig endet. "
    "Notiere unterwegs funktionierende Zugänge, damit du gute Lösungen später "
    "wiederfindest und anderen zuverlässig direkt weitergeben kannst. "
    "Packe notwendige Hilfsmittel griffbereit ein, bevor du dein Zuhause verlässt. "
    "Vereinbare für unterwegs vorher einen klaren Treffpunkt mit deiner vertrauten "
    "Begleitung."
)

VALUE_RECOVERY_SOURCE = (
    "Prüfe öffentliche Wege heute vorab auf abgesenkte Bordsteine, sichere Querungen "
    "und erreichbare Alternativen für unerwartete Sperrungen. "
    "Dokumentiere konkrete Barrieren mit Ort, Zeitpunkt und Foto, damit zuständige "
    "Stellen den Hinweis nachvollziehen können. "
    "Frage bei Veranstaltern oder Behörden früh nach Zugängen, Begleitung und einer "
    "verlässlichen Ausweichroute für deinen Termin. "
    "So sparst du unnötige Umwege und kannst Entscheidungen auf klare, überprüfbare "
    "Informationen statt Vermutungen stützen. "
    "Speichere bestätigte Öffnungszeiten und Ansprechpartner direkt, damit du bei "
    "kurzfristigen Änderungen schnell verlässliche Auskunft erhältst. "
    "Vergleiche mindestens zwei erreichbare Routen, bevor du dich auf eine Verbindung "
    "für deinen wichtigen Termin festlegst. "
    "Teile geprüfte Zugänge mit anderen, damit verlässliche Hinweise schneller "
    "gefunden und gemeinsam aktuell gehalten werden können. "
    "Nimm für längere Wege Ladegerät, Medikamente und notwendige Hilfsmittel mit, "
    "damit eine Verzögerung deinen Plan nicht sofort beendet. "
    "Melde neue Hindernisse sofort mit genauer Ortsangabe an zuständige Stellen. "
    "Hole vor wichtigen Wegen bei deiner Begleitung eine kurze Rückmeldung ein."
)

PRODUCT_RECOVERY_SOURCE = (
    "Ein Plattformlift kann gerade, kurvige, steile oder enge Treppen für deinen "
    "Alltag wieder sicher nutzbar machen. "
    "Vor dem Einbau des Lifts werden Fahrweg, Platz, Tragkraft und Bedienung gemeinsam an deine "
    "konkrete Wohnsituation angepasst. "
    "Eine verständliche Liftsteuerung und klar erreichbare Haltepunkte erleichtern dir "
    "die regelmäßige Nutzung ohne unnötige Umwege. "
    "Kläre Wartung, mögliche Nachrüstung und die gewünschte Ausstattung früh, damit "
    "die Liftlösung langfristig zu dir passt. "
    "Lass dir Notabsenkung, Sicherheitsstopps und tägliche Bedienung praktisch zeigen, "
    "bevor du den Plattformlift regelmäßig alleine nutzt. "
    "Prüfe Stromanschluss, Parkposition und freie Durchgänge gemeinsam, damit der Lift "
    "keine neuen Hindernisse im Zuhause schafft. "
    "Vereinbare klare Lift-Wartungsintervalle, damit Verschleiß früh erkannt und die sichere "
    "Nutzung dauerhaft zuverlässig erhalten bleibt. "
    "Bewahre Lift-Kontaktdaten, Serviceunterlagen und Bedienhinweise griffbereit auf, damit "
    "bei einer Störung schnell die passende Fachhilfe erreicht werden kann. "
    "Teste alle Lift-Bedienelemente selbst, bevor der tägliche Einsatz verbindlich beginnt. "
    "Halte den Bereich um den Lift dauerhaft frei von losen Gegenständen."
)


class _UnavailableLLM:
    def generate_gemini_text(self, **_kwargs):
        raise ThirdPartyError("provider unavailable")


def _sentence(index: int, word_count: int) -> str:
    sentence_banks = [
        "Geprüfte Aufzugsmeldungen zeigen früh verlässliche Alternativen für spontane Termine ohne unnötige Wartezeit und hektische Rückwege heute deutlich",
        "Breite Wendeflächen erleichtern sichere Richtungswechsel im engen Eingangsbereich und bewahren täglich wertvolle Kraft bei jedem Besuch zuverlässig",
        "Aktuelle Baustellenhinweise verhindern überraschende Sperrungen während wichtiger Fahrten und nennen erreichbare Ersatzrouten rechtzeitig vor der Abreise klar",
        "Gespeicherte Ansprechpartner organisieren passende Unterstützung direkt sobald technische Störungen auftreten oder fremde Hilfe kurzfristig notwendig wird unkompliziert",
        "Niedrige Bedienelemente ermöglichen selbstständige Nutzung zuhause weil Tasten bequem erreichbar bleiben und klare Symbole jeden Schritt verständlich begleiten",
        "Rutschfeste Bodenflächen geben dem Rollstuhl stabilen Halt beim Einsteigen und senken das Risiko gefährlicher Bewegungen auf nassen Wegen",
        "Regelmäßige Wartung erhält die zuverlässige Funktion langfristig erkennt Verschleiß früh und schützt geplante Abläufe vor vermeidbaren Ausfällen wirksam",
        "Früh gebuchte Mobilitätshilfen sichern ruhige Bahnreisen nennen eindeutige Treffpunkte und vermeiden belastende Suche auf unbekannten Bahnsteigen zuverlässig",
    ]
    words = sentence_banks[index].split()
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


@pytest.mark.parametrize(
    "script",
    [
        (
            "Als Rollstuhlnutzerin kennst du diese Unsicherheit, wenn Menschen "
            "vermeintlich helfen wollen. Doch diese gut gemeinte, aber fehlgeleitete "
            "Unterstützung führt oft zu unangebrachten Fragen oder bevormundendem "
            "Verhalten. Stattdessen basiert ein respektvoller Umgang auf Natürlichkeit "
            "und der Wahrnehmung als vollwertige Person. Schluss mit übergriffiger "
            "Pseudo Hilfe. Dies führt häufig zu übergriffigem Verhalten und "
            "unangebrachten Fragen, die bei Betroffenen als verletzend oder "
            "diskriminierend empfunden werden."
        ),
        (
            "Wenn PNV Umwege und im Alltag mehr Kraft kostet, merkst du das oft erst "
            "nach mehreren kleinen Umwegen. Genau solche Routinen nehmen Druck raus, "
            "wenn der Alltag sowieso schon genug Energie kostet. So bleibt mehr Kraft "
            "für das, was du eigentlich vorhast, statt für zusätzliche Barrieren "
            "draufzugehen. Gerade bei Wenn PNV Umwege summieren sich kleine Umwege "
            "schneller, als viele von außen erwarten."
        ),
    ],
)
def test_repeated_live_source_claims_are_rejected_as_non_audience_copy(script):
    with pytest.raises(ValueError, match="repeated source"):
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


def test_repetitive_audited_32_second_source_uses_family_recovery():
    source = (
        "Als Rollstuhlnutzerin kennst du diese Unsicherheit, wenn Menschen "
        "vermeintlich helfen wollen. Doch diese gut gemeinte, aber fehlgeleitete "
        "Unterstützung führt oft zu unangebrachten Fragen oder bevormundendem "
        "Verhalten. Stattdessen basiert ein respektvoller Umgang auf Natürlichkeit "
        "und der Wahrnehmung als vollwertige Person. Schluss mit übergriffiger "
        "Pseudo Hilfe. Dies führt häufig zu übergriffigem Verhalten und "
        "unangebrachten Fragen, die bei Betroffenen als verletzend oder "
        "diskriminierend empfunden werden."
    )

    result = generate_semantic_script(
        post_type="value",
        title="Respektvoll helfen",
        cta="Speichere dir die Hinweise.",
        facts=[source],
        recovery_facts=[VALUE_RECOVERY_SOURCE],
        requested_duration_seconds=32,
        llm_client=_UnavailableLLM(),
    )

    validate_semantic_script_audience_copy(result.script)
    assert result.provenance["source"] == "deterministic_recovery"
    assert "unangebrachten Fragen" not in result.script


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
            "Bordsteinkanten erschweren sichere Wege im Rollstuhl-Alltag. "
            "Getrennte Überquerungsstelle: Diese Variante sieht zwei Bereiche vor.",
            "recovery scaffolding",
        ),
        (
            "(TAKE 1) Eine Treppe schien mir oft unüberwindbar. "
            "(TAKE 2) Mit einem passenden Lift wird sie wieder nutzbar.",
            "recovery scaffolding",
        ),
        (
            "TAKE 1 Als Rollstuhlfahrerin antworte ich selbst auf direkte Fragen. "
            "Meine Begleitung unterstützt nur, wenn ich ausdrücklich darum bitte.",
            "recovery scaffolding",
        ),
        (
            "Kennst du das Gefühl, wenn du denkst, eine Treppe ist einfach zu "
            "kompliziert? Das gibt mir ein echt gutes Gefühl der Freiheit.",
            "generic padding",
        ),
        (
            "Die flexible Anpassung von Stuhl zu Plattform auf derselben Schiene "
            "macht den Alltag wirklich einfacher.",
            "generic padding",
        ),
    ],
)
def test_non_substantive_source_sentences_are_rejected(script, message):
    with pytest.raises(ValueError, match=message):
        validate_semantic_script_audience_copy(script)


def test_semantic_script_rejects_verbatim_editorial_topic_title():
    title = (
        "Handreichung oder Hürde? Die drei größten Helfer-Fehler, "
        "die Rollstuhlfahrende nerven"
    )
    script = (
        "Gut gemeinte Hilfe wird zur Hürde, wenn sie ohne Rückfrage beginnt. "
        "Handreichung oder Hürde? Die drei größten Helfer-Fehler, die "
        "Rollstuhlfahrende nerven. Frage zuerst, welche Unterstützung wirklich "
        "gewünscht ist."
    )

    with pytest.raises(ValueError, match="editorial topic title"):
        validate_semantic_script_audience_copy(script, topic_title=title)


def test_semantic_product_script_rejects_vague_pronoun_copy():
    script = (
        "Manchmal überrascht mich die Vielseitigkeit unserer Lösungen für den "
        "Alltag immer noch. Heute nutze ich ihn drinnen und draußen."
    )

    with pytest.raises(ValueError, match="must name the lift"):
        validate_semantic_script_audience_copy(script, post_type="product")


@pytest.mark.parametrize(
    "script",
    [
        (
            "Reedereien verlangen auf manchen Reisen eine Begleitperson. "
            "Mehr Platz und breitere Türen als in Standardkabinen."
        ),
        (
            "Treppen unterscheiden sich in vielen Wohnungen deutlich. "
            "Gerade, kurvig, mal steil, mal eng."
        ),
    ],
)
def test_semantic_script_rejects_punctuated_sentence_fragments(script):
    with pytest.raises(ValueError, match="sentence fragment"):
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


@pytest.mark.parametrize("post_type", ["value", "lifestyle"])
def test_32_second_recovery_waits_for_topic_aware_generation(post_type):
    topic_script = _valid_script_for_duration(32)

    class _TopicAwareLLM:
        def __init__(self):
            self.calls = []

        def generate_gemini_text(self, **kwargs):
            self.calls.append(kwargs)
            return topic_script

    client = _TopicAwareLLM()
    result = generate_semantic_script(
        post_type=post_type,
        title="Sicher entscheiden",
        cta="Speichere dir den Hinweis.",
        facts=["Ein kurzer, aber eindeutiger Themenhinweis."],
        recovery_facts=[
            VALUE_RECOVERY_SOURCE
            if post_type == "value"
            else LIFESTYLE_RECOVERY_SOURCE
        ],
        requested_duration_seconds=32,
        llm_client=client,
    )

    assert result.script == topic_script
    assert result.provenance["source"] == "gemini"
    assert len(client.calls) == 1
    assert "Sicher entscheiden" in client.calls[0]["prompt"]


def test_topic_aware_generation_gets_bounded_final_repair_before_family_recovery():
    valid_script = _valid_script_for_duration(32)

    class _LateValidLLM:
        def __init__(self):
            self.calls = []

        def generate_gemini_text(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) < 4:
                return "Dieser Entwurf ist weiterhin deutlich zu kurz."
            return valid_script

    client = _LateValidLLM()
    result = generate_semantic_script(
        post_type="lifestyle",
        title="Reifendruck vor längeren Wegen prüfen",
        cta="Speichere dir den Wochencheck.",
        facts=["Der richtige Reifendruck reduziert vermeidbare Pannen unterwegs."],
        recovery_facts=[LIFESTYLE_RECOVERY_SOURCE],
        requested_duration_seconds=32,
        llm_client=client,
    )

    assert result.script == valid_script
    assert result.provenance["source"] == "gemini_repair"
    assert len(client.calls) == 4
    assert "exakt 64 Wörter insgesamt" in client.calls[1]["prompt"]
    assert "jeder Satz hat exakt 16 Wörter" in client.calls[1]["prompt"]


def test_32_second_prompt_requires_ceiling_words_per_take():
    prompt = build_semantic_script_prompt(
        post_type="lifestyle",
        title="Reifendruck vor längeren Wegen prüfen",
        cta="Speichere dir den Wochencheck.",
        facts=["Der richtige Reifendruck reduziert vermeidbare Pannen unterwegs."],
        requested_duration_seconds=32,
    )

    assert "Satzlänge: 16 bis 18 Wörter" in prompt


def test_near_valid_provider_script_is_padded_without_family_recovery():
    near_valid_script = " ".join(_sentence(index, 15) for index in range(4))

    class _NearValidLLM:
        def __init__(self):
            self.calls = 0

        def generate_gemini_text(self, **_kwargs):
            self.calls += 1
            return near_valid_script

    client = _NearValidLLM()
    result = generate_semantic_script(
        post_type="lifestyle",
        title="Reifendruck vor längeren Wegen prüfen",
        cta="Speichere dir den Wochencheck.",
        facts=["Der richtige Reifendruck reduziert vermeidbare Pannen unterwegs."],
        recovery_facts=[LIFESTYLE_RECOVERY_SOURCE],
        requested_duration_seconds=32,
        llm_client=client,
    )

    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=32,
    )
    assert validation.word_count == 64
    assert result.provenance["source"] == "gemini"
    assert client.calls == 1


@pytest.mark.parametrize(
    ("post_type", "recovery_source"),
    [
        ("value", VALUE_RECOVERY_SOURCE),
        ("lifestyle", LIFESTYLE_RECOVERY_SOURCE),
        ("product", PRODUCT_RECOVERY_SOURCE),
    ],
)
@pytest.mark.parametrize("seconds", range(8, 61))
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
