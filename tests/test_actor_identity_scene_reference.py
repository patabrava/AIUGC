from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.features.characters.scene_reference import (
    NEUTRAL_SCENE_POOL,
    SCENE_CATALOG,
    WARDROBE_SET,
    build_scene_reference_prompt,
    build_scene_reference_prompt_for_angle,
    get_scene_bible,
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


def _set_gate(reference_set_id: str = "set-1") -> dict:
    return {
        "status": "passed",
        "reason": "ok",
        "gate_type": "manual",
        "details": {
            "scene_consistency_set_approved": True,
            "actor_identity_match_confirmed": True,
            "reference_set_id": reference_set_id,
        },
    }


def _lora_metadata(angle_key: str = "front_mid", reference_set_id: str = "set-1") -> dict:
    return {
        "reference_set_id": reference_set_id,
        "angle_key": angle_key,
        "identity_lock_contract": {
            "provider": "magnific",
            "provider_lora_id": "1786946",
            "provider_lora_name": "ayra-actor-longchar-20260521",
            "actor_identity_id": "actor-1",
            "identity_strength": 100,
            "prompt_lora_handle_required": True,
            "styling_characters_required": True,
        },
        "mystic_request": {
            "prompt": "Photorealistic still of @ayra-actor-longchar-20260521::100 in a stable scene.",
            "styling": {"characters": [{"id": "1786946", "strength": 100}]},
        },
    }


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


def test_scene_reference_requires_provider_lora_name(monkeypatch):
    from app.features.characters import handlers as character_handlers

    actor = _ready_actor("actor-without-name", is_active=True).model_copy(update={"provider_lora_name": None})
    monkeypatch.setattr(
        character_handlers.character_queries,
        "get_actor_identity_by_id",
        lambda actor_identity_id: actor,
    )

    with pytest.raises(character_handlers.HTTPException) as exc_info:
        character_handlers._ready_actor_identity_for_batch({"actor_identity_id": "actor-without-name"})

    assert exc_info.value.status_code == 422
    assert "provider LoRA name" in exc_info.value.detail


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


def test_ambiguous_script_rotates_within_neutral_pool():
    result = map_script_to_scene_intent(
        script="Ein kurzer Tipp fuer heute.",
        post_type="value",
        target_length_tier=8,
        seed_data={},
    )
    # Content with no scene keyword lands on a neutral talking-head plate (deterministically).
    assert result.scene_key in NEUTRAL_SCENE_POOL
    assert result.reason_code == "neutral_rotation"
    assert result.wardrobe_key == "everyday_sweater"


def test_scene_reference_prompt_does_not_include_freeform_script_text():
    prompt = build_scene_reference_prompt(
        actor_name="AYRA",
        scene_key="bathroom_adaptation",
        wardrobe_key="everyday_sweater",
        post_type="value",
    )
    assert "Badezimmer" not in prompt
    assert "the same compact accessible bathroom" in prompt
    assert "Accessible bathroom scene A" not in prompt
    assert "texture-only" not in prompt


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
                "identity_gate_result": _set_gate(),
            },
            {
                "id": "ref-2",
                "status": "approved",
                "image_url": "https://cdn.example.com/left.png",
                "provider_metadata": {"angle_key": "left_three_quarter"},
                "identity_gate_result": _set_gate(),
            },
        ],
    )

    assert summary.is_ready is False
    assert summary.missing_angle_keys == ["right_profile"]


def test_scene_reference_set_summary_marks_two_actor_refs_video_ready_before_full_review_ready():
    from app.features.characters.schemas import SceneReferenceSetSummary

    rows = [
        {
            "id": "ref-front",
            "status": "approved",
            "image_url": "https://cdn.example.com/front.png",
            "provider_metadata": _lora_metadata("front_mid", "set-1"),
            "identity_gate_result": _set_gate(),
        },
        {
            "id": "ref-left",
            "status": "approved",
            "image_url": "https://cdn.example.com/left.png",
            "provider_metadata": _lora_metadata("left_three_quarter", "set-1"),
            "identity_gate_result": _set_gate(),
        },
    ]

    summary = SceneReferenceSetSummary.from_rows(post_id="post-1", reference_set_id="set-1", rows=rows)

    assert summary.is_ready is False
    assert summary.is_video_actor_ready is True
    assert [row["id"] for row in summary.video_actor_rows] == ["ref-front", "ref-left"]
    assert summary.missing_video_actor_angle_keys == []


def test_scene_reference_set_summary_marks_full_review_set_and_two_video_refs_ready():
    from app.features.characters.schemas import SceneReferenceSetSummary

    rows = [
        {
            "id": f"ref-{angle}",
            "status": "approved",
            "image_url": f"https://cdn.example.com/{angle}.png",
            "provider_metadata": _lora_metadata(angle, "set-1"),
            "identity_gate_result": _set_gate(),
        }
        for angle in ("front_mid", "left_three_quarter", "right_profile")
    ]

    summary = SceneReferenceSetSummary.from_rows(post_id="post-1", reference_set_id="set-1", rows=rows)

    assert summary.is_ready is True
    assert summary.is_video_actor_ready is True
    assert [row["id"] for row in summary.video_actor_rows] == [
        "ref-front_mid",
        "ref-left_three_quarter",
    ]
    assert summary.missing_video_actor_angle_keys == []


def test_scene_reference_set_summary_requires_full_set_gate_details():
    from app.features.characters.schemas import SceneReferenceSetSummary

    rows = [
        {
            "id": "ref-1",
            "status": "approved",
            "image_url": "https://cdn.example.com/front.png",
            "provider_metadata": {"angle_key": "front_mid"},
            "identity_gate_result": {"status": "passed", "reason": "single image approval", "gate_type": "manual"},
        },
        {
            "id": "ref-2",
            "status": "approved",
            "image_url": "https://cdn.example.com/left.png",
            "provider_metadata": {"angle_key": "left_three_quarter"},
            "identity_gate_result": {"status": "passed", "reason": "single image approval", "gate_type": "manual"},
        },
        {
            "id": "ref-3",
            "status": "approved",
            "image_url": "https://cdn.example.com/profile.png",
            "provider_metadata": {"angle_key": "right_profile"},
            "identity_gate_result": {"status": "passed", "reason": "single image approval", "gate_type": "manual"},
        },
    ]

    summary = SceneReferenceSetSummary.from_rows(post_id="post-1", reference_set_id="set-1", rows=rows)

    assert summary.is_ready is False
    assert summary.missing_angle_keys == ["front_mid", "left_three_quarter", "right_profile"]


def test_video_scene_reference_set_gate_requires_actor_identity_confirmation():
    from app.core.errors import FlowForgeException
    from app.features.characters.actor_identity import ensure_video_scene_reference_set_ready
    from app.features.characters.schemas import SceneReferenceSetSummary

    rows = [
        {
            "id": "front",
            "actor_identity_id": "actor-1",
            "status": "approved",
            "image_url": "https://cdn.example.com/front.png",
            "scene_key": "home_living_room_advice_a",
            "wardrobe_key": "everyday_sweater",
            "provider_metadata": _lora_metadata("front_mid", "set-1"),
            "identity_gate_result": {
                "status": "passed",
                "reason": "scene only",
                "gate_type": "manual",
                "details": {"scene_consistency_set_approved": True, "reference_set_id": "set-1"},
            },
        },
        {
            "id": "left",
            "actor_identity_id": "actor-1",
            "status": "approved",
            "image_url": "https://cdn.example.com/left.png",
            "scene_key": "home_living_room_advice_a",
            "wardrobe_key": "everyday_sweater",
            "provider_metadata": _lora_metadata("left_three_quarter", "set-1"),
            "identity_gate_result": {
                "status": "passed",
                "reason": "scene only",
                "gate_type": "manual",
                "details": {"scene_consistency_set_approved": True, "reference_set_id": "set-1"},
            },
        },
        {
            "id": "profile",
            "actor_identity_id": "actor-1",
            "status": "approved",
            "image_url": "https://cdn.example.com/profile.png",
            "scene_key": "home_living_room_advice_a",
            "wardrobe_key": "everyday_sweater",
            "provider_metadata": _lora_metadata("right_profile", "set-1"),
            "identity_gate_result": {
                "status": "passed",
                "reason": "scene only",
                "gate_type": "manual",
                "details": {"scene_consistency_set_approved": True, "reference_set_id": "set-1"},
            },
        },
    ]
    summary = SceneReferenceSetSummary.from_rows(post_id="post-1", reference_set_id="set-1", rows=rows)

    with pytest.raises(FlowForgeException) as exc:
        ensure_video_scene_reference_set_ready(
            batch={"id": "batch-1", "creation_mode": "character_consistency_mid", "actor_identity_id": "actor-1"},
            post={"id": "post-1", "batch_id": "batch-1"},
            scene_reference_set=summary,
            route="short",
        )

    assert "actor identity match" in exc.value.message


