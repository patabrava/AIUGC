from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
from threading import Lock
from time import sleep

import pytest
from PIL import Image, ImageDraw

from app.features.semantic_videos.visual_contract import SEMANTIC_WARDROBES
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


def _triptych_png() -> bytes:
    image = Image.new("RGB", (180, 320), (70, 90, 110))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 104, 179, 108), fill=(255, 255, 255))
    draw.rectangle((0, 211, 179, 215), fill=(255, 255, 255))
    draw.rectangle((15, 15, 80, 90), fill=(180, 120, 90))
    draw.rectangle((90, 125, 165, 195), fill=(80, 150, 105))
    draw.rectangle((30, 230, 145, 305), fill=(145, 85, 155))
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


def test_take_prompt_uses_first_frame_as_visual_authority_without_re_describing_it():
    from app.features.shot_production.prompts import build_veo_take_prompt

    beat = EditorialBeat(
        index=0,
        text="Dieser Alltagstipp macht den nächsten Schritt leichter.",
        word_count=8,
        estimated_speech_seconds=4.0,
        provider_duration_seconds=8,
    )

    prompt = build_veo_take_prompt(beat, visual_contract=_visual_contract())

    assert "animated from the supplied first frame" in prompt
    assert "source frame is the authority" in prompt
    assert "scene and background" in prompt
    assert "wardrobe, wheelchair, lighting, and visual style" in prompt
    assert "exact supplied garden patio" not in prompt
    assert "light-grey cardigan over a plain white top" not in prompt
    assert "cream knit sweater" not in prompt
    assert "home-office" not in prompt
    assert "consistent with the source-frame location" in prompt
    assert "Dialogue: “Dieser Alltagstipp" in prompt


@pytest.mark.parametrize(
    ("scene_description", "wardrobe_description"),
    [
        ("the exact supplied garden patio", "navy cotton blouse"),
        ("the exact supplied accessible bathroom", "cream knit sweater"),
        ("the exact supplied home office", "soft-beige blazer over a plain white top"),
    ],
)
def test_take_prompt_preserves_dynamic_scene_and_wardrobe_through_source_frame_authority(
    scene_description,
    wardrobe_description,
):
    from app.features.shot_production.prompts import build_veo_take_prompt

    contract = _visual_contract()
    contract["scene_description"] = scene_description
    contract["wardrobe_description"] = wardrobe_description
    beat = EditorialBeat(
        index=0,
        text="Dieser Hinweis bleibt eine rein gesprochene Erklärung.",
        word_count=7,
        estimated_speech_seconds=4.0,
        provider_duration_seconds=8,
    )

    prompt = build_veo_take_prompt(beat, visual_contract=contract)

    assert scene_description not in prompt
    assert wardrobe_description not in prompt
    assert "source frame is the authority" in prompt
    assert "scene and background" in prompt
    assert "wardrobe, wheelchair, lighting, and visual style" in prompt
    assert "consistent with the source-frame location" in prompt


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

        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_image(self, **kwargs):
            self.calls.append(kwargs)
            marker = next(
                f"plate-{index}".encode()
                for index in range(1, 4)
                if f"Candidate {index} composition:" in kwargs["prompt"]
            )
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
    assert all(call["model"] == "gemini-3.1-flash-image" for call in client.calls)
    assert all(call["provider_max_attempts"] == 1 for call in client.calls)
    assert all("system_prompt" not in call for call in client.calls)
    assert all(call["image_size"] == "2K" for call in client.calls)
    assert actor_front.image_bytes == b"front"
    assert actor_support.image_bytes == b"support"
    assert all("manual wheelchair" in call["prompt"] for call in client.calls)
    assert all("do not copy their clothing" in call["prompt"] for call in client.calls)
    assert all("visible pores" in call["prompt"] for call in client.calls)
    assert all("face averaging" in call["prompt"] for call in client.calls)
    assert len(set(result.prompts)) == 3
    assert all(
        f"Candidate {index} composition:" in prompt
        for index, prompt in enumerate(result.prompts, start=1)
    )


