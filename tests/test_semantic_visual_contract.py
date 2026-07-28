from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from threading import Barrier, Lock

from PIL import Image, ImageDraw

from app.features.shot_frames.service import ShotFrameReference
from app.features.shot_production.planner import EditorialBeat


def _reference(role: str, marker: bytes) -> ShotFrameReference:
    return ShotFrameReference(role=role, mime_type="image/png", image_bytes=marker)


def _png(*, block: str | None = None, value: int = 128) -> bytes:
    image = Image.new("RGB", (64, 64), (value, value, value))
    if block:
        draw = ImageDraw.Draw(image)
        x = 4 if block == "left" else 44
        draw.rectangle((x, 8, x + 15, 55), fill=(15, 15, 15))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


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


def test_scene_plate_bootstrap_candidates_are_independent_from_original_actor_inputs():
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
    assert len(client.calls) == 3
    assert all(
        [item["image_bytes"] for item in call["input_images"]]
        == [b"front", b"support", b"location"]
        for call in client.calls
    )
    assert all(call["model"] == "gemini-3-pro-image" for call in client.calls)
    assert all(call["image_size"] == "2K" for call in client.calls)
    assert actor_front.image_bytes == b"front"
    assert actor_support.image_bytes == b"support"
    assert all("manual wheelchair" in call["prompt"] for call in client.calls)
    assert all("visible pores" in call["prompt"] for call in client.calls)
    assert all("face averaging" in call["prompt"] for call in client.calls)
    assert len(set(result.prompts)) == 3
    assert all(
        f"Candidate {index} composition:" in prompt
        for index, prompt in enumerate(result.prompts, start=1)
    )


def test_scene_plate_candidates_generate_concurrently_and_keep_candidate_order():
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    barrier = Barrier(3)
    call_lock = Lock()
    call_number = 0

    class ConcurrentClient:
        def generate_gemini_image(self, **kwargs):
            nonlocal call_number
            with call_lock:
                call_number += 1
                marker = call_number
            barrier.wait(timeout=2)
            return {
                "image_bytes": f"parallel-{marker}".encode(),
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    result = generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        candidate_count=3,
        llm_client=ConcurrentClient(),
    )

    assert [candidate.index for candidate in result.candidates] == [1, 2, 3]
    assert len({candidate.image_bytes for candidate in result.candidates}) == 3


def test_scene_plate_candidate_progress_reports_real_generation_phases():
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    phases: list[tuple[str, dict]] = []

    class DistinctClient:
        def generate_gemini_image(self, **kwargs):
            prompt = kwargs["prompt"]
            index = next(
                index
                for index in range(1, 4)
                if f"Candidate {index} composition:" in prompt
            )
            return {
                "image_bytes": _png(block="left" if index == 1 else "right", value=80 + (index * 40)),
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        llm_client=DistinctClient(),
        progress_callback=lambda phase, details: phases.append(
            (phase, dict(details))
        ),
    )

    assert [phase for phase, _details in phases] == [
        "generating_images",
        "checking_diversity",
    ]
    assert phases[0][1]["candidate_count"] == 3


def test_scene_plate_near_duplicate_gate_detects_pixel_only_variation():
    from app.features.shot_frames.wheelchair_scene_plate import (
        scene_plates_are_near_duplicates,
    )

    assert scene_plates_are_near_duplicates(
        _png(value=128),
        _png(value=129),
    )
    assert not scene_plates_are_near_duplicates(
        _png(block="left"),
        _png(block="right"),
    )


def test_scene_plate_candidates_regenerate_perceptual_duplicates_with_distinct_prompts():
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    calls: list[str] = []
    calls_lock = Lock()

    class DiversityClient:
        def generate_gemini_image(self, **kwargs):
            prompt = kwargs["prompt"]
            with calls_lock:
                calls.append(prompt)
            if "DIVERSITY RECOVERY" not in prompt:
                image_bytes = _png(value=128)
            elif "Candidate 2 composition:" in prompt:
                image_bytes = _png(block="left")
            else:
                image_bytes = _png(block="right")
            return {
                "image_bytes": image_bytes,
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    result = generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        candidate_count=3,
        llm_client=DiversityClient(),
    )

    assert len(calls) == 5
    assert [candidate.index for candidate in result.candidates] == [1, 2, 3]
    assert "DIVERSITY RECOVERY ATTEMPT 2" not in result.prompts[0]
    assert all(
        "DIVERSITY RECOVERY ATTEMPT 2" in prompt
        for prompt in result.prompts[1:]
    )


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
    assert len(set(result.prompts)) == 3


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


def test_scene_plate_generation_contract_binds_model_gate_and_actor_fingerprint():
    from app.core.config import Settings
    from app.features.semantic_videos.visual_contract import (
        build_scene_plate_generation_contract,
        validate_scene_plate_generation_contract,
    )

    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_key="x",
        supabase_service_key="y",
        cloudflare_r2_public_base_url="https://r2.example.com",
    )
    contract = build_scene_plate_generation_contract(
        actor_reference_fingerprint="a" * 64,
        settings=settings,
    )

    assert contract["model"] == "gemini-3-pro-image"
    assert contract["image_size"] == "2K"
    assert contract["reference_roles"] == [
        "actor_front",
        "actor_three_quarter",
        "actor_free_location",
    ]
    assert contract["minimum_identity_confidence"] == 0.90
    assert len(contract["contract_hash"]) == 64
    assert (
        validate_scene_plate_generation_contract(
            contract,
            actor_reference_fingerprint="a" * 64,
            settings=settings,
        )
        == contract
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
        SCENE_IDENTITY_COMPONENT_FIELDS,
        build_actor_reference_fingerprint,
        build_scene_plate_generation_contract,
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
    generation_contract = build_scene_plate_generation_contract(
        actor_reference_fingerprint=actor_fingerprint
    )
    reference["scene_plate_generation_contract"] = generation_contract
    master_hash = sha256(b"scene-plate").hexdigest()
    scene_plate = {
        "index": 1,
        "storage_uri": "https://cdn/scene-plate.png",
        "mime_type": "image/png",
        "byte_length": 11,
        "sha256": master_hash,
        "provider_model": generation_contract["model"],
        "visual_contract_hash": contract["contract_hash"],
        "generation_contract_hash": generation_contract["contract_hash"],
        "actor_reference_fingerprint": actor_fingerprint,
        "identity_gate_result": {
            "status": "passed",
            "passed": True,
            "evaluator_model": generation_contract["identity_evaluator_model"],
            "evaluator_contract_version": generation_contract[
                "identity_evaluator_contract_version"
            ],
            "evaluated_actor_reference_fingerprint": actor_fingerprint,
            "candidate_sha256": master_hash,
            "component_results": {
                field: True for field in SCENE_IDENTITY_COMPONENT_FIELDS
            },
            "confidence": 0.99,
            "blocking_reasons": [],
            "observed_differences": [],
        },
        "derivation_mode": "bootstrap",
        "canonical_anchor_id": None,
        "canonical_anchor_sha256": None,
    }

    _assert_scene_plate_master(
        reference_snapshot=reference,
        master_snapshot=scene_plate,
    )
    scene_plate["storage_uri"] = "https://cdn/scene-plate.jpg"
    scene_plate["mime_type"] = "image/jpeg"
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
