from copy import deepcopy

import pytest

from app.core.errors import ValidationError
from app.features.publish import handlers as publish_handlers
from app.features.topics import captions
from app.features.topics.seed_builders import build_seed_payload
from app.features.topics.schemas import DialogScripts, ResearchAgentItem, ResearchAgentSource, SeedData


SHORT_BODY = (
    "Viele merken bei barrierefreien Wegen erst zu spät, wie viel an kleinen Details hängt. "
    "Wenn du vorher sortierst, sparst du Stress, Rückfragen und unnötige Umwege im Alltag. "
    "#Barrierefrei #RollstuhlAlltag"
)

MEDIUM_BODY = (
    "Bei Anträgen kippt selten das große Ganze, sondern fast immer ein kleines Detail.\n\n"
    "• Prüfe Fristen und Nachweise, bevor du loslegst.\n"
    "• Halte Rückfragen kurz, weil deine Unterlagen schon sortiert sind.\n"
    "• Plane Puffer ein, damit unterwegs nichts unnötig eskaliert.\n\n"
    "#Barrierefrei #Alltagstipps #Selbstbestimmt"
)

LONG_BODY = (
    "Wenn du bei Unterstützung nur auf den ersten Hinweis hörst, verlierst du oft Zeit und Nerven. "
    "Ein klarer Ablauf macht den Unterschied, gerade wenn mehrere Stellen beteiligt sind.\n\n"
    "Gerade bei längeren Themen hilft ein zweiter kurzer Absatz, damit die Einordnung nicht als Wand aus Text endet.\n\n"
    "1. Sammle zuerst alle Nachweise, die wirklich verlangt werden.\n"
    "2. Prüfe danach, welche Stelle in deinem Fall zuständig ist.\n"
    "3. Halte Termine, Rückfragen und Bestätigungen sauber fest.\n"
    "4. Plane genug Puffer ein, damit du nicht unter Druck nachreichen musst.\n\n"
    "#Barrierefrei #Selbstbestimmt #RollstuhlAlltag"
)


class _StubLLM:
    def __init__(self, payload):
        self.payload = payload

    def generate_gemini_text(self, **_kwargs):
        variants = self.payload.get("variants", [])
        parts = []
        for v in variants:
            parts.append(f"[{v['key']}]")
            parts.append(v["body"])
            parts.append("")
        return "\n".join(parts)


@pytest.mark.parametrize(
    ("key", "body"),
    [
        ("short_paragraph", SHORT_BODY),
        ("medium_bullets", MEDIUM_BODY),
        ("long_structured", LONG_BODY),
    ],
)
def test_validate_caption_variant_accepts_expected_structure(key, body):
    validated = captions.validate_caption_variant(key, body, "Das Skript selbst ist bewusst anders formuliert.")
    assert validated["key"] == key
    assert validated["char_count"] >= captions.FAMILY_SPECS[key]["min_chars"]



def test_select_caption_variant_key_is_deterministic():
    first = captions.select_caption_variant_key(topic_title="Thema", post_type="value", script="Script")
    second = captions.select_caption_variant_key(topic_title="Thema", post_type="value", script="Script")
    assert first == second
    assert first in {"medium_bullets", "long_structured"}


def test_attach_caption_bundle_sets_description_and_short_caption():
    llm = _StubLLM(
        {
            "variants": [
                {"key": "short_paragraph", "body": SHORT_BODY},
                {"key": "medium_bullets", "body": MEDIUM_BODY},
                {"key": "long_structured", "body": LONG_BODY},
            ]
        }
    )
    payload = {"script": "Kurzes Skript, aber nicht identisch mit der Caption.", "strict_seed": {"facts": ["Fakt eins"]}}
    enriched = captions.attach_caption_bundle(
        payload,
        topic_title="Barrierefreier ÖPNV",
        post_type="value",
        llm_factory=lambda: llm,
    )
    assert enriched["caption_bundle"]["selected_key"] in captions.FAMILY_ORDER
    assert enriched["description"] == enriched["caption_bundle"]["selected_body"]
    assert enriched["caption"] == enriched["caption_bundle"]["selected_body"]