def test_video_scene_reference_set_gate_accepts_manual_matched_refs_without_lora_metadata():
    from app.features.characters.actor_identity import ensure_video_scene_reference_set_ready
    from app.features.characters.schemas import SceneReferenceSetSummary

    rows = []
    for angle_key in ("front_mid", "left_three_quarter", "right_profile"):
        rows.append(
            {
                "id": f"ref-{angle_key}",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": f"https://cdn.example.com/{angle_key}.png",
                "scene_key": "home_living_room_advice_a",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {"reference_set_id": "set-1", "angle_key": angle_key},
                "identity_gate_result": _set_gate(),
            }
        )
    summary = SceneReferenceSetSummary.from_rows(post_id="post-1", reference_set_id="set-1", rows=rows)

    result = ensure_video_scene_reference_set_ready(
        batch={"id": "batch-1", "creation_mode": "character_consistency_mid", "actor_identity_id": "actor-1"},
        post={"id": "post-1", "batch_id": "batch-1"},
        scene_reference_set=summary,
        route="short",
    )

    assert result["source"] == "actor_identity_scene_reference_set"
    assert result["scene_reference_set"].is_video_actor_ready is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("structure_reference", "unsafe-reference"),
        ("structure_reference", None),
        ("structure_reference", ""),
        ("style_reference", "unsafe-reference"),
        ("style_reference", None),
        ("style_reference", ""),
        ("model", "fluid"),
        ("model", None),
    ],
)
def test_video_scene_reference_set_gate_accepts_manual_matched_refs_with_legacy_mystic_metadata(field, value):
    from app.features.characters.actor_identity import ensure_video_scene_reference_set_ready
    from app.features.characters.schemas import SceneReferenceSetSummary

    rows = []
    for angle_key in ("front_mid", "left_three_quarter", "right_profile"):
        metadata = _lora_metadata(angle_key, "set-1")
        metadata["mystic_request"][field] = value
        rows.append(
            {
                "id": f"ref-{angle_key}",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": f"https://cdn.example.com/{angle_key}.png",
                "scene_key": "home_living_room_advice_a",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": metadata,
                "identity_gate_result": _set_gate(),
            }
        )
    summary = SceneReferenceSetSummary.from_rows(post_id="post-1", reference_set_id="set-1", rows=rows)

    result = ensure_video_scene_reference_set_ready(
        batch={"id": "batch-1", "creation_mode": "character_consistency_mid", "actor_identity_id": "actor-1"},
        post={"id": "post-1", "batch_id": "batch-1"},
        scene_reference_set=summary,
        route="short",
    )

    assert result["compatible"] is True


def test_video_scene_reference_set_gate_blocks_extended_lora_route():
    from app.core.errors import FlowForgeException
    from app.features.characters.actor_identity import ensure_video_scene_reference_set_ready
    from app.features.characters.schemas import SceneReferenceSetSummary

    rows = []
    for angle_key in ("front_mid", "left_three_quarter", "right_profile"):
        rows.append(
            {
                "id": f"ref-{angle_key}",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": f"https://cdn.example.com/{angle_key}.png",
                "scene_key": "home_living_room_advice_a",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": _lora_metadata(angle_key, "set-1"),
                "identity_gate_result": _set_gate(),
            }
        )
    summary = SceneReferenceSetSummary.from_rows(post_id="post-1", reference_set_id="set-1", rows=rows)

    with pytest.raises(FlowForgeException) as exc:
        ensure_video_scene_reference_set_ready(
            batch={"id": "batch-1", "creation_mode": "character_consistency_mid", "actor_identity_id": "actor-1"},
            post={"id": "post-1", "batch_id": "batch-1"},
            scene_reference_set=summary,
            route="extended",
        )

    assert "8-second VEO base request" in exc.value.message


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


def test_filter_reference_rows_for_set_keeps_newest_row_per_angle():
    from app.features.characters.queries import filter_reference_rows_for_set

    rows = [
        {
            "id": "old-front",
            "created_at": "2026-05-21T10:00:00Z",
            "provider_metadata": {"reference_set_id": "set-1", "angle_key": "front_mid"},
        },
        {
            "id": "new-front",
            "created_at": "2026-05-21T10:10:00Z",
            "provider_metadata": {"reference_set_id": "set-1", "angle_key": "front_mid"},
        },
        {
            "id": "left",
            "created_at": "2026-05-21T10:00:01Z",
            "provider_metadata": {"reference_set_id": "set-1", "angle_key": "left_three_quarter"},
        },
        {
            "id": "profile",
            "created_at": "2026-05-21T10:00:02Z",
            "provider_metadata": {"reference_set_id": "set-1", "angle_key": "right_profile"},
        },
    ]

    assert [row["id"] for row in filter_reference_rows_for_set(rows, "set-1")] == [
        "new-front",
        "left",
        "profile",
    ]


def test_record_scene_reference_set_gate_updates_only_requested_set(monkeypatch):
    from app.features.characters import queries as character_queries
    from app.features.characters.actor_identity import passed_manual_gate

    rows = [
        {"id": "old-front", "post_id": "post-1", "provider_metadata": {"reference_set_id": "old", "angle_key": "front_mid"}},
        {"id": "new-front", "post_id": "post-1", "provider_metadata": {"reference_set_id": "new", "angle_key": "front_mid"}},
        {"id": "new-left", "post_id": "post-1", "provider_metadata": {"reference_set_id": "new", "angle_key": "left_three_quarter"}},
        {"id": "new-profile", "post_id": "post-1", "provider_metadata": {"reference_set_id": "new", "angle_key": "right_profile"}},
    ]
    recorded = []

    monkeypatch.setattr(character_queries, "list_scene_references_for_post", lambda post_id: rows)
    monkeypatch.setattr(
        character_queries,
        "record_scene_reference_gate",
        lambda **kwargs: recorded.append(kwargs),
    )

    updated = character_queries.record_scene_reference_set_gate(
        post_id="post-1",
        reference_set_id="new",
        gate_result=passed_manual_gate("Operator approved actor identity and scene consistency for the full set"),
        status="approved",
        correlation_id="corr-1",
    )

    assert [row["id"] for row in updated] == ["new-front", "new-left", "new-profile"]
    assert [item["reference_id"] for item in recorded] == ["new-front", "new-left", "new-profile"]
    assert all(item["status"] == "approved" for item in recorded)


def test_angle_specific_prompts_keep_same_background_and_matched_framing():
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
    assert all("waist-up seated smartphone reference" in prompt for prompt in prompts)
    assert all("lap, hands, scene anchors, and wheelchair armrest visible" in prompt for prompt in prompts)
    assert all("face occupying 10 to 16 percent of image height" in prompt for prompt in prompts)
    assert all("headshot" in prompt.lower() for prompt in prompts)
    assert all("business portrait" in prompt.lower() for prompt in prompts)
    assert all("medium close-up" not in prompt.lower() for prompt in prompts)
    assert any("front-facing" in prompt for prompt in prompts)
    assert any("slight left three-quarter" in prompt for prompt in prompts)
    assert any("slight right three-quarter" in prompt for prompt in prompts)
    assert all("right-side profile" not in prompt for prompt in prompts)
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


