import pytest

from app.core.errors import ValidationError
from app.features.publish import handlers as publish_handlers
from app.features.topics import captions
from app.features.topics.response_parsers import parse_topic_research_response


CURIOSITY_BODY = (
    "Das sagt dir dein Verkehrsbetrieb nicht: "
    "Viele Kommunen nutzten Ausnahmen, um Barrierefreiheit zu umgehen. "
    "Ab 2026 ist Schluss.\n\n"
    "Speicher dir das.\n\n"
    "#BarriereFreiheit #ÖPNV #Inklusion"
)

PERSONAL_BODY = (
    "Wenn du ÖPNV fährst, betrifft dich das ab 2026. "
    "Viele Haltestellen sind bis heute nicht barrierefrei.\n\n"
    "Schick das jemandem, der das wissen muss.\n\n"
    "#Barrierefrei #Alltag #Selbstbestimmt"
)

PROVOCATIVE_BODY = (
    "Keine Rampe, kein Aufzug — und trotzdem barrierefrei? "
    "Ab 2026 gibt es für Kommunen keine Ausreden mehr.\n\n"
    "Kommentier, wenn deine Haltestelle betroffen ist.\n\n"
    "#BarriereFreiheit #ÖPNV #Teilhabe"
)

VARIANT_KEYS = ("curiosity", "personal", "provocative")


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


def _make_stub_llm():
    return _StubLLM(
        {
            "variants": [
                {"key": "curiosity", "body": CURIOSITY_BODY},
                {"key": "personal", "body": PERSONAL_BODY},
                {"key": "provocative", "body": PROVOCATIVE_BODY},
            ]
        }
    )


# --- Validation ---


@pytest.mark.parametrize(
    ("key", "body"),
    [
        ("curiosity", CURIOSITY_BODY),
        ("personal", PERSONAL_BODY),
        ("provocative", PROVOCATIVE_BODY),
    ],
)
def test_validate_caption_variant_accepts_new_structure(key, body):
    result = captions.validate_caption_variant(
        key, body, "Ein ganz anderes Skript das nichts mit der Caption zu tun hat."
    )
    assert result["key"] == key
    assert 80 <= result["char_count"] <= 400


def test_validate_caption_variant_rejects_unknown_key():
    with pytest.raises(ValidationError, match="Unknown caption family"):
        captions.validate_caption_variant(
            "short_paragraph", CURIOSITY_BODY, "Ein anderes Skript."
        )


def test_validate_caption_variant_rejects_too_short():
    with pytest.raises(ValidationError, match="target length"):
        captions.validate_caption_variant(
            "curiosity", "Zu kurz. #Tag", "Ein anderes Skript."
        )


def test_validate_caption_variant_rejects_too_long():
    long = "A" * 401 + "\n\nSpeicher dir das.\n\n#Tag1 #Tag2"
    with pytest.raises(ValidationError, match="target length"):
        captions.validate_caption_variant("curiosity", long, "Ein anderes Skript.")


def test_validate_caption_variant_rejects_more_than_one_emoji():
    body = (
        "Das wissen die wenigsten über Barrierefreiheit im ÖPNV ✨ 🚦 "
        "Viele Haltestellen sind nicht barrierefrei.\n\n"
        "Speicher dir das.\n\n"
        "#Barrierefrei #ÖPNV"
    )
    with pytest.raises(ValidationError):
        captions.validate_caption_variant("curiosity", body, "Ein anderes Skript.")


def test_validate_caption_variant_rejects_research_label_leakage():
    body = (
        "Zentrale Erkenntnisse auf einen Blick:** Barrierefreiheit ist wichtig. "
        "Viele wissen das nicht und merken es erst zu spaet.\n\n"
        "Speicher dir das fuer spaeter.\n\n"
        "#Barrierefrei #ÖPNV #Inklusion"
    )
    with pytest.raises(ValidationError, match="research-note leakage"):
        captions.validate_caption_variant("curiosity", body, "Ein anderes Skript.")


