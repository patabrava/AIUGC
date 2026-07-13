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


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", text)


def _is_ordered_subsequence(candidate: list[str], source: list[str]) -> bool:
    source_iter = iter(word.casefold() for word in source)
    return all(
        any(token.casefold() == source_token for source_token in source_iter)
        for token in candidate
    )


def _is_contiguous_span(candidate: list[str], source: list[str]) -> bool:
    folded_candidate = [word.casefold() for word in candidate]
    folded_source = [word.casefold() for word in source]
    return any(
        folded_source[start : start + len(folded_candidate)] == folded_candidate
        for start in range(len(folded_source) - len(folded_candidate) + 1)
    )


def _quoted_spans(text: str) -> list[str]:
    return re.findall(r'[„"]([^“”"]+)[“”"]', text)


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


def test_validation_rejects_a_take_over_eighteen_words_or_seven_point_five_seconds():
    first_sentence = [
        *[f"Quelle{index}/Zusatz{index}" for index in range(9)],
        *[f"Anker{index}" for index in range(4)],
    ]
    first_sentence[-1] += "."
    sentences = [" ".join(first_sentence)]
    for sentence_index in range(1, 7):
        words = [
            f"Satz{sentence_index}Wort{word_index}"
            for word_index in range(16)
        ]
        words[-1] += "."
        sentences.append(" ".join(words))
    script = " ".join(sentences)
    beats = plan_editorial_beats(script)

    assert script_word_count(script) == 118
    assert len(beats) == 7
    assert beats[0].word_count == 22
    assert beats[0].estimated_speech_seconds == 9.05
    with pytest.raises(ValueError, match="18 words|7.5 seconds"):
        validate_semantic_script(script, requested_duration_seconds=50)


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


def test_eight_second_fallback_packs_two_short_facts_into_one_take():
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    facts = ["Erster Hinweis.", "Zweiter Beleg."]
    result = generate_semantic_script(
        post_type="value",
        title="Titel",
        cta="",
        facts=facts,
        requested_duration_seconds=8,
        llm_client=_UnavailableLLM(),
    )
    validation = validate_semantic_script(
        result.script,
        requested_duration_seconds=8,
    )

    assert validation.planned_take_count == validation.minimum_take_count == 1
    assert _quoted_spans(result.script) == ["Erster Hinweis", "Zweiter Beleg"]
    assert result.provenance["source"] == "fallback"


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


def test_result_provenance_returns_defensive_nested_copies():
    valid_script = _complete_semantic_script([16, 16, 16, 16, 15, 15, 15])
    result = generate_semantic_script(
        post_type="value",
        title="Titel",
        cta="",
        facts=["Fakt"],
        requested_duration_seconds=50,
        llm_client=_FakeLLM(valid_script),
        research_provenance={"audit": {"status": "approved"}},
        source_urls=["https://example.test/source"],
    )

    exposed = result.provenance
    exposed["research"]["audit"]["status"] = "mutated"
    exposed["source_urls"].append("https://example.test/injected")

    assert result.provenance["research"]["audit"]["status"] == "approved"
    assert result.provenance["source_urls"] == ["https://example.test/source"]


def test_programming_type_error_from_llm_client_propagates():
    class _BrokenClient:
        def generate_gemini_text(self, **_kwargs):
            raise TypeError("programming defect")

    with pytest.raises(TypeError, match="programming defect"):
        generate_semantic_script(
            post_type="value",
            title="Titel",
            cta="",
            facts=["Fakt"],
            requested_duration_seconds=16,
            llm_client=_BrokenClient(),
        )


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
    assert all(_quoted_spans(sentence) for sentence in sentences)
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
    assert all(_quoted_spans(sentence) for sentence in sentences)
    assert len({_normalized_template_signature(sentence) for sentence in sentences}) == len(
        sentences
    )
    assert all(sentence.endswith((".", "!", "?")) for sentence in sentences)
    assert all("Fakt Fakt" not in sentence for sentence in sentences)
    assert "Fakt" in result.script
    assert result.provenance["source"] == "fallback"