def test_scene_bible_prompt_reuses_exact_scene_identity_across_angles():
    from app.features.characters.scene_reference import REQUIRED_SCENE_REFERENCE_ANGLES

    bible = get_scene_bible("bathroom_accessibility_a")
    prompts = [
        build_scene_reference_prompt_for_angle(
            actor_name="AYRA",
            scene_key=bible.scene_id,
            wardrobe_key="everyday_sweater",
            post_type="value",
            angle_key=angle.key,
            provider_lora_name="ayra_actor",
        )
        for angle in REQUIRED_SCENE_REFERENCE_ANGLES
    ]

    assert len(set(prompts)) == 3
    for prompt in prompts:
        assert bible.scene_identity not in prompt
        assert bible.generation_anchor in prompt
        assert "cream crewneck sweater, neutral beige trousers, no logos, no jewelry, no glasses, natural makeup" in prompt
        assert "Background Anchor:" not in prompt
        assert "Scene Consistency:" not in prompt
        assert "pale grey microcement floor" not in prompt
        assert "texture-only" not in prompt
        assert "room surfaces" not in prompt

    assert any("front-facing" in prompt for prompt in prompts)
    assert any("slight left three-quarter" in prompt for prompt in prompts)
    assert any("slight right three-quarter" in prompt for prompt in prompts)
    assert all("right-side profile" not in prompt for prompt in prompts)


def test_scene_bible_prompt_prioritizes_scene_composition_before_actor_handle():
    prompt = build_scene_reference_prompt_for_angle(
        actor_name="AYRA",
        scene_key="bathroom_accessibility_a",
        wardrobe_key="everyday_sweater",
        post_type="value",
        angle_key="front_mid",
        provider_lora_name="ayra_actor",
    )

    assert prompt.startswith("Photorealistic vertical UGC smartphone still, waist-up seated wheelchair-user reference")
    assert prompt.index("the same compact accessible bathroom") < prompt.index("@ayra_actor::100")
    assert "Actor Identity:" not in prompt
    assert "Primary Subject:" not in prompt
    assert "@ayra_actor::100 is the dominant identity signal" in prompt
    assert "only visible adult person in the frame" in prompt
    assert "waist-up seated smartphone reference" in prompt
    assert "face occupying 10 to 16 percent of image height" in prompt
    assert "Background is the same supporting scene" in prompt
    assert "lap, hands, scene anchors, and wheelchair armrest visible" in prompt
    assert "wide establishing shot" in prompt
    assert "headshot" in prompt.lower()
    assert "white blouse" in prompt.lower()
    assert "suit jacket" in prompt.lower()
    assert "natural skin texture" in prompt
    assert "texture-only frame" not in prompt
    assert "Scene Bible:" not in prompt


def test_scene_reference_prompts_share_same_location_lock_without_labels():
    from app.features.characters.scene_reference import REQUIRED_SCENE_REFERENCE_ANGLES

    prompts = [
        build_scene_reference_prompt_for_angle(
            actor_name="AYRA",
            scene_key="car_transfer_residential_a",
            wardrobe_key="everyday_sweater",
            post_type="value",
            angle_key=angle.key,
            provider_lora_name="ayra_actor",
        )
        for angle in REQUIRED_SCENE_REFERENCE_ANGLES
    ]

    shared_sentence = (
        "Keep the same supporting location details across every angle: same silver hatchback, open passenger door, "
        "quiet curb, low brick garden wall, muted hedge, overcast daylight."
    )
    assert all(shared_sentence in prompt for prompt in prompts)
    assert all("@ayra_actor::100" in prompt for prompt in prompts)
    assert all(prompt.index("quiet residential curb") < prompt.index("@ayra_actor::100") for prompt in prompts)
    assert all("Scene Consistency:" not in prompt for prompt in prompts)
    assert all("Background Anchor:" not in prompt for prompt in prompts)
    assert all("Scene Bible:" not in prompt for prompt in prompts)
    assert all("texture-only" not in prompt for prompt in prompts)
    assert all("room surfaces" not in prompt for prompt in prompts)


def test_all_canonical_scenes_build_valid_person_free_prompts():
    # Guards future scene additions: every SceneBible must produce a person-free image plate
    # prompt and a complete provider metadata / consistency contract.
    from app.features.characters.scene_reference import (
        SCENE_BIBLES,
        build_scene_bible_provider_metadata,
        build_scene_consistency_contract,
    )
    from app.features.scenes.background_comparison import build_raw_camera_background_brief

    assert len(SCENE_BIBLES) >= 10
    for scene_id, bible in SCENE_BIBLES.items():
        prompt = build_raw_camera_background_brief(scene_id)
        assert "environment-only" in prompt, scene_id
        assert "No people, faces, bodies, body parts, hands, or wheelchairs" in prompt, scene_id
        assert bible.generation_anchor[:25] in prompt, scene_id

        metadata = build_scene_bible_provider_metadata(scene_id)
        assert metadata["scene_bible_id"] == scene_id
        contract = build_scene_consistency_contract(scene_id)
        for key in ("layout_lock", "anchor_lock", "wardrobe_lock", "must_match", "acceptance_checklist"):
            assert contract.get(key), f"{scene_id}: empty {key}"


def test_script_intent_routes_specialized_and_neutral_scenes():
    specialized = [
        ("Im Badezimmer gibt ein Haltegriff mehr Sicherheit.", "value", "bathroom_accessibility_a"),
        ("Ein Treppenlift macht das Treppensteigen wieder sicher.", "value", "hallway_stairlift_a"),
        ("Beim Transfer ins Auto hilft eine klare Routine.", "value", "car_transfer_residential_a"),
        ("Im Schlafzimmer erleichtert ein Pflegebett das Aufstehen.", "value", "bedroom_accessibility_a"),
        ("Eine Rampe am Hauseingang beseitigt die Türschwelle.", "value", "entryway_ramp_a"),
        ("Frische Luft im Garten tut gut.", "value", "garden_patio_a"),
    ]
    for script, post_type, expected_scene_id in specialized:
        result = map_script_to_scene_intent(
            script=script,
            post_type=post_type,
            target_length_tier=8,
            seed_data={},
        )
        assert result.scene_key == expected_scene_id
        assert result.scene_key in SCENE_CATALOG
        assert result.wardrobe_key == "everyday_sweater"

    # Generic advice content carries no scene keyword -> deterministic neutral-pool rotation.
    for script in ("Ein ruhiger Alltagstipp fuer heute.", "Dieses Hilfsmittel liegt auf dem Tisch."):
        result = map_script_to_scene_intent(
            script=script,
            post_type="value",
            target_length_tier=8,
            seed_data={},
        )
        assert result.scene_key in NEUTRAL_SCENE_POOL
        assert result.reason_code == "neutral_rotation"

    # Empty content falls back to the stable home default.
    empty = map_script_to_scene_intent(script="", post_type="value", target_length_tier=8, seed_data={})
    assert empty.scene_key == "home_living_room_advice_a"
    assert empty.reason_code == "default"


def test_script_intent_dodges_german_false_friends():
    # Realistic German care/welfare vocabulary that must NOT be forced onto a specialized
    # scene by an over-broad substring token (verified against a red-team corpus).
    neutral_traps = [
        "Autonomie und Selbstbestimmung in der Pflege",   # 'auto' in 'autonomie'
        "Mobilitaetshilfe beantragen welche Zuschuesse",  # 'mobilität' funding topic
        "Die fuenf Pflegestufen im Ueberblick",            # 'stufen' in 'Pflegestufen'
        "Kururlaub in Bad Kissingen",                      # 'bad' spa-town name
        "Reisekosten zur Reha steuerlich absetzen",        # 'reise' travel topic
        "Kindergarten-Aktion fuer Senioren",               # 'garten' in 'Kindergarten'
        "Wintergarten als barrierefreier Wohnraum",        # 'garten' in 'Wintergarten' (indoor)
        "Morgens leichter aufstehen mit Gelenkschmerzen",  # generic 'aufstehen' advice
    ]
    for topic in neutral_traps:
        result = map_script_to_scene_intent(
            script="", post_type="value", target_length_tier=8, seed_data={"topic_title": topic}
        )
        assert result.scene_key in NEUTRAL_SCENE_POOL, f"{topic!r} -> {result.scene_key}"

    # Real product / Hilfsmittel vocabulary that must route to its specialized scene even
    # when only the topic names it (empty script).
    specialized_vocab = [
        ("Plattformlift fuer den Rollstuhl", "hallway_stairlift_a"),
        ("Duschhocker und Duschsitz im Test", "bathroom_accessibility_a"),
        ("Krankenbett fuer zu Hause mieten", "bedroom_accessibility_a"),
        ("Tuerverbreiterung fuer den Rollstuhl", "entryway_ramp_a"),
        ("Beifahrersitz drehbar machen fuer leichteren Einstieg", "car_transfer_residential_a"),
        ("Treppenrampe vor der Haustuer", "entryway_ramp_a"),  # ramp at the door beats stairs
    ]
    for topic, expected in specialized_vocab:
        result = map_script_to_scene_intent(
            script="", post_type="value", target_length_tier=8, seed_data={"topic_title": topic}
        )
        assert result.scene_key == expected, f"{topic!r} -> {result.scene_key}"


