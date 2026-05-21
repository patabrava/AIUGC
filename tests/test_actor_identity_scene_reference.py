from __future__ import annotations

from app.features.characters.scene_reference import (
    SCENE_CATALOG,
    WARDROBE_SET,
    build_scene_reference_prompt,
    map_script_to_scene_intent,
)


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
