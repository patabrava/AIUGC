from __future__ import annotations

import pytest

import app.features.topics.handlers as topic_handlers
from app.features.topics.schemas import DialogScripts


def _dialog_scripts(script: str) -> DialogScripts:
    return DialogScripts(
        problem_agitate_solution=[script],
        testimonial=[script],
        transformation=[script],
        description="Beschreibung fuer einen direkten Lifestyle-Test.",
    )


@pytest.mark.parametrize("post_type", ["lifestyle", "product"])
def test_direct_generated_posts_survive_topic_bank_persistence_failure(monkeypatch, post_type):
    batch = {
        "id": f"batch-direct-bank-failure-{post_type}",
        "brand": "Direct Bank Failure",
        "state": "S1_SETUP",
        "post_type_counts": {
            "value": 0,
            "lifestyle": 1 if post_type == "lifestyle" else 0,
            "product": 1 if post_type == "product" else 0,
        },
        "target_length_tier": 32,
    }
    created_posts = []

    lifestyle_script = "Ein guter Alltag beginnt, wenn Wege planbar bleiben und kleine Routinen dir echte Kraft sparen."
    product_script = (
        "VARIO PLUS hilft dir zuhause. Der modulare Aufbau bleibt der zentrale Vorteil. "
        "Das gibt dir mehr Sicherheit auf Wegen, die jeden Tag zaehlen. "
        "Die Planung bleibt klar und alltagstauglich. So wird dein Zuhause ohne unnoetigen Umbau besser nutzbar."
    )

    def fake_generate_lifestyle_topics(count=1, target_length_tier=None):
        return [
            {
                "title": "Planbarer Alltag",
                "rotation": lifestyle_script,
                "cta": "Plane deinen Weg bewusst.",
                "spoken_duration": 12,
                "dialog_scripts": _dialog_scripts(lifestyle_script),
                "framework": "PAL",
            }
        ][:count]

    def fake_generate_product_topics(count=1, target_length_tier=None):
        return [
            {
                "title": "VARIO PLUS: sicherer Alltag",
                "rotation": product_script,
                "cta": "Frag nach VARIO PLUS.",
                "spoken_duration": 20,
                "script": product_script,
                "framework": "PAL",
                "product_name": "VARIO PLUS",
                "angle": "sicherer Alltag",
                "facts": ["Modularer Aufbau"],
                "source_summary": "Static product knowledge.",
                "support_facts": [],
            }
        ][:count]

    def fake_create_post_for_batch(**kwargs):
        post = {"id": f"post-{len(created_posts) + 1}", **kwargs}
        created_posts.append(post)
        return post

    monkeypatch.setattr(topic_handlers, "get_batch_by_id", lambda batch_id: dict(batch))
    monkeypatch.setattr(topic_handlers, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_handlers, "generate_lifestyle_topics", fake_generate_lifestyle_topics)
    monkeypatch.setattr(topic_handlers, "generate_product_topics", fake_generate_product_topics)
    monkeypatch.setattr(topic_handlers, "build_lifestyle_seed_payload", lambda topic_data, dialog_scripts: {"script": dialog_scripts.problem_agitate_solution[0], "canonical_topic": topic_data["title"]})
    monkeypatch.setattr(topic_handlers, "_attach_publish_captions", lambda **kwargs: dict(kwargs["seed_payload"], caption=f"Caption for {kwargs['topic_title']}"))
    monkeypatch.setattr(topic_handlers, "store_topic_bank_entry", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bank unavailable")))
    monkeypatch.setattr(topic_handlers, "upsert_topic_script_variants", lambda **kwargs: (_ for _ in ()).throw(AssertionError("upsert should not run after store failure")))
    monkeypatch.setattr(topic_handlers, "create_post_for_batch", fake_create_post_for_batch)
    monkeypatch.setattr(topic_handlers, "update_batch_state", lambda batch_id, target_state: {"id": batch_id, "state": getattr(target_state, "value", target_state)})

    topic_handlers.clear_seeding_progress(batch["id"])
    result = topic_handlers._discover_topics_for_batch_sync(batch["id"])

    assert result["posts_created"] == 1
    assert result["state"] == "S2_SEEDED"
    assert [post["post_type"] for post in created_posts] == [post_type]
    assert created_posts[0]["target_length_tier"] == 32