def test_validate_caption_variant_rejects_high_script_overlap():
    script = "Viele Kommunen nutzten Ausnahmen um Barrierefreiheit zu umgehen und das betrifft fast alle Haltestellen im ganzen Land."
    body = (
        "Viele Kommunen nutzten Ausnahmen um Barrierefreiheit zu umgehen. "
        "Das betrifft fast alle Haltestellen im ganzen Land.\n\n"
        "Speicher dir das fuer spaeter.\n\n"
        "#Barrierefrei #ÖPNV #Inklusion"
    )
    with pytest.raises(ValidationError, match="repeats script"):
        captions.validate_caption_variant("curiosity", body, script)


def test_validate_caption_variant_rejects_missing_hashtags():
    body = (
        "Das sagt dir keiner: Viele Kommunen nutzten Ausnahmen, um Barrierefreiheit zu umgehen. "
        "Ab 2026 ist damit endgueltig Schluss und es gibt keine Ausreden mehr.\n\n"
        "Speicher dir das fuer spaeter."
    )
    with pytest.raises(ValidationError, match="hashtag"):
        captions.validate_caption_variant("curiosity", body, "Ein anderes Skript.")


# --- Bundle validation ---


def test_validate_caption_bundle_accepts_three_variants():
    parsed = {
        "variants": [
            {"key": "curiosity", "body": CURIOSITY_BODY},
            {"key": "personal", "body": PERSONAL_BODY},
            {"key": "provocative", "body": PROVOCATIVE_BODY},
        ]
    }
    result = captions.validate_caption_bundle(
        parsed, "Ein unabhängiges Skript das bewusst anders formuliert ist."
    )
    assert len(result["variants"]) == 3
    assert {v["key"] for v in result["variants"]} == set(VARIANT_KEYS)


def test_validate_caption_bundle_accepts_partial():
    parsed = {
        "variants": [
            {"key": "curiosity", "body": CURIOSITY_BODY},
            {"key": "provocative", "body": PROVOCATIVE_BODY},
        ]
    }
    result = captions.validate_caption_bundle(
        parsed, "Ein unabhängiges Skript."
    )
    assert len(result["variants"]) == 2


def test_validate_caption_bundle_raises_when_no_valid_variants():
    parsed = {"variants": [{"key": "curiosity", "body": "Zu kurz."}]}
    with pytest.raises(ValidationError):
        captions.validate_caption_bundle(parsed, "Ein Skript.")


# --- Selection ---


def test_select_caption_variant_key_is_deterministic():
    first = captions.select_caption_variant_key(
        topic_title="Thema", post_type="value", script="Script"
    )
    second = captions.select_caption_variant_key(
        topic_title="Thema", post_type="value", script="Script"
    )
    assert first == second
    assert first in VARIANT_KEYS


# --- Script hook extraction ---


def test_extract_script_hook_gets_first_sentence():
    script = (
        "Das sagt dir dein Verkehrsbetrieb nicht: Viele Kommunen nutzten Ausnahmen. "
        "Noch mehr Details hier."
    )
    hook = captions.extract_script_hook(script)
    assert hook == "Das sagt dir dein Verkehrsbetrieb nicht: Viele Kommunen nutzten Ausnahmen."


def test_extract_script_hook_returns_full_short_script():
    script = "Kurzer Satz ohne zweiten"
    hook = captions.extract_script_hook(script)
    assert hook == script


# --- Prompt building ---


def test_build_caption_prompt_includes_new_fields():
    prompt = captions._build_caption_prompt(
        topic_title="Barrierefreiheit",
        post_type="value",
        script="Ein Skript.",
        script_hook="Der Hook.",
        research_facts=["Fakt eins.", "Fakt zwei."],
    )
    assert "Der Hook." in prompt
    assert "1. Fakt eins." in prompt
    assert "2. Fakt zwei." in prompt
    assert "[curiosity]" in prompt
    assert "[personal]" in prompt
    assert "[provocative]" in prompt
    assert "[short_paragraph]" not in prompt
    assert "STANDARD-Kurzcaption-Profil" in prompt


# --- Parse ---


