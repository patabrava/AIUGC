from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from app.core.errors import ThirdPartyError, ValidationError


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate_gemini_text(self, **kwargs):
        self.calls.append(kwargs)
        response = self.payload[len(self.calls) - 1] if isinstance(self.payload, list) else self.payload
        if isinstance(response, Exception):
            raise response
        return response


def _image(marker: bytes, mime_type: str = "image/png") -> dict:
    return {
        "mime_type": mime_type,
        "image_bytes": marker,
        "byte_length": len(marker),
        "sha256": sha256(marker).hexdigest(),
    }


def _scene_payload(**updates) -> str:
    payload = {
        "same_person": True,
        "facial_geometry_consistent": True,
        "apparent_age_consistent": True,
        "hairline_and_hair_consistent": True,
        "skin_texture_natural": True,
        "not_beautified_or_stylized": True,
        "no_face_artifacts": True,
        "confidence": 0.94,
        "blocking_reasons": [],
        "observed_differences": ["Different wardrobe and room."],
    }
    payload.update(updates)
    return json.dumps(payload)


def test_scene_identity_gate_uses_fixed_original_reference_order_and_server_pass():
    from app.features.shot_frames.identity_qa import evaluate_scene_plate_identity

    front = _image(b"front")
    support = _image(b"support")
    candidate = _image(b"candidate")
    llm = _FakeLLM(_scene_payload())

    report = evaluate_scene_plate_identity(
        front,
        support,
        candidate,
        llm_client=llm,
        model="gemini-2.5-pro",
        minimum_confidence=0.90,
        location="global",
    )

    assert report.passed is True
    assert llm.calls[0]["input_images"] == [
        {"mime_type": "image/png", "image_bytes": b"front"},
        {"mime_type": "image/png", "image_bytes": b"support"},
        {"mime_type": "image/png", "image_bytes": b"candidate"},
    ]
    assert llm.calls[0]["location"] == "global"
    assert "immutable original references" in llm.calls[0]["prompt"]
    assert "confidence in actor-identity preservation only" in llm.calls[0]["prompt"]
    assert "do not reduce it for an expected change of room" in llm.calls[0]["prompt"]
    assert "one continuous camera frame" in llm.calls[0]["prompt"]
    assert "composite_layout" in llm.calls[0]["prompt"]


def test_deadline_bounded_scene_identity_disables_adapter_retries():
    from app.features.shot_frames.identity_qa import evaluate_scene_plate_identity

    llm = _FakeLLM(_scene_payload())
    evaluate_scene_plate_identity(
        _image(b"front"),
        _image(b"support"),
        _image(b"candidate"),
        llm_client=llm,
        model="gemini-2.5-pro",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=2),
    )

    assert llm.calls[0]["provider_max_attempts"] == 1
    assert 0 < llm.calls[0]["timeout_seconds"] <= 45


@pytest.mark.parametrize(
    "updates",
    [
        {"same_person": False, "confidence": 1.0},
        {"not_beautified_or_stylized": False, "confidence": 1.0},
        {"confidence": 0.899},
        {"blocking_reasons": ["changed_hairline"], "confidence": 1.0},
    ],
)
def test_scene_identity_gate_fails_when_any_server_condition_fails(updates):
    from app.features.shot_frames.identity_qa import evaluate_scene_plate_identity

    report = evaluate_scene_plate_identity(
        _image(b"front"),
        _image(b"support"),
        _image(b"candidate"),
        llm_client=_FakeLLM(_scene_payload(**updates)),
        model="gemini-2.5-pro",
    )

    assert report.passed is False


def test_scene_identity_gate_rejects_model_supplied_passed_and_malformed_json():
    from app.features.shot_frames.identity_qa import evaluate_scene_plate_identity

    payload = json.loads(_scene_payload())
    payload["passed"] = True
    with pytest.raises(ValidationError, match="strict schema"):
        evaluate_scene_plate_identity(
            _image(b"front"),
            _image(b"support"),
            _image(b"candidate"),
            llm_client=_FakeLLM(json.dumps(payload)),
            model="gemini-2.5-pro",
        )
    with pytest.raises(ValidationError, match="valid JSON"):
        evaluate_scene_plate_identity(
            _image(b"front"),
            _image(b"support"),
            _image(b"candidate"),
            llm_client=_FakeLLM("broken"),
            model="gemini-2.5-pro",
        )


def test_scene_identity_gate_rejects_changed_candidate_bytes_before_model_call():
    from app.features.shot_frames.identity_qa import evaluate_scene_plate_identity

    candidate = _image(b"candidate")
    candidate["sha256"] = "0" * 64
    llm = _FakeLLM(_scene_payload())

    with pytest.raises(ValidationError, match="hash changed"):
        evaluate_scene_plate_identity(
            _image(b"front"),
            _image(b"support"),
            candidate,
            llm_client=llm,
            model="gemini-2.5-pro",
        )
    assert llm.calls == []