def test_build_seed_payload_keeps_caption_blank_until_bundle_applied():
    item = ResearchAgentItem(
        topic="Barrierefreiheit im ÖPNV",
        script="Das Skript ist bewusst kurz und endet mit einem Punkt.",
        caption="",
        sources=[ResearchAgentSource(title="Quelle", url="https://example.com")],
        source_summary="Eine kurze Zusammenfassung mit genug Kontext.",
        estimated_duration_s=8,
    )
    strict_seed = SeedData(facts=["Fakt eins"], source_context="Kontext")
    dialog_scripts = DialogScripts(
        problem_agitate_solution=["Skript Problem."],
        testimonial=["Skript Testimonial."],
        transformation=["Skript Transformation."],
        description="Eine ausreichend lange Beschreibung fuer den Testlauf.",
    )

    payload = build_seed_payload(
        item,
        strict_seed,
        dialog_scripts,
        source_title="Quelle",
        source_url="https://example.com",
        source_summary="Eine kurze Zusammenfassung mit genug Kontext.",
    )

    assert payload["caption"] == ""

    enriched = captions.attach_caption_bundle(
        payload,
        topic_title="Barrierefreiheit im ÖPNV",
        post_type="value",
        llm_factory=lambda: _StubLLM(
            {
                "variants": [
                    {"key": "short_paragraph", "body": SHORT_BODY},
                    {"key": "medium_bullets", "body": MEDIUM_BODY},
                    {"key": "long_structured", "body": LONG_BODY},
                ]
            }
        ),
    )

    assert enriched["caption"] == enriched["caption_bundle"]["selected_body"]
    assert enriched["description"] == enriched["caption_bundle"]["selected_body"]


def test_build_seed_payload_separates_research_caption_from_publish_caption():
    item = ResearchAgentItem(
        topic="Barrierefreiheit im ÖPNV",
        script="Das Skript ist bewusst kurz und endet mit einem Punkt.",
        caption="Kurzer Forschungs-Hook mit Social-Ton.",
        sources=[ResearchAgentSource(title="Quelle", url="https://example.com")],
        source_summary="Eine kurze Zusammenfassung mit genug Kontext.",
        estimated_duration_s=8,
    )
    strict_seed = SeedData(facts=["Fakt eins"], source_context="Kontext")
    payload = build_seed_payload(
        item,
        strict_seed,
        None,
        source_title="Quelle",
        source_url="https://example.com",
        source_summary="Eine kurze Zusammenfassung mit genug Kontext.",
    )

    assert payload["caption"] == ""
    assert payload["research_caption"] == "Kurzer Forschungs-Hook mit Social-Ton."
    assert payload["canonical_topic"] == "Barrierefreiheit im ÖPNV"
    assert payload["research_title"] == "Barrierefreiheit im ÖPNV"


def test_default_publish_caption_prefers_caption_bundle_over_legacy_description():
    post = {
        "publish_caption": "",
        "seed_data": {
            "description": "Legacy description",
            "caption_bundle": {
                "selected_body": MEDIUM_BODY,
            },
        },
    }
    assert publish_handlers._default_publish_caption(post) == MEDIUM_BODY


def test_resolve_selected_caption_prefers_bundle_over_caption_even_for_fallback():
    """caption_bundle.selected_body always wins, even if it's from fallback."""
    seed_data = {
        "caption": "Das ist die eigentlich gewollte Caption mit sauberem Deutsch.",
        "description": "Legacysummary",
        "caption_bundle": {
            "selected_body": "Titel wurde irrtuemlich in die Caption kopiert.",
            "selection_reason": "fallback_hash_variant",
        },
    }

    assert captions.resolve_selected_caption(seed_data) == seed_data["caption_bundle"]["selected_body"]


def test_resolve_selected_caption_prefers_bundle_over_stale_caption():
    """When caption_bundle.selected_body exists, it wins over seed_data.caption."""
    seed_data = {
        "caption": "Stale research caption without hashtags.",
        "description": "Legacy description",
        "caption_bundle": {
            "selected_body": MEDIUM_BODY,
            "selection_reason": "hash_variant",
        },
    }
    assert captions.resolve_selected_caption(seed_data) == MEDIUM_BODY


def test_resolve_selected_caption_uses_selected_body_when_publish_caption_missing():
    seed_data = {
        "caption": "",
        "description": "Legacysummary",
        "caption_bundle": {
            "selected_body": MEDIUM_BODY,
            "selection_reason": "fallback_hash_variant",
        },
    }

    assert captions.resolve_selected_caption(seed_data) == MEDIUM_BODY


def test_generate_caption_bundle_falls_back_to_synthesized_bundle(monkeypatch):
    class FakeLLM:
        def generate_gemini_text(self, *args, **kwargs):
            raise RuntimeError("boom")

    bundle = captions.generate_caption_bundle(
        topic_title="Topic A",
        post_type="value",
        script=(
            "Erster langer Skripttext mit genug Inhalt, damit auch die längeren Caption-Familien "
            "ihre Mindestlaenge erreichen koennen."
        ),
        context="Kontext A mit ausreichend Text fuer die laengeren Varianten und saubere Validierung.",
        llm_factory=lambda: FakeLLM(),
    )

    assert bundle["selected_key"] in {"medium_bullets", "long_structured"}
    assert len(bundle["variants"]) == 3
    assert bundle["selected_body"]
    assert bundle["selection_reason"] in {"fallback_hash_variant", "hash_variant"}