def test_resolve_canonical_scene_key_per_post_intent_wins_over_scene_plan_prose():
    from app.features.scenes import queries as scene_queries

    # The home fallback scene_plan sets every post's scene_text to the verbatim home
    # identity string, which previously short-circuited resolution to home for all posts.
    home_scene_text = f"Scene: {get_scene_bible('home_living_room_advice_a').scene_identity}"

    bathroom_key = scene_queries.resolve_canonical_scene_key(
        scene_text=home_scene_text,
        post_type="value",
        seed_data={"script": "Im Badezimmer gibt ein Haltegriff mehr Sicherheit."},
    )
    car_key = scene_queries.resolve_canonical_scene_key(
        scene_text=home_scene_text,
        post_type="lifestyle",
        seed_data={"script": "Beim Transfer ins Auto hilft eine klare Routine."},
    )

    # Per-video topic wins over the shared home scene_plan prose.
    assert bathroom_key == "bathroom_accessibility_a"
    assert car_key == "car_transfer_residential_a"
    # Two posts sharing a post_type but different scenes no longer collapse together.
    assert bathroom_key != car_key


def test_resolve_canonical_scene_key_generic_and_explicit_fallbacks():
    from app.features.scenes import queries as scene_queries

    home_scene_text = f"Scene: {get_scene_bible('home_living_room_advice_a').scene_identity}"

    # Generic topic with no scene keyword rotates across the neutral pool (no longer pinned
    # to home by the shared scene_plan prose).
    assert (
        scene_queries.resolve_canonical_scene_key(
            scene_text=home_scene_text,
            post_type="value",
            seed_data={"script": "Ein ruhiger Alltagstipp fuer heute."},
        )
        in NEUTRAL_SCENE_POOL
    )

    # Secondary callers pass seed_data=None with no scene_text; this must stay safe.
    assert (
        scene_queries.resolve_canonical_scene_key(scene_text=None, seed_data=None)
        == "home_living_room_advice_a"
    )

    # An explicit, valid scene key/alias in scene_text stays authoritative.
    assert (
        scene_queries.resolve_canonical_scene_key(scene_text="car_transfer_residential_a", seed_data=None)
        == "car_transfer_residential_a"
    )
    assert (
        scene_queries.resolve_canonical_scene_key(scene_text="bathroom_adaptation", seed_data=None)
        == "bathroom_accessibility_a"
    )


def test_resolve_canonical_scene_key_can_prefer_scene_plan_prose_for_actor_fallback():
    from app.features.scenes import queries as scene_queries

    living_room_scene_text = f"Scene: {get_scene_bible('home_living_room_advice_a').scene_identity}"

    assert (
        scene_queries.resolve_canonical_scene_key(
            scene_text=living_room_scene_text,
            post_type="product",
            seed_data={
                "script": "Der Vario Plus wird ruhig am Tisch erklaert.",
                "topic_title": "VARIO PLUS Gebrauch und Bedienung",
            },
            prefer_scene_text=True,
        )
        == "home_living_room_advice_a"
    )


def test_neutral_pool_rotation_is_deterministic_and_varied():
    from app.features.scenes import queries as scene_queries

    # Even when the shared scene_plan prose is the home identity, abstract advice topics
    # (no scene keyword) spread across the neutral pool deterministically.
    home_scene_text = f"Scene: {get_scene_bible('home_living_room_advice_a').scene_identity}"
    topics = [
        "Assistenzleistungen vs Pflegeleistungen",
        "Pflegegrad beantragen Tipps",
        "Kostentraeger und Zuschuesse erklaert",
        "Hausnotruf sinnvoll einrichten",
    ]
    resolved = {}
    for topic in topics:
        first = scene_queries.resolve_canonical_scene_key(
            scene_text=home_scene_text, post_type="value", seed_data={"topic_title": topic, "script": "x"}
        )
        second = scene_queries.resolve_canonical_scene_key(
            scene_text=home_scene_text, post_type="value", seed_data={"topic_title": topic, "script": "x"}
        )
        assert first == second  # deterministic for the same input
        assert first in NEUTRAL_SCENE_POOL
        resolved[topic] = first

    # The pool is genuinely exercised: these topics do not all collapse onto one scene.
    assert len(set(resolved.values())) >= 2


def test_scene_reference_metadata_includes_scene_bible_contract():
    from app.features.characters.scene_reference import build_scene_bible_provider_metadata, build_scene_consistency_contract

    assert build_scene_bible_provider_metadata("car_transfer_residential_a") == {
        "scene_bible_id": "car_transfer_residential_a",
        "scene_bible_version": 1,
        "scene_bible_name": "Residential car transfer A",
        "scene_bible_identity": get_scene_bible("car_transfer_residential_a").scene_identity,
        "scene_generation_anchor": get_scene_bible("car_transfer_residential_a").generation_anchor,
        "scene_consistency_contract": build_scene_consistency_contract("car_transfer_residential_a"),
    }


def test_parse_scene_reference_style_loras_maps_scene_to_style():
    from app.features.characters.scene_reference import parse_scene_reference_style_loras

    result = parse_scene_reference_style_loras(
        "bathroom_accessibility_a=bathroom-accessibility-a:65,"
        "car_transfer_residential_a=car-transfer-residential-a:70,"
        "home_living_room_advice_a=home-living-room-advice-a"
    )

    assert result == {
        "bathroom_accessibility_a": [{"name": "bathroom-accessibility-a", "strength": 65}],
        "car_transfer_residential_a": [{"name": "car-transfer-residential-a", "strength": 70}],
        "home_living_room_advice_a": [{"name": "home-living-room-advice-a", "strength": 65}],
    }


def test_parse_scene_reference_style_loras_ignores_unknown_scene_keys():
    from app.features.characters.scene_reference import parse_scene_reference_style_loras

    result = parse_scene_reference_style_loras(
        "unknown_scene=unused-style:65,bathroom_accessibility_a=bathroom-style:80"
    )

    assert result == {"bathroom_accessibility_a": [{"name": "bathroom-style", "strength": 80}]}


