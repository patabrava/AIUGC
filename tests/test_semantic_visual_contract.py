from __future__ import annotations

from hashlib import sha256

from app.features.shot_frames.service import ShotFrameReference
from app.features.shot_production.planner import EditorialBeat


def _reference(role: str, marker: bytes) -> ShotFrameReference:
    return ShotFrameReference(role=role, mime_type="image/png", image_bytes=marker)


def _visual_contract() -> dict:
    return {
        "version": "semantic_visual_contract_v1",
        "scene_key": "garden_patio_a",
        "scene_description": "the exact supplied garden patio",
        "wardrobe_key": "grey_cardigan",
        "wardrobe_description": "light-grey cardigan over a plain white top",
        "wheelchair_description": (
            "manual wheelchair with matte dark-graphite frame, black cushions, "
            "slim black armrests, and silver hand rims"
        ),
        "framing_description": (
            "static vertical seated eye-level medium close-up with one armrest "
            "and part of a rear wheel visible"
        ),
        "location_reference_sha256": "3" * 64,
    }


def test_take_prompt_uses_frozen_scene_outfit_and_wheelchair_without_old_room_lock():
    from app.features.shot_production.prompts import build_veo_take_prompt

    beat = EditorialBeat(
        index=0,
        text="Dieser Alltagstipp macht den nächsten Schritt leichter.",
        word_count=8,
        estimated_speech_seconds=4.0,
        provider_duration_seconds=8,
    )

    prompt = build_veo_take_prompt(beat, visual_contract=_visual_contract())

    assert "exact supplied garden patio" in prompt
    assert "light-grey cardigan over a plain white top" in prompt
    assert "manual wheelchair" in prompt
    assert "rear wheel" in prompt
    assert "cream knit sweater" not in prompt
    assert "room, posture" not in prompt


