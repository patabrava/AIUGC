from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.features.topics.agents as topic_agents
import app.features.topics.handlers as topic_handlers
from app.core.errors import ValidationError
from app.features.topics.schemas import DialogScripts


LIFESTYLE_TEMPLATES = {
    "Rollstuhl-Alltag – Tipps & Tricks",
    "Barrierefreiheit im Alltag erleben",
    "Community-Erfahrungen teilen",
    "Freizeit mit Rollstuhl genießen",
    "Alltägliche Herausforderungen meistern",
}


def _dialog_scripts(
    script: str,
    description: str = "Lifestyle-Beitrag mit ausreichend langer Beschreibung.",
) -> DialogScripts:
    return DialogScripts(
        problem_agitate_solution=[script],
        testimonial=[script],
        transformation=[script],
        description=description,
    )


def test_generate_lifestyle_topics_derives_content_titles(monkeypatch):
    scripts = [
        "Dieser Bordstein kostet dich täglich unnötig Kraft, bis du deinen Winkel clever änderst.",
        "Mit dieser kleinen Freizeitplanung wird dein spontaner Ausflug endlich entspannter und leichter.",
        "Die besten Alltagstipps lernst du oft erst, wenn du mit anderen Rollstuhlnutzern sprichst.",
        "Schon eine kleine Rampe verändert deinen Weg zur Arbeit spürbar und dauerhaft positiv.",
    ]
    call_index = {"value": 0}

    def fake_generate_dialog_scripts(topic: str, scripts_required: int = 1, previously_used_hooks=None, profile=None):
        script = scripts[call_index["value"]]
        call_index["value"] += 1
        return _dialog_scripts(
            script,
            description=f"Ausführliche Lifestyle-Beschreibung für {topic} mit genug Kontext.",
        )

    monkeypatch.setattr(topic_agents, "generate_dialog_scripts", fake_generate_dialog_scripts)

    generated = topic_agents.generate_lifestyle_topics(count=4, seed=7)

    generated_titles = [item["title"] for item in generated]
    assert len(set(generated_titles)) == 4, generated_titles
    assert all(title not in LIFESTYLE_TEMPLATES for title in generated_titles), generated_titles


def test_discover_topics_creates_lifestyle_posts_even_when_registry_contains_template_titles(monkeypatch):
    created_posts = []
    registry_rows = []

    batch = {
        "id": "batch-lifestyle-regression",
        "brand": "Lifestyle Regression Fixture",
        "state": "S1_SETUP",
        "post_type_counts": {"value": 0, "lifestyle": 4, "product": 0},
    }

    scripts = [
        "Dieser Bordstein kostet dich täglich unnötig Kraft, bis du deinen Winkel clever änderst.",
        "Mit dieser kleinen Freizeitplanung wird dein spontaner Ausflug endlich entspannter und leichter.",
        "Die besten Alltagstipps lernst du oft erst, wenn du mit anderen Rollstuhlnutzern sprichst.",
        "Schon eine kleine Rampe verändert deinen Weg zur Arbeit spürbar und dauerhaft positiv.",
    ]
    call_index = {"value": 0}

    def fake_generate_dialog_scripts(topic: str, scripts_required: int = 1, previously_used_hooks=None, profile=None):
        script = scripts[call_index["value"]]
        call_index["value"] += 1
        return _dialog_scripts(
            script,
            description=f"Ausführliche Lifestyle-Beschreibung für {topic} mit genug Kontext.",
        )

    def fake_get_batch_by_id(batch_id: str):
        assert batch_id == batch["id"]
        return dict(batch)

    def fake_get_all_topics_from_registry():
        return [
            {
                "id": f"registry-{index}",
                "title": title,
                "rotation": "Bekannte Lifestyle-Rotation aus der Registry",
                "cta": "Bekannte Lifestyle-CTA aus der Registry",
            }
            for index, title in enumerate(LIFESTYLE_TEMPLATES, start=1)
        ]

    def fake_add_topic_to_registry(title: str, rotation: str, cta: str):
        registry_rows.append((title, rotation, cta))
        return {"id": f"registry-new-{len(registry_rows)}"}

    def fake_create_post_for_batch(
        batch_id: str,
        post_type: str,
        topic_title: str,
        topic_rotation: str,
        topic_cta: str,
        spoken_duration: float,
        seed_data,
    ):
        post = {
            "id": f"post-{len(created_posts) + 1}",
            "batch_id": batch_id,
            "post_type": post_type,
            "topic_title": topic_title,
            "topic_rotation": topic_rotation,
            "topic_cta": topic_cta,
            "spoken_duration": spoken_duration,
            "seed_data": seed_data,
        }
        created_posts.append(post)
        return post

    def fake_update_batch_state(batch_id: str, target_state):
        batch["state"] = target_state.value if hasattr(target_state, "value") else target_state
        return dict(batch)

    monkeypatch.setattr(topic_agents, "generate_dialog_scripts", fake_generate_dialog_scripts)
    monkeypatch.setattr(topic_handlers, "get_batch_by_id", fake_get_batch_by_id)
    monkeypatch.setattr(topic_handlers, "get_all_topics_from_registry", fake_get_all_topics_from_registry)
    monkeypatch.setattr(topic_handlers, "add_topic_to_registry", fake_add_topic_to_registry)
    monkeypatch.setattr(topic_handlers, "create_post_for_batch", fake_create_post_for_batch)
    monkeypatch.setattr(topic_handlers, "update_batch_state", fake_update_batch_state)
    topic_handlers.clear_seeding_progress(batch["id"])

    result = topic_handlers._discover_topics_for_batch_sync(batch["id"])

    assert result["posts_created"] == 4, result
    assert batch["state"] == "S2_SEEDED", batch
    assert len(created_posts) == 4, created_posts
    assert {post["post_type"] for post in created_posts} == {"lifestyle"}, created_posts
    assert all(post["topic_title"] not in LIFESTYLE_TEMPLATES for post in created_posts), created_posts
    assert len(registry_rows) == 4, registry_rows


