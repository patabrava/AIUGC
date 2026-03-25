from copy import deepcopy

import pytest

from app.core.errors import ValidationError
from app.features.publish import handlers as publish_handlers
from app.features.topics import captions


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

    def generate_gemini_json(self, **_kwargs):
        return deepcopy(self.payload)

    def generate_gemini_text(self, **_kwargs):
        return ""


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


def test_validate_caption_variant_rejects_missing_paragraph_break_for_bullets():
    broken = MEDIUM_BODY.replace("\n\n", "\n", 1)
    with pytest.raises(ValidationError):
        captions.validate_caption_variant("medium_bullets", broken, "Ganz anderes Skript.")


def test_validate_caption_variant_rejects_long_medium_without_second_prose_paragraph():
    too_dense = (
        "Bei Anträgen kippt selten das große Ganze, sondern fast immer ein kleines Detail, und genau deshalb lohnt sich ein klarer Ablauf schon vor dem ersten Formular.\n\n"
        "• Prüfe Fristen und Nachweise, bevor du loslegst.\n"
        "• Halte Rückfragen kurz, weil deine Unterlagen schon sortiert sind.\n"
        "• Plane Puffer ein, damit unterwegs nichts unnötig eskaliert.\n\n"
        "#Barrierefrei #Alltagstipps #Selbstbestimmt"
    )
    assert len(too_dense) >= 320
    with pytest.raises(ValidationError):
        captions.validate_caption_variant("medium_bullets", too_dense, "Ganz anderes Skript.")


def test_validate_caption_variant_rejects_long_structured_without_second_prose_paragraph():
    broken = (
        "Wenn du bei Unterstützung nur auf den ersten Hinweis hörst, verlierst du oft Zeit und Nerven. "
        "Ein klarer Ablauf macht den Unterschied, gerade wenn mehrere Stellen beteiligt sind.\n\n"
        "1. Sammle zuerst alle Nachweise, die wirklich verlangt werden.\n"
        "2. Prüfe danach, welche Stelle in deinem Fall zuständig ist.\n"
        "3. Halte Termine, Rückfragen und Bestätigungen sauber fest.\n"
        "4. Plane genug Puffer ein, damit du nicht unter Druck nachreichen musst.\n\n"
        "#Barrierefrei #Selbstbestimmt #RollstuhlAlltag"
    )
    with pytest.raises(ValidationError):
        captions.validate_caption_variant("long_structured", broken, "Ganz anderes Skript.")


def test_select_caption_variant_key_is_deterministic():
    first = captions.select_caption_variant_key(topic_title="Thema", post_type="value", script="Script")
    second = captions.select_caption_variant_key(topic_title="Thema", post_type="value", script="Script")
    assert first == second
    assert first in captions.FAMILY_ORDER


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
    assert enriched["caption"] == SHORT_BODY


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
