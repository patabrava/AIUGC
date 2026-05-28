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


def test_mystic_payload_supports_lora_safe_style_loras():
    payload = build_mystic_character_payload(
        prompt="Photorealistic actor in the exact bathroom scene",
        lora_id="actor-110",
        strength=100,
        aspect_ratio="social_story_9_16",
        resolution="2k",
        style_loras=[{"name": "bathroom-accessibility-a", "strength": 65}],
    )

    assert payload["styling"] == {
        "characters": [{"id": "actor-110", "strength": 100}],
        "styles": [{"name": "bathroom-accessibility-a", "strength": 65}],
    }
    assert "structure_reference" not in payload
    assert "style_reference" not in payload
    assert "model" not in payload


@pytest.mark.parametrize(
    "style_loras, message",
    [
        ([{"strength": 65}], "requires a name"),
        ([{"name": "bathroom-accessibility-a", "strength": "high"}], "strength must be an integer"),
        ([{"name": "bathroom-accessibility-a", "strength": -1}], "between 0 and 200"),
        ([{"name": "bathroom-accessibility-a", "strength": 201}], "between 0 and 200"),
        (["bathroom-accessibility-a"], "rows must be objects"),
    ],
)
def test_mystic_payload_rejects_invalid_style_lora_rows(style_loras, message):
    with pytest.raises(MagnificCompatibilityError, match=message):
        build_mystic_character_payload(
            prompt="Actor in bathroom",
            lora_id="actor-110",
            strength=100,
            style_loras=style_loras,
        )


def test_mystic_scene_payload_omits_model_for_lora_safe_request():
    payload = build_mystic_character_payload(
        prompt="Scene Identity:\nHome living room advice scene A.\n\nActor Identity:\n@ayra_actor::200",
        lora_id="110",
        strength=200,
        aspect_ratio="social_story_9_16",
        resolution="2k",
        fixed_generation=True,
        extra_options={"engine": "magnific_sparkle", "creative_detailing": 18},
    )

    assert payload["styling"]["characters"] == [{"id": "110", "strength": 200}]
    assert payload["fixed_generation"] is True
    assert payload["engine"] == "magnific_sparkle"
    assert payload["creative_detailing"] == 18
    assert "model" not in payload
    assert "structure_reference" not in payload
    assert "style_reference" not in payload


def test_mystic_scene_payload_supports_fixed_generation_true():
    payload = build_mystic_character_payload(
        prompt="Scene Identity:\nHome living room advice scene A.\n\nActor Identity:\n@ayra_actor::200",
        lora_id="110",
        strength=200,
        fixed_generation=True,
    )

    assert payload["fixed_generation"] is True


@pytest.mark.parametrize("field", ["structure_reference", "style_reference"])
def test_mystic_payload_rejects_lora_incompatible_reference_fields(field):
    with pytest.raises(MagnificCompatibilityError):
        build_mystic_character_payload(
            prompt="Actor in car",
            lora_id="110",
            strength=100,
            extra_options={field: "base64"},
        )


@pytest.mark.parametrize("field", ["structure_reference", "style_reference"])
def test_mystic_payload_rejects_lora_incompatible_reference_fields_even_when_empty(field):
    with pytest.raises(MagnificCompatibilityError):
        build_mystic_character_payload(
            prompt="Actor in car",
            lora_id="110",
            strength=100,
            extra_options={field: None},
        )


@pytest.mark.parametrize("model", ["fluid", "flexible", "super_real", "editorial_portraits", "some_new_model"])
def test_mystic_payload_rejects_lora_incompatible_models(model):
    with pytest.raises(MagnificCompatibilityError):
        build_mystic_character_payload(
            prompt="Actor in car",
            lora_id="110",
            strength=100,
            extra_options={"model": model},
        )


