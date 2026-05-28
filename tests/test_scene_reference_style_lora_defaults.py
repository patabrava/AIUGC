from app.core.config import Settings
from app.features.characters.scene_reference import scene_reference_style_loras_for


def test_scene_reference_style_lora_defaults_cover_all_video_scenes():
    settings = Settings(
        supabase_url="https://supabase.example.com",
        supabase_key="test-key",
        supabase_service_key="test-service-key",
        cloudflare_r2_public_base_url="https://cdn.example.com",
    )

    assert scene_reference_style_loras_for(
        "bathroom_accessibility_a",
        settings.scene_reference_style_loras,
    ) == [{"name": "bathroom-accessibility-a", "strength": 65}]
    assert scene_reference_style_loras_for(
        "car_transfer_residential_a",
        settings.scene_reference_style_loras,
    ) == [{"name": "car-transfer-residential-a", "strength": 65}]
    assert scene_reference_style_loras_for(
        "home_living_room_advice_a",
        settings.scene_reference_style_loras,
    ) == [{"name": "home-living-room-advice-a", "strength": 65}]