def test_parse_text_variants_with_new_markers():
    text = (
        f"[curiosity]\n{CURIOSITY_BODY}\n\n"
        f"[personal]\n{PERSONAL_BODY}\n\n"
        f"[provocative]\n{PROVOCATIVE_BODY}"
    )
    parsed = captions._parse_text_variants(text)
    assert len(parsed["variants"]) == 3
    assert {v["key"] for v in parsed["variants"]} == set(VARIANT_KEYS)


# --- End-to-end generation ---


def test_caption_profile_gate_uses_extended_only_for_deep_payloads():
    deep_payload = {
        "strict_seed": {"facts": ["F1", "F2", "F3", "F4", "F5"]},
        "source": {"url": "https://one.example"},
        "source_urls": [
            {"url": "https://one.example"},
            {"url": "https://two.example"},
            {"url": "https://three.example"},
        ],
    }
    thin_payload = {
        "strict_seed": {"facts": ["F1", "F2"]},
        "source_urls": [{"url": "https://one.example"}],
    }

    assert captions.select_caption_profile(deep_payload) == "extended"
    assert captions.select_caption_profile(thin_payload) == "standard"


def test_generate_caption_bundle_with_new_structure():
    class FakeLLM:
        def generate_gemini_text(self, **kwargs):
            return (
                f"[curiosity]\n{CURIOSITY_BODY}\n\n"
                f"[personal]\n{PERSONAL_BODY}\n\n"
                f"[provocative]\n{PROVOCATIVE_BODY}"
            )

    bundle = captions.generate_caption_bundle(
        topic_title="Barrierefreiheit im ÖPNV",
        post_type="value",
        script="Ein komplett anderes Skript das bewusst nichts wiederholt.",
        research_facts=["Viele Kommunen nutzten Ausnahmen.", "Ab 2026 gelten neue Regeln."],
        llm_factory=lambda: FakeLLM(),
    )
    assert len(bundle["variants"]) == 3
    assert bundle["selected_key"] in VARIANT_KEYS
    assert bundle["selected_body"]
    assert bundle["selection_reason"] == "hash_variant"
    assert bundle["caption_profile"] == "standard"


def test_generate_caption_bundle_uses_extended_profile_when_research_is_deep():
    bundle = captions.generate_caption_bundle(
        topic_title="Barrierefreiheit",
        post_type="value",
        script="Ein anderes Skript mit klar getrennten Aussagen fuer den Test.",
        research_facts=["F1", "F2", "F3", "F4", "F5"],
        seed_payload={
            "strict_seed": {
                "facts": [
                    "Aufzuege fallen oft genau dann aus, wenn du keine Alternative hast.",
                    "Viele Fahrgastinfos bleiben fuer Screenreader unklar formatiert.",
                    "Niederflureinstiege helfen nur, wenn der Spalt wirklich ueberbrueckt wird.",
                    "Assistenz muss haeufig vorab angemeldet werden und kostet sonst Zeit.",
                    "Klare Echtzeitdaten senken Stress bei knappen Umstiegen spuerbar.",
                ]
            },
            "source": {"url": "https://source-a.example"},
            "source_urls": [
                {"url": "https://source-a.example"},
                {"url": "https://source-b.example"},
                {"url": "https://source-c.example"},
            ],
        },
    )
    assert bundle["caption_profile"] == "extended"
    assert bundle["selected_key"] == "extended"
    assert "TL;DR:" in bundle["selected_body"]
    assert "Quellen" in bundle["selected_body"]
    assert "https://source-a.example" in bundle["selected_body"]


