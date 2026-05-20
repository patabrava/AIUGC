from __future__ import annotations

import pytest

from app.adapters.magnific_client import (
    MagnificClient,
    MagnificCompatibilityError,
    build_mystic_character_payload,
    normalize_lora_training_status,
)


def test_build_magnific_training_payload_uses_required_fields():
    client = MagnificClient(api_key="test-key")
    payload = client.build_character_training_payload(
        name="ayra_actor",
        quality="high",
        gender="female",
        images=[f"https://cdn.example.com/{idx}.png" for idx in range(8)],
        description="Primary AYRA actor",
        webhook_url=None,
    )
    assert payload["name"] == "ayra_actor"
    assert payload["quality"] == "high"
    assert payload["gender"] == "female"
    assert len(payload["images"]) == 8
    assert "webhook_url" not in payload


def test_mystic_payload_uses_styling_characters():
    payload = build_mystic_character_payload(
        prompt="Portrait of the actor in a bright bathroom",
        lora_id="110",
        strength=100,
        aspect_ratio="social_story_9_16",
        resolution="2k",
    )
    assert payload["styling"]["characters"] == [{"id": "110", "strength": 100}]
    assert "structure_reference" not in payload
    assert "style_reference" not in payload
    assert "model" not in payload


@pytest.mark.parametrize("field", ["structure_reference", "style_reference"])
def test_mystic_payload_rejects_lora_incompatible_reference_fields(field):
    with pytest.raises(MagnificCompatibilityError):
        build_mystic_character_payload(
            prompt="Actor in car",
            lora_id="110",
            strength=100,
            extra_options={field: "base64"},
        )


@pytest.mark.parametrize("model", ["fluid", "flexible", "super_real", "editorial_portraits"])
def test_mystic_payload_rejects_lora_incompatible_models(model):
    with pytest.raises(MagnificCompatibilityError):
        build_mystic_character_payload(
            prompt="Actor in car",
            lora_id="110",
            strength=100,
            extra_options={"model": model},
        )


def test_normalize_training_status_maps_completed_to_ready():
    status = normalize_lora_training_status({"training": {"status": "completed"}, "id": 110, "name": "ayra_actor"})
    assert status.phase == "ready"
    assert status.progress_percent == 100
    assert status.provider_lora_id == "110"