def test_scene_consistency_contract_is_stable_for_supported_bibles():
    from app.features.characters.scene_reference import SCENE_BIBLES, build_scene_consistency_contract

    expected_contracts = {
        "bathroom_accessibility_a": {
            "layout_lock": "same compact accessible bathroom: grab rail behind actor left, white sink behind actor right, frosted window high rear-left, oak towel shelf with folded sage-green towel",
            "must_match": [
                "grab rail remains behind actor left",
                "white wall-mounted sink remains behind actor right",
                "frosted window remains high on the rear-left wall",
                "sage-green towel remains folded on a narrow oak shelf",
                "soft daylight stays consistent across all three angles",
            ],
        },
        "car_transfer_residential_a": {
            "layout_lock": "same quiet residential curb: silver compact hatchback beside actor, open passenger door, dark grey interior, low brick garden wall, muted green hedge",
            "must_match": [
                "silver hatchback remains next to the actor",
                "front passenger door remains open",
                "dark grey car interior remains visible",
                "low brick garden wall remains behind the car",
                "muted green hedge remains in the far background",
            ],
        },
        "home_living_room_advice_a": {
            "layout_lock": "same quiet living room: warm off-white wall, narrow light-oak side table on actor right, white mug, small green plant, beige curtain at far left",
            "must_match": [
                "light-oak side table remains on actor right",
                "single white mug remains on the side table",
                "small green plant remains in a terracotta pot",
                "beige curtain remains at far left",
                "warm off-white wall and soft window light remain consistent",
            ],
        },
    }

    for scene_id, expected in expected_contracts.items():
        contract = build_scene_consistency_contract(scene_id)
        assert contract["scene_bible_id"] == scene_id
        assert contract["scene_bible_version"] == SCENE_BIBLES[scene_id].version
        assert contract["layout_lock"] == expected["layout_lock"]
        assert contract["anchor_lock"]
        assert contract["wardrobe_lock"] == "cream crewneck sweater, neutral trousers, no logos, no jewelry"
        assert contract["wardrobe_drift_rejector"] == (
            "different pants color, changed sweater, logos, jewelry, glasses, hat, or changed hairstyle"
        )
        assert contract["drift_rejectors_by_scene"]
        assert contract["must_match"] == expected["must_match"]
        assert contract["drift_rejectors"] == [
            "different room or location",
            "moved anchor objects",
            "missing wheelchair context",
            "extra adult person",
            "changed wardrobe",
            "changed lighting family",
        ]
        assert len(contract["acceptance_checklist"]) == 5


def test_scene_reference_prompt_includes_scene_specific_rejectors_and_wardrobe_lock():
    prompt = build_scene_reference_prompt_for_angle(
        actor_name="AYRA Actor",
        scene_key="bathroom_accessibility_a",
        wardrobe_key="everyday_sweater",
        post_type="value",
        angle_key="front_mid",
        provider_lora_name="ayra-actor-longchar-20260521",
    )

    assert "same cream crewneck sweater and neutral trousers" in prompt
    assert (
        "Do not add tall cabinets, doors behind the actor, plants, ladder shelves, radiators, mirrors, shower curtains, or extra towels"
        in prompt
    )
    assert "same grab rail, sink, sage-green towel shelf, frosted window" in prompt


def test_generate_scene_reference_uses_lora_safe_mystic_options_and_metadata(monkeypatch):
    from app.core.config import Settings
    from app.features.characters import handlers as character_handlers

    actor = _ready_actor("actor-1", is_active=True)
    captured_tasks = []
    captured_candidates = []

    class _Response:
        def __init__(self, data):
            self.data = data

    class _Table:
        def __init__(self, name):
            self.name = name

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def execute(self):
            if self.name == "posts":
                return _Response(
                    [
                        {
                            "id": "post-1",
                            "batch_id": "batch-1",
                            "post_type": "value",
                            "topic_title": "WC-Fiasko im Bad",
                            "seed_data": {
                                "script": "Ein klarer Alltagstipp, der nicht frei in die Szene kopiert wird.",
                                "target_length_tier": 8,
                            },
                        }
                    ]
                )
            if self.name == "batches":
                return _Response([{"id": "batch-1", "actor_identity_id": "actor-1", "target_length_tier": 8}])
            return _Response([])

    class _Supabase:
        def table(self, name):
            return _Table(name)

    class _SupabaseContainer:
        client = _Supabase()

    class _MysticClient:
        def create_mystic_scene_reference(self, **kwargs):
            captured_tasks.append(kwargs)
            return {
                "task_id": f"task-{len(captured_tasks)}",
                "generated": [f"https://cdn.example.com/{len(captured_tasks)}.png"],
                "_request_payload": {
                    "prompt": kwargs["prompt"],
                    "resolution": kwargs["resolution"],
                    "fixed_generation": kwargs["fixed_generation"],
                    "engine": kwargs["extra_options"]["engine"],
                    "creative_detailing": kwargs["extra_options"]["creative_detailing"],
                    "styling": {
                        "characters": [{"id": kwargs["lora_id"], "strength": kwargs["strength"]}],
                        "styles": kwargs["style_loras"],
                    },
                },
            }

    monkeypatch.setattr(character_handlers, "get_supabase", lambda: _SupabaseContainer())
    monkeypatch.setattr(character_handlers, "get_magnific_client", lambda: _MysticClient())
    monkeypatch.setattr(
        character_handlers,
        "get_settings",
        lambda: Settings(
            supabase_url="https://supabase.example.com",
            supabase_key="test-key",
            supabase_service_key="test-service-key",
            cloudflare_r2_public_base_url="https://cdn.example.com",
            scene_reference_style_loras="bathroom_accessibility_a=bathroom-accessibility-a:65",
        ),
    )
    monkeypatch.setattr(character_handlers.character_queries, "get_actor_identity_by_id", lambda actor_identity_id: actor)
    monkeypatch.setattr(
        character_handlers.character_queries,
        "create_scene_reference_candidate",
        lambda **kwargs: captured_candidates.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        character_handlers,
        "_store_scene_reference_image_url",
        lambda *, image_url, file_stem, correlation_id: (
            f"https://cdn.example.com/durable/{file_stem}.png",
            {"provider_source_image_rehosted": True, "durable_image_storage": {"storage_key": f"images/{file_stem}.png"}},
        ),
    )

    response = character_handlers.generate_scene_reference("post-1")

    assert response.status_code == 303
    assert len(captured_tasks) == 3
    assert all(task["strength"] == 100 for task in captured_tasks)
    assert all(task["resolution"] == "2k" for task in captured_tasks)
    assert all(task["fixed_generation"] is False for task in captured_tasks)
    assert all(task["extra_options"] == {"engine": "magnific_sparkle", "creative_detailing": 18} for task in captured_tasks)
    assert len(captured_candidates) == 3
    for candidate in captured_candidates:
        metadata = candidate["provider_metadata"]
        assert metadata["scene_bible_id"] == "bathroom_accessibility_a"
        assert metadata["scene_bible_version"] == 1
        assert metadata["scene_bible_name"] == "Accessible bathroom A"
        assert metadata["scene_bible_identity"].startswith("Accessible bathroom scene A")
        assert metadata["scene_generation_anchor"].startswith("the same compact accessible bathroom")
        assert metadata["scene_consistency_contract"]["scene_bible_id"] == "bathroom_accessibility_a"
        assert metadata["scene_consistency_contract"]["layout_lock"].startswith("same compact accessible bathroom")
        assert metadata["reason_code"] == "bathroom_terms"
        assert "the same compact accessible bathroom" in candidate["prompt"]
        assert "Accessible bathroom scene A" not in candidate["prompt"]
        assert metadata["angle_key"] in {"front_mid", "left_three_quarter", "right_profile"}
        assert metadata["angle_label"] in {"Front", "Left three-quarter", "Right profile"}
        assert metadata["reference_set_id"]
        assert metadata["reference_set_status"] == "pending_review"
        assert metadata["identity_lock_contract"]["actor_identity_id"] == "actor-1"
        assert metadata["identity_lock_contract"]["provider_lora_id"] == "lora-actor-1"
        assert metadata["identity_lock_contract"]["identity_strength"] == 100
        assert metadata["identity_lock_contract"]["prompt_lora_handle_required"] is True
        assert metadata["identity_lock_contract"]["styling_characters_required"] is True
        assert metadata["scene_style_loras"] == [{"name": "bathroom-accessibility-a", "strength": 65}]
        assert metadata["provider_source_image_rehosted"] is True
        assert metadata["durable_image_storage"]["storage_key"].startswith("images/scene-reference-")
        assert str(candidate["image_url"]).startswith("https://cdn.example.com/durable/scene-reference-")
        assert metadata["mystic_request"]["styling"]["characters"] == [{"id": "lora-actor-1", "strength": 100}]
        assert metadata["mystic_request"]["styling"]["styles"] == [{"name": "bathroom-accessibility-a", "strength": 65}]
        assert metadata["mystic_request"]["fixed_generation"] is False
        assert metadata["mystic_request"]["engine"] == "magnific_sparkle"
        assert metadata["mystic_request"]["creative_detailing"] == 18
        assert "model" not in metadata["mystic_request"]
        assert set(metadata["task"]) == {"task_id", "generated"}
        assert "_request_payload" not in metadata["task"]