def test_scene_plate_candidates_keep_actor_inputs_immutable_and_chain_from_first_plate():
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    actor_front = _reference("actor_front", b"front")
    actor_support = _reference("actor_three_quarter", b"support")
    location = _reference("location", b"location")

    class RecordingClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def generate_gemini_image(self, **kwargs):
            self.calls.append(kwargs)
            marker = f"plate-{len(self.calls)}".encode()
            return {
                "image_bytes": marker,
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    client = RecordingClient()
    result = generate_scene_plate_candidates(
        actor_references=[actor_front, actor_support],
        location_reference=location,
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        candidate_count=3,
        llm_client=client,
    )

    assert [candidate.image_bytes for candidate in result.candidates] == [
        b"plate-1",
        b"plate-2",
        b"plate-3",
    ]
    assert [item["image_bytes"] for item in client.calls[0]["input_images"]] == [
        b"front",
        b"support",
        b"location",
    ]
    assert [item["image_bytes"] for item in client.calls[1]["input_images"]] == [
        b"plate-1",
        b"front",
        b"location",
    ]
    assert actor_front.image_bytes == b"front"
    assert actor_support.image_bytes == b"support"
    assert all("manual wheelchair" in call["prompt"] for call in client.calls)


def test_scene_plate_candidates_derive_every_option_from_established_actor_anchor():
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    anchor = _reference("canonical_scene_plate", b"approved-anchor")
    actor_front = _reference("actor_front", b"front")
    actor_support = _reference("actor_three_quarter", b"support")
    location = _reference("location", b"location")

    class RecordingClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def generate_gemini_image(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "image_bytes": f"derived-{len(self.calls)}".encode(),
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    client = RecordingClient()
    result = generate_scene_plate_candidates(
        actor_references=[actor_front, actor_support],
        location_reference=location,
        canonical_scene_plate=anchor,
        scene="the exact supplied home office",
        wardrobe="navy cotton blouse",
        candidate_count=3,
        llm_client=client,
    )

    assert result.derivation_mode == "canonical_anchor"
    assert len(client.calls) == 3
    assert all(
        [item["image_bytes"] for item in call["input_images"]]
        == [b"approved-anchor", b"front", b"location"]
        for call in client.calls
    )
    assert b"support" not in {
        item["image_bytes"]
        for call in client.calls
        for item in call["input_images"]
    }
    assert all("canonical scene plate" in call["prompt"] for call in client.calls)


def test_actor_reference_fingerprint_is_ordered_and_byte_bound():
    from app.features.semantic_videos.visual_contract import (
        build_actor_reference_fingerprint,
    )

    rows = [
        {
            "role": "actor_front",
            "storage_uri": "https://cdn/front.png",
            "mime_type": "image/png",
            "byte_length": 5,
            "sha256": sha256(b"front").hexdigest(),
        },
        {
            "role": "actor_three_quarter",
            "storage_uri": "https://cdn/support.png",
            "mime_type": "image/png",
            "byte_length": 7,
            "sha256": sha256(b"support").hexdigest(),
        },
    ]

    fingerprint = build_actor_reference_fingerprint(rows)

    assert len(fingerprint) == 64
    assert fingerprint == build_actor_reference_fingerprint(rows)
    assert fingerprint != build_actor_reference_fingerprint(list(reversed(rows)))
    assert fingerprint != build_actor_reference_fingerprint(
        [{**rows[0], "sha256": "0" * 64}, rows[1]]
    )


def test_semantic_wardrobe_rotation_is_distinct_for_first_three_posts_and_override_wins():
    from app.features.semantic_videos.visual_contract import select_semantic_wardrobe

    rotated = [
        select_semantic_wardrobe(post_id=f"post-{index}", rotation_index=index)
        for index in range(3)
    ]

    assert len({key for key, _description in rotated}) == 3
    assert len({description for _key, description in rotated}) == 3
    assert select_semantic_wardrobe(
        post_id="post-override",
        rotation_index=0,
        wardrobe_description="navy blue cotton blouse",
    ) == ("custom", "navy blue cotton blouse")


def test_scene_plate_master_is_bound_to_frozen_visual_contract_not_actor_front_bytes():
    from app.features.semantic_videos.handlers import _assert_scene_plate_master
    from app.features.semantic_videos.visual_contract import (
        build_actor_reference_fingerprint,
        build_visual_contract,
    )

    reference = {
        "scene_key": "garden_patio_a",
        "scene_description": "the exact supplied garden patio",
        "wardrobe_key": "grey_cardigan",
        "wardrobe_description": "light-grey cardigan over a plain white top",
        "actor_references": [
            {
                "role": "actor_front",
                "storage_uri": "https://cdn/front.png",
                "mime_type": "image/png",
                "byte_length": 5,
                "sha256": sha256(b"front").hexdigest(),
            },
            {
                "role": "actor_three_quarter",
                "storage_uri": "https://cdn/support.png",
                "mime_type": "image/png",
                "byte_length": 7,
                "sha256": sha256(b"support").hexdigest(),
            },
        ],
        "location_reference": {
            "role": "location",
            "storage_uri": "https://cdn/location.png",
            "mime_type": "image/png",
            "byte_length": 8,
            "sha256": "3" * 64,
        },
    }
    contract = build_visual_contract(reference)
    reference["visual_contract"] = contract
    actor_fingerprint = build_actor_reference_fingerprint(reference["actor_references"])
    reference["actor_reference_fingerprint"] = actor_fingerprint
    scene_plate = {
        "index": 1,
        "storage_uri": "https://cdn/scene-plate.png",
        "mime_type": "image/png",
        "byte_length": 11,
        "sha256": sha256(b"scene-plate").hexdigest(),
        "provider_model": "gemini-3.1-flash-image",
        "visual_contract_hash": contract["contract_hash"],
        "actor_reference_fingerprint": actor_fingerprint,
        "derivation_mode": "bootstrap",
        "canonical_anchor_id": None,
        "canonical_anchor_sha256": None,
    }

    _assert_scene_plate_master(
        reference_snapshot=reference,
        master_snapshot=scene_plate,
    )


def test_visual_contract_hash_changes_with_location_or_outfit_but_not_actor_references():
    from app.features.semantic_videos.visual_contract import build_visual_contract

    reference = {
        "scene_key": "garden_patio_a",
        "scene_description": "the exact supplied garden patio",
        "wardrobe_key": "grey_cardigan",
        "wardrobe_description": "light-grey cardigan over a plain white top",
        "location_reference": {
            "role": "location",
            "storage_uri": "https://cdn/garden.png",
            "sha256": "3" * 64,
        },
    }
    original = build_visual_contract(reference)
    changed = build_visual_contract(
        {
            **reference,
            "wardrobe_key": "beige_blazer",
            "wardrobe_description": "soft-beige blazer over a plain white top",
        }
    )

    assert original["contract_hash"] != changed["contract_hash"]
    assert "actor_references" not in original
    assert original["wheelchair_description"] == changed["wheelchair_description"]
