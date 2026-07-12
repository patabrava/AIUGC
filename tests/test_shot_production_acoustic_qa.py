import json

import pytest

from app.core.errors import ValidationError
from app.features.shot_production.acoustic_qa import evaluate_acoustic_seam_continuity


def _clips():
    return [{"mime_type": "audio/wav", "media_bytes": b"wav"} for _ in range(3)]


def _response(**overrides):
    payload = {
        "no_breath_restart": True,
        "no_duplicated_breath": True,
        "no_click": True,
        "no_room_tone_reset": True,
        "cadence_continuous": True,
        "speaker_continuous": True,
        "evidence_sufficient": True,
        "confidence": 0.94,
        "blocking_reasons": [],
        "observed_differences": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_gemini_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_acoustic_qa_passes_only_clean_three_seam_review():
    client = _Client(_response())
    report = evaluate_acoustic_seam_continuity(_clips(), llm_client=client)
    assert report.passed is True
    assert client.calls[0]["temperature"] == 0
    assert client.calls[0]["model"] == "gemini-2.5-flash"
    assert len(client.calls[0]["input_media"]) == 3


def test_acoustic_qa_fails_closed_on_breath_restart():
    report = evaluate_acoustic_seam_continuity(
        _clips(),
        llm_client=_Client(_response(no_breath_restart=False, blocking_reasons=["seam 2 restarts on an inhale"])),
    )
    assert report.passed is False


def test_acoustic_qa_requires_exact_schema_and_sufficient_confidence():
    with pytest.raises(ValidationError, match="schema"):
        evaluate_acoustic_seam_continuity(
            _clips(), llm_client=_Client(_response(extra=True))
        )
    report = evaluate_acoustic_seam_continuity(
        _clips(), llm_client=_Client(_response(confidence=0.84))
    )
    assert report.passed is False


def test_acoustic_qa_requires_three_nonempty_audio_clips():
    with pytest.raises(ValidationError, match="exactly three"):
        evaluate_acoustic_seam_continuity(_clips()[:2], llm_client=_Client(_response()))