def test_scene_plate_fresh_candidates_use_one_multi_image_provider_request(monkeypatch):
    from app.features.shot_frames import wheelchair_scene_plate
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    monkeypatch.setattr(wheelchair_scene_plate, "_SCENE_PLATE_BUNDLE_ENABLED", True)

    class BundledClient:
        def __init__(self) -> None:
            self.bundle_calls: list[dict] = []

        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_images(self, **kwargs):
            self.bundle_calls.append(kwargs)
            return {
                "images": [
                    {"image_bytes": _png(block="left", value=70), "mime_type": "image/png"},
                    {"image_bytes": _png(block="right", value=130), "mime_type": "image/png"},
                    {"image_bytes": _png(block="left", value=200), "mime_type": "image/png"},
                ],
                "model": kwargs["model"],
            }

        def generate_gemini_image(self, **_kwargs):
            raise AssertionError("Fresh bundled generation must not buy a single-image call.")

    client = BundledClient()
    persisted_indexes: list[int] = []
    result = generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        llm_client=client,
        candidate_ready_callback=lambda candidate: persisted_indexes.append(candidate.index),
    )

    assert [candidate.index for candidate in result.candidates] == [1, 2, 3]
    assert sorted(persisted_indexes) == [1, 2, 3]
    assert len(client.bundle_calls) == 1
    call = client.bundle_calls[0]
    assert call["provider_max_attempts"] == 1
    assert call["image_size"] == "2K"
    assert "exactly 3 separate image parts" in call["prompt"]
    assert "Never combine them" in call["prompt"]
    assert [item["image_bytes"] for item in call["input_images"]] == [
        b"front",
        b"support",
        b"location",
    ]


def test_scene_plate_partial_multi_image_response_generates_only_missing_output(monkeypatch):
    from app.features.shot_frames import wheelchair_scene_plate
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    monkeypatch.setattr(wheelchair_scene_plate, "_SCENE_PLATE_BUNDLE_ENABLED", True)

    class PartialBundledClient:
        def __init__(self) -> None:
            self.single_indexes: list[int] = []

        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_images(self, **kwargs):
            return {
                "images": [
                    {"image_bytes": _png(block="left", value=70), "mime_type": "image/png"},
                    {"image_bytes": _png(block="right", value=130), "mime_type": "image/png"},
                ],
                "model": kwargs["model"],
            }

        def generate_gemini_image(self, **kwargs):
            index = next(
                candidate_index
                for candidate_index in range(1, 4)
                if f"Candidate {candidate_index} composition:" in kwargs["prompt"]
            )
            self.single_indexes.append(index)
            return {
                "image_bytes": _png(block="left", value=200),
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    client = PartialBundledClient()
    persisted_indexes: list[int] = []
    result = generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        llm_client=client,
        candidate_ready_callback=lambda candidate: persisted_indexes.append(candidate.index),
    )

    assert [candidate.index for candidate in result.candidates] == [1, 2, 3]
    assert client.single_indexes == [3]
    assert sorted(persisted_indexes) == [1, 2, 3]


def test_scene_plate_unsupported_bundle_falls_back_to_reliable_single_image_path(monkeypatch):
    from app.features.shot_frames import wheelchair_scene_plate
    from app.core.errors import ThirdPartyError
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    monkeypatch.setattr(wheelchair_scene_plate, "_SCENE_PLATE_BUNDLE_ENABLED", True)

    class UnsupportedBundleClient:
        def __init__(self) -> None:
            self.bundle_calls = 0
            self.single_indexes: list[int] = []

        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_images(self, **_kwargs):
            self.bundle_calls += 1
            raise ThirdPartyError(
                "Multi-image output is unavailable.",
                {"status_code": 400},
            )

        def generate_gemini_image(self, **kwargs):
            index = next(
                candidate_index
                for candidate_index in range(1, 4)
                if f"Candidate {candidate_index} composition:" in kwargs["prompt"]
            )
            self.single_indexes.append(index)
            return {
                "image_bytes": _png(
                    block="left" if index % 2 else "right",
                    value=60 + (index * 60),
                ),
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    client = UnsupportedBundleClient()
    phases: list[tuple[str, dict]] = []
    result = generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        llm_client=client,
        progress_callback=lambda phase, details: phases.append(
            (phase, dict(details))
        ),
    )

    assert [candidate.index for candidate in result.candidates] == [1, 2, 3]
    assert client.bundle_calls == 1
    assert sorted(client.single_indexes) == [1, 2, 3]
    assert any(details.get("bundle_fallback") is True for _phase, details in phases)