def test_discover_topics_does_not_finalize_when_requested_post_type_is_missing(monkeypatch):
    created_posts = []

    batch = {
        "id": "batch-missing-lifestyle",
        "brand": "Missing Lifestyle Fixture",
        "state": "S1_SETUP",
        "post_type_counts": {"value": 1, "lifestyle": 1, "product": 0},
    }

    value_item = SimpleNamespace(topic="Value topic")
    value_topic = SimpleNamespace(
        title="Value topic title",
        rotation="Value rotation",
        cta="Value cta",
        spoken_duration=6.0,
    )

    duplicate_lifestyle_topic = {
        "title": "Community-Erfahrungen teilen",
        "rotation": "Bekannte Lifestyle-Rotation aus der Registry",
        "cta": "Bekannte Lifestyle-CTA aus der Registry",
        "spoken_duration": 6.0,
        "dialog_scripts": _dialog_scripts("Bekannte Lifestyle-Rotation aus der Registry."),
        "framework": "PAL",
    }

    def fake_get_batch_by_id(batch_id: str):
        assert batch_id == batch["id"]
        return dict(batch)

    def fake_get_all_topics_from_registry():
        return [
            {
                "id": "registry-dup",
                "title": duplicate_lifestyle_topic["title"],
                "rotation": duplicate_lifestyle_topic["rotation"],
                "cta": duplicate_lifestyle_topic["cta"],
            }
        ]

    def fake_generate_topics_research_agent(post_type: str, count: int, progress_callback=None):
        assert post_type == "value"
        assert count == 1
        return [value_item]

    def fake_convert_research_item_to_topic(item):
        return value_topic

    def fake_generate_dialog_scripts(topic: str, scripts_required: int = 1, previously_used_hooks=None, profile=None):
        return _dialog_scripts("Value dialog script stays valid for the regression harness.")

    def fake_generate_topic_script_candidate(**kwargs):
        return SimpleNamespace(script="Value prompt-one script remains isolated in this regression.")

    def fake_extract_seed_strict_extractor(topic):
        return SimpleNamespace(facts=["Value fact"], source_context="Value context")

    def fake_build_seed_payload(original_item, strict_seed, dialog_scripts, **_kwargs):
        script = dialog_scripts.problem_agitate_solution[0] if dialog_scripts else original_item.script
        return {"script": script, "strict_seed": strict_seed.facts}

    def fake_generate_lifestyle_topics(count: int = 1, target_length_tier=None):
        return [dict(duplicate_lifestyle_topic) for _ in range(count)]

    def fake_build_lifestyle_seed_payload(topic_data, dialog_scripts):
        return {"script": dialog_scripts.problem_agitate_solution[0]}

    def fake_add_topic_to_registry(title: str, rotation: str, cta: str):
        return {"id": f"registry-{title}"}

    def fake_create_post_for_batch(
        batch_id: str,
        post_type: str,
        topic_title: str,
        topic_rotation: str,
        topic_cta: str,
        spoken_duration: float,
        seed_data,
    ):
        post = {
            "id": f"post-{len(created_posts) + 1}",
            "batch_id": batch_id,
            "post_type": post_type,
            "topic_title": topic_title,
            "seed_data": seed_data,
        }
        created_posts.append(post)
        return post

    def fail_update_batch_state(batch_id: str, target_state):
        raise AssertionError("Batch state should not advance when a requested post type is missing")

    monkeypatch.setattr(topic_handlers, "get_batch_by_id", fake_get_batch_by_id)
    monkeypatch.setattr(topic_handlers, "get_all_topics_from_registry", fake_get_all_topics_from_registry)
    monkeypatch.setattr(topic_handlers, "list_topic_suggestions", lambda **kwargs: [])
    monkeypatch.setattr(topic_handlers, "generate_topics_research_agent", fake_generate_topics_research_agent)
    monkeypatch.setattr(topic_handlers, "convert_research_item_to_topic", fake_convert_research_item_to_topic)
    monkeypatch.setattr(topic_handlers, "generate_dialog_scripts", fake_generate_dialog_scripts)
    monkeypatch.setattr(topic_handlers, "generate_topic_script_candidate", fake_generate_topic_script_candidate)
    monkeypatch.setattr(topic_handlers, "extract_seed_strict_extractor", fake_extract_seed_strict_extractor)
    monkeypatch.setattr(topic_handlers, "build_seed_payload", fake_build_seed_payload)
    monkeypatch.setattr(topic_handlers, "generate_lifestyle_topics", fake_generate_lifestyle_topics)
    monkeypatch.setattr(topic_handlers, "build_lifestyle_seed_payload", fake_build_lifestyle_seed_payload)
    monkeypatch.setattr(topic_handlers, "add_topic_to_registry", fake_add_topic_to_registry)
    monkeypatch.setattr(topic_handlers, "create_post_for_batch", fake_create_post_for_batch)
    monkeypatch.setattr(topic_handlers, "update_batch_state", fail_update_batch_state)
    topic_handlers.clear_seeding_progress(batch["id"])

    with pytest.raises(ValidationError) as exc_info:
        topic_handlers._discover_topics_for_batch_sync(batch["id"])

    assert exc_info.value.message == "Topic discovery did not create all requested post types."
    assert batch["state"] == "S1_SETUP", batch
    assert len(created_posts) == 1, created_posts
    assert created_posts[0]["post_type"] == "value", created_posts