def test_video_scene_reference_set_generation_requires_operator_review(monkeypatch):
    from app.core.config import Settings
    from app.features.characters import handlers as character_handlers

    actor = _ready_actor("actor-1", is_active=True)
    captured_tasks = []
    created_rows = []
    gate_calls = []
    attach_calls = []

    class _MysticClient:
        def create_mystic_scene_reference(self, **kwargs):
            captured_tasks.append(kwargs)
            return {
                "task_id": f"task-{len(captured_tasks)}",
                "generated": [f"https://cdn.example.com/{len(captured_tasks)}.png"],
                "_request_payload": {
                    "prompt": kwargs["prompt"],
                    "styling": {"characters": [{"id": kwargs["lora_id"], "strength": kwargs["strength"]}]},
                    "fixed_generation": kwargs["fixed_generation"],
                    "engine": kwargs["extra_options"]["engine"],
                    "creative_detailing": kwargs["extra_options"]["creative_detailing"],
                },
            }

    def _create_candidate(**kwargs):
        row = {
            "id": f"ref-{kwargs['angle_key']}",
            "actor_identity_id": kwargs["actor_identity_id"],
            "post_id": kwargs["post_id"],
            "scene_key": kwargs["scene_key"],
            "wardrobe_key": kwargs["wardrobe_key"],
            "status": "generated",
            "image_url": kwargs["image_url"],
            "prompt": kwargs["prompt"],
            "provider_metadata": kwargs["provider_metadata"],
            "identity_gate_result": None,
        }
        created_rows.append(row)
        return row

    monkeypatch.setattr(character_handlers, "get_magnific_client", lambda: _MysticClient())
    monkeypatch.setattr(
        character_handlers,
        "get_settings",
        lambda: Settings(
            supabase_url="https://supabase.example.com",
            supabase_key="test-key",
            supabase_service_key="test-service-key",
            cloudflare_r2_public_base_url="https://cdn.example.com",
            scene_reference_style_loras="bathroom_accessibility_a=bathroom-accessibility-a:65",
        ),
    )
    monkeypatch.setattr(character_handlers.character_queries, "get_actor_identity_by_id", lambda actor_identity_id: actor)
    monkeypatch.setattr(character_handlers.character_queries, "get_active_actor_identity", lambda: actor)
    monkeypatch.setattr(character_handlers.character_queries, "get_approved_scene_reference_set_for_post", lambda post_id: None)
    monkeypatch.setattr(character_handlers.character_queries, "get_approved_video_actor_scene_reference_set_for_post", lambda post_id: None)
    monkeypatch.setattr(character_handlers.character_queries, "create_scene_reference_candidate", _create_candidate)
    monkeypatch.setattr(
        character_handlers.character_queries,
        "list_scene_references_for_set",
        lambda *, post_id, reference_set_id: created_rows,
    )
    monkeypatch.setattr(
        character_handlers,
        "_store_scene_reference_image_url",
        lambda *, image_url, file_stem, correlation_id: (
            f"https://cdn.example.com/durable/{file_stem}.png",
            {"provider_source_image_rehosted": True, "durable_image_storage": {"storage_key": f"images/{file_stem}.png"}},
        ),
    )
    monkeypatch.setattr(
        character_handlers.character_queries,
        "record_scene_reference_set_gate",
        lambda **kwargs: gate_calls.append(kwargs) or created_rows,
    )
    monkeypatch.setattr(
        character_handlers.character_queries,
        "attach_scene_reference_to_post",
        lambda **kwargs: attach_calls.append(kwargs) or None,
    )

    summary = character_handlers.create_scene_reference_set_for_video_review(
        post={
            "id": "post-1",
            "batch_id": "batch-1",
            "post_type": "value",
            "topic_title": "WC-Fiasko im Bad",
            "seed_data": {
                "script": "Ein klarer Alltagstipp, der nicht frei in die Szene kopiert wird.",
                "target_length_tier": 8,
            },
        },
        batch={"id": "batch-1", "actor_identity_id": "actor-1", "target_length_tier": 8},
        correlation_id="corr-auto-scene-set",
    )

    assert summary.is_ready is False
    assert summary.is_video_actor_ready is False
    assert len(summary.rows) == 3
    assert len(captured_tasks) == 3
    assert all(task["lora_id"] == "lora-actor-1" for task in captured_tasks)
    assert all(task["strength"] == 100 for task in captured_tasks)
    assert [row["id"] for row in summary.rows] == [
        "ref-front_mid",
        "ref-left_three_quarter",
        "ref-right_profile",
    ]
    assert gate_calls[0]["status"] == "generated"
    assert gate_calls[0]["gate_result"].status == "manual_required"
    assert gate_calls[0]["gate_result"].details["auto_approved_for_video_submission"] is False
    assert gate_calls[0]["gate_result"].details["requires_operator_visual_review"] is True
    assert gate_calls[0]["gate_result"].details["actor_identity_match_confirmed"] is False
    assert gate_calls[0]["gate_result"].details["hybrid_reference_bundle_approved"] is False
    assert attach_calls == []