def test_scene_plate_composite_layout_detector_rejects_triptych_not_single_frame():
    from app.features.shot_frames.wheelchair_scene_plate import (
        scene_plate_has_composite_layout,
    )

    assert scene_plate_has_composite_layout(_triptych_png()) is True
    assert scene_plate_has_composite_layout(_png(block="left", value=120)) is False


def test_scene_plate_bundle_composite_falls_back_to_three_standalone_frames(monkeypatch):
    from app.features.shot_frames import wheelchair_scene_plate
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    monkeypatch.setattr(wheelchair_scene_plate, "_SCENE_PLATE_BUNDLE_ENABLED", True)

    class CompositeBundleClient:
        def __init__(self) -> None:
            self.bundle_calls = 0
            self.single_indexes: list[int] = []

        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_images(self, **kwargs):
            self.bundle_calls += 1
            return {
                "images": [
                    {"image_bytes": _triptych_png(), "mime_type": "image/png"},
                    {"image_bytes": _png(block="right", value=130), "mime_type": "image/png"},
                    {"image_bytes": _png(block="left", value=200), "mime_type": "image/png"},
                ],
                "model": kwargs["model"],
            }

        def generate_gemini_image(self, **kwargs):
            index = next(
                candidate_index
                for candidate_index in range(1, 4)
                if f"Candidate {candidate_index} composition:" in kwargs["prompt"]
            )
            self.single_indexes.append(index)
            return {
                "image_bytes": _png(
                    block="left" if index % 2 else "right",
                    value=40 + (index * 55),
                ),
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    client = CompositeBundleClient()
    result = generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        llm_client=client,
    )

    assert client.bundle_calls == 1
    assert sorted(client.single_indexes) == [1, 2, 3]
    assert all(
        not wheelchair_scene_plate.scene_plate_has_composite_layout(candidate.image_bytes)
        for candidate in result.candidates
    )


def test_scene_plate_standalone_composite_retries_only_affected_candidate(monkeypatch):
    from app.features.shot_frames import wheelchair_scene_plate
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    monkeypatch.setattr(wheelchair_scene_plate, "_SCENE_PLATE_BUNDLE_ENABLED", False)

    class CompositeThenValidClient:
        def __init__(self) -> None:
            self.attempts = {1: 0, 2: 0, 3: 0}

        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_image(self, **kwargs):
            index = next(
                candidate_index
                for candidate_index in range(1, 4)
                if f"Candidate {candidate_index} composition:" in kwargs["prompt"]
            )
            self.attempts[index] += 1
            if index == 2 and self.attempts[index] == 1:
                image_bytes = _triptych_png()
            else:
                image_bytes = _png(
                    block="left" if index % 2 else "right",
                    value=40 + (index * 55),
                )
            return {
                "image_bytes": image_bytes,
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    client = CompositeThenValidClient()
    result = generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        llm_client=client,
    )

    assert client.attempts == {1: 1, 2: 2, 3: 1}
    assert all(
        not wheelchair_scene_plate.scene_plate_has_composite_layout(candidate.image_bytes)
        for candidate in result.candidates
    )


def test_scene_plate_candidates_generate_with_bounded_concurrency_and_keep_candidate_order():
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    call_lock = Lock()
    call_number = 0

    class ConcurrentClient:
        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_image(self, **kwargs):
            nonlocal call_number
            with call_lock:
                call_number += 1
                marker = call_number
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


