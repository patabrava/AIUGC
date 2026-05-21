from __future__ import annotations

import pytest

from app.adapters.magnific_client import (
    MagnificClient,
    MagnificCompatibilityError,
    build_mystic_character_payload,
    list_lora_rows,
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


def test_list_lora_rows_flattens_grouped_provider_response():
    rows = list_lora_rows(
        {
            "data": {
                "default": [{"id": 1, "name": "stock-style"}],
                "customs": [{"id": 110, "name": "ayra_actor", "training": {"status": "completed"}}],
            }
        }
    )
    assert [row["id"] for row in rows] == [1, 110]


def test_list_lora_rows_accepts_saved_training_result_shape():
    rows = list_lora_rows(
        {
            "task_id": "train-1786946",
            "lora": {
                "id": 1786946,
                "name": "ayra-actor-longchar-20260521",
                "category": "my-character",
                "type": "character",
                "training": {"status": "completed", "defaultScale": 1, "quality": "ultra"},
            },
        }
    )

    assert rows == [
        {
            "id": 1786946,
            "name": "ayra-actor-longchar-20260521",
            "category": "my-character",
            "type": "character",
            "training": {"status": "completed", "defaultScale": 1, "quality": "ultra"},
            "task_id": "train-1786946",
        }
    ]


def test_submit_character_training_unwraps_data_payload(monkeypatch):
    class _FakeHttp:
        def request(self, *_args, **_kwargs):
            class _Response:
                status_code = 200
                text = "{}"

                def json(self):
                    return {"data": {"task_id": "train-1", "status": "CREATED"}}

            return _Response()

    client = MagnificClient(api_key="test-key", http_client=_FakeHttp())
    response = client.submit_character_training(
        name="ayra_actor",
        quality="high",
        gender="female",
        images=[f"https://cdn.example.com/{idx}.png" for idx in range(8)],
        description=None,
        webhook_url=None,
        correlation_id="corr",
    )
    assert response["task_id"] == "train-1"


def test_get_mystic_task_unwraps_data_payload():
    class _FakeHttp:
        def request(self, *_args, **_kwargs):
            class _Response:
                status_code = 200
                text = "{}"

                def json(self):
                    return {
                        "data": {
                            "task_id": "mystic-1",
                            "status": "COMPLETED",
                            "generated": ["https://cdn.example.com/still.png"],
                        }
                    }

            return _Response()

    client = MagnificClient(api_key="test-key", http_client=_FakeHttp())
    response = client.get_mystic_task(task_id="mystic-1", correlation_id="corr")
    assert response["task_id"] == "mystic-1"
    assert response["generated"] == ["https://cdn.example.com/still.png"]
