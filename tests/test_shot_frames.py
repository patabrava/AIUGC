from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ValidationError


LONG_CHARACTER_DESCRIPTION = (
    "38-year-old German woman with long, light brown hair with natural blonde highlights, straight with a slight "
    "natural wave, parted slightly off-center to the left, falling softly around the shoulders and framing the face; "
    "hazel, almond-shaped eyes with subtle crow's feet at the outer corners; naturally full, soft-arched eyebrows in "
    "a light brown shade; a straight nose with a gently rounded tip; medium-full lips with a natural muted-pink tone; "
    "a friendly oval face with a soft jawline and gently rounded chin; soft forehead lines that are faint at rest; "
    "gentle laugh lines framing the mouth; warm light-medium skin tone with neutral undertones and smooth natural skin "
    "texture; slim build with relaxed upright posture."
)


class FakeLLMClient:
    def __init__(self):
        self.text_calls = []
        self.image_calls = []

    def generate_gemini_text(self, **kwargs):
        self.text_calls.append(kwargs)
        return "An unretouched casting-style camera photo of the supplied adult woman in a cream knit sweater, framed vertically in the supplied ordinary living room with soft window daylight, visible skin texture, quiet direct expression, natural optics, muted color, no beauty retouching, no logos, and no readable text."

    def generate_gemini_image(self, **kwargs):
        self.image_calls.append(kwargs)
        index = len(self.image_calls)
        return {
            "image_bytes": f"candidate-{index}".encode(),
            "mime_type": "image/png",
            "model": "gemini-3-pro-image-preview",
            "aspect_ratio": kwargs["aspect_ratio"],
            "image_size": kwargs["image_size"],
        }


def _reference(role: str, payload: bytes):
    from app.features.shot_frames.service import ShotFrameReference

    return ShotFrameReference(role=role, mime_type="image/png", image_bytes=payload)


def test_raw_camera_system_prompt_is_preserved_as_prompt_writer_instruction():
    from app.features.shot_frames.service import load_raw_camera_system_prompt

    prompt = load_raw_camera_system_prompt()

    assert prompt.startswith("You are a platform neutral image prompt writer")
    assert "Do not generate the image." in prompt
    assert prompt.rstrip().endswith(
        "Realism must be produced through specific physical evidence: skin microgeometry, material imperfections, ordinary light, natural optics, muted color, soft background falloff, asymmetry, and anti-retouching constraints."
    )


def test_generate_shot_frame_candidates_uses_two_actor_refs_then_location_and_stops_before_veo():
    from app.features.shot_frames.service import generate_shot_frame_candidates

    client = FakeLLMClient()
    result = generate_shot_frame_candidates(
        script="Als Rollstuhlfahrer kennst du das.",
        actor_name="AYRA Actor Long Character",
        character_description=LONG_CHARACTER_DESCRIPTION,
        scene_description="Warm off-white living room with beige curtain and oak side table.",
        wardrobe_description="Cream knit sweater from actor reference Image 1.",
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"three-quarter"),
        ],
        location_reference=_reference("location", b"location"),
        candidate_count=2,
        llm_client=client,
    )

    assert len(client.text_calls) == 1
    assert "Do not generate the image." in client.text_calls[0]["system_prompt"]
    assert client.text_calls[0]["thinking_budget"] == 0
    assert client.text_calls[0]["max_tokens"] == 4096
    assert len(client.image_calls) == 2
    assert [image["image_bytes"] for image in client.image_calls[0]["input_images"]] == [
        b"front",
        b"three-quarter",
        b"location",
    ]
    assert client.image_calls[0]["model"] == "gemini-3.1-flash-image"
    assert client.image_calls[0]["aspect_ratio"] == "9:16"
    assert "Image 1" in client.image_calls[0]["prompt"]
    assert "Image 2" in client.image_calls[0]["prompt"]
    assert "Image 3" in client.image_calls[0]["prompt"]
    assert "blazer" in client.image_calls[0]["prompt"].lower()
    assert LONG_CHARACTER_DESCRIPTION in client.text_calls[0]["prompt"]
    assert LONG_CHARACTER_DESCRIPTION in client.image_calls[0]["prompt"]
    assert [candidate.image_bytes for candidate in result.candidates] == [b"candidate-1", b"candidate-2"]


def test_generate_shot_frame_candidates_rejects_truncated_prompt_writer_output():
    from app.features.shot_frames.service import generate_shot_frame_candidates

    client = FakeLLMClient()
    client.generate_gemini_text = lambda **_kwargs: "A plain natural-light portrait with subtle"

    with pytest.raises(ValidationError, match="incomplete"):
        generate_shot_frame_candidates(
            script="Script",
            actor_name="Actor",
            character_description=LONG_CHARACTER_DESCRIPTION,
            scene_description="Room",
            wardrobe_description="Sweater",
            actor_references=[
                _reference("actor_front", b"front"),
                _reference("actor_three_quarter", b"three-quarter"),
            ],
            location_reference=_reference("location", b"location"),
            candidate_count=1,
            llm_client=client,
        )


@pytest.mark.parametrize("actor_count", [0, 1, 3])
def test_generate_shot_frame_candidates_requires_exactly_two_actor_references(actor_count):
    from app.features.shot_frames.service import generate_shot_frame_candidates

    with pytest.raises(ValidationError, match="exactly two actor"):
        generate_shot_frame_candidates(
            script="Script",
            actor_name="Actor",
            character_description=LONG_CHARACTER_DESCRIPTION,
            scene_description="Room",
            wardrobe_description="Sweater",
            actor_references=[_reference(f"actor_{index}", b"actor") for index in range(actor_count)],
            location_reference=_reference("location", b"location"),
            candidate_count=1,
            llm_client=FakeLLMClient(),
        )


def test_attached_prompt_matches_repo_copy_byte_for_byte():
    attachment = Path(
        "/Users/camiloecheverri/.codex/attachments/b7fdc676-8c1c-47e0-a2ab-4cf93efad6c5/pasted-text.txt"
    )
    if not attachment.exists():
        pytest.skip("Codex attachment is not present outside the authoring environment")

    from app.features.shot_frames.service import RAW_CAMERA_SYSTEM_PROMPT_PATH

    assert RAW_CAMERA_SYSTEM_PROMPT_PATH.read_bytes().rstrip(b"\n") == attachment.read_bytes().rstrip(b"\n")