def test_scene_plate_candidates_retry_only_the_transiently_failed_candidate(monkeypatch):
    from app.core.errors import ThirdPartyError
    from app.features.shot_frames import wheelchair_scene_plate

    attempts: dict[int, int] = {}
    attempts_lock = Lock()

    class TransientClient:
        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_image(self, **kwargs):
            prompt = kwargs["prompt"]
            index = next(
                candidate_index
                for candidate_index in range(1, 4)
                if f"Candidate {candidate_index} composition:" in prompt
            )
            with attempts_lock:
                attempts[index] = attempts.get(index, 0) + 1
                attempt = attempts[index]
            if index == 2 and attempt == 1:
                raise ThirdPartyError(
                    "Vertex Gemini generateContent failed",
                    {"status_code": 503},
                )
            return {
                "image_bytes": f"candidate-{index}".encode(),
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    result = wheelchair_scene_plate.generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        llm_client=TransientClient(),
    )

    assert [candidate.index for candidate in result.candidates] == [1, 2, 3]
    assert attempts == {1: 1, 2: 2, 3: 1}


def test_scene_plate_checkpoint_failure_does_not_purchase_another_image():
    from app.core.errors import ThirdPartyError
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    attempts: dict[int, int] = {}
    attempts_lock = Lock()

    class SuccessfulProvider:
        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_image(self, **kwargs):
            index = next(
                candidate_index
                for candidate_index in range(1, 4)
                if f"Candidate {candidate_index} composition:" in kwargs["prompt"]
            )
            with attempts_lock:
                attempts[index] = attempts.get(index, 0) + 1
            return {
                "image_bytes": f"candidate-{index}".encode(),
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    def fail_checkpoint(candidate):
        if candidate.index == 2:
            raise ThirdPartyError("storage checkpoint failed", {"status_code": 503})

    try:
        generate_scene_plate_candidates(
            actor_references=[
                _reference("actor_front", b"front"),
                _reference("actor_three_quarter", b"support"),
            ],
            location_reference=_reference("location", b"location"),
            scene="the exact supplied garden patio",
            wardrobe="light-grey cardigan over a plain white top",
            llm_client=SuccessfulProvider(),
            candidate_ready_callback=fail_checkpoint,
        )
    except ThirdPartyError:
        pass
    else:
        raise AssertionError("Checkpoint failure should fail the request.")

    assert attempts == {1: 1, 2: 1, 3: 1}


def test_scene_plate_candidates_bound_image_render_bursts_across_candidate_threads(
    monkeypatch,
):
    from app.features.shot_frames import wheelchair_scene_plate

    active = 0
    peak_active = 0
    active_lock = Lock()

    class PeakTrackingClient:
        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_image(self, **kwargs):
            nonlocal active, peak_active
            with active_lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                sleep(0.03)
                prompt = kwargs["prompt"]
                index = next(
                    candidate_index
                    for candidate_index in range(1, 4)
                    if f"Candidate {candidate_index} composition:" in prompt
                )
                return {
                    "image_bytes": f"candidate-{index}".encode(),
                    "mime_type": "image/png",
                    "model": kwargs["model"],
                }
            finally:
                with active_lock:
                    active -= 1

    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_IMAGE_TRAFFIC_GATE",
        wheelchair_scene_plate._ScenePlateImageTrafficGate(),
    )
    wheelchair_scene_plate._SCENE_PLATE_IMAGE_TRAFFIC_GATE._adaptive_start_interval_seconds = 0.0
    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_START_INTERVAL_SECONDS",
        0.0,
    )
    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_SUCCESS_RAMP",
        1,
    )
    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_IMAGE_MAX_CONCURRENCY",
        2,
    )
    result = wheelchair_scene_plate.generate_scene_plate_candidates(
        actor_references=[
            _reference("actor_front", b"front"),
            _reference("actor_three_quarter", b"support"),
        ],
        location_reference=_reference("location", b"location"),
        scene="the exact supplied garden patio",
        wardrobe="light-grey cardigan over a plain white top",
        llm_client=PeakTrackingClient(),
        traffic_key="run-1",
    )

    assert [candidate.index for candidate in result.candidates] == [1, 2, 3]
    assert peak_active == 2


