from __future__ import annotations

from app.features.characters.scene_reference import (
    SCENE_CATALOG,
    WARDROBE_SET,
    build_scene_reference_prompt,
    map_script_to_scene_intent,
)


def _ready_actor(actor_id: str, *, is_active: bool):
    from app.features.characters.schemas import ActorIdentityRecord

    return ActorIdentityRecord(
        id=actor_id,
        name=f"Actor {actor_id}",
        is_active=is_active,
        provider="magnific",
        provider_lora_id=f"lora-{actor_id}",
        provider_lora_name=f"lora_{actor_id}",
        provider_training_task_id=f"task-{actor_id}",
        training_status="completed",
        training_phase="ready",
        training_progress_percent=100,
        training_error=None,
        training_images=[f"https://cdn.example.com/{actor_id}/{idx}.png" for idx in range(8)],
        consent_source="operator",
        created_at="2026-05-21T00:00:00Z",
        updated_at="2026-05-21T00:00:00Z",
    )


def test_batch_scene_reference_uses_batch_actor_after_active_switch(monkeypatch):
    from app.features.characters import handlers as character_handlers

    batch_actor = _ready_actor("batch-actor", is_active=False)
    active_actor = _ready_actor("active-actor", is_active=True)
    monkeypatch.setattr(
        character_handlers.character_queries,
        "get_actor_identity_by_id",
        lambda actor_identity_id: batch_actor,
    )
    monkeypatch.setattr(
        character_handlers.character_queries,
        "get_active_actor_identity",
        lambda: active_actor,
    )

    result = character_handlers._ready_actor_identity_for_batch({"actor_identity_id": "batch-actor"})

    assert result.id == "batch-actor"


def test_regenerated_scene_reference_uses_original_reference_actor(monkeypatch):
    from app.features.characters import handlers as character_handlers

    reference_actor = _ready_actor("reference-actor", is_active=False)
    active_actor = _ready_actor("active-actor", is_active=True)
    monkeypatch.setattr(
        character_handlers.character_queries,
        "get_actor_identity_by_id",
        lambda actor_identity_id: reference_actor,
    )
    monkeypatch.setattr(
        character_handlers.character_queries,
        "get_active_actor_identity",
        lambda: active_actor,
    )

    result = character_handlers._ready_actor_identity_for_reference({"actor_identity_id": "reference-actor"})

    assert result.id == "reference-actor"


def test_script_intent_maps_only_to_catalog_values():
    result = map_script_to_scene_intent(
        script="Im Badezimmer zeigt sie, wie kleine Anpassungen am Morgen Sicherheit geben.",
        post_type="value",
        target_length_tier=8,
        seed_data={},
    )
    assert result.scene_key in SCENE_CATALOG
    assert result.wardrobe_key in WARDROBE_SET
    assert result.reason_code == "bathroom_terms"


def test_ambiguous_script_uses_conservative_default():
    result = map_script_to_scene_intent(
        script="Ein kurzer Tipp fuer heute.",
        post_type="value",
        target_length_tier=8,
        seed_data={},
    )
    assert result.scene_key == "neutral_home"
    assert result.wardrobe_key == "everyday_sweater"


def test_scene_reference_prompt_does_not_include_freeform_script_text():
    prompt = build_scene_reference_prompt(
        actor_name="AYRA",
        scene_key="bathroom_adaptation",
        wardrobe_key="everyday_sweater",
        post_type="value",
    )
    assert "Badezimmer" not in prompt
    assert "bright accessible bathroom" in prompt


def test_scene_reference_prompt_can_include_provider_lora_handle():
    prompt = build_scene_reference_prompt(
        actor_name="AYRA",
        scene_key="bathroom_adaptation",
        wardrobe_key="everyday_sweater",
        post_type="value",
        provider_lora_name="ayra_actor_codex",
    )
    assert "@ayra_actor_codex::100" in prompt


def test_actor_identity_video_gate_defaults_to_manual_required():
    from app.features.characters.actor_identity import build_video_identity_gate_result

    result = build_video_identity_gate_result(video_url="https://cdn.example.com/video.mp4", automated_available=False)
    assert result.status == "manual_required"
    assert result.gate_type == "manual"
    assert "manual review" in result.reason.lower()


def test_mystic_generated_response_extracts_scene_reference_url():
    from app.features.characters.handlers import _extract_mystic_image_url

    assert (
        _extract_mystic_image_url({"generated": ["https://cdn.example.com/still.png"]})
        == "https://cdn.example.com/still.png"
    )


