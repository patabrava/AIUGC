from __future__ import annotations

import json

import pytest

from app.core.errors import ValidationError


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def generate_gemini_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _audio(value: bytes = b"audio") -> dict:
    return {"mime_type": "audio/wav", "media_bytes": value}


def _response(**overrides) -> str:
    payload = {
        "same_speaker_across_takes": True,
        "vocal_timbre_consistent": True,
        "apparent_vocal_age_consistent": True,
        "german_accent_consistent": True,
        "evidence_sufficient": True,
        "delivery_style_consistent": True,
        "single_speaker_each_clip": True,
        "no_music": True,
        "no_background_voices": True,
        "outlier_take_indexes": [],
        "confidence": 0.96,
        "blocking_reasons": [],
        "observed_differences": ["Take 3 is slightly more emphatic."],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_voice_qa_returns_typed_report_and_sends_audio_in_take_order():
    from app.features.shot_production.voice_qa import evaluate_voice_consistency

    clips = [_audio(f"take-{index}".encode()) for index in range(4)]
    llm = _FakeLLM(_response())

    report = evaluate_voice_consistency(
        clips,
        llm_client=llm,
        model="gemini-2.5-flash",
    )

    assert report.passed is True
    assert report.same_speaker_across_takes is True
    assert report.vocal_timbre_consistent is True
    assert report.observed_differences == ("Take 3 is slightly more emphatic.",)
    assert llm.calls[0]["input_media"] == clips
    assert llm.calls[0]["model"] == "gemini-2.5-flash"
    assert "take order 0, 1, 2, 3" in llm.calls[0]["prompt"].lower()


@pytest.mark.parametrize("clip_count", [2, 7])
def test_voice_qa_accepts_dynamic_ordered_take_counts(clip_count):
    from app.features.shot_production.voice_qa import evaluate_voice_consistency

    clips = [_audio(f"take-{index}".encode()) for index in range(clip_count)]
    llm = _FakeLLM(_response())
    report = evaluate_voice_consistency(clips, llm_client=llm)

    assert report.passed is True
    assert f"zero-based take indexes from 0 through {clip_count - 1}" in llm.calls[0]["prompt"]


def test_voice_qa_uses_actual_dynamic_outlier_range():
    from app.features.shot_production.voice_qa import evaluate_voice_consistency

    clips = [_audio(f"take-{index}".encode()) for index in range(7)]
    report = evaluate_voice_consistency(
        clips,
        llm_client=_FakeLLM(_response(outlier_take_indexes=[6])),
    )
    assert report.outlier_take_indexes == (6,)
    assert report.passed is False

    with pytest.raises(ValidationError, match="0 through 6"):
        evaluate_voice_consistency(
            clips,
            llm_client=_FakeLLM(_response(outlier_take_indexes=[7])),
        )


@pytest.mark.parametrize(
    "failed_component",
    [
        "vocal_timbre_consistent",
        "same_speaker_across_takes",
        "apparent_vocal_age_consistent",
        "german_accent_consistent",
        "evidence_sufficient",
        "single_speaker_each_clip",
        "no_music",
        "no_background_voices",
    ],
)
def test_voice_qa_any_component_failure_blocks(failed_component):
    from app.features.shot_production.voice_qa import evaluate_voice_consistency

    report = evaluate_voice_consistency(
        [_audio(bytes([index + 1])) for index in range(4)],
        llm_client=_FakeLLM(_response(**{failed_component: False})),
    )

    assert report.passed is False


def test_voice_qa_delivery_variation_is_observed_but_not_blocking():
    from app.features.shot_production.voice_qa import evaluate_voice_consistency

    report = evaluate_voice_consistency(
        [_audio(bytes([index + 1])) for index in range(4)],
        llm_client=_FakeLLM(
            _response(
                delivery_style_consistent=False,
                observed_differences=["Take 3 is more emphatic."],
            )
        ),
    )

    assert report.delivery_style_consistent is False
    assert report.passed is True


def test_voice_qa_blocking_reason_low_confidence_or_outlier_fails_closed():
    from app.features.shot_production.voice_qa import evaluate_voice_consistency

    blocked = evaluate_voice_consistency(
        [_audio(bytes([index + 1])) for index in range(4)],
        llm_client=_FakeLLM(_response(blocking_reasons=["Take 2 has a different voice."])),
    )
    uncertain = evaluate_voice_consistency(
        [_audio(bytes([index + 1])) for index in range(4)],
        llm_client=_FakeLLM(_response(confidence=0.84)),
    )
    outlier = evaluate_voice_consistency(
        [_audio(bytes([index + 1])) for index in range(4)],
        llm_client=_FakeLLM(_response(outlier_take_indexes=[2])),
    )

    assert blocked.passed is False
    assert uncertain.passed is False
    assert outlier.passed is False
    assert outlier.outlier_take_indexes == (2,)


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        json.dumps([]),
        json.dumps({"vocal_timbre_consistent": True}),
        _response(extra_field=True),
        _response(confidence=float("nan")),
        _response(no_music="true"),
        _response(blocking_reasons="none"),
        _response(outlier_take_indexes=["2"]),
        _response(outlier_take_indexes=[2, 2]),
        _response(outlier_take_indexes=[4]),
    ],
)
def test_voice_qa_rejects_malformed_or_non_strict_responses(response):
    from app.features.shot_production.voice_qa import evaluate_voice_consistency

    with pytest.raises(ValidationError, match="Voice QA"):
        evaluate_voice_consistency(
            [_audio(bytes([index + 1])) for index in range(4)],
            llm_client=_FakeLLM(response),
        )


@pytest.mark.parametrize(
    "clips",
    [
        [_audio(b"one")],
        [_audio(bytes([index + 1])) for index in range(3)]
        + [{"mime_type": "image/png", "media_bytes": b"bad"}],
        [_audio(bytes([index + 1])) for index in range(3)]
        + [{"mime_type": "audio/wav", "media_bytes": b""}],
    ],
)
def test_voice_qa_requires_two_or_more_valid_audio_clips(clips):
    from app.features.shot_production.voice_qa import evaluate_voice_consistency

    llm = _FakeLLM(_response())
    with pytest.raises(ValidationError, match="Voice QA"):
        evaluate_voice_consistency(clips, llm_client=llm)
    assert llm.calls == []
