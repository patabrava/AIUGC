from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.core.video_profiles import get_duration_profile
from app.features.topics.prompt3_runtime import generate_product_topics
from app.features.topics.research_runtime import _synthesize_prompt2_fallback, generate_dialog_scripts
from app.features.topics.response_parsers import parse_prompt2_response, parse_prompt3_response
from app.features.topics.response_parsers import _synthesize_research_dossier_from_seed
from app.features.topics.schemas import ProductKnowledgeEntry, ResearchAgentItem
from app.features.topics.topic_validation import (
    find_german_only_violations,
    validate_german_content,
    validate_german_only_text,
    validate_pre_persistence_topic_payload,
)


def test_find_german_only_violations_catches_common_anglicisms():
    text = "Diese Community braucht besseren Support, nicht noch eine App mit Feedback."

    violations = find_german_only_violations(text)

    assert {item["token"] for item in violations} >= {"community", "support", "app", "feedback"}


def test_validate_german_only_text_rejects_spoken_script_anglicisms():
    with pytest.raises(ValidationError) as excinfo:
        validate_german_only_text(
            "Dein Smart Home braucht ein Update, bevor der Alltag wirklich leichter wird.",
            field_name="script",
            context="product",
        )

    assert excinfo.value.message == "Generated text contains Anglicisms"
    assert excinfo.value.details["field"] == "script"
    tokens = {item["token"] for item in excinfo.value.details["violations"]}
    assert "smart home" in tokens or "update" in tokens


def test_find_german_only_violations_catches_hyphenated_anglicisms():
    text = "Smart-Home, Call-to-Action, Social-Media und Peer-Support gehören nicht in den Sprechtext."

    violations = find_german_only_violations(text)

    assert {item["token"] for item in violations} >= {
        "smart home",
        "call to action",
        "social media",
        "peer support",
    }


def test_validate_german_only_text_allows_german_domain_terms_and_umlauts():
    validate_german_only_text(
        "Dein Zuhause wird leichter nutzbar, wenn Wege, Türen und Übergänge vorher klar geprüft werden.",
        field_name="script",
        context="value",
    )


def test_validate_german_content_rejects_single_anglicism_in_prompt1_script():
    item = ResearchAgentItem(
        topic="Barrierefreie Wege",
        script="Dein Alltag braucht keinen Support, sondern klare Wege und gute Planung.",
        caption="Klare Wege helfen im Alltag.",
        source_summary="Klare Wege helfen im Alltag.",
        estimated_duration_s=8,
        tone="direkt, freundlich, bestärkend, du-Form",
        disclaimer="Keine Rechts- oder medizinische Beratung.",
    )

    with pytest.raises(ValidationError) as excinfo:
        validate_german_content(item)

    assert excinfo.value.message == "PROMPT_1 output must be fully in German"
    assert excinfo.value.details["violations"][0]["field"] == "script"


def test_pre_persistence_rejects_anglicism_for_all_post_types():
    with pytest.raises(ValidationError) as excinfo:
        validate_pre_persistence_topic_payload(
            {
                "title": "Barrierefreie Wege",
                "topic": "Barrierefreie Wege",
                "script": "Diese App gibt dir Support und macht deinen Alltag planbarer.",
                "caption": "Barrierefreie Wege helfen im Alltag.",
                "source_summary": "Barrierefreie Wege helfen im Alltag.",
            },
            target_length_tier=8,
            post_type="lifestyle",
        )

    assert excinfo.value.message == "Generated text contains Anglicisms"
    assert excinfo.value.details["field"] == "script"


def test_parse_prompt2_response_accepts_german_heading_without_ads():
    raw = """## Problem, Zuspitzung, Lösung
Dieser Bordstein kostet dich Kraft, wenn du jeden Morgen denselben Umweg fahren musst.

## Beschreibung
Der Begleittext erklärt, warum kleine Umwege im Alltag viel Energie kosten und wie klare Planung entlasten kann. #Barrierefrei #RollstuhlAlltag #Planung
"""

    result = parse_prompt2_response(raw, max_per_category=1)

    assert result.problem_agitate_solution[0].startswith("Dieser Bordstein")