def test_scene_plate_traffic_gate_round_robins_every_waiting_run():
    from app.features.shot_frames.wheelchair_scene_plate import (
        _ScenePlateImageTrafficGate,
    )

    gate = _ScenePlateImageTrafficGate()
    waiter_a = object()
    waiter_b = object()
    waiter_c = object()
    gate._pending = [
        (waiter_a, "run-a"),
        (waiter_b, "run-b"),
        (waiter_c, "run-c"),
    ]

    gate._last_started_key = "run-a"
    assert gate._next_waiter_locked() is waiter_b
    gate._last_started_key = "run-b"
    assert gate._next_waiter_locked() is waiter_c
    gate._last_started_key = "run-c"
    assert gate._next_waiter_locked() is waiter_a


def test_scene_plate_traffic_gate_removes_a_waiter_at_job_deadline():
    from datetime import datetime, timedelta, timezone

    from app.core.errors import ThirdPartyError
    from app.features.shot_frames.wheelchair_scene_plate import (
        _ScenePlateImageTrafficGate,
    )

    gate = _ScenePlateImageTrafficGate()
    gate._active = 1

    with pytest.raises(ThirdPartyError, match="traffic wait") as exc_info:
        gate.acquire(
            "blocked-run",
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=54),
            execution_guard=lambda: None,
        )

    assert exc_info.value.details["reason_code"] == "scene_image_deadline"
    assert gate._pending == []
    assert gate._active == 1


def test_scene_plate_production_default_keeps_one_provider_render_in_flight():
    from app.features.shot_frames import wheelchair_scene_plate

    assert wheelchair_scene_plate._SCENE_PLATE_IMAGE_MAX_CONCURRENCY == 1


def test_three_simultaneous_posts_share_one_fair_provider_lane(monkeypatch):
    from app.features.shot_frames import wheelchair_scene_plate

    active = 0
    peak_active = 0
    active_lock = Lock()

    class PeakTrackingClient:
        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_image(self, **kwargs):
            nonlocal active, peak_active
            with active_lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                sleep(0.005)
                index = next(
                    candidate_index
                    for candidate_index in range(1, 4)
                    if f"Candidate {candidate_index} composition:" in kwargs["prompt"]
                )
                return {
                    "image_bytes": f"candidate-{index}".encode(),
                    "mime_type": "image/png",
                    "model": kwargs["model"],
                }
            finally:
                with active_lock:
                    active -= 1

    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_IMAGE_TRAFFIC_GATE",
        wheelchair_scene_plate._ScenePlateImageTrafficGate(),
    )
    wheelchair_scene_plate._SCENE_PLATE_IMAGE_TRAFFIC_GATE._adaptive_start_interval_seconds = 0.0
    monkeypatch.setattr(wheelchair_scene_plate, "_SCENE_PLATE_IMAGE_MAX_CONCURRENCY", 1)
    client = PeakTrackingClient()

    def generate_for_post(post_index):
        return wheelchair_scene_plate.generate_scene_plate_candidates(
            actor_references=[
                _reference("actor_front", b"front"),
                _reference("actor_three_quarter", b"support"),
            ],
            location_reference=_reference("location", b"location"),
            scene=f"the exact supplied room for post {post_index}",
            wardrobe="light-grey cardigan over a plain white top",
            llm_client=client,
            traffic_key=f"run-{post_index}",
        )

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(generate_for_post, range(3)))

    assert all(len(result.candidates) == 3 for result in results)
    assert peak_active == 1