def test_generate_caption_bundle_fallback_handles_long_topic_and_context():
    class FakeLLM:
        def generate_gemini_text(self, *args, **kwargs):
            raise RuntimeError("boom")

    bundle = captions.generate_caption_bundle(
        topic_title="Sehr langer Titel " * 20,
        post_type="value",
        script="Das ist ein unabhängiges Skript mit ausreichender Länge und klarer Satzstruktur.",
        context="Kontext " * 200,
        llm_factory=lambda: FakeLLM(),
    )

    assert len(bundle["variants"]) == 3
    assert {item["key"] for item in bundle["variants"]} == set(captions.FAMILY_ORDER)


def test_build_caption_prompt_discourages_title_copying():
    prompt = captions._build_caption_prompt(
        topic_title="Gesetzliche Rahmenbedingungen Fristen Ausnahmeregelungen",
        post_type="value",
        script="Das Skript ist bewusst kurz und endet mit einem Punkt.",
        context="Kontext",
    )

    assert "Thema ist nur Hintergrundkontext" in prompt
    assert "wiederhole den Titel nicht" in prompt
    assert "Topic-Titel" in prompt
    assert "Emoji" in prompt
    assert "klar anderen Einstieg" in prompt
    assert "Vermeide generische Platzhalter" in prompt
    assert "[short_paragraph]" in prompt
    assert "[medium_bullets]" in prompt
    assert "[long_structured]" in prompt
    assert "BEISPIEL-OUTPUT" in prompt


def test_generate_caption_bundle_uses_canonical_topic_for_title_checks(monkeypatch):
    captured = {}

    class FakeLLM:
        def generate_gemini_text(self, **kwargs):
            captured["prompt"] = kwargs["prompt"]
            return (
                f"[short_paragraph]\n{SHORT_BODY}\n\n"
                f"[medium_bullets]\n{MEDIUM_BODY}\n\n"
                f"[long_structured]\n{LONG_BODY}"
            )

    bundle = captions.generate_caption_bundle(
        topic_title="Rechtliche Grundlagen Zielsetzung - Forschungsdossier: Barrierefreiheit im ÖPNV-Alltag",
        canonical_topic="Barrierefreiheit im ÖPNV",
        post_type="value",
        script="Das Skript ist bewusst anders formuliert und endet mit einem Punkt.",
        context="Kontext",
        llm_factory=lambda: FakeLLM(),
    )

    assert bundle["selected_key"] == "medium_bullets"
    assert "Barrierefreiheit im ÖPNV" in captured["prompt"]
    assert "Forschungsdossier" not in captured["prompt"]


def test_fallback_caption_openers_are_not_generic():
    short = captions._fallback_body("Barrierefreiheit im ÖPNV", "Kontext", "short_paragraph")
    medium = captions._fallback_body("Barrierefreiheit im ÖPNV", "Kontext", "medium_bullets")
    long = captions._fallback_body("Barrierefreiheit im ÖPNV", "Kontext", "long_structured")

    assert "Barrierefreiheit" in short
    assert "Barrierefreiheit" in medium or "Kleine Details" in medium
    assert "diesem Thema" not in short
    assert "diesem Thema" not in medium
    assert "diesem Thema" not in long
    assert "Kontext" not in medium.split("\n\n")[1]
    assert "Kontext" not in long.split("\n\n")[1]
    assert len({short.splitlines()[0], medium.splitlines()[0], long.splitlines()[0]}) == 3


def test_validate_caption_variant_rejects_more_than_one_emoji():
    broken = (
        "Bei diesem Thema hilft dir ein klarer Blick auf die kleinen Details im Alltag ✨ 🚦 "
        "Wenn du die wichtigsten Punkte vorher sortierst, vermeidest du Stress und reagierst unterwegs ruhiger. "
        "#Barrierefrei #RollstuhlAlltag"
    )
    with pytest.raises(ValidationError):
        captions.validate_caption_variant("short_paragraph", broken, "Ein anderes Skript mit genügend Abstand.")


