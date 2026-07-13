import re
from types import SimpleNamespace

import pytest

from app.core.video_profiles import script_word_count
from app.features.shot_production.duration import build_semantic_duration_contract
from app.features.shot_production.planner import plan_editorial_beats
from app.features.topics.semantic_scripts import (
    build_semantic_script_prompt,
    generate_semantic_script,
    validate_semantic_script,
)
import app.features.topics.semantic_scripts as semantic_scripts


def _words(count: int) -> str:
    return " ".join(f"Wort{index}" for index in range(count)) + "."


def _complete_semantic_script(word_counts: list[int]) -> str:
    filler = ("konkret", "frühzeitig", "sicher", "praktisch", "bewusst", "direkt")
    sentences = []
    for index, word_count in enumerate(word_counts):
        prefix = ["Dieser", f"Hinweis{index}", "hilft", "dir"]
        suffix = ["bei", "einer", "klaren", "Entscheidung", "weiter"]
        middle = [
            filler[offset % len(filler)]
            for offset in range(word_count - len(prefix) - len(suffix))
        ]
        sentences.append(" ".join([*prefix, *middle, *suffix]) + ".")
    return " ".join(sentences)


def _sentences(script: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", script)
        if sentence.strip()
    ]


def _normalized_template_signature(sentence: str) -> str:
    tokens = re.findall(r"[A-Za-zÀ-ÿÄÖÜäöüß]+", sentence.casefold())[:7]
    signature = " ".join(tokens)
    return re.sub(
        r"\b(dieser|diesen)\s+\w+\b",
        r"\1 <modifier>",
        signature,
    )


class _FakeLLM:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def generate_gemini_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.text


@pytest.mark.parametrize(
    ("post_type", "seconds"),
    [("value", 17), ("lifestyle", 33), ("product", 50)],
)
def test_semantic_prompt_renders_arbitrary_duration_without_tier_file(
    post_type,
    seconds,
):
    contract = build_semantic_duration_contract(seconds)

    prompt = build_semantic_script_prompt(
        post_type=post_type,
        title="Barrierefreie Bahnreisen",
        cta="Speichere dir den Tipp.",
        facts=["Der Mobilitätsservice muss häufig vorab gebucht werden."],
        requested_duration_seconds=seconds,
        language="Deutsch",
        actor_context="Ruhige, direkte Ansprache in die Kamera.",
    )

    assert str(seconds) in prompt
    assert str(contract.minimum_words) in prompt
    assert str(contract.maximum_words) in prompt
    assert str(contract.minimum_take_count) in prompt
    assert post_type.upper() in prompt
    assert "Barrierefreie Bahnreisen" in prompt
    assert "Mobilitätsservice" in prompt
    assert "Speichere dir den Tipp." in prompt
    assert "Ruhige, direkte Ansprache" in prompt


def test_generated_script_must_fit_same_contract_and_strips_response_wrappers():
    valid_script = _complete_semantic_script([16, 16, 16, 16, 15, 15, 15])
    fake_llm = _FakeLLM(f"```text\nSkript: {valid_script}\n```")

    result = generate_semantic_script(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="Speichere dir den Tipp.",
        facts=["Fakt"],
        requested_duration_seconds=50,
        llm_client=fake_llm,
    )

    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=50,
    )
    assert validation.minimum_take_count == 7
    assert result.script.startswith("Dieser Hinweis0")
    assert "```" not in result.script
    assert result.contract_hash == validation.contract_hash
    assert result.provenance["source"] == "gemini"
    assert len(fake_llm.calls) == 1


@pytest.mark.parametrize("word_count", [108, 119])
def test_semantic_script_validation_rejects_copy_outside_word_envelope(word_count):
    with pytest.raises(ValueError, match="word envelope"):
        validate_semantic_script(_words(word_count), requested_duration_seconds=50)


def test_semantic_script_validation_rejects_repeated_padding_sentences():
    sentence = (
        "Dieser Fakt hilft dir heute bei einer klaren sicheren und gut "
        "vorbereiteten Entscheidung im Alltag weiter."
    )
    script = " ".join([sentence] * 7)
    assert script_word_count(script) == 112

    with pytest.raises(ValueError, match="distinct sentences"):
        validate_semantic_script(script, requested_duration_seconds=50)


def test_validation_rejects_planner_fragments_without_terminal_punctuation():
    one_long_sentence = _words(109)
    beats = plan_editorial_beats(one_long_sentence)
    assert len(beats) == 7
    assert any(not beat.text.endswith((".", "!", "?")) for beat in beats)

    with pytest.raises(ValueError, match="complete semantic statement"):
        validate_semantic_script(
            one_long_sentence,
            requested_duration_seconds=50,
        )