def test_scene_plate_traffic_gate_ramps_after_one_healthy_render(monkeypatch):
    from app.features.shot_frames import wheelchair_scene_plate

    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_SUCCESS_RAMP",
        1,
    )
    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_IMAGE_MAX_CONCURRENCY",
        3,
    )
    gate = wheelchair_scene_plate._ScenePlateImageTrafficGate()
    gate._active = 1

    gate.release(succeeded=True, status_code=None)

    assert gate._current_limit == 2
    assert gate._healthy_successes == 0


def test_scene_plate_traffic_gate_spaces_next_start_after_response(monkeypatch):
    from app.features.shot_frames import wheelchair_scene_plate

    gate = wheelchair_scene_plate._ScenePlateImageTrafficGate()
    gate._active = 1
    monkeypatch.setattr(wheelchair_scene_plate.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_START_INTERVAL_SECONDS",
        5.0,
    )

    gate.release(succeeded=True, status_code=None)

    assert gate._next_start_at == 105.0


def test_scene_plate_traffic_gate_learns_wider_spacing_after_quota_error(
    monkeypatch,
):
    from app.features.shot_frames import wheelchair_scene_plate

    gate = wheelchair_scene_plate._ScenePlateImageTrafficGate()
    gate._active = 1
    monkeypatch.setattr(wheelchair_scene_plate.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(wheelchair_scene_plate.random, "uniform", lambda *_args: 0.0)
    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_START_INTERVAL_SECONDS",
        5.0,
    )
    monkeypatch.setattr(
        wheelchair_scene_plate,
        "_SCENE_PLATE_THROTTLE_COOLDOWN_SECONDS",
        30.0,
    )
    gate._adaptive_start_interval_seconds = 5.0

    gate.release(succeeded=False, status_code=429)

    assert gate._adaptive_start_interval_seconds == 15.0
    assert gate._next_start_at == 115.0
    assert gate._cooldown_until == 130.0


def test_scene_plate_candidates_do_not_retry_permanent_provider_contract_errors(
    monkeypatch,
):
    from app.core.errors import ThirdPartyError
    from app.features.shot_frames import wheelchair_scene_plate

    calls = 0

    class InvalidRequestClient:
        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_image(self, **kwargs):
            nonlocal calls
            calls += 1
            raise ThirdPartyError(
                "Vertex Gemini generateContent failed",
                {"status_code": 400},
            )

    monkeypatch.setattr(
        wheelchair_scene_plate.time,
        "sleep",
        lambda _seconds: None,
    )
    try:
        wheelchair_scene_plate.generate_scene_plate_candidates(
            actor_references=[
                _reference("actor_front", b"front"),
                _reference("actor_three_quarter", b"support"),
            ],
            location_reference=_reference("location", b"location"),
            scene="the exact supplied garden patio",
            wardrobe="light-grey cardigan over a plain white top",
            llm_client=InvalidRequestClient(),
        )
    except ThirdPartyError:
        pass
    else:
        raise AssertionError("Permanent provider error should fail the candidate set.")

    assert calls == 3


def test_scene_plate_candidate_progress_reports_real_generation_phases():
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    phases: list[tuple[str, dict]] = []

    class DistinctClient:
        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

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
        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

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


def test_scene_plate_candidates_keep_valid_images_when_diversity_recovery_is_exhausted():
    from app.features.shot_frames.wheelchair_scene_plate import (
        generate_scene_plate_candidates,
    )

    class ConvergingClient:
        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

        def generate_gemini_image(self, **kwargs):
            return {
                "image_bytes": _png(value=128),
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
        llm_client=ConvergingClient(),
    )

    assert [candidate.index for candidate in result.candidates] == [1, 2, 3]
    assert result.remaining_duplicate_candidate_indexes == (2, 3)
    assert result.diversity_recovery_exhausted is True
    assert all(
        "DIVERSITY RECOVERY ATTEMPT 3" in prompt
        for prompt in result.prompts[1:]
    )