@pytest.mark.parametrize(
    "option",
    [
        {"prompt": "override"},
        {"resolution": "4k"},
        {"aspect_ratio": "1:1"},
        {"styling": {}},
        {"webhook_url": "https://example.com/webhook"},
        {"fixed_generation": False},
    ],
)
def test_mystic_payload_rejects_protected_payload_overrides(option):
    with pytest.raises(MagnificCompatibilityError):
        build_mystic_character_payload(
            prompt="Actor in car",
            lora_id="110",
            strength=100,
            extra_options=option,
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


def test_create_mystic_scene_reference_exposes_exact_request_payload():
    captured = {}

    class _FakeHttp:
        def request(self, _method, _path, *, headers, json):
            captured["headers"] = headers
            captured["json"] = json

            class _Response:
                status_code = 200
                text = "{}"

                def json(self):
                    return {"data": {"task_id": "mystic-1", "generated": []}}

            return _Response()

    client = MagnificClient(api_key="test-key", http_client=_FakeHttp())
    response = client.create_mystic_scene_reference(
        prompt="Scene Identity:\nHome living room advice scene A.\n\nActor Identity:\n@ayra_actor::200",
        lora_id="110",
        strength=200,
        correlation_id="corr",
        resolution="2k",
        fixed_generation=True,
        style_loras=[{"name": "bathroom-accessibility-a", "strength": 65}],
        extra_options={"engine": "magnific_sparkle", "creative_detailing": 18},
    )

    expected_payload = {
        "prompt": "Scene Identity:\nHome living room advice scene A.\n\nActor Identity:\n@ayra_actor::200",
        "resolution": "2k",
        "aspect_ratio": "social_story_9_16",
        "styling": {
            "characters": [{"id": "110", "strength": 200}],
            "styles": [{"name": "bathroom-accessibility-a", "strength": 65}],
        },
        "fixed_generation": True,
        "engine": "magnific_sparkle",
        "creative_detailing": 18,
    }
    assert captured["json"] == expected_payload
    assert response["_request_payload"] == expected_payload


def test_build_style_training_payload_uses_required_fields():
    client = MagnificClient(api_key="test-key")
    payload = client.build_style_training_payload(
        name="bathroom-accessibility-a",
        quality="high",
        images=[f"https://cdn.example.com/bathroom/{idx}.png" for idx in range(6)],
        description="Accessible bathroom scene A anchors",
        webhook_url=None,
    )

    assert payload == {
        "name": "bathroom-accessibility-a",
        "quality": "high",
        "images": [f"https://cdn.example.com/bathroom/{idx}.png" for idx in range(6)],
        "description": "Accessible bathroom scene A anchors",
    }


def test_build_style_training_payload_requires_6_to_20_cleaned_image_urls():
    client = MagnificClient(api_key="test-key")

    with pytest.raises(MagnificCompatibilityError, match="requires 6 to 20 image URLs"):
        client.build_style_training_payload(
            name="bathroom-accessibility-a",
            quality="high",
            images=[" https://cdn.example.com/1.png ", "", "   ", "https://cdn.example.com/2.png"],
        )


def test_submit_style_training_unwraps_data_payload():
    captured = {}

    class _FakeHttp:
        def request(self, method, path, *, headers, json):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json

            class _Response:
                status_code = 200
                text = "{}"

                def json(self):
                    return {"data": {"task_id": "style-train-1", "status": "CREATED"}}

            return _Response()

    client = MagnificClient(api_key="test-key", http_client=_FakeHttp())
    response = client.submit_style_training(
        name="bathroom-accessibility-a",
        quality="high",
        images=[f"https://cdn.example.com/bathroom/{idx}.png" for idx in range(6)],
        description="Accessible bathroom scene A anchors",
        webhook_url=None,
        correlation_id="corr",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/ai/loras/styles"
    assert captured["json"]["name"] == "bathroom-accessibility-a"
    assert response["task_id"] == "style-train-1"


def test_create_image_to_prompt_task_unwraps_data_payload():
    captured = {}

    class _FakeHttp:
        def request(self, method, path, *, headers, json):
            captured["method"] = method
            captured["path"] = path
            captured["json"] = json

            class _Response:
                status_code = 200
                text = "{}"

                def json(self):
                    return {"data": {"task_id": "itp-1", "status": "CREATED", "generated": []}}

            return _Response()

    client = MagnificClient(api_key="test-key", http_client=_FakeHttp())
    response = client.create_image_to_prompt_task(
        image="https://cdn.example.com/scene.png",
        webhook_url=None,
        correlation_id="corr",
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/ai/image-to-prompt"
    assert captured["json"] == {"image": "https://cdn.example.com/scene.png"}
    assert response["task_id"] == "itp-1"


def test_get_image_to_prompt_task_unwraps_data_payload():
    class _FakeHttp:
        def request(self, method, path, *, headers, json=None):
            class _Response:
                status_code = 200
                text = "{}"

                def json(self):
                    return {"data": {"task_id": "itp-1", "status": "COMPLETED", "generated": ["bathroom with grab rail"]}}

            return _Response()

    client = MagnificClient(api_key="test-key", http_client=_FakeHttp())
    response = client.get_image_to_prompt_task(task_id="itp-1", correlation_id="corr")

    assert response["task_id"] == "itp-1"
    assert response["generated"] == ["bathroom with grab rail"]