@pytest.mark.parametrize("seconds", [8, 16, 32, 50, 60])
@pytest.mark.parametrize(
    ("fact_word_count", "fact"),
    [
        (1, "Achtung."),
        (7, "Der Reisehinweis unterstützt eine rechtzeitige sichere Abfahrt."),
        (
            14,
            "Der Reisehinweis unterstützt Reisende bei der rechtzeitigen Prüfung "
            "wichtiger Schritte vor einer geplanten Abfahrt.",
        ),
        (
            15,
            "Heute unterstützt der Reisehinweis Reisende bei der rechtzeitigen "
            "Prüfung wichtiger Schritte vor einer geplanten Abfahrt.",
        ),
        (
            16,
            "Heute unterstützt der Reisehinweis Reisende verlässlich bei der "
            "rechtzeitigen Prüfung wichtiger Schritte vor einer geplanten Abfahrt.",
        ),
        (
            17,
            "Heute unterstützt der Reisehinweis Reisende besonders verlässlich bei "
            "der rechtzeitigen Prüfung wichtiger Schritte vor einer geplanten Abfahrt.",
        ),
        (
            25,
            "Heute unterstützt der Reisehinweis Reisende verlässlich bei der "
            "rechtzeitigen Prüfung wichtiger Schritte vor einer geplanten Abfahrt "
            "und nennt dafür alle relevanten Quellen ohne unbelegte Annahmen.",
        ),
    ],
)
def test_generic_provider_fallback_is_contract_safe_for_fact_length_matrix(
    seconds,
    fact_word_count,
    fact,
):
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    assert script_word_count(fact) == fact_word_count
    result = generate_semantic_script(
        post_type="value",
        title="Reiseplanung",
        cta="",
        facts=[fact],
        requested_duration_seconds=seconds,
        llm_client=_UnavailableLLM(),
    )
    contract = validate_semantic_script(
        result.script,
        requested_duration_seconds=seconds,
    )
    sentences = _sentences(result.script)
    source_words = re.findall(r"[A-Za-zÀ-ÿ0-9ÄÖÜäöüß-]+", fact)
    maximum_block_words = (
        contract.minimum_words + contract.minimum_take_count - 1
    ) // contract.minimum_take_count

    assert contract.minimum_words <= script_word_count(result.script) <= contract.maximum_words
    assert len(plan_editorial_beats(result.script)) == contract.minimum_take_count
    assert len(sentences) == contract.minimum_take_count
    assert len(sentences) == len(set(sentences))
    assert all(sentence.endswith((".", "!", "?")) for sentence in sentences)
    quoted_spans = [span for sentence in sentences for span in _quoted_spans(sentence)]
    assert all(_quoted_spans(sentence) for sentence in sentences)
    assert all(
        _is_ordered_subsequence(_word_tokens(span), source_words)
        for span in quoted_spans
    )
    assert all(
        _is_contiguous_span(_word_tokens(span), source_words)
        for span in quoted_spans
    )
    assert source_words[0] in result.script
    assert source_words[-1] in result.script
    if fact_word_count <= maximum_block_words:
        assert sentences[0].startswith(f'„{" ".join(source_words)}')
    if fact_word_count > maximum_block_words:
        assert "Quellenauszug" in result.script
    assert result.provenance["source"] == "fallback"


def test_fallback_quellenauszug_quotes_contain_only_ordered_source_tokens():
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
    quoted_spans = re.findall(r'Quellenauszug:\s*[„"]([^“”"]+)[“”"]', result.script)

    assert result.script.count("Quellenauszug:") > 0
    assert len(quoted_spans) == result.script.count("Quellenauszug:")
    source_words = _word_tokens(fact)
    assert all(
        _is_ordered_subsequence(_word_tokens(span), source_words)
        for span in quoted_spans
    )


def test_shortened_fallback_quotes_are_contiguous_and_keep_middle_negation():
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    fact = (
        "Anfang Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa Lambda "
        "NICHT Mu Nu Xi Omikron Pi Rho Sigma Tau Upsilon Phi Chi Ende."
    )
    result = generate_semantic_script(
        post_type="value",
        title="Titel",
        cta="",
        facts=[fact],
        requested_duration_seconds=8,
        llm_client=_UnavailableLLM(),
    )
    source_words = _word_tokens(fact)
    quoted_spans = _quoted_spans(result.script)

    assert quoted_spans
    assert all(
        _is_contiguous_span(_word_tokens(span), source_words)
        for span in quoted_spans
    )
    assert any("NICHT" in _word_tokens(span) for span in quoted_spans)
    assert "Gekürzter Quellenauszug:" in result.script
    assert "…" in result.script


def test_every_provider_failure_beat_contains_a_quoted_source_anchor():
    class _UnavailableLLM:
        def generate_gemini_text(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    result = generate_semantic_script(
        post_type="value",
        title="Titel",
        cta="",
        facts=["Signalwort.", "Quellenkern."],
        requested_duration_seconds=60,
        llm_client=_UnavailableLLM(),
    )
    beats = plan_editorial_beats(result.script)
    quoted_anchors = [_quoted_spans(beat.text) for beat in beats]

    assert len(beats) == 8
    assert all(len(anchors) == 1 for anchors in quoted_anchors)
    assert [anchors[0] for anchors in quoted_anchors] == [
        "Signalwort",
        "Quellenkern",
        "Signalwort",
        "Quellenkern",
        "Signalwort",
        "Quellenkern",
        "Signalwort",
        "Quellenkern",
    ]
    assert len({_normalized_template_signature(beat.text) for beat in beats}) == len(beats)


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
    assert result.script.index(condition) < result.script.index(consequence)
    assert len(beats) == 7
    assert all(beat.text.endswith((".", "!", "?")) for beat in beats)
    assert all(
        "Wenn" not in beat.text or "Quellenauszug" in beat.text
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
    ordered_source_fragments = (
        "Wenn der Aufzug am Bahnhof kurzfristig ausfällt und niemand erreichbar ist",
        "obwohl die Reise bereits verbindlich geplant wurde",
        "dann muss der alternative Einstieg vor der Abfahrt bestätigt werden",
    )

    statement_positions = [result.script.index(text) for text in ordered_source_fragments]
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
        "damit die notwendige Unterstützung am Bahnsteig vollständig und zuverlässig "
        "bereitsteht"
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