def test_validation_rejects_extra_take_without_recorded_exception(monkeypatch):
    valid_script = _complete_semantic_script([16, 16, 16, 16, 15, 15, 15])
    monkeypatch.setattr(
        semantic_scripts,
        "plan_editorial_beats",
        lambda _script: [
            SimpleNamespace(index=index, text=f"Vollständiger Satz {index}.")
            for index in range(8)
        ],
    )

    with pytest.raises(ValueError, match="recorded semantic-boundary exception"):
        validate_semantic_script(valid_script, requested_duration_seconds=50)


def test_validation_records_reason_for_one_unavoidable_extra_take(monkeypatch):
    valid_script = _complete_semantic_script([16, 16, 16, 16, 15, 15, 15])
    monkeypatch.setattr(
        semantic_scripts,
        "plan_editorial_beats",
        lambda _script: [
            SimpleNamespace(index=index, text=f"Vollständiger Satz {index}.")
            for index in range(8)
        ],
    )

    validation = validate_semantic_script(
        valid_script,
        requested_duration_seconds=50,
        take_count_exception_reason="CTA muss als eigener vollständiger Satz stehen.",
    )

    assert validation.planned_take_count == 8
    assert validation.take_count_exception == {
        "minimum_take_count": 7,
        "planned_take_count": 8,
        "reason": "CTA muss als eigener vollständiger Satz stehen.",
    }


def test_provider_fallback_uses_multiple_supplied_facts():
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    result = generate_semantic_script(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="Speichere dir den Tipp.",
        facts=[
            "Der Mobilitätsservice braucht eine frühe Buchung.",
            "Aufzüge können kurzfristig außer Betrieb sein.",
            "Das Merkzeichen B erlaubt kostenlose Begleitung.",
        ],
        requested_duration_seconds=50,
        llm_client=_UnavailableLLM(),
    )

    assert "Mobilitätsservice" in result.script
    assert "Aufzüge" in result.script
    assert "Merkzeichen" in result.script


def test_provider_fallback_uses_structurally_distinct_sentence_templates():
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    result = generate_semantic_script(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="Speichere dir den Tipp.",
        facts=[
            "Der Mobilitätsservice braucht eine frühe Buchung.",
            "Aufzüge können kurzfristig außer Betrieb sein.",
            "Das Merkzeichen B erlaubt kostenlose Begleitung.",
        ],
        requested_duration_seconds=50,
        llm_client=_UnavailableLLM(),
    )
    signatures = [
        _normalized_template_signature(sentence)
        for sentence in _sentences(result.script)
    ]

    assert len(signatures) == 7
    assert len(set(signatures)) == len(signatures)


@pytest.mark.parametrize("provider_available", [True, False])
def test_result_preserves_research_provenance_and_source_urls(provider_available):
    valid_script = _complete_semantic_script([16, 16, 16, 16, 15, 15, 15])

    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    research_provenance = {
        "dossier_id": "dossier-17",
        "audit": {"status": "approved", "score": 94},
        "citations": [{"title": "DB Barrierefrei", "fact_indexes": [0, 1]}],
    }
    source_urls = [
        "https://www.bahn.de/service/individuelle-reise/barrierefrei",
        "https://www.bundesfachstelle-barrierefreiheit.de/",
    ]
    result = generate_semantic_script(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="Speichere dir den Tipp.",
        facts=[
            "Der Mobilitätsservice braucht eine frühe Buchung.",
            "Aufzüge können kurzfristig außer Betrieb sein.",
        ],
        requested_duration_seconds=50,
        llm_client=(
            _FakeLLM(valid_script) if provider_available else _UnavailableLLM()
        ),
        research_provenance=research_provenance,
        source_urls=source_urls,
    )

    assert result.provenance["research"] == research_provenance
    assert result.provenance["source_urls"] == source_urls


@pytest.mark.parametrize("seconds", range(8, 61))
def test_provider_failure_uses_distinct_fact_aware_contract_safe_fallback(seconds):
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    result = generate_semantic_script(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="Speichere dir den Tipp.",
        facts=["Der Mobilitätsservice muss häufig vorab gebucht werden."],
        requested_duration_seconds=seconds,
        llm_client=_UnavailableLLM(),
    )
    contract = validate_semantic_script(
        result.script,
        requested_duration_seconds=seconds,
    )
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", result.script)
        if sentence.strip()
    ]

    assert contract.minimum_words <= script_word_count(result.script) <= contract.maximum_words
    assert len(plan_editorial_beats(result.script)) == contract.minimum_take_count
    assert len(sentences) == len(set(sentences))
    assert "Mobilitätsservice" in result.script
    assert result.provenance["source"] == "fallback"


