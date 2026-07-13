import re

import pytest

from app.core.video_profiles import script_word_count
from app.features.shot_production.duration import build_semantic_duration_contract
from app.features.shot_production.planner import plan_editorial_beats
from app.features.topics.semantic_scripts import (
    build_semantic_script_prompt,
    generate_semantic_script,
    validate_semantic_script,
)


def _words(count: int) -> str:
    return " ".join(f"Wort{index}" for index in range(count)) + "."


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
    fake_llm = _FakeLLM(f"```text\nSkript: {_words(109)}\n```")

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
    assert result.script.startswith("Wort0")
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


def test_semantic_prompt_rejects_unknown_post_family():
    with pytest.raises(ValueError, match="post_type"):
        build_semantic_script_prompt(
            post_type="unknown",
            title="Titel",
            cta="CTA",
            facts=["Fakt"],
            requested_duration_seconds=16,
        )