def test_scene_reference_set_for_video_review_polls_async_mystic_tasks(monkeypatch):
    from app.core.config import Settings
    from app.features.characters import handlers as character_handlers

    actor = _ready_actor("actor-1", is_active=True)
    captured_tasks = []
    polled_task_ids = []
    created_rows = []
    gate_calls = []
    attach_calls = []

    class _MysticClient:
        def create_mystic_scene_reference(self, **kwargs):
            captured_tasks.append(kwargs)
            return {
                "task_id": f"task-{len(captured_tasks)}",
                "generated": [],
                "_request_payload": {
                    "prompt": kwargs["prompt"],
                    "styling": {"characters": [{"id": kwargs["lora_id"], "strength": kwargs["strength"]}]},
                    "fixed_generation": kwargs["fixed_generation"],
                    "engine": kwargs["extra_options"]["engine"],
                    "creative_detailing": kwargs["extra_options"]["creative_detailing"],
                },
            }

        def get_mystic_task(self, *, task_id, correlation_id):
            polled_task_ids.append(task_id)
            return {
                "task_id": task_id,
                "generated": [f"https://cdn.example.com/{task_id}.png"],
            }

    def _create_candidate(**kwargs):
        row = {
            "id": f"ref-{kwargs['angle_key']}",
            "actor_identity_id": kwargs["actor_identity_id"],
            "post_id": kwargs["post_id"],
            "scene_key": kwargs["scene_key"],
            "wardrobe_key": kwargs["wardrobe_key"],
            "status": "generated" if kwargs["image_url"] else "submitted",
            "provider_task_id": kwargs["provider_task_id"],
            "image_url": kwargs["image_url"],
            "prompt": kwargs["prompt"],
            "provider_metadata": kwargs["provider_metadata"],
            "identity_gate_result": None,
        }
        created_rows.append(row)
        return row

    def _mark_generated(**kwargs):
        for row in created_rows:
            if row["id"] == kwargs["reference_id"]:
                row["status"] = "generated"
                row["image_url"] = kwargs["image_url"]
                row["provider_metadata"] = kwargs["provider_metadata"]

    def _store_image(*, image_url, file_stem, correlation_id):
        if not image_url:
            return None, {}
        return (
            f"https://cdn.example.com/durable/{file_stem}.png",
            {"provider_source_image_rehosted": True, "durable_image_storage": {"storage_key": f"images/{file_stem}.png"}},
        )

    monkeypatch.setattr(character_handlers, "get_magnific_client", lambda: _MysticClient())
    monkeypatch.setattr(
        character_handlers,
        "get_settings",
        lambda: Settings(
            supabase_url="https://supabase.example.com",
            supabase_key="test-key",
            supabase_service_key="test-service-key",
            cloudflare_r2_public_base_url="https://cdn.example.com",
            scene_reference_style_loras="bathroom_accessibility_a=bathroom-accessibility-a:65",
            magnific_timeout_seconds=5,
            magnific_poll_seconds=2,
        ),
    )
    monkeypatch.setattr(character_handlers.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(character_handlers.character_queries, "get_actor_identity_by_id", lambda actor_identity_id: actor)
    monkeypatch.setattr(character_handlers.character_queries, "get_active_actor_identity", lambda: actor)
    monkeypatch.setattr(character_handlers.character_queries, "get_approved_video_actor_scene_reference_set_for_post", lambda post_id: None)
    monkeypatch.setattr(character_handlers.character_queries, "create_scene_reference_candidate", _create_candidate)
    monkeypatch.setattr(
        character_handlers.character_queries,
        "list_scene_references_for_set",
        lambda *, post_id, reference_set_id: created_rows,
    )
    monkeypatch.setattr(
        character_handlers,
        "_store_scene_reference_image_url",
        _store_image,
    )
    monkeypatch.setattr(character_handlers.character_queries, "mark_scene_reference_generated", _mark_generated)
    monkeypatch.setattr(
        character_handlers.character_queries,
        "record_scene_reference_set_gate",
        lambda **kwargs: gate_calls.append(kwargs) or created_rows,
    )
    monkeypatch.setattr(
        character_handlers.character_queries,
        "attach_scene_reference_to_post",
        lambda **kwargs: attach_calls.append(kwargs) or None,
    )

    summary = character_handlers.create_scene_reference_set_for_video_review(
        post={
            "id": "post-1",
            "batch_id": "batch-1",
            "post_type": "value",
            "topic_title": "WC-Fiasko im Bad",
            "seed_data": {
                "script": "Ein klarer Alltagstipp, der nicht frei in die Szene kopiert wird.",
                "target_length_tier": 8,
            },
        },
        batch={"id": "batch-1", "actor_identity_id": "actor-1", "target_length_tier": 8},
        correlation_id="corr-auto-scene-set",
    )

    assert len(captured_tasks) == 3
    assert polled_task_ids == ["task-1", "task-2", "task-3"]
    assert summary.is_video_actor_ready is False
    assert [row["image_url"] for row in summary.rows] == [
        "https://cdn.example.com/durable/scene-reference-ref-front_mid-task-1.png",
        "https://cdn.example.com/durable/scene-reference-ref-left_three_quarter-task-2.png",
        "https://cdn.example.com/durable/scene-reference-ref-right_profile-task-3.png",
    ]
    assert all(row["provider_metadata"]["auto_polled_for_video_submission"] is True for row in summary.rows)
    assert gate_calls[0]["status"] == "generated"
    assert gate_calls[0]["gate_result"].status == "manual_required"
    assert gate_calls[0]["gate_result"].details["requires_operator_visual_review"] is True
    assert attach_calls == []


def test_regenerate_scene_reference_keeps_identity_lock_contract(monkeypatch):
    from app.core.config import Settings
    from app.features.characters import handlers as character_handlers

    actor = _ready_actor("actor-1", is_active=True)
    created = []

    class _MysticClient:
        def create_mystic_scene_reference(self, **kwargs):
            return {
                "task_id": "task-regenerated",
                "generated": ["https://cdn.example.com/regenerated.png"],
                "_request_payload": {
                    "prompt": kwargs["prompt"],
                    "resolution": kwargs["resolution"],
                    "fixed_generation": kwargs["fixed_generation"],
                    "engine": kwargs["extra_options"]["engine"],
                    "creative_detailing": kwargs["extra_options"]["creative_detailing"],
                    "styling": {
                        "characters": [{"id": kwargs["lora_id"], "strength": kwargs["strength"]}],
                        "styles": kwargs["style_loras"],
                    },
                },
            }

    reference = {
        "id": "ref-1",
        "actor_identity_id": "actor-1",
        "post_id": "post-1",
        "scene_key": "bathroom_accessibility_a",
        "wardrobe_key": "everyday_sweater",
        "provider_metadata": {"angle_key": "front_mid", "reference_set_id": "set-1"},
    }

    monkeypatch.setattr(character_handlers.character_queries, "get_scene_reference_by_id", lambda reference_id: reference)
    monkeypatch.setattr(character_handlers.character_queries, "get_actor_identity_by_id", lambda actor_identity_id: actor)
    monkeypatch.setattr(character_handlers, "get_magnific_client", lambda: _MysticClient())
    monkeypatch.setattr(
        character_handlers,
        "get_settings",
        lambda: Settings(
            supabase_url="https://supabase.example.com",
            supabase_key="test-key",
            supabase_service_key="test-service-key",
            cloudflare_r2_public_base_url="https://cdn.example.com",
            scene_reference_style_loras="bathroom_accessibility_a=bathroom-accessibility-a:65",
        ),
    )
    monkeypatch.setattr(
        character_handlers.character_queries,
        "create_scene_reference_candidate",
        lambda **kwargs: created.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        character_handlers,
        "_store_scene_reference_image_url",
        lambda *, image_url, file_stem, correlation_id: (
            f"https://cdn.example.com/durable/{file_stem}.png",
            {"provider_source_image_rehosted": True, "durable_image_storage": {"storage_key": f"images/{file_stem}.png"}},
        ),
    )
    monkeypatch.setattr(character_handlers.character_queries, "record_scene_reference_gate", lambda **_kwargs: None)
    monkeypatch.setattr(character_handlers, "_post_batch_id", lambda post_id: "batch-1")

    response = character_handlers.regenerate_scene_reference("ref-1")

    assert response.status_code == 303
    metadata = created[0]["provider_metadata"]
    assert metadata["scene_bible_id"] == "bathroom_accessibility_a"
    assert metadata["scene_bible_version"] == 1
    assert metadata["scene_bible_name"] == "Accessible bathroom A"
    assert metadata["scene_consistency_contract"]["scene_bible_id"] == "bathroom_accessibility_a"
    assert metadata["scene_consistency_contract"]["layout_lock"].startswith("same compact accessible bathroom")
    assert metadata["angle_key"] == "front_mid"
    assert metadata["angle_label"] == "Front"
    assert metadata["reference_set_id"] == "set-1"
    assert metadata["reference_set_status"] == "pending_review"
    assert metadata["identity_lock_contract"]["actor_identity_id"] == "actor-1"
    assert metadata["identity_lock_contract"]["provider_lora_id"] == "lora-actor-1"
    assert metadata["identity_lock_contract"]["identity_strength"] == 100
    assert metadata["regenerated_from_reference_id"] == "ref-1"
    assert metadata["scene_style_loras"] == [{"name": "bathroom-accessibility-a", "strength": 65}]
    assert metadata["provider_source_image_rehosted"] is True
    assert metadata["durable_image_storage"]["storage_key"].startswith("images/scene-reference-set-1-front_mid-task-regenerated")
    assert created[0]["image_url"].startswith("https://cdn.example.com/durable/scene-reference-set-1-front_mid-task-regenerated")
    assert metadata["mystic_request"]["resolution"] == "2k"
    assert metadata["mystic_request"]["fixed_generation"] is False
    assert metadata["mystic_request"]["engine"] == "magnific_sparkle"
    assert metadata["mystic_request"]["creative_detailing"] == 18
    assert metadata["mystic_request"]["styling"]["characters"] == [{"id": "lora-actor-1", "strength": 100}]
    assert metadata["mystic_request"]["styling"]["styles"] == [{"name": "bathroom-accessibility-a", "strength": 65}]
    assert "model" not in metadata["mystic_request"]
    assert set(metadata["task"]) == {"task_id", "generated"}
    assert "_request_payload" not in metadata["task"]


def test_approve_scene_reference_set_requires_three_generated_images(monkeypatch):
    from app.features.characters import handlers as character_handlers

    rows = [
        {"id": "front", "post_id": "post-1", "image_url": "https://cdn.example.com/front.png", "provider_metadata": {"angle_key": "front_mid", "reference_set_id": "set-1"}},
        {"id": "left", "post_id": "post-1", "image_url": None, "provider_metadata": {"angle_key": "left_three_quarter", "reference_set_id": "set-1"}},
        {"id": "profile", "post_id": "post-1", "image_url": "https://cdn.example.com/profile.png", "provider_metadata": {"angle_key": "right_profile", "reference_set_id": "set-1"}},
    ]

    monkeypatch.setattr(character_handlers.character_queries, "list_scene_references_for_set", lambda **_kwargs: rows)

    with pytest.raises(character_handlers.HTTPException) as exc_info:
        character_handlers.approve_scene_reference_set("post-1", "set-1")

    assert exc_info.value.status_code == 422
    assert "three generated scene references" in exc_info.value.detail


def test_approve_scene_reference_set_requires_canonical_angles(monkeypatch):
    from app.features.characters import handlers as character_handlers

    rows = [
        {"id": "front-a", "post_id": "post-1", "image_url": "https://cdn.example.com/front-a.png", "provider_metadata": {"angle_key": "front_mid", "reference_set_id": "set-1"}},
        {"id": "front-b", "post_id": "post-1", "image_url": "https://cdn.example.com/front-b.png", "provider_metadata": {"angle_key": "front_mid", "reference_set_id": "set-1"}},
        {"id": "left", "post_id": "post-1", "image_url": "https://cdn.example.com/left.png", "provider_metadata": {"angle_key": "left_three_quarter", "reference_set_id": "set-1"}},
    ]

    monkeypatch.setattr(character_handlers.character_queries, "list_scene_references_for_set", lambda **_kwargs: rows)

    with pytest.raises(character_handlers.HTTPException) as exc_info:
        character_handlers.approve_scene_reference_set("post-1", "set-1")

    assert exc_info.value.status_code == 422
    assert "each required angle" in exc_info.value.detail


def test_approve_scene_reference_set_marks_all_rows_and_attaches_front(monkeypatch):
    from app.features.characters import handlers as character_handlers

    rows = [
        {"id": "front", "post_id": "post-1", "image_url": "https://cdn.example.com/front.png", "provider_metadata": {"angle_key": "front_mid", "reference_set_id": "set-1"}},
        {"id": "left", "post_id": "post-1", "image_url": "https://cdn.example.com/left.png", "provider_metadata": {"angle_key": "left_three_quarter", "reference_set_id": "set-1"}},
        {"id": "profile", "post_id": "post-1", "image_url": "https://cdn.example.com/profile.png", "provider_metadata": {"angle_key": "right_profile", "reference_set_id": "set-1"}},
    ]
    recorded = []
    attached = []

    monkeypatch.setattr(character_handlers.character_queries, "list_scene_references_for_set", lambda **_kwargs: rows)
    monkeypatch.setattr(
        character_handlers.character_queries,
        "record_scene_reference_set_gate",
        lambda **kwargs: recorded.append(kwargs) or rows,
    )
    monkeypatch.setattr(
        character_handlers.character_queries,
        "attach_scene_reference_to_post",
        lambda **kwargs: attached.append(kwargs),
    )
    monkeypatch.setattr(character_handlers, "_post_batch_id", lambda post_id: "batch-1")

    response = character_handlers.approve_scene_reference_set("post-1", "set-1")

    assert response.status_code == 303
    assert recorded[0]["reference_set_id"] == "set-1"
    assert recorded[0]["status"] == "approved"
    assert recorded[0]["gate_result"].status == "passed"
    assert "scene consistency" in recorded[0]["gate_result"].reason
    assert recorded[0]["gate_result"].details == {
        "scene_consistency_set_approved": True,
        "actor_identity_match_confirmed": True,
        "reference_set_id": "set-1",
    }
    assert attached[0]["post_id"] == "post-1"
    assert attached[0]["reference_id"] == "front"


def test_reject_scene_reference_set_marks_all_rows(monkeypatch):
    from app.features.characters import handlers as character_handlers

    rows = [
        {"id": "front", "post_id": "post-1", "image_url": "https://cdn.example.com/front.png", "provider_metadata": {"angle_key": "front_mid", "reference_set_id": "set-1"}},
        {"id": "left", "post_id": "post-1", "image_url": "https://cdn.example.com/left.png", "provider_metadata": {"angle_key": "left_three_quarter", "reference_set_id": "set-1"}},
        {"id": "profile", "post_id": "post-1", "image_url": "https://cdn.example.com/profile.png", "provider_metadata": {"angle_key": "right_profile", "reference_set_id": "set-1"}},
    ]
    recorded = []

    monkeypatch.setattr(character_handlers.character_queries, "list_scene_references_for_set", lambda **_kwargs: rows)
    monkeypatch.setattr(
        character_handlers.character_queries,
        "record_scene_reference_set_gate",
        lambda **kwargs: recorded.append(kwargs) or rows,
    )
    monkeypatch.setattr(character_handlers, "_post_batch_id", lambda post_id: "batch-1")

    response = character_handlers.reject_scene_reference_set("post-1", "set-1")

    assert response.status_code == 303
    assert recorded[0]["reference_set_id"] == "set-1"
    assert recorded[0]["status"] == "rejected"
    assert recorded[0]["gate_result"].status == "manual_required"
    assert "Regenerate the full set" in recorded[0]["gate_result"].reason


def test_regenerate_scene_reference_accepts_legacy_scene_alias(monkeypatch):
    from app.features.characters import handlers as character_handlers

    actor = _ready_actor("actor-1", is_active=True)
    created = []

    class _MysticClient:
        def create_mystic_scene_reference(self, **kwargs):
            return {
                "task_id": "task-regenerated",
                "generated": ["https://cdn.example.com/regenerated.png"],
                "_request_payload": {"prompt": kwargs["prompt"]},
            }

    reference = {
        "id": "ref-1",
        "actor_identity_id": "actor-1",
        "post_id": "post-1",
        "scene_key": "bathroom_adaptation",
        "wardrobe_key": "everyday_sweater",
        "provider_metadata": {"angle_key": "front_mid", "reference_set_id": "set-1"},
    }

    monkeypatch.setattr(character_handlers.character_queries, "get_scene_reference_by_id", lambda reference_id: reference)
    monkeypatch.setattr(character_handlers.character_queries, "get_actor_identity_by_id", lambda actor_identity_id: actor)
    monkeypatch.setattr(character_handlers, "get_magnific_client", lambda: _MysticClient())
    monkeypatch.setattr(
        character_handlers,
        "get_settings",
        lambda: SimpleNamespace(scene_reference_style_loras=""),
    )
    monkeypatch.setattr(
        character_handlers.character_queries,
        "create_scene_reference_candidate",
        lambda **kwargs: created.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        character_handlers,
        "_store_scene_reference_image_url",
        lambda *, image_url, file_stem, correlation_id: (
            f"https://cdn.example.com/durable/{file_stem}.png",
            {"provider_source_image_rehosted": True, "durable_image_storage": {"storage_key": f"images/{file_stem}.png"}},
        ),
    )
    monkeypatch.setattr(character_handlers.character_queries, "record_scene_reference_gate", lambda **_kwargs: None)
    monkeypatch.setattr(character_handlers, "_post_batch_id", lambda post_id: "batch-1")

    response = character_handlers.regenerate_scene_reference("ref-1")

    assert response.status_code == 303
    metadata = created[0]["provider_metadata"]
    assert created[0]["scene_key"] == "bathroom_adaptation"
    assert metadata["scene_bible_id"] == "bathroom_accessibility_a"
    assert metadata["provider_source_image_rehosted"] is True
    assert created[0]["image_url"].startswith("https://cdn.example.com/durable/scene-reference-set-1-front_mid-task-regenerated")
    assert "the same compact accessible bathroom" in created[0]["prompt"]
    assert "Accessible bathroom scene A" not in created[0]["prompt"]


def test_regenerate_scene_reference_rejects_unknown_scene_key(monkeypatch):
    from app.features.characters import handlers as character_handlers

    reference = {
        "id": "ref-1",
        "actor_identity_id": "actor-1",
        "post_id": "post-1",
        "scene_key": "unknown_scene",
        "wardrobe_key": "everyday_sweater",
        "provider_metadata": {"angle_key": "front_mid", "reference_set_id": "set-1"},
    }

    monkeypatch.setattr(character_handlers.character_queries, "get_scene_reference_by_id", lambda reference_id: reference)

    with pytest.raises(character_handlers.HTTPException) as exc_info:
        character_handlers.regenerate_scene_reference("ref-1")

    assert exc_info.value.status_code == 422
    assert "unknown scene bible" in exc_info.value.detail
