"""Lift topics remain speakable, but never prescribe visible lift equipment."""
from types import SimpleNamespace

import pytest

from app.features.characters.scene_reference import (
    LIFT_EQUIPMENT_REJECTORS,
    SCENE_BIBLES,
    build_scene_consistency_contract,
    build_scene_reference_prompt,
    get_scene_bible,
    map_script_to_scene_intent,
)
from app.features.scenes.background_comparison import build_raw_camera_background_brief


@pytest.mark.parametrize("scene_key", SCENE_BIBLES)
def test_every_scene_excludes_lifts_at_background_and_actor_reference_boundaries(scene_key):
    background = build_raw_camera_background_brief(scene_key)
    actor = build_scene_reference_prompt(
        actor_name="reference actor", scene_key=scene_key,
        wardrobe_key="everyday_sweater", post_type="product",
    )
    contract = build_scene_consistency_contract(scene_key)
    assert get_scene_bible(scene_key).version == (4 if scene_key == "home_living_room_advice_a" else 3)
    for equipment in LIFT_EQUIPMENT_REJECTORS:
        assert equipment in background.split("Also exclude: ")[1]
        assert equipment in actor.split("Do not add ")[1]
        assert equipment in contract["drift_rejectors_by_scene"]


@pytest.mark.parametrize("script", [
    "Ein Treppenlift macht Wege einfacher.",
    "Ein Plattformlift hilft im Alltag.",
    "Ein Lift verbindet die Etagen.",
])
def test_product_routing_uses_living_room_without_stair_or_lift_anchors(script):
    intent = map_script_to_scene_intent(script=script, post_type="product", target_length_tier=8, seed_data={})
    assert intent.scene_key == "home_living_room_advice_a"
    bible = get_scene_bible(intent.scene_key)
    positive_anchors = " ".join((
        bible.name, bible.generation_anchor, bible.consistency_anchor,
        bible.layout_lock, bible.anchor_lock, bible.scene_moment, *bible.must_match,
    )).lower()
    assert "oak" in positive_anchors and "living room" in positive_anchors
    for obsolete in ("stairs", "staircase", "handrail", "stairlift", "folded chair", "rail track", "cabin lift"):
        assert obsolete not in positive_anchors
    assert "installed stairlift" not in bible.scene_identity


@pytest.mark.parametrize("builder_name", ["build_canonical_scene_plate_prompt", "build_derived_scene_plate_prompt"])
@pytest.mark.parametrize("presentation_mode", ["wheelchair_seated", "standing_presenter"])
def test_scene_plate_builders_omit_lifts_in_reference_images(builder_name, presentation_mode):
    from app.features.shot_frames import wheelchair_scene_plate as plates
    prompt = getattr(plates, builder_name)(
        scene=get_scene_bible("hallway_stairlift_a").scene_identity,
        wardrobe="sage cardigan", presentation_mode=presentation_mode,
    )
    assert "sage cardigan" in prompt
    for equipment in LIFT_EQUIPMENT_REJECTORS:
        assert equipment in prompt
    assert "If any supplied reference contains this equipment, omit it" in prompt
    assert "No stairs, staircases, stairwells, steps, split-level floors, or stair railings" in prompt
    assert "manual wheelchair when applicable" in prompt


def test_default_canonical_lookup_does_not_reuse_pre_exclusion_assets(monkeypatch):
    from app.features.scenes import queries
    filters = {}

    class Query:
        def table(self, *_): return self
        def select(self, *_): return self
        def eq(self, key, value):
            filters[key] = value
            return self
        def order(self, *_, **__): return self
        def limit(self, *_): return self
        def maybe_single(self): return self
        def execute(self):
            # Only an obsolete version-2 asset exists.
            return SimpleNamespace(data=None if filters["scene_bible_version"] == 4 else {"id": "old"})

    monkeypatch.setattr(queries, "get_supabase", lambda: SimpleNamespace(client=Query()))
    assert queries.get_canonical_scene_asset(scene_key="hallway_stairlift_a") is None
    assert filters["scene_bible_version"] == 4
    assert filters["scene_key"] == "home_living_room_advice_a"


def test_general_shot_frame_path_excludes_equipment_despite_lift_dialogue():
    from app.features.shot_frames.service import _build_prompt_writer_brief, _build_composition_prompt
    brief = _build_prompt_writer_brief(
        script="Ein Treppenlift hilft dir.", actor_name="reference actor",
        scene_description=get_scene_bible("hallway_stairlift_a").scene_identity,
        wardrobe_description="sage cardigan",
    )
    composed = _build_composition_prompt(prompt_writer_output="Finished photographic prompt.")
    assert "Ein Treppenlift hilft dir." in brief
    for prompt in (brief, composed):
        for equipment in LIFT_EQUIPMENT_REJECTORS:
            assert equipment in prompt
        assert "If any supplied reference contains this equipment, omit it" in prompt


def test_historical_staircase_selection_resolves_to_living_room_everywhere():
    from app.features.characters.scene_reference import SCENE_CATALOG
    from app.features.scenes.queries import resolve_canonical_scene_key
    assert "hallway_stairlift_a" not in SCENE_BIBLES
    assert get_scene_bible("hallway_stairlift_a") is get_scene_bible("home_living_room_advice_a")
    assert SCENE_CATALOG["hallway_stairlift_a"] == get_scene_bible("home_living_room_advice_a").scene_identity
    assert resolve_canonical_scene_key(scene_text="hallway_stairlift_a") == "home_living_room_advice_a"
    brief = build_raw_camera_background_brief("hallway_stairlift_a")
    assert "living room" in brief
    assert "stairs, staircases, stairwells, steps, split-level floors, and stair railings" in brief.split("Also exclude: ")[1]