class _Prompt2GermanOnlyFakeLLM:
    def __init__(self):
        self.prompts = []
        self.responses = [
            """## Problem, Zuspitzung, Lösung
Diese Community braucht Support, wenn der Aufzug schon wieder ausfällt.

## Beschreibung
Diese Community braucht mehr Support im Alltag und bessere Apps für Wege. #Barrierefrei #Alltag #Rollstuhl
""",
            """## Problem, Zuspitzung, Lösung
Dieser Aufzug kostet dich Kraft, wenn du wieder ohne klare Vorwarnung umplanen musst und dein Termin wackelt.

## Beschreibung
Der Begleittext erklärt, warum verlässliche Aufzüge und klare Hinweise im Alltag entlasten. #Barrierefrei #Alltag #Rollstuhl
""",
        ]

    def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_generate_dialog_scripts_retries_when_prompt2_contains_anglicisms():
    fake = _Prompt2GermanOnlyFakeLLM()

    result = generate_dialog_scripts(
        topic="Aufzug im Alltag",
        scripts_required=1,
        profile=get_duration_profile(8),
        llm_factory=lambda: fake,
    )

    assert len(fake.prompts) == 2
    assert "Anglizismen" in fake.prompts[1]
    assert "Support" not in result.problem_agitate_solution[0]


def test_parse_prompt3_response_accepts_german_only_labels():
    raw = """Produkt: VARIO PLUS
Winkel: Eine Schiene für heute und später
Sprechtext: Deine Treppe bleibt besser nutzbar, wenn eine Lösung heute und später zu deinem Alltag passt.
Handlungsaufforderung: Frag nach einer passenden Lösung für dein Zuhause.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfähigkeit bis 300 kg
"""

    result = parse_prompt3_response(raw)

    assert result.product_name == "VARIO PLUS"
    assert result.angle.startswith("Eine Schiene")
    assert result.script.startswith("Deine Treppe")
    assert result.cta.startswith("Frag nach")


class _Prompt3GermanOnlyFakeLLM:
    def __init__(self):
        self.prompts = []
        self.responses = [
            """Produkt: VARIO PLUS
Winkel: Mehr Sicherheit
Sprechtext: VARIO PLUS gibt dir Support und Made in Germany sorgt für Vertrauen im Alltag.
Handlungsaufforderung: Frag nach VARIO PLUS.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
""",
            """Produkt: VARIO PLUS
Winkel: Mehr Sicherheit
Sprechtext: Deine Treppe bleibt besser nutzbar, wenn dieselbe Schiene heute und später gut zu deinem Alltag passt.
Handlungsaufforderung: Frag nach einer passenden Lösung für dein Zuhause.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
""",
        ]

    def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
        self.prompts.append(prompt)
        return self.responses.pop(0)


def test_generate_product_topics_retries_when_prompt3_contains_anglicisms(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfähigkeit bis 300 kg"],
                support_facts=["Fünf Jahre Gewährleistung auf den gesamten Lift"],
            )
        ],
    )
    fake = _Prompt3GermanOnlyFakeLLM()

    generated = generate_product_topics(count=1, target_length_tier=8, llm_factory=lambda: fake)

    assert len(fake.prompts) == 2
    assert "Anglizismen" in fake.prompts[1]
    assert "Support" not in generated[0]["script"]
    assert "Made in Germany" not in generated[0]["script"]


def test_prompt2_fallback_description_is_german_only():
    fallback = _synthesize_prompt2_fallback(
        "Rollstuhl Alltag",
        scripts_required=1,
        target_length_tier=8,
    )

    validate_german_only_text(fallback.description, field_name="description", context="prompt2_fallback")


def test_seed_dossier_fallback_is_german_only():
    fallback = _synthesize_research_dossier_from_seed(
        seed_topic="Barrierefreie Wege",
        post_type="value",
        target_length_tier=8,
    )

    for field in ("cluster_summary", "source_summary"):
        assert find_german_only_violations(fallback[field]) == []
