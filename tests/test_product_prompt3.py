from __future__ import annotations

import pytest

from app.core.video_profiles import get_duration_profile
from app.core.errors import ThirdPartyError, ValidationError
from app.features.topics.product_knowledge import parse_product_knowledge_base, plan_product_mix
from app.features.topics.prompts import build_prompt3
from app.features.topics.prompt3_runtime import (
    _build_product_fallback_topic,
    _strip_user_facing_product_markers,
    generate_product_topics,
)
from app.features.topics.response_parsers import parse_prompt3_response
from app.features.topics.schemas import ProductKnowledgeEntry


SAMPLE_KNOWLEDGE_BASE = """
1. UNTERNEHMEN
- 100% Made in Germany
- 5 Jahre Gewaehrleistung auf den gesamten Lift

2. PRODUKTE (AKTIV IM SORTIMENT)
WICHTIG: LL12 und Konstanz werden NICHT mehr kommuniziert.
Aktive Produkte: T80 Plattform, Hublift STL300, Sitzlift T80, Sitzlift ST70

A) PLATTFORMTREPPENLIFT T80 (Marketingname:VARIO PLUS)
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit: 300 kg
- Innen- und Aussenbereich

B) HUBLIFT STL300 (Marketingname: LEVEL)
- Fuer Hoehen bis 2.990 mm
- Kein Aufzugsschacht erforderlich
- Tragfaehigkeit: 300 kg

C) SITZTREPPENLIFT T80 (Marketingname:VARIO ONE)
- Gerade und kurvige Treppen
- Austausch Sitz gegen Plattform nachtraeglich moeglich

D) SITZTREPPENLIFT ST70 - Der Klassiker (Marketingname: VIA)
- Speziell fuer kurvige, mehrstoeckige Treppen
- Mehrere Haltestellen moeglich
"""


def test_parse_product_knowledge_base_returns_only_active_products():
    entries = parse_product_knowledge_base(SAMPLE_KNOWLEDGE_BASE)
    assert [entry.product_name for entry in entries] == [
        "VARIO PLUS",
        "LEVEL",
        "VARIO ONE",
        "VIA",
    ]
    assert all(entry.is_active for entry in entries)


def test_parse_product_knowledge_base_attaches_support_facts_to_each_product():
    entries = parse_product_knowledge_base(SAMPLE_KNOWLEDGE_BASE)
    assert "in Deutschland gefertigt" in entries[0].support_facts
    assert "5 Jahre Gewaehrleistung auf den gesamten Lift" in entries[1].support_facts


def test_build_prompt3_sanitizes_anglicism_support_facts():
    prompt = build_prompt3(
        product=ProductKnowledgeEntry(
            product_name="VARIO PLUS",
            source_label="PLATTFORMTREPPENLIFT T80",
            aliases=["VARIO PLUS"],
            summary="Plattform oder Sitzlift auf derselben Schiene.",
            facts=["Smart-Home Anschluss per App."],
            support_facts=["100% Made in Germany", "LIPPE Lift Website Service Update"],
        ),
        profile=get_duration_profile(8),
    )

    assert "Made in Germany" not in prompt
    assert "LIPPE Lift" not in prompt
    assert "Smart-Home" not in prompt
    assert "Website" not in prompt
    assert "Service" not in prompt
    assert "Update" not in prompt
    assert "der Hersteller" in prompt
    assert "in Deutschland gefertigt" in prompt
    assert "vernetzte Wohnhilfen" in prompt


def test_parse_product_knowledge_base_clips_facts_to_schema_limit():
    raw = """
1. UNTERNEHMEN
- This is a sufficiently long supporting fact for the parser.
- Another sufficiently long supporting fact for the parser.

2. PRODUKTE
A) TESTPRODUKT X (Marketingname: X ONE)
- Fact 1 has enough length for validation.
- Fact 2 has enough length for validation.
- Fact 3 has enough length for validation.
- Fact 4 has enough length for validation.
- Fact 5 has enough length for validation.
- Fact 6 has enough length for validation.
- Fact 7 has enough length for validation.
- Fact 8 has enough length for validation.
- Fact 9 has enough length for validation.
- Fact 10 has enough length for validation.
- Fact 11 has enough length for validation.
- Fact 12 has enough length for validation.
- Fact 13 has enough length for validation.
"""
    entries = parse_product_knowledge_base(raw)
    assert len(entries) == 1
    assert len(entries[0].facts) == 12
    assert entries[0].facts[-1].startswith("Fact 12")


