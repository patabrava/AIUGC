from __future__ import annotations

from types import SimpleNamespace

import app.features.topics.handlers as topic_handlers
from app.features.topics.seed_builders import build_product_seed_payload


def test_build_product_seed_payload_keeps_product_context():
    payload = build_product_seed_payload(
        {
            "title": "VARIO PLUS: Eine Schiene fuer heute und spaeter",
            "rotation": "Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?",
            "cta": "Lass dir zeigen, wie eine Schiene beide Wege offen haelt.",
            "spoken_duration": 6,
            "script": "Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?",
            "framework": "PAL",
            "product_name": "VARIO PLUS",
            "angle": "Eine Schiene fuer heute und spaeter",
            "facts": [
                "Plattform oder Sitzlift auf derselben Schiene",
                "Tragfaehigkeit bis 300 kg",
            ],
            "source_summary": "Plattform oder Sitzlift auf derselben Schiene.",
            "support_facts": ["100% Made in Germany"],
        }
    )

    assert payload["canonical_topic"] == "VARIO PLUS"
    assert payload["product_name"] == "VARIO PLUS"
    assert payload["product_angle"] == "Eine Schiene fuer heute und spaeter"
    assert payload["strict_seed"]["facts"][0] == "Plattform oder Sitzlift auf derselben Schiene"


def test_discover_topics_routes_product_batches_to_prompt3(monkeypatch):
    created_posts = []
    stored_entries = []
    variant_calls = []

    batch = {
        "id": "batch-product",
        "brand": "Product Fixture",
        "state": "S1_SETUP",
        "post_type_counts": {"value": 0, "lifestyle": 0, "product": 2},
        "target_length_tier": 8,
    }

    generated_topics = [
        {
            "title": "VARIO PLUS: Eine Schiene fuer heute und spaeter",
            "rotation": "Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?",
            "cta": "Lass dir zeigen, wie eine Schiene beide Wege offen haelt.",
            "spoken_duration": 6,
            "script": "Kennst du eine Treppe, die heute geht und morgen ploetzlich nicht mehr sicher ist?",
            "framework": "PAL",
            "product_name": "VARIO PLUS",
            "angle": "Eine Schiene fuer heute und spaeter",
            "facts": ["Plattform oder Sitzlift auf derselben Schiene"],
            "source_summary": "Plattform oder Sitzlift auf derselben Schiene.",
            "support_facts": ["100% Made in Germany"],
        },
        {
            "title": "LEVEL: Ohne Schacht nach oben",
            "rotation": "Schon ein kleiner Hoehenunterschied blockiert dich jeden Tag, obwohl dafuer kein Aufzugsschacht noetig waere.",
            "cta": "Frag nach, wie LEVEL kurze Hoehen sauber ueberbrueckt.",
            "spoken_duration": 6,
            "script": "Schon ein kleiner Hoehenunterschied blockiert dich jeden Tag, obwohl dafuer kein Aufzugsschacht noetig waere.",
            "framework": "PAL",
            "product_name": "LEVEL",
            "angle": "Ohne Schacht nach oben",
            "facts": ["Kein Aufzugsschacht erforderlich"],
            "source_summary": "Kein Aufzugsschacht erforderlich.",
            "support_facts": ["100% Made in Germany"],
        },
    ]

    monkeypatch.setattr(topic_handlers, "get_batch_by_id", lambda batch_id: dict(batch))
    monkeypatch.setattr(topic_handlers, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_handlers, "list_topic_suggestions", lambda **kwargs: [])
    monkeypatch.setattr(topic_handlers, "generate_product_topics", lambda count=1, seed=None, target_length_tier=None: generated_topics[:count])
    monkeypatch.setattr(topic_handlers, "add_topic_to_registry", lambda **kwargs: {"id": "topic-registry-id"})
    monkeypatch.setattr(topic_handlers, "_attach_publish_captions", lambda **kwargs: dict(kwargs["seed_payload"], caption=f"Caption for {kwargs['topic_title']}"))
    monkeypatch.setattr(topic_handlers, "update_batch_state", lambda batch_id, target_state: {"id": batch_id, "state": getattr(target_state, "value", target_state)})
    monkeypatch.setattr(topic_handlers, "store_topic_bank_entry", lambda **kwargs: {"id": f"bank-{len(stored_entries) + 1}", "title": kwargs.get("title", ""), "family_fingerprint": f"fingerprint-{len(stored_entries) + 1}"})

    def _fake_upsert_topic_script_variants(**kwargs):
        variant_calls.append(kwargs)

    def _fake_create_post_for_batch(**kwargs):
        post = {
            "id": f"post-{len(created_posts) + 1}",
            **kwargs,
        }
        created_posts.append(post)
        return post

    monkeypatch.setattr(topic_handlers, "upsert_topic_script_variants", _fake_upsert_topic_script_variants)
    monkeypatch.setattr(topic_handlers, "create_post_for_batch", _fake_create_post_for_batch)
    topic_handlers.clear_seeding_progress(batch["id"])

    result = topic_handlers._discover_topics_for_batch_sync(batch["id"])

    assert result["posts_created"] == 2
    assert {post["post_type"] for post in created_posts} == {"product"}
    assert created_posts[0]["seed_data"]["product_name"] == "VARIO PLUS"
    assert variant_calls, "Product posts should still create registry variants"