def test_generate_caption_bundle_rejects_title_like_opening(monkeypatch):
    class FakeLLM:
        def generate_gemini_text(self, *args, **kwargs):
            short = "Bei Gesetzliche Rahmenbedingungen Fristen hilft dir ein klarer Blick. #Tag1 #Tag2"
            medium = (
                "Bei Gesetzliche Rahmenbedingungen Fristen hilft dir ein klarer Blick.\n\n"
                "• Punkt eins.\n"
                "• Punkt zwei.\n\n"
                "#Tag1 #Tag2 #Tag3"
            )
            long = (
                "Bei Gesetzliche Rahmenbedingungen Fristen hilft dir ein klarer Blick.\n\n"
                "Noch mehr Einordnung.\n\n"
                "1. Punkt eins.\n"
                "2. Punkt zwei.\n\n"
                "#Tag1 #Tag2 #Tag3"
            )
            return f"[short_paragraph]\n{short}\n\n[medium_bullets]\n{medium}\n\n[long_structured]\n{long}"

    bundle = captions.generate_caption_bundle(
        topic_title="Gesetzliche Rahmenbedingungen Fristen Ausnahmeregelungen",
        post_type="value",
        script="Das Skript ist bewusst kurz und endet mit einem Punkt.",
        context="Kontext",
        llm_factory=lambda: FakeLLM(),
    )

    assert bundle["selection_reason"] in ("hash_variant", "fallback_hash_variant")
    assert len(bundle["variants"]) == 3


def test_attach_caption_bundle_overwrites_preexisting_caption():
    """Even when caption has a value, attach_caption_bundle must overwrite it with bundle body."""
    llm = _StubLLM(
        {
            "variants": [
                {"key": "short_paragraph", "body": SHORT_BODY},
                {"key": "medium_bullets", "body": MEDIUM_BODY},
                {"key": "long_structured", "body": LONG_BODY},
            ]
        }
    )
    payload = {
        "script": "Kurzes Skript, aber nicht identisch mit der Caption.",
        "caption": "Stale research caption that should be overwritten.",
        "strict_seed": {"facts": ["Fakt eins"]},
    }
    enriched = captions.attach_caption_bundle(
        payload,
        topic_title="Barrierefreier ÖPNV",
        post_type="value",
        llm_factory=lambda: llm,
    )
    assert enriched["caption"] == enriched["caption_bundle"]["selected_body"]
    assert enriched["caption"] != "Stale research caption that should be overwritten."


def test_build_caption_prompt_requests_marker_format_not_json():
    prompt = captions._build_caption_prompt(
        topic_title="Barrierefreiheit",
        post_type="value",
        script="Ein Skript.",
        context="Kontext",
    )
    assert "[short_paragraph]" in prompt
    assert "[medium_bullets]" in prompt
    assert "[long_structured]" in prompt
    assert "valides JSON" not in prompt


def test_generate_caption_bundle_text_first_with_markers():
    marker_response = (
        "[short_paragraph]\n"
        f"{SHORT_BODY}\n\n"
        "[medium_bullets]\n"
        f"{MEDIUM_BODY}\n\n"
        "[long_structured]\n"
        f"{LONG_BODY}"
    )

    class TextLLM:
        def generate_gemini_text(self, **kwargs):
            return marker_response

    bundle = captions.generate_caption_bundle(
        topic_title="Barrierefreier ÖPNV",
        post_type="value",
        script="Das Skript ist bewusst anders formuliert und endet mit einem Punkt.",
        context="Kontext mit ausreichend Text fuer die Varianten.",
        llm_factory=lambda: TextLLM(),
    )

    assert len(bundle["variants"]) == 3
    assert {v["key"] for v in bundle["variants"]} == set(captions.FAMILY_ORDER)
    assert bundle["selected_body"]
    assert bundle["selection_reason"] == "hash_variant"


def test_parse_text_variants_round_trip():
    """Marker-formatted text should parse and validate as a complete caption bundle."""
    marker_text = (
        "[short_paragraph]\n"
        f"{SHORT_BODY}\n\n"
        "[medium_bullets]\n"
        f"{MEDIUM_BODY}\n\n"
        "[long_structured]\n"
        f"{LONG_BODY}"
    )
    parsed = captions._parse_text_variants(marker_text)
    assert len(parsed["variants"]) == 3
    assert {v["key"] for v in parsed["variants"]} == set(captions.FAMILY_ORDER)

    validated = captions.validate_caption_bundle(
        parsed, "Ein unabhaengiges Skript das absichtlich anders formuliert ist."
    )
    assert len(validated["variants"]) == 3
    for variant in validated["variants"]:
        assert variant["char_count"] >= captions.FAMILY_SPECS[variant["key"]]["min_chars"]


def test_parse_text_variants_missing_variant_fills_fallback():
    """If Gemini only returns 2 of 3 markers, missing variant gets filled from fallback."""
    marker_text = (
        "[short_paragraph]\n"
        f"{SHORT_BODY}\n\n"
        "[medium_bullets]\n"
        f"{MEDIUM_BODY}"
    )
    parsed = captions._parse_text_variants(marker_text)
    assert len(parsed["variants"]) == 2

    validated = captions.validate_caption_bundle(
        parsed, "Ein unabhaengiges Skript."
    )
    assert len(validated["variants"]) == 3
    assert {v["key"] for v in validated["variants"]} == set(captions.FAMILY_ORDER)