def test_plan_product_mix_covers_all_products_before_repeat():
    entries = parse_product_knowledge_base(SAMPLE_KNOWLEDGE_BASE)
    planned = plan_product_mix(entries, count=6)
    assert [entry.product_name for entry in planned[:4]] == [
        "VARIO PLUS",
        "LEVEL",
        "VARIO ONE",
        "VIA",
    ]
    assert len(planned) == 6
    assert planned[4].product_name == "VARIO PLUS"


def test_parse_prompt3_response_reads_plain_text_blocks():
    raw = """Produkt: VARIO PLUS
Angle: Eine Schiene fuer heute und spaeter
Script: Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?
CTA: Lass dir zeigen, wie eine Schiene beide Wege offen haelt.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
"""
    candidate = parse_prompt3_response(raw)
    assert candidate.product_name == "VARIO PLUS"
    assert candidate.angle.startswith("Eine Schiene")
    assert candidate.script.endswith("?")
    assert candidate.cta.startswith("Lass dir zeigen")
    assert candidate.facts[0].startswith("Plattform")


def test_parse_prompt3_response_accepts_close_field_variants():
    raw = """Produktname: VARIO PLUS
Winkel: Eine Schiene fuer heute und spaeter
Hook: Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?
Call to action: Lass dir zeigen, wie eine Schiene beide Wege offen haelt.
Stichpunkte:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
"""
    candidate = parse_prompt3_response(raw)
    assert candidate.product_name == "VARIO PLUS"
    assert candidate.angle.startswith("Eine Schiene")
    assert candidate.script.startswith("Kennst du")
    assert candidate.cta.startswith("Lass dir zeigen")
    assert len(candidate.facts) == 2


def test_parse_prompt3_response_accepts_multiline_script_blocks():
    raw = """Produkt: VARIO PLUS
Angle: Mehr Freiheit im Alltag
Script:
Hast du eine Treppe, die dir jeden Tag Kraft zieht?
Der VARIO PLUS macht gerade, kurvige und steile Wege wieder alltagstauglich.
CTA:
Frag jetzt nach dem VARIO PLUS.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
"""
    candidate = parse_prompt3_response(raw)
    assert candidate.product_name == "VARIO PLUS"
    assert "Kraft zieht" in candidate.script
    assert candidate.script.endswith(".")
    assert candidate.cta.startswith("Frag jetzt")
    assert candidate.facts[0].startswith("Plattform")


def test_parse_prompt3_response_synthesizes_missing_angle_and_cta():
    raw = """Produkt: VARIO PLUS
Script: VARIO PLUS gibt dir heute Sicherheit und morgen Flexibilitaet fuer deine Treppe zuhause ohne komplizierten Umbau. So bleibt dein Alltag planbar.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
"""
    candidate = parse_prompt3_response(raw, fallback_product_name="VARIO PLUS")
    assert candidate.product_name == "VARIO PLUS"
    assert candidate.script.startswith("VARIO PLUS gibt dir heute Sicherheit")
    assert candidate.angle
    assert candidate.cta


def test_parse_prompt3_response_rejects_missing_required_fields():
    raw = """Produkt: VARIO PLUS
CTA: Mehr erfahren.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
"""
    with pytest.raises(ValidationError):
        parse_prompt3_response(raw)


class _FakeProductLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.text_prompts = []

    def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
        self.text_prompts.append((prompt, system_prompt, kwargs))
        return self.responses.pop(0)


class _FlakyProductLLM:
    def __init__(self, responses, failures):
        self.responses = list(responses)
        self.failures = list(failures)
        self.text_prompts = []

    def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
        self.text_prompts.append((prompt, system_prompt, kwargs))
        if self.failures:
            raise self.failures.pop(0)
        return self.responses.pop(0)


def test_generate_product_topics_retries_when_wrong_product_is_returned(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["100% Made in Germany"],
            )
        ],
    )
    fake_llm = _FakeProductLLM(
        [
            """Produkt: LL12
Angle: Falsches Produkt
Script: Dieses Produkt sollte hier gar nicht auftauchen.
CTA: Nicht verwenden.
Fakten:
- Falscher Fakt
""",
            """Produkt: VARIO PLUS
Angle: Eine Schiene fuer heute und spaeter
Script: Dieselbe Schiene gibt dir heute Sicherheit und später Flexibilität für deine Treppe zuhause ohne komplizierten Umbau.
Schlusssatz: So bleibt mehr Ruhe in einer Entscheidung, die jeden Tag spürbar wird.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
""",
        ]
    )

    generated = generate_product_topics(
        count=1,
        target_length_tier=8,
        llm_factory=lambda: fake_llm,
    )

    assert generated[0]["product_name"] == "VARIO PLUS"
    assert len(fake_llm.text_prompts) == 2