def test_required_scene_reference_angles_are_stable():
    from app.features.characters.scene_reference import REQUIRED_SCENE_REFERENCE_ANGLES

    assert [angle.key for angle in REQUIRED_SCENE_REFERENCE_ANGLES] == [
        "front_mid",
        "left_three_quarter",
        "right_profile",
    ]
    assert len({angle.seed_offset for angle in REQUIRED_SCENE_REFERENCE_ANGLES}) == 3


def test_scene_reference_set_summary_requires_three_approved_images():
    from app.features.characters.schemas import SceneReferenceSetSummary

    summary = SceneReferenceSetSummary.from_rows(
        post_id="post-1",
        reference_set_id="set-1",
        rows=[
            {
                "id": "ref-1",
                "status": "approved",
                "image_url": "https://cdn.example.com/front.png",
                "provider_metadata": {"angle_key": "front_mid"},
                "identity_gate_result": {"status": "passed", "reason": "ok", "gate_type": "manual"},
            },
            {
                "id": "ref-2",
                "status": "approved",
                "image_url": "https://cdn.example.com/left.png",
                "provider_metadata": {"angle_key": "left_three_quarter"},
                "identity_gate_result": {"status": "passed", "reason": "ok", "gate_type": "manual"},
            },
        ],
    )

    assert summary.is_ready is False
    assert summary.missing_angle_keys == ["right_profile"]


def test_select_latest_reference_set_id_uses_newest_complete_set():
    from app.features.characters.queries import select_latest_reference_set_id

    rows = [
        {"created_at": "2026-05-21T10:00:00Z", "provider_metadata": {"reference_set_id": "old", "angle_key": "front_mid"}},
        {
            "created_at": "2026-05-21T10:00:01Z",
            "provider_metadata": {"reference_set_id": "old", "angle_key": "left_three_quarter"},
        },
        {"created_at": "2026-05-21T10:00:02Z", "provider_metadata": {"reference_set_id": "new", "angle_key": "front_mid"}},
        {
            "created_at": "2026-05-21T10:00:03Z",
            "provider_metadata": {"reference_set_id": "new", "angle_key": "left_three_quarter"},
        },
        {"created_at": "2026-05-21T10:00:04Z", "provider_metadata": {"reference_set_id": "new", "angle_key": "right_profile"}},
    ]

    assert select_latest_reference_set_id(rows) == "new"


def test_filter_reference_rows_for_set_keeps_requested_set_only():
    from app.features.characters.queries import filter_reference_rows_for_set

    rows = [
        {"id": "1", "provider_metadata": {"reference_set_id": "set-a"}},
        {"id": "2", "provider_metadata": {"reference_set_id": "set-b"}},
    ]

    assert [row["id"] for row in filter_reference_rows_for_set(rows, "set-b")] == ["2"]


def test_angle_specific_prompts_keep_same_background_and_distinct_angles():
    from app.features.characters.scene_reference import (
        REQUIRED_SCENE_REFERENCE_ANGLES,
        build_scene_reference_prompt_for_angle,
    )

    prompts = [
        build_scene_reference_prompt_for_angle(
            actor_name="AYRA",
            scene_key="bathroom_adaptation",
            wardrobe_key="everyday_sweater",
            post_type="value",
            angle_key=angle.key,
            provider_lora_name="ayra_actor",
        )
        for angle in REQUIRED_SCENE_REFERENCE_ANGLES
    ]

    assert all("same background" in prompt.lower() for prompt in prompts)
    assert any("front-facing" in prompt for prompt in prompts)
    assert any("left three-quarter" in prompt for prompt in prompts)
    assert any("right-side profile" in prompt for prompt in prompts)
    assert all("@ayra_actor::100" in prompt for prompt in prompts)


def test_reference_candidates_keep_latest_set_group_together():
    from app.features.characters.queries import filter_reference_rows_for_set, select_latest_reference_set_id

    rows = [
        {
            "id": "old-front",
            "created_at": "2026-05-21T10:00:00Z",
            "provider_metadata": {"reference_set_id": "old", "angle_key": "front_mid"},
        },
        {
            "id": "new-front",
            "created_at": "2026-05-21T10:10:00Z",
            "provider_metadata": {"reference_set_id": "new", "angle_key": "front_mid"},
        },
        {
            "id": "new-left",
            "created_at": "2026-05-21T10:10:01Z",
            "provider_metadata": {"reference_set_id": "new", "angle_key": "left_three_quarter"},
        },
        {
            "id": "new-profile",
            "created_at": "2026-05-21T10:10:02Z",
            "provider_metadata": {"reference_set_id": "new", "angle_key": "right_profile"},
        },
    ]

    latest = select_latest_reference_set_id(rows)

    assert latest == "new"
    assert [row["id"] for row in filter_reference_rows_for_set(rows, latest)] == [
        "new-front",
        "new-left",
        "new-profile",
    ]