def test_generate_caption_bundle_falls_back_to_standard_when_extended_validation_fails():
    class FakeLLM:
        def generate_gemini_text(self, **kwargs):
            return (
                f"[curiosity]\n{CURIOSITY_BODY}\n\n"
                f"[personal]\n{PERSONAL_BODY}\n\n"
                f"[provocative]\n{PROVOCATIVE_BODY}"
            )

    bundle = captions.generate_caption_bundle(
        topic_title="Barrierefreiheit",
        post_type="value",
        script="Ein anderes Skript mit klar getrennten Aussagen fuer den Test.",
        research_facts=["F1", "F2", "F3", "F4", "F5"],
        llm_factory=lambda: FakeLLM(),
        seed_payload={
            "strict_seed": {
                "facts": [
                    "This source says the platform fails users when the lift is broken.",
                    "The update explains that screenreader labels remain inconsistent.",
                    "The report shows that boarding gaps still block independent travel.",
                    "The guide says assistance must be booked early with your operator.",
                    "The dashboard claims realtime alerts reduce missed transfers with good data.",
                ]
            },
            "source_urls": [
                {"url": "https://source-a.example"},
                {"url": "https://source-b.example"},
                {"url": "https://source-c.example"},
            ],
        },
    )
    assert bundle["selected_body"]
    assert bundle["caption_profile"] == "standard"
    assert bundle["selection_reason"] == "hash_variant"


def test_generate_caption_bundle_falls_back_on_persistent_failure():
    class BadLLM:
        def generate_gemini_text(self, **kwargs):
            return "no markers here"

    bundle = captions.generate_caption_bundle(
        topic_title="Barrierefreiheit im ÖPNV",
        post_type="value",
        script="Ein Skript zum Testen.",
        research_facts=[],
        llm_factory=lambda: BadLLM(),
    )
    assert len(bundle["variants"]) == 3
    assert bundle["selected_key"] in VARIANT_KEYS
    assert bundle["selected_body"]
    assert bundle["selection_reason"] == "local_fallback"
    assert bundle["caption_profile"] == "standard"


def test_generate_caption_bundle_falls_back_on_llm_error():
    class ErrorLLM:
        def generate_gemini_text(self, **kwargs):
            raise RuntimeError("boom")

    bundle = captions.generate_caption_bundle(
        topic_title="Topic",
        post_type="value",
        script="Skript.",
        research_facts=[],
        llm_factory=lambda: ErrorLLM(),
    )
    assert len(bundle["variants"]) == 3
    assert bundle["selected_key"] in VARIANT_KEYS
    assert bundle["selection_reason"] == "local_fallback"
    assert bundle["caption_profile"] == "standard"


def test_extended_caption_includes_source_links_and_preserves_bundle_shape():
    bundle = captions.generate_caption_bundle(
        topic_title="Thema",
        post_type="value",
        script="Ein anderes Skript fuer den Bundle-Shape-Test.",
        research_facts=["F1", "F2", "F3", "F4", "F5"],
        seed_payload={
            "strict_seed": {
                "facts": [
                    "Digitale Wegeleitung spart Zeit, wenn die Ansage spaet kommt.",
                    "Viele Aufzuege melden Ausfaelle nicht konsistent an Apps weiter.",
                    "Barrierefreie Toiletten helfen nur, wenn sie klar ausgeschildert sind.",
                    "Begleitservice braucht oft Vorlauf und klare Kontaktwege.",
                    "Klare Notfallinfos senken Stress im Umstieg deutlich.",
                ]
            },
            "source_urls": [
                {"url": "https://one.example"},
                {"url": "https://two.example"},
                {"url": "https://three.example"},
            ],
        },
    )
    assert bundle["caption_profile"] == "extended"
    assert "https://one.example" in bundle["selected_body"]
    assert "TL;DR:" in bundle["selected_body"]
    assert set(bundle.keys()) >= {
        "variants",
        "selected_key",
        "selected_body",
        "selection_reason",
        "caption_profile",
        "caption_depth_reason",
        "source_urls",
    }


def test_thin_payload_keeps_standard_path_and_bundle_shape():
    bundle = captions.generate_caption_bundle(
        topic_title="Thema",
        post_type="value",
        script="Ein anderes Skript fuer den Standardpfad.",
        research_facts=["F1"],
        llm_factory=lambda: _make_stub_llm(),
        seed_payload={
            "strict_seed": {"facts": ["Nur ein Fakt."]},
            "source_urls": [{"url": "https://one.example"}],
        },
    )
    assert bundle["caption_profile"] == "standard"
    assert bundle["selected_key"] in VARIANT_KEYS
    assert set(bundle.keys()) >= {
        "variants",
        "selected_key",
        "selected_body",
        "selection_reason",
        "caption_profile",
        "caption_depth_reason",
        "source_urls",
    }