def test_generate_product_topics_retries_on_malformed_prompt3_response(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["100% Made in Germany"],
            )
        ],
    )
    fake_llm = _FakeProductLLM(
        [
            """Einleitung ohne passende Feldnamen.\nDieses Produkt ist fuer zuhause gedacht.""",
            """Produktname: VARIO PLUS\nWinkel: Mehr Sicherheit mit einer Liftlösung im Alltag\nHook: Dieselbe Schiene gibt dir heute Sicherheit und später Flexibilität für deine Treppe zuhause ohne komplizierten Umbau.\nSchlusssatz: So bleibt mehr Ruhe in einer Entscheidung, die jeden Tag spürbar wird.\nStichpunkte:\n- Plattform oder Sitzlift auf derselben Schiene\n- Tragfaehigkeit bis 300 kg\n""",
        ]
    )

    generated = generate_product_topics(
        count=1,
        target_length_tier=8,
        llm_factory=lambda: fake_llm,
    )

    assert generated[0]["product_name"] == "VARIO PLUS"
    assert len(fake_llm.text_prompts) == 2


def test_generate_product_topics_retries_on_anglicism_in_prompt3_script(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["in Deutschland gefertigt"],
            )
        ],
    )
    fake_llm = _FakeProductLLM(
        [
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
Schlusssatz: So bleibt Planung näher an deinem Alltag und weniger an Verkaufsdruck.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
""",
        ]
    )

    generated = generate_product_topics(count=1, target_length_tier=8, llm_factory=lambda: fake_llm)

    assert len(fake_llm.text_prompts) == 2
    assert "Anglizismen" in fake_llm.text_prompts[1][0]
    assert "Support" not in generated[0]["script"]


def test_generate_product_topics_retries_on_user_facing_product_name_leak(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["in Deutschland gefertigt", "verlässliche Fertigung"],
            )
        ],
    )
    fake_llm = _FakeProductLLM(
        [
            """Produkt: VARIO PLUS
Winkel: VARIO PLUS fuer zuhause
Sprechtext: VARIO PLUS gibt dir heute Sicherheit und morgen Flexibilitaet fuer deine Treppe zuhause ohne komplizierten Umbau.
Handlungsaufforderung: Frag nach VARIO PLUS fuer dein Zuhause.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
""",
            """Produkt: VARIO PLUS
Winkel: Mehr Ruhe mit einem Plattformlift im Alltag
Sprechtext: Ein Plattformlift macht Wege zuhause ruhiger, wenn er zuverlässig fährt und langfristig zu deinem Alltag passt.
Schlusssatz: So bleibt Planung näher an deinem Alltag und weniger an Verkaufsdruck.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
""",
        ]
    )

    generated = generate_product_topics(count=1, target_length_tier=8, llm_factory=lambda: fake_llm)

    assert generated[0]["product_name"] == "VARIO PLUS"
    assert "VARIO PLUS" not in generated[0]["angle"]
    assert "VARIO PLUS" not in generated[0]["script"]
    assert "VARIO PLUS" not in generated[0]["cta"]
    assert "VARIO PLUS" not in generated[0]["title"]
    assert "PLATTFORMTREPPENLIFT T80" not in generated[0]["title"]
    assert len(fake_llm.text_prompts) == 2
    assert "nicht in Winkel, Sprechtext oder Schlusssatz" in fake_llm.text_prompts[1][0]


def test_generate_product_topics_retries_on_sales_like_closing(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["in Deutschland gefertigt", "verlässliche Fertigung"],
            )
        ],
    )
    fake_llm = _FakeProductLLM(
        [
            """Produkt: VARIO PLUS
Winkel: Mehr Ruhe mit einem Plattformlift im Alltag
Sprechtext: Ein Plattformlift macht Wege zuhause ruhiger, wenn er zuverlässig fährt und langfristig zu deinem Alltag passt.
Schlusssatz: Frag nach einer passenden Lösung für dein Zuhause.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
""",
            """Produkt: VARIO PLUS
Winkel: Mehr Ruhe mit einem Plattformlift im Alltag
Sprechtext: Ein Plattformlift macht Wege zuhause ruhiger, wenn er zuverlässig fährt und langfristig zu deinem Alltag passt.
Schlusssatz: So fühlt sich Planung weniger nach Druck und mehr nach Alltag an.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
""",
        ]
    )

    generated = generate_product_topics(count=1, target_length_tier=8, llm_factory=lambda: fake_llm)

    assert generated[0]["cta"] == "So fühlt sich Planung weniger nach Druck und mehr nach Alltag an."
    assert len(fake_llm.text_prompts) == 2
    assert "keine Verkaufsaufforderung" in fake_llm.text_prompts[1][0]


def test_generate_product_topics_accepts_32s_product_scripts_inside_current_contract(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="LEVEL",
                source_label="HUBLIFT STL300",
                aliases=["LEVEL", "HUBLIFT STL300"],
                summary="Für Höhen bis 2.990 mm, unabhängig von einer Treppe.",
                facts=["Für Höhen bis 2.990 mm", "Kein Aufzugsschacht erforderlich"],
                support_facts=["Tragfaehigkeit bis 300 kg"],
            )
        ],
    )
    fake_llm = _FakeProductLLM(
        [
            """Produkt: LEVEL
Angle: Vier klare Schritte bis zur barrierefreien Loesung
Script: Du brauchst einen Zugang, der nicht an einer Treppe haengen bleibt und deinen Alltag sofort ruhiger macht. LEVEL loest das mit bis zu 2.990 Millimetern Hoehe und gibt dir klare Planung. Kein Aufzugsschacht noetig macht den Umbau entspannter. Und genau das macht die Entscheidung fuer dein Zuhause deutlich einfacher.
CTA: Frag nach LEVEL.
Fakten:
- Fuer Hoehen bis 2.990 mm
- Kein Aufzugsschacht erforderlich
""",
            """Produkt: LEVEL
Angle: Sechs klare Schritte bis zur barrierefreien Loesung
Script: Du brauchst einen Zugang, der nicht an einer Treppe hängen bleibt und deinen Alltag sofort ruhiger macht. Der Plattformlift überbrückt bis zu 2.990 Millimeter Höhe und gibt dir klare Planung. Kein Aufzugsschacht nötig macht den Umbau entspannter, besonders wenn draußen wenig Platz ist. Vor dem Termin klärst du Fläche, Bedienung und Sicherheitsgefühl gemeinsam. So weißt du früher, welche Lösung wirklich passt und dein Zuhause deutlich einfacher wird.
Schlusssatz: So wird aus Technik eher Alltag als Verkaufsgespräch.
Fakten:
- Fuer Hoehen bis 2.990 mm
- Kein Aufzugsschacht erforderlich
""",
        ]
    )

    generated = generate_product_topics(
        count=1,
        target_length_tier=32,
        llm_factory=lambda: fake_llm,
    )

    assert generated[0]["product_name"] == "LEVEL"
    assert 64 <= len(generated[0]["script"].split()) <= 84
    assert generated[0]["script"].count(".") >= 5


def test_generate_product_topics_retries_on_retryable_provider_error(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["100% Made in Germany"],
            )
        ],
    )
    flaky_llm = _FlakyProductLLM(
        [
            """Produkt: VARIO PLUS
Angle: Eine Schiene fuer heute und spaeter
Script: Dieselbe Schiene gibt dir heute Sicherheit und später Flexibilität für deine Treppe zuhause ohne komplizierten Umbau.
Schlusssatz: So bleibt mehr Ruhe in einer Entscheidung, die jeden Tag spürbar wird.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
""",
        ],
        [
            ThirdPartyError(
                message="Gemini generateContent failed",
                details={"status_code": 503, "model": "gemini-2.5-flash"},
            )
        ],
    )

    generated = generate_product_topics(
        count=1,
        target_length_tier=8,
        llm_factory=lambda: flaky_llm,
    )

    assert generated[0]["product_name"] == "VARIO PLUS"
    assert len(flaky_llm.text_prompts) == 2


@pytest.mark.parametrize("target_length_tier", [8, 16, 32])
def test_generate_product_topics_synthesizes_tier_fallback_on_vertex_credential_failure(monkeypatch, target_length_tier):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["100% Made in Germany"],
            )
        ],
    )

    class FakeUnavailableProductLLM:
        def __init__(self):
            self.text_prompts = []

        def generate_gemini_text(self, prompt, system_prompt=None, **kwargs):
            self.text_prompts.append((prompt, system_prompt, kwargs))
            raise ValidationError(
                message="No Google Cloud Application Default Credentials found.",
                details={"provider": "vertex"},
            )

    fake_llm = FakeUnavailableProductLLM()

    generated = generate_product_topics(
        count=1,
        target_length_tier=target_length_tier,
        llm_factory=lambda: fake_llm,
    )

    min_words, max_words = {
        8: (16, 20),
        16: (28, 34),
        32: (64, 84),
    }[target_length_tier]
    assert generated[0]["product_name"] == "VARIO PLUS"
    assert min_words <= len(generated[0]["script"].split()) <= max_words
    assert generated[0]["cta"]
    assert len(fake_llm.text_prompts) == 3


def test_generate_product_topics_trims_overlong_prompt3_response(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["100% Made in Germany"],
            )
        ],
    )
    long_script = " ".join(["Wort"] * 75) + "."
    fake_llm = _FakeProductLLM(
        [
            f"""Produkt: VARIO PLUS
Angle: Flexibilitaet fuer jede Treppe
Script: {long_script.split('.')[0]}. {long_script.split('.')[0]}. {long_script.split('.')[0]}. {long_script.split('.')[0]}.
Schlusssatz: So bleibt Planung näher an deinem Alltag und weniger an Verkaufsdruck.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
            """,
            """Produkt: VARIO PLUS
Angle: Flexibilitaet fuer jede Treppe
Script: Eine passende Liftlösung passt sich an deine Treppe an und bleibt dabei klar und verlässlich. Du nutzt dieselbe Schiene für heute und später. Innen und außen bleibt die Lösung flexibel und alltagstauglich. Mit 300 Kilo Tragkraft bekommst du Sicherheit im Alltag und mehr Ruhe. Vor dem Einbau klärst du gemeinsam, welche Variante, Bedienung und Ausstattung wirklich zu deinem Zuhause passen. Das macht Entscheidungen deutlich leichter.
Schlusssatz: So bleibt Planung näher an deinem Alltag und weniger an Verkaufsdruck.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
""",
        ]
    )

    generated = generate_product_topics(
        count=1,
        target_length_tier=32,
        llm_factory=lambda: fake_llm,
    )

    assert generated[0]["product_name"] == "VARIO PLUS"
    assert len(generated[0]["script"].split()) <= 66
    assert generated[0]["cta"]
    assert len(fake_llm.text_prompts) == 2


def test_generate_product_topics_returns_sanitized_script_when_cleaned_copy_is_in_bounds(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["100% Made in Germany"],
            )
        ],
    )
    live_shape_script = (
        "Dein Alltag wird einfacher mit einer passenden Liftlösung! "
        "Ob steile Treppe oder enge Kurve, die Lösung macht Wege wieder ruhiger. "
        "Und das Beste: Er passt sich jederzeit deinen Bedürfnissen an, auch nachträglich als Sitz- oder Plattformlift."
    )
    fake_llm = _FakeProductLLM(
        [
            f"""Produkt: VARIO PLUS
Angle: Der einzige Lift, der mit dir und deinem Leben mitwächst.
Script: {live_shape_script}
Schlusssatz: So bleibt Planung näher an deinem Alltag und weniger an Verkaufsdruck.
Fakten:
- Plattform oder Sitzlift auf derselben Schiene
- Tragfaehigkeit bis 300 kg
"""
        ]
    )

    generated = generate_product_topics(count=1, target_length_tier=16, llm_factory=lambda: fake_llm)

    assert "Und das Beste:" not in generated[0]["script"]
    assert 24 <= len(generated[0]["script"].split()) <= 34


def test_generate_product_topics_accepts_decimal_product_copy(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="LEVEL",
                source_label="HUBLIFT STL300",
                aliases=["LEVEL", "HUBLIFT STL300"],
                summary="Für Höhen bis 2.990 mm, unabhängig von einer Treppe.",
                facts=["Für Höhen bis 2.990 mm, unabhängig von einer Treppe."],
                support_facts=["100% Made in Germany"],
            )
        ],
    )
    fake_llm = _FakeProductLLM(
        [
            """Produkt: LEVEL
Angle: Barrierefreiheit ohne Treppe
Script: Der Plattformlift bringt dich bis zu 2.990 mm hoch und hält den Raum darunter im Alltag nutzbar und frei.
Schlusssatz: So wird ein Höhenunterschied im Alltag weniger bestimmend.
Fakten:
- Für Höhen bis 2.990 mm, unabhängig von einer Treppe.
- Raum unter Plattform bleibt vollständig nutzbar.
""",
        ]
    )

    generated = generate_product_topics(
        count=1,
        target_length_tier=8,
        llm_factory=lambda: fake_llm,
    )

    assert generated[0]["product_name"] == "LEVEL"
    assert "2.990 mm" in generated[0]["script"]
    assert len(fake_llm.text_prompts) == 1


def test_generate_product_topics_disables_thinking_budget(monkeypatch):
    monkeypatch.setattr(
        "app.features.topics.prompt3_runtime.get_product_knowledge_base",
        lambda: [
            ProductKnowledgeEntry(
                product_name="VARIO PLUS",
                source_label="PLATTFORMTREPPENLIFT T80",
                aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
                summary="Plattform oder Sitzlift auf derselben Schiene.",
                facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
                support_facts=["100% Made in Germany"],
            )
        ],
    )
    fake_llm = _FakeProductLLM(
        [
            """Produkt: VARIO PLUS\nAngle: Maximale Flexibilitaet\nScript: VARIO PLUS hilft dir bei jeder Treppe und bleibt flexibel. Du nutzt dieselbe Schiene fuer heute und spaeter. Innen und aussen bleibt die Loesung ruhig, sicher und gut planbar. Mit 300 Kilo Tragkraft bekommst du zusaetzliche Sicherheit im Alltag. Vor dem Einbau klaerst du Kurven, Bedienung, Sitzwechsel und die passende Ausstattung gemeinsam. So passt sich dein Zuhause spuerbar an und bleibt selbstbestimmt und frei.\nCTA: Frag jetzt nach VARIO PLUS.\nFakten:\n- Plattform oder Sitzlift auf derselben Schiene\n- Tragfaehigkeit bis 300 kg\n""",
            """Produkt: VARIO PLUS\nAngle: Maximale Flexibilitaet\nScript: Eine passende Liftlösung hilft dir bei jeder Treppe und bleibt flexibel. Du nutzt dieselbe Schiene für heute und später. Innen und außen bleibt die Lösung ruhig, sicher und gut planbar. Mit 300 Kilo Tragkraft bekommst du zusätzliche Sicherheit im Alltag. Vor dem Einbau klärst du Kurven, Bedienung, Sitzwechsel und die passende Ausstattung gemeinsam. So passt sich dein Zuhause spürbar an und bleibt selbstbestimmt und frei.\nSchlusssatz: So bleibt Planung näher an deinem Alltag und weniger an Verkaufsdruck.\nFakten:\n- Plattform oder Sitzlift auf derselben Schiene\n- Tragfaehigkeit bis 300 kg\n""",
        ]
    )

    generated = generate_product_topics(
        count=1,
        target_length_tier=32,
        llm_factory=lambda: fake_llm,
    )

    assert generated[0]["product_name"] == "VARIO PLUS"
    assert fake_llm.text_prompts[0][2]["thinking_budget"] == 0


def test_strip_user_facing_product_markers_removes_all_name_variants():
    leaked = "Mit VARIO PLUS und dem PLATTFORMTREPPENLIFT T80 von LIPPE Lift bleibst du flexibel."
    cleaned = _strip_user_facing_product_markers(
        leaked,
        product_name="VARIO PLUS",
        aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
    )
    assert "VARIO PLUS" not in cleaned
    assert "PLATTFORMTREPPENLIFT" not in cleaned
    assert "T80" not in cleaned
    assert "LIPPE Lift" not in cleaned
    # The generic, non-name remainder survives without leaving double spaces.
    assert "bleibst du flexibel." in cleaned
    assert "  " not in cleaned


def test_product_fallback_topic_never_exposes_product_name():
    entry = ProductKnowledgeEntry(
        product_name="VARIO PLUS",
        source_label="PLATTFORMTREPPENLIFT T80",
        aliases=["VARIO PLUS", "PLATTFORMTREPPENLIFT T80"],
        summary="Plattform oder Sitzlift auf derselben Schiene.",
        facts=["Plattform oder Sitzlift auf derselben Schiene", "Tragfaehigkeit bis 300 kg"],
        support_facts=["in Deutschland gefertigt"],
    )
    topic = _build_product_fallback_topic(entry, target_length_tier=8, reason="provider_retry_exhausted")
    for field in ("title", "angle", "script", "rotation", "cta"):
        assert "VARIO PLUS" not in topic[field]
        assert "PLATTFORMTREPPENLIFT" not in topic[field]
        assert "T80" not in topic[field]
    assert topic["product_name"] == "VARIO PLUS"