def test_video_identity_gate_adds_speech_transition_contract():
    from app.features.shot_frames.identity_qa import evaluate_video_actor_identity

    payload = json.loads(_scene_payload())
    payload["speech_facial_transitions_natural"] = True
    llm = _FakeLLM(json.dumps(payload))

    report = evaluate_video_actor_identity(
        _image(b"front"),
        _image(b"support"),
        _image(b"contact", "image/jpeg"),
        llm_client=llm,
        model="gemini-2.5-pro",
    )

    assert report.passed is True
    assert report.speech_facial_transitions_natural is True
    assert "speech transitions" in llm.calls[0]["prompt"]
    assert "confidence in actor-identity preservation only" in llm.calls[0]["prompt"]
    assert "0.90 through 1.00" in llm.calls[0]["prompt"]
    assert "outside this actor-identity evaluation" in llm.calls[0]["prompt"]


def test_video_identity_gate_retries_an_internally_inconsistent_low_confidence_report():
    from app.features.shot_frames.identity_qa import evaluate_video_actor_identity

    low_confidence = json.loads(_scene_payload(confidence=0.78))
    low_confidence["speech_facial_transitions_natural"] = True
    corrected = {**low_confidence, "confidence": 0.92, "observed_differences": []}
    llm = _FakeLLM([json.dumps(low_confidence), json.dumps(corrected)])

    report = evaluate_video_actor_identity(
        _image(b"front"),
        _image(b"support"),
        _image(b"contact", "image/jpeg"),
        llm_client=llm,
        model="gemini-3.6-flash",
        minimum_confidence=0.90,
    )

    assert report.passed is True
    assert report.confidence == 0.92
    assert len(llm.calls) == 2
    assert "Consistency correction" in llm.calls[1]["prompt"]


@pytest.mark.parametrize("failure", [
    {"status_code": 429},
    {"status_code": 503},
    {"error_class": "ReadTimeout"},
])
def test_scene_identity_retries_service_failures_using_the_same_bytes(monkeypatch, failure):
    from app.features.shot_frames import identity_qa

    waits = []
    monkeypatch.setattr(identity_qa.time, "sleep", waits.append)
    monkeypatch.setattr(identity_qa.random, "uniform", lambda *_: 0.0)
    llm = _FakeLLM([ThirdPartyError("temporary", failure), ThirdPartyError("temporary", failure), _scene_payload()])
    report = identity_qa.evaluate_scene_plate_identity(
        _image(b"front"), _image(b"support"), _image(b"already-rendered"),
        llm_client=llm, model="gemini-2.5-flash",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=8),
    )
    assert report.passed is True
    assert len(llm.calls) == 3
    assert all(call["provider_max_attempts"] == 1 for call in llm.calls)
    assert all(call["input_images"] == llm.calls[0]["input_images"] for call in llm.calls)
    assert sum(waits) == (90.0 if failure.get("status_code") == 429 else 6.0)


@pytest.mark.parametrize("failure,expected_calls", [
    ({"status_code": 429}, 3),
    ({"status_code": 403}, 1),
    ({"status_code": 429, "retry_after_seconds": 500.0}, 1),
])
def test_scene_identity_retry_budget_and_retry_after_fail_closed(monkeypatch, failure, expected_calls):
    from app.features.shot_frames import identity_qa

    monkeypatch.setattr(identity_qa.time, "sleep", lambda _: None)
    monkeypatch.setattr(identity_qa.random, "uniform", lambda *_: 0.0)
    llm = _FakeLLM(ThirdPartyError("service failed", failure))
    with pytest.raises(ThirdPartyError, match="service failed"):
        identity_qa.evaluate_scene_plate_identity(
            _image(b"front"), _image(b"support"), _image(b"candidate"),
            llm_client=llm, model="gemini-2.5-flash",
            deadline_at=datetime.now(timezone.utc) + timedelta(minutes=8),
        )
    assert len(llm.calls) == expected_calls


def test_scene_identity_retry_respects_lease_loss_before_another_call(monkeypatch):
    from app.features.shot_frames import identity_qa

    checks = []
    def guard():
        checks.append(True)
        if len(checks) > 1:
            raise RuntimeError("lease lost")
    llm = _FakeLLM(ThirdPartyError("capacity", {"status_code": 429}))
    with pytest.raises(RuntimeError, match="lease lost"):
        identity_qa.evaluate_scene_plate_identity(
            _image(b"front"), _image(b"support"), _image(b"candidate"),
            llm_client=llm, model="gemini-2.5-flash", execution_guard=guard,
            deadline_at=datetime.now(timezone.utc) + timedelta(minutes=8),
        )
    assert len(llm.calls) == 1


def test_scene_identity_actual_identity_failure_is_not_service_retried():
    from app.features.shot_frames.identity_qa import evaluate_scene_plate_identity

    llm = _FakeLLM(_scene_payload(same_person=False, blocking_reasons=["different_person"]))
    report = evaluate_scene_plate_identity(
        _image(b"front"), _image(b"support"), _image(b"candidate"),
        llm_client=llm, model="gemini-2.5-flash",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=8),
    )
    assert report.passed is False
    assert len(llm.calls) == 1
