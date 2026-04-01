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


def test_generate_lifestyle_topics_retries_when_request_level_script_shape_repeats(monkeypatch):
    scripts = [
        "Kennst du dieses Schlagloch jeden Morgen und planst deshalb schon vor dem Kaffee Umwege.",
        "Kennst du dieses Schlagloch jeden Morgen und planst deshalb schon vor dem Kaffee Umwege.",
        "Schon ein schmaler Eingang kippt deinen Tagesplan sofort, wenn niemand an Wendeflächen denkt.",
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

    generated = topic_agents.generate_lifestyle_topics(count=2, seed=11)

    rotations = [item["rotation"] for item in generated]
    assert len(rotations) == 2
    assert rotations[0] != rotations[1]
    assert call_index["value"] == 3


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

    def fake_add_topic_to_registry(title: str, rotation: str = "", cta: str = "", **kwargs):
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
        **kwargs,
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

    stored_bank_entries = []
    stored_variants = []

    def fake_list_topic_suggestions(**kwargs):
        return []

    def fake_store_topic_bank_entry(**kwargs):
        entry = {"id": f"bank-{len(stored_bank_entries) + 1}", "title": kwargs.get("title", "")}
        stored_bank_entries.append(entry)
        return entry

    def fake_upsert_topic_script_variants(**kwargs):
        stored_variants.append(kwargs)

    def fake_attach_publish_captions(**kwargs):
        seed_payload = dict(kwargs["seed_payload"])
        seed_payload["caption"] = seed_payload.get("caption") or f"Caption for {kwargs['topic_title']}"
        return seed_payload

    monkeypatch.setattr(topic_agents, "generate_dialog_scripts", fake_generate_dialog_scripts)
    monkeypatch.setattr(topic_handlers, "get_batch_by_id", fake_get_batch_by_id)
    monkeypatch.setattr(topic_handlers, "get_all_topics_from_registry", fake_get_all_topics_from_registry)
    monkeypatch.setattr(topic_handlers, "add_topic_to_registry", fake_add_topic_to_registry)
    monkeypatch.setattr(topic_handlers, "create_post_for_batch", fake_create_post_for_batch)
    monkeypatch.setattr(topic_handlers, "update_batch_state", fake_update_batch_state)
    monkeypatch.setattr(topic_handlers, "list_topic_suggestions", fake_list_topic_suggestions)
    monkeypatch.setattr(topic_handlers, "store_topic_bank_entry", fake_store_topic_bank_entry)
    monkeypatch.setattr(topic_handlers, "upsert_topic_script_variants", fake_upsert_topic_script_variants)
    monkeypatch.setattr(topic_handlers, "_attach_publish_captions", fake_attach_publish_captions)
    topic_handlers.clear_seeding_progress(batch["id"])

    result = topic_handlers._discover_topics_for_batch_sync(batch["id"])

    assert result["posts_created"] == 4, result
    assert batch["state"] == "S2_SEEDED", batch
    assert len(created_posts) == 4, created_posts
    assert {post["post_type"] for post in created_posts} == {"lifestyle"}, created_posts
    assert all(post["topic_title"] not in LIFESTYLE_TEMPLATES for post in created_posts), created_posts
    assert len(stored_bank_entries) == 4, stored_bank_entries


def test_unique_topic_suggestions_collapses_same_family_topics():
    suggestions = [
        {"title": "Merkzeichen B: Freifahrt", "seed_payload": {"canonical_topic": "Begleitperson im Nahverkehr mit Merkzeichen B"}},
        {"title": "Begleitperson gratis mitfahren", "seed_payload": {"canonical_topic": "Begleitperson im Nahverkehr mit Merkzeichen B"}},
        {"title": "Barrierefreiheit im ÖPNV-Alltag", "seed_payload": {"canonical_topic": "Barrierefreiheit im ÖPNV-Alltag"}},
        {"title": "Noch ein B-Thema", "seed_payload": {"canonical_topic": "Begleitperson im Nahverkehr mit Merkzeichen B"}},
    ]

    unique = topic_handlers._unique_topic_suggestions(suggestions, limit=10)

    assert [item["title"] for item in unique] == [
        "Merkzeichen B: Freifahrt",
        "Barrierefreiheit im ÖPNV-Alltag",
    ]


def test_unique_topic_suggestions_filters_semantic_near_duplicates():
    suggestions = [
        {
            "title": "Freifahrt für Begleitpersonen",
            "seed_payload": {"canonical_topic": "Begleitperson im Nahverkehr mit Merkzeichen B"},
        },
        {
            "title": "Begleitperson gratis im Nahverkehr",
            "seed_payload": {"canonical_topic": "Begleitperson im Nahverkehr mit Merkzeichen B"},
        },
        {
            "title": "Barrierefreiheit im Alltag",
            "seed_payload": {"canonical_topic": "Barrierefreiheit im Alltag"},
        },
    ]

    unique = topic_handlers._unique_topic_suggestions(suggestions, limit=10, existing_topics=[])

    assert len(unique) == 2
    assert unique[0]["title"] == "Freifahrt für Begleitpersonen"
    assert unique[1]["title"] == "Barrierefreiheit im Alltag"


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

    def fake_add_topic_to_registry(title: str, rotation: str = "", cta: str = "", **kwargs):
        return {"id": f"registry-{title}"}

    def fake_create_post_for_batch(
        batch_id: str,
        post_type: str,
        topic_title: str,
        topic_rotation: str,
        topic_cta: str,
        spoken_duration: float,
        seed_data,
        **kwargs,
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

    value_suggestion = {
        "id": "suggestion-value-1",
        "script_id": "script-value-1",
        "family_id": "topic-family-1",
        "family_fingerprint": "value topic title",
        "title": "Value topic title",
        "rotation": "Value rotation",
        "cta": "Value cta",
        "spoken_duration": 6.0,
        "seed_payload": {"script": "Value dialog script stays valid for the regression harness.", "facts": ["Value fact"]},
    }

    def fake_list_topic_suggestions(**kwargs):
        if kwargs.get("post_type") == "value":
            return [value_suggestion]
        return []

    def fake_store_topic_bank_entry(**kwargs):
        return {"id": "bank-1", "title": kwargs.get("title", "")}

    def fake_upsert_topic_script_variants(**kwargs):
        pass

    monkeypatch.setattr(topic_handlers, "get_batch_by_id", fake_get_batch_by_id)
    monkeypatch.setattr(topic_handlers, "get_all_topics_from_registry", fake_get_all_topics_from_registry)
    monkeypatch.setattr(topic_handlers, "list_topic_suggestions", fake_list_topic_suggestions)
    monkeypatch.setattr(topic_agents, "generate_dialog_scripts", fake_generate_dialog_scripts)
    monkeypatch.setattr(topic_handlers, "generate_lifestyle_topics", fake_generate_lifestyle_topics)
    monkeypatch.setattr(topic_handlers, "build_lifestyle_seed_payload", fake_build_lifestyle_seed_payload)
    monkeypatch.setattr(topic_handlers, "add_topic_to_registry", fake_add_topic_to_registry)
    monkeypatch.setattr(topic_handlers, "create_post_for_batch", fake_create_post_for_batch)
    monkeypatch.setattr(topic_handlers, "update_batch_state", fail_update_batch_state)
    monkeypatch.setattr(topic_handlers, "store_topic_bank_entry", fake_store_topic_bank_entry)
    monkeypatch.setattr(topic_handlers, "upsert_topic_script_variants", fake_upsert_topic_script_variants)
    topic_handlers.clear_seeding_progress(batch["id"])

    with pytest.raises(ValidationError) as exc_info:
        topic_handlers._discover_topics_for_batch_sync(batch["id"])

    assert exc_info.value.message == "Topic discovery did not create all requested post types."
    assert batch["state"] == "S1_SETUP", batch
    assert len(created_posts) == 1, created_posts
    assert created_posts[0]["post_type"] == "value", created_posts


def test_discover_topics_returns_coverage_pending_for_value_shortage(monkeypatch):
    batch = {
        "id": "batch-coverage-pending",
        "brand": "Coverage Pending Fixture",
        "state": "S1_SETUP",
        "post_type_counts": {"value": 1, "lifestyle": 0, "product": 0},
        "target_length_tier": 8,
    }
    queued = []

    monkeypatch.setattr(topic_handlers, "get_batch_by_id", lambda batch_id: dict(batch))
    monkeypatch.setattr(topic_handlers, "get_all_topics_from_registry", lambda: [])
    monkeypatch.setattr(topic_handlers, "list_topic_suggestions", lambda **kwargs: [])
    monkeypatch.setattr(
        topic_handlers,
        "_schedule_coverage_warmup",
        lambda **kwargs: queued.append(kwargs),
    )
    monkeypatch.setattr(
        topic_handlers,
        "create_post_for_batch",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("posts must not be created while coverage is pending")),
    )
    topic_handlers.clear_seeding_progress(batch["id"])

    result = topic_handlers._discover_topics_for_batch_sync(batch["id"])

    progress = topic_handlers.get_seeding_progress(batch["id"])
    assert result["coverage_pending"] is True
    assert result["posts_created"] == 0
    assert progress is not None
    assert progress["stage"] == "coverage_pending"
    assert queued == [
        {
            "batch_id": batch["id"],
            "post_type": "value",
            "target_length_tier": 8,
            "required_count": 1,
        }
    ]


def test_discover_topics_reuses_bank_suggestions_without_self_deduping(monkeypatch):
    batch = {
        "id": "batch-bank-reuse",
        "brand": "Bank Reuse Fixture",
        "state": "S1_SETUP",
        "post_type_counts": {"value": 1, "lifestyle": 0, "product": 0},
        "target_length_tier": 16,
    }
    created_posts = []

    def fake_create_post_for_batch(**kwargs):
        post = {
            "id": f"post-{len(created_posts) + 1}",
            "batch_id": kwargs["batch_id"],
            "post_type": kwargs["post_type"],
            "topic_title": kwargs["topic_title"],
            "seed_data": kwargs["seed_data"],
        }
        created_posts.append(post)
        return post

    def fake_update_batch_state(batch_id: str, target_state):
        batch["state"] = target_state.value if hasattr(target_state, "value") else target_state
        return dict(batch)

    suggestion = {
        "id": "topic-bank-1",
        "script_id": "script-bank-1",
        "family_id": "family-1",
        "family_fingerprint": "begleitperson nahverkehr merkzeichen b",
        "canonical_topic": "Begleitperson im Nahverkehr mit Merkzeichen B",
        "title": "Begleitperson im Nahverkehr erklärt",
        "rotation": "Mit Merkzeichen B planst du Begleitung und Freifahrt deutlich entspannter im Alltag.",
        "cta": "Prüf deine Nachweise rechtzeitig.",
        "spoken_duration": 16.0,
        "seed_payload": {
            "canonical_topic": "Begleitperson im Nahverkehr mit Merkzeichen B",
            "script": "Mit Merkzeichen B planst du Begleitung und Freifahrt deutlich entspannter im Alltag.",
            "facts": ["Begleitperson und Freifahrt müssen sauber nachgewiesen werden."],
        },
    }

    monkeypatch.setattr(topic_handlers, "get_batch_by_id", lambda batch_id: dict(batch))
    monkeypatch.setattr(
        topic_handlers,
        "get_all_topics_from_registry",
        lambda: [
            {
                "id": suggestion["id"],
                "title": suggestion["title"],
                "script": suggestion["rotation"],
                "seed_payload": {"canonical_topic": suggestion["canonical_topic"]},
            }
        ],
    )
    monkeypatch.setattr(topic_handlers, "list_topic_suggestions", lambda **kwargs: [suggestion])
    monkeypatch.setattr(
        topic_handlers,
        "_schedule_coverage_warmup",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("coverage warmup should not run when bank coverage exists")),
    )
    monkeypatch.setattr(
        topic_handlers,
        "add_topic_to_registry",
        lambda **kwargs: {"id": suggestion["id"], "canonical_topic": suggestion["canonical_topic"]},
    )
    monkeypatch.setattr(topic_handlers, "mark_topic_script_used", lambda script_id: None)
    monkeypatch.setattr(topic_handlers, "create_post_for_batch", fake_create_post_for_batch)
    monkeypatch.setattr(topic_handlers, "update_batch_state", fake_update_batch_state)
    topic_handlers.clear_seeding_progress(batch["id"])

    result = topic_handlers._discover_topics_for_batch_sync(batch["id"])

    assert result["posts_created"] == 1
    assert batch["state"] == "S2_SEEDED"
    assert len(created_posts) == 1
    assert created_posts[0]["seed_data"]["family_id"] == "family-1"


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