# --- attach_caption_bundle ---


def test_attach_caption_bundle_sets_description_and_caption():
    payload = {
        "script": "Ein komplett anderes Skript das bewusst nichts wiederholt.",
        "strict_seed": {
            "facts": [
                "Viele Kommunen nutzten Ausnahmen.",
                "Ab 2026 gelten neue Regeln.",
            ],
        },
    }
    enriched = captions.attach_caption_bundle(
        payload,
        topic_title="Barrierefreier ÖPNV",
        post_type="value",
        llm_factory=lambda: _make_stub_llm(),
    )
    assert enriched["caption_bundle"]["selected_key"] in VARIANT_KEYS
    assert enriched["description"] == enriched["caption_bundle"]["selected_body"]
    assert enriched["caption"] == enriched["caption_bundle"]["selected_body"]


def test_attach_caption_bundle_overwrites_preexisting_caption():
    payload = {
        "script": "Ein komplett anderes Skript das bewusst nichts wiederholt.",
        "caption": "Stale caption that should be overwritten.",
        "strict_seed": {"facts": ["Fakt eins."]},
    }
    enriched = captions.attach_caption_bundle(
        payload,
        topic_title="Barrierefreier ÖPNV",
        post_type="value",
        llm_factory=lambda: _make_stub_llm(),
    )
    assert enriched["caption"] != "Stale caption that should be overwritten."
    assert enriched["caption"] == enriched["caption_bundle"]["selected_body"]


# --- resolve_selected_caption ---


def test_resolve_selected_caption_prefers_bundle():
    seed_data = {
        "caption": "Stale.",
        "description": "Legacy.",
        "caption_bundle": {
            "selected_body": CURIOSITY_BODY,
            "selection_reason": "hash_variant",
        },
    }
    assert captions.resolve_selected_caption(seed_data) == CURIOSITY_BODY


def test_resolve_selected_caption_falls_back_to_caption():
    seed_data = {"caption": "Fallback caption.", "description": "Legacy."}
    assert captions.resolve_selected_caption(seed_data) == "Fallback caption."


def test_parse_topic_research_response_preserves_multiple_source_urls():
    raw = """
    # Forschungsdossier: Barrierefreiheit

    Das Thema betrifft aktuelle Regeln, praktische Ausnahmen und konkrete Alltagshuerden im deutschen Alltag.
    Wichtige Details sind Fristen, Zuständigkeiten und die Frage, welche Stellen die Informationen aktuell halten.

    Quellen:
    - Tagesschau: [Tagesschau](https://www.tagesschau.de/)
    - BMAS: [BMAS](https://www.bmas.de/)
    - DB: [Deutsche Bahn](https://www.bahn.de/)

    Weitere Details:
    - Fristen und Ausnahmen werden sauber eingeordnet.
    - Praxistaugliche Hinweise helfen dabei, die Lage im Alltag sofort zu verstehen.
    - Der Text liefert genug Kontext, damit die Normalisierung nicht auf den Seed zurueckfallen muss.
    """

    dossier = parse_topic_research_response(
        raw,
        seed_topic="Barrierefreiheit",
        post_type="value",
        target_length_tier=8,
    )
    payload = dossier.model_dump(mode="json")
    assert len(payload["sources"]) >= 3
    assert len(payload["source_urls"]) >= 3
    assert payload["source_urls"][0]["url"].startswith("https://")
    assert payload["sources"][0]["url"] == payload["source_urls"][0]["url"]


# --- default_publish_caption ---


def test_default_publish_caption_prefers_caption_bundle():
    post = {
        "publish_caption": "",
        "seed_data": {
            "description": "Legacy description",
            "caption_bundle": {"selected_body": CURIOSITY_BODY},
        },
    }
    assert publish_handlers._default_publish_caption(post) == CURIOSITY_BODY