def test_prompt2_structured_payload_rejects_chopped_script():
    payload = topic_agents._coerce_prompt2_payload(
        {
            "problem_agitate_solution": [
                "Niemand sagt dir, dass Spontanität dein bester Reiseführer ist. Manchmal sind die ungeplant"
            ],
            "testimonial": [],
            "transformation": [],
            "description": "Ausführliche Lifestyle-Beschreibung mit genug Kontext und drei Hashtags am Ende. #Rollstuhl #Alltag #Tipps",
        },
        scripts_required=1,
    )

    assert payload.problem_agitate_solution == [
        "Niemand sagt dir, dass Spontanität dein bester Reiseführer ist. Manchmal sind die ungeplant."
    ]
    assert payload.testimonial == payload.problem_agitate_solution
    assert payload.transformation == payload.problem_agitate_solution
    assert payload.description.startswith("Ausführliche Lifestyle-Beschreibung")


def test_prompt2_structured_payload_requires_description():
    with pytest.raises(ValidationError, match="PROMPT_2 structured response invalid"):
        topic_agents._coerce_prompt2_payload(
            {
                "problem_agitate_solution": [
                    "Was dir bei nassem Wetter niemand klar sagt: Deine Hände und Felgen werden sonst schnell zur Schlammschlacht."
                ],
                "testimonial": [],
                "transformation": [],
                "description": None,
            },
            scripts_required=1,
        )