def test_scene_plate_authorizes_then_rechecks_lease_and_deadline_after_gate(
    monkeypatch,
):
    from datetime import datetime, timedelta, timezone

    from app.features.shot_frames import wheelchair_scene_plate as module

    events: list[str] = []

    class Client:
        def generate_gemini_text(self, **_kwargs):
            events.append("prompt_writer")
            return "A complete raw camera scene prompt."

        def generate_gemini_image(self, **kwargs):
            events.append("paid_provider")
            assert kwargs["provider_timeout_seconds"] == 17.0
            return {
                "image_bytes": _png(),
                "mime_type": "image/png",
                "model": kwargs["model"],
            }

    monkeypatch.setattr(
        module._SCENE_PLATE_IMAGE_TRAFFIC_GATE,
        "acquire",
        lambda _key, **_kwargs: events.append("traffic_gate_acquired"),
    )
    monkeypatch.setattr(
        module._SCENE_PLATE_IMAGE_TRAFFIC_GATE,
        "release",
        lambda **_kwargs: events.append("traffic_gate_released"),
    )

    def deadline_timeout(**kwargs):
        if kwargs["cap_seconds"] == 45.0:
            events.append("prompt_deadline")
            return 20.0
        events.append("provider_deadline")
        return 17.0

    monkeypatch.setattr(module, "_deadline_timeout", deadline_timeout)

    module.generate_scene_plate(
        references=(
            _reference("identity_primary", b"front"),
            _reference("identity_support", b"support"),
            _reference("location", b"location"),
        ),
        prompt="Create one scene.",
        llm_client=Client(),
        traffic_key="run-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        execution_guard=lambda: events.append("lease_checked"),
        provider_attempt_callback=lambda: events.append("paid_authorized"),
    )

    assert events.index("traffic_gate_acquired") < events.index("paid_authorized")
    assert events.index("paid_authorized") < events.index(
        "lease_checked", events.index("paid_authorized")
    )
    assert events.index("paid_authorized") < events.index("provider_deadline")
    assert events.index("provider_deadline") < events.index("paid_provider")


def test_scene_plate_does_not_call_provider_when_lease_is_lost_during_authorization():
    from app.core.errors import ValidationError
    from app.features.shot_frames.wheelchair_scene_plate import generate_scene_plate

    active = {"value": True}
    provider_calls: list[str] = []

    class Client:
        def generate_gemini_text(self, **_kwargs):
            return "A complete raw camera scene prompt."

        def generate_gemini_image(self, **_kwargs):
            provider_calls.append("started")
            return {"image_bytes": _png(), "mime_type": "image/png", "model": "x"}

    def guard():
        if not active["value"]:
            raise ValidationError("lease lost")

    def authorize():
        active["value"] = False

    with pytest.raises(ValidationError, match="lease lost"):
        generate_scene_plate(
            references=(
                _reference("identity_primary", b"front"),
                _reference("identity_support", b"support"),
                _reference("location", b"location"),
            ),
            prompt="Create one scene.",
            llm_client=Client(),
            execution_guard=guard,
            provider_attempt_callback=authorize,
        )

    assert provider_calls == []


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

        def generate_gemini_text(self, **kwargs):
            return kwargs["prompt"]

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

    assert contract["model"] == "gemini-3.1-flash-image"
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
        select_semantic_wardrobe(
            post_id=f"post-{index}",
            rotation_index=index,
            rotation_seed="batch-1",
        )
        for index in range(3)
    ]

    assert len({key for key, _description in rotated}) == 3
    assert len({description for _key, description in rotated}) == 3
    assert select_semantic_wardrobe(
        post_id="post-override",
        rotation_index=0,
        rotation_seed="batch-1",
        wardrobe_description="navy blue cotton blouse",
    ) == ("custom", "navy blue cotton blouse")


def test_semantic_single_post_batches_do_not_all_default_to_cream_sweater():
    from app.features.semantic_videos.visual_contract import select_semantic_wardrobe

    selected = {
        select_semantic_wardrobe(
            post_id=f"post-{index}",
            rotation_index=0,
            rotation_seed=f"batch-{index}",
        )[0]
        for index in range(12)
    }

    assert selected == set(SEMANTIC_WARDROBES)


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
