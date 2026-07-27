import json

import pytest

from app.core.errors import ValidationError
from app.features.characters.reference_generation import (
    ACTOR_THREE_QUARTER_MODEL,
    generate_verified_three_quarter_reference,
)


class _GeminiClient:
    def __init__(self, *, same_person: bool = True, confidences: list[float] | None = None):
        self.same_person = same_person
        self.confidences = confidences or ([0.96] * 3)
        self.image_calls = []
        self.text_call_count = 0

    def generate_gemini_image(self, **kwargs):
        self.image_calls.append(kwargs)
        return {
            "image_bytes": f"generated-three-quarter-{len(self.image_calls)}".encode(),
            "mime_type": "image/png",
            "model": ACTOR_THREE_QUARTER_MODEL,
        }

    def generate_gemini_text(self, **_kwargs):
        component_value = self.same_person
        confidence = self.confidences[self.text_call_count]
        self.text_call_count += 1
        return json.dumps(
            {
                "same_person": component_value,
                "facial_geometry_consistent": component_value,
                "apparent_age_consistent": component_value,
                "hairline_and_hair_consistent": component_value,
                "skin_texture_natural": True,
                "not_beautified_or_stylized": True,
                "no_face_artifacts": True,
                "three_quarter_pose": True,
                "confidence": confidence if component_value else 0.35,
                "blocking_reasons": [] if component_value else ["different facial geometry"],
                "observed_differences": ["three-quarter viewpoint"],
            }
        )


def test_gemini_pro_derives_three_quarter_from_only_canonical_front():
    client = _GeminiClient(confidences=[0.93, 0.98, 0.95])

    result = generate_verified_three_quarter_reference(
        {"mime_type": "image/png", "image_bytes": b"canonical-front"},
        llm_client=client,
        identity_model="gemini-2.5-flash",
        minimum_confidence=0.90,
    )

    assert len(client.image_calls) == 3
    for call in client.image_calls:
        assert call["model"] == "gemini-3-pro-image"
        assert call["input_images"] == [
            {"mime_type": "image/png", "image_bytes": b"canonical-front"}
        ]
    assert result["identity_gate_result"]["passed"] is True
    assert result["selected_index"] == 2
    assert result["image_bytes"] == b"generated-three-quarter-2"
    assert len(result["candidates"]) == 3


def test_gemini_pro_derivative_fails_closed_when_identity_changes():
    with pytest.raises(ValidationError, match="No Gemini Pro"):
        generate_verified_three_quarter_reference(
            {"mime_type": "image/png", "image_bytes": b"canonical-front"},
            llm_client=_GeminiClient(same_person=False),
            identity_model="gemini-2.5-flash",
            minimum_confidence=0.90,
        )
