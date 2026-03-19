"""
Lifestyle topic deduplication testscript.
Verifies S1_SETUP lifestyle generation filters duplicate-like candidates before creating posts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.features.topics.handlers as topic_handlers


def main() -> None:
    created_posts = []
    registry_rows = []

    batch = {
        "id": "batch-lifestyle",
        "brand": "Lifestyle Dedup Fixture",
        "state": "S1_SETUP",
        "post_type_counts": {"value": 0, "lifestyle": 1, "product": 0},
    }

    duplicate_topic = {
        "title": "Community-Erfahrungen teilen",
        "rotation": "Check mal, wie gemeinsame Tipps aus der Community dir im Alltag sofort mehr Sicherheit und Mut geben",
        "cta": "mehr Sicherheit und Mut geben.",
        "spoken_duration": 8.0,
        "dialog_scripts": SimpleNamespace(
            problem_agitate_solution=[
                "Check mal, wie gemeinsame Tipps aus der Community dir im Alltag sofort mehr Sicherheit und Mut geben."
            ],
            testimonial=[],
            transformation=[],
            description="Community-basierter Lifestyle-Beitrag",
        ),
        "framework": "PAL",
    }
    unique_topic = {
        "title": "Freizeit mit Rollstuhl genießen",
        "rotation": "Weißt du, welche kleinen Freizeittricks spontane Ausflüge mit Rollstuhl deutlich entspannter und flexibler machen",
        "cta": "deutlich entspannter und flexibler machen.",
        "spoken_duration": 8.0,
        "dialog_scripts": SimpleNamespace(
            problem_agitate_solution=[
                "Weißt du, welche kleinen Freizeittricks spontane Ausflüge mit Rollstuhl deutlich entspannter und flexibler machen."
            ],
            testimonial=[],
            transformation=[],
            description="Freizeitfokussierter Lifestyle-Beitrag",
        ),
        "framework": "PAL",
    }

    call_counter = {"count": 0}

    def fake_get_batch_by_id(batch_id: str):
        assert batch_id == batch["id"]
        return dict(batch)

    def fake_get_all_topics_from_registry():
        return [
            {
                "id": "registry-dup",
                "title": duplicate_topic["title"],
                "rotation": duplicate_topic["rotation"],
                "cta": duplicate_topic["cta"],
            }
        ]

    def fake_generate_lifestyle_topics(count: int = 1):
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            return [dict(duplicate_topic), dict(duplicate_topic)]
        return [dict(unique_topic), dict(duplicate_topic)]

    def fake_add_topic_to_registry(title: str, rotation: str, cta: str):
        registry_rows.append((title, rotation, cta))
        return {"id": f"registry-{len(registry_rows)}", "title": title, "rotation": rotation, "cta": cta}

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

    def fake_build_lifestyle_seed_payload(topic_data, dialog_scripts):
        return {
            "script": dialog_scripts.problem_agitate_solution[0],
            "description": dialog_scripts.description,
            "framework": topic_data["framework"],
        }

    def fake_update_batch_state(batch_id: str, target_state):
        batch["state"] = target_state.value if hasattr(target_state, "value") else target_state
        return dict(batch)

    topic_handlers.get_batch_by_id = fake_get_batch_by_id
    topic_handlers.get_all_topics_from_registry = fake_get_all_topics_from_registry
    topic_handlers.generate_lifestyle_topics = fake_generate_lifestyle_topics
    topic_handlers.add_topic_to_registry = fake_add_topic_to_registry
    topic_handlers.create_post_for_batch = fake_create_post_for_batch
    topic_handlers.build_lifestyle_seed_payload = fake_build_lifestyle_seed_payload
    topic_handlers.update_batch_state = fake_update_batch_state
    topic_handlers.clear_seeding_progress(batch["id"])

    result = topic_handlers._discover_topics_for_batch_sync(batch["id"])

    assert result["posts_created"] == 1, result
    assert len(created_posts) == 1, created_posts
    assert created_posts[0]["topic_title"] == unique_topic["title"], created_posts
    assert created_posts[0]["post_type"] == "lifestyle", created_posts
    assert registry_rows == [
        (unique_topic["title"], unique_topic["rotation"], unique_topic["cta"])
    ], registry_rows
    assert call_counter["count"] >= 2, call_counter


if __name__ == "__main__":
    main()