@pytest.mark.parametrize("seconds", range(8, 61))
def test_short_provider_failure_fallback_is_contract_safe_at_every_duration(seconds):
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    result = generate_semantic_script(
        post_type="value",
        title="Titel",
        cta="",
        facts=["Fakt"],
        requested_duration_seconds=seconds,
        llm_client=_UnavailableLLM(),
    )
    contract = validate_semantic_script(
        result.script,
        requested_duration_seconds=seconds,
    )
    sentences = _sentences(result.script)

    assert contract.minimum_words <= script_word_count(result.script) <= contract.maximum_words
    assert len(plan_editorial_beats(result.script)) == contract.minimum_take_count
    assert len(sentences) == contract.minimum_take_count
    assert len(sentences) == len(set(sentences))
    assert len({_normalized_template_signature(sentence) for sentence in sentences}) == len(
        sentences
    )
    assert all(sentence.endswith((".", "!", "?")) for sentence in sentences)
    assert all("Fakt Fakt" not in sentence for sentence in sentences)
    assert "Fakt" in result.script
    assert result.provenance["source"] == "fallback"


def test_long_conditional_fallback_preserves_complete_clauses_in_every_beat():
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    condition = (
        "der Mobilitätsservice wegen hoher Auslastung nicht rechtzeitig gebucht wird"
    )
    consequence = (
        "Dann kann die notwendige Unterstützung beim Einsteigen und Umsteigen "
        "am Reisetag fehlen"
    )
    result = generate_semantic_script(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="Speichere dir den Tipp.",
        facts=[f"Wenn {condition}, {consequence}."],
        requested_duration_seconds=50,
        llm_client=_UnavailableLLM(),
    )
    beats = plan_editorial_beats(result.script)

    assert condition in result.script
    assert consequence in result.script
    assert len(beats) == 7
    assert all(beat.text.endswith((".", "!", "?")) for beat in beats)
    assert all(
        not re.search(r"\bWenn\b", beat.text)
        or re.search(r"\bdann\b.+\bfehlen\b", beat.text, re.IGNORECASE)
        for beat in beats
    )


def test_overlong_conditional_fallback_preserves_ordered_condition_and_consequence():
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    fact = (
        "Wenn der Aufzug am Bahnhof kurzfristig ausfällt und niemand erreichbar ist "
        "obwohl die Reise bereits verbindlich geplant wurde dann muss der alternative "
        "Einstieg vor der Abfahrt bestätigt werden."
    )
    result = generate_semantic_script(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="",
        facts=[fact],
        requested_duration_seconds=50,
        llm_client=_UnavailableLLM(),
    )
    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=50,
    )
    beats = plan_editorial_beats(result.script)
    complete_statements = (
        "Als Bedingung gilt, dass der Aufzug am Bahnhof kurzfristig ausfällt und niemand erreichbar ist",
        "Obwohl die Reise bereits verbindlich geplant wurde, bleibt diese Bedingung bestehen",
        "Dann muss der alternative Einstieg vor der Abfahrt bestätigt werden, sofern diese Bedingungen gelten",
    )

    statement_positions = [result.script.index(text) for text in complete_statements]
    assert statement_positions == sorted(statement_positions)
    assert validation.planned_take_count == validation.minimum_take_count == 7
    assert all(beat.text.endswith((".", "!", "?")) for beat in beats)
    assert result.script.count("der alternative Einstieg") < len(beats)


def test_overlong_booking_fact_fallback_preserves_source_requirement():
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    fact = (
        "Der Mobilitätsservice muss für barrierefreie Bahnreisen mindestens "
        "vierundzwanzig Stunden vor der Abfahrt verbindlich gebucht werden, damit "
        "die notwendige Unterstützung am Bahnsteig vollständig und zuverlässig "
        "bereitsteht."
    )
    assert script_word_count(fact) == 25

    result = generate_semantic_script(
        post_type="value",
        title="Barrierefreie Bahnreisen",
        cta="",
        facts=[fact],
        requested_duration_seconds=50,
        llm_client=_UnavailableLLM(),
    )
    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=50,
    )
    main_requirement = (
        "Der Mobilitätsservice muss für barrierefreie Bahnreisen mindestens "
        "vierundzwanzig Stunden vor der Abfahrt verbindlich gebucht werden"
    )
    purpose_statement = (
        "Damit die notwendige Unterstützung am Bahnsteig vollständig und zuverlässig "
        "bereitsteht, ist diese Buchung nötig"
    )

    assert result.script.index(main_requirement) < result.script.index(purpose_statement)
    assert "Klare Vorbereitung erleichtert deinen nächsten Schritt" not in result.script
    assert validation.planned_take_count == validation.minimum_take_count == 7
    assert all(
        beat.text.endswith((".", "!", "?"))
        for beat in plan_editorial_beats(result.script)
    )


def test_semantic_prompt_rejects_unknown_post_family():
    with pytest.raises(ValueError, match="post_type"):
        build_semantic_script_prompt(
            post_type="unknown",
            title="Titel",
            cta="CTA",
            facts=["Fakt"],
            requested_duration_seconds=16,
        )
