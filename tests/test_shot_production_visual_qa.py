from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
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


def _image(image_bytes: bytes, mime_type: str = "image/png") -> dict:
    return {"mime_type": mime_type, "image_bytes": image_bytes}


def _response(**overrides) -> str:
    payload = {
        "identity_same_person": True,
        "apparent_age_consistent": True,
        "hair_consistent": True,
        "wardrobe_consistent": True,
        "room_consistent": True,
        "framing_stable": True,
        "no_artifacts": True,
        "confidence": 0.93,
        "blocking_reasons": [],
        "observed_differences": ["Minor natural expression changes across frames."],
        "passed": False,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_visual_qa_returns_frozen_typed_report_and_sends_master_before_contact_sheet():
    from app.features.shot_production.visual_qa import (
        VisualQAReport,
        evaluate_visual_consistency,
    )

    master = _image(b"approved-master")
    contact_sheet = _image(b"labeled-contact-sheet", "image/jpeg")
    llm = _FakeLLM(_response())

    report = evaluate_visual_consistency(
        master,
        contact_sheet,
        llm_client=llm,
        model="gemini-2.5-flash",
    )

    assert [field.name for field in fields(VisualQAReport)] == [
        "identity_same_person",
        "apparent_age_consistent",
        "hair_consistent",
        "wardrobe_consistent",
        "room_consistent",
        "framing_stable",
        "no_artifacts",
        "confidence",
        "blocking_reasons",
        "observed_differences",
        "passed",
    ]
    assert report == VisualQAReport(
        identity_same_person=True,
        apparent_age_consistent=True,
        hair_consistent=True,
        wardrobe_consistent=True,
        room_consistent=True,
        framing_stable=True,
        no_artifacts=True,
        confidence=0.93,
        blocking_reasons=(),
        observed_differences=("Minor natural expression changes across frames.",),
        passed=True,
    )
    assert llm.calls[0]["input_images"] == [master, contact_sheet]
    assert llm.calls[0]["model"] == "gemini-2.5-flash"
    assert llm.calls[0]["temperature"] == 0
    prompt = llm.calls[0]["prompt"]
    assert "Image 1 is the approved master" in prompt
    assert "Image 2 is the labeled multi-frame contact sheet" in prompt
    assert "same person" in prompt
    assert "apparent age" in prompt
    assert "hair" in prompt
    assert "cream sweater" in prompt
    assert "room" in prompt
    assert "framing" in prompt
    assert "artifacts" in prompt
    assert "no face-recognition identification" in prompt
    assert "JSON only" in prompt
    with pytest.raises(FrozenInstanceError):
        report.passed = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "failed_component",
    [
        "identity_same_person",
        "apparent_age_consistent",
        "hair_consistent",
        "wardrobe_consistent",
        "room_consistent",
        "framing_stable",
        "no_artifacts",
    ],
)
def test_visual_qa_any_single_component_failure_blocks_even_at_full_confidence(failed_component):
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    llm = _FakeLLM(_response(**{failed_component: False, "confidence": 1.0, "passed": True}))

    report = evaluate_visual_consistency(
        _image(b"approved-master"),
        _image(b"contact-sheet"),
        llm_client=llm,
    )

    assert report.passed is False


@pytest.mark.parametrize(
    ("confidence", "expected_passed"),
    [(0.75, True), (0.749, False)],
)
def test_visual_qa_requires_confidence_threshold(confidence, expected_passed):
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    report = evaluate_visual_consistency(
        _image(b"approved-master"),
        _image(b"contact-sheet"),
        llm_client=_FakeLLM(_response(confidence=confidence)),
    )

    assert report.passed is expected_passed


def test_visual_qa_blocking_reasons_fail_an_otherwise_valid_report():
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    report = evaluate_visual_consistency(
        _image(b"approved-master"),
        _image(b"contact-sheet"),
        llm_client=_FakeLLM(
            _response(
                confidence=1.0,
                blocking_reasons=["A generated subtitle appears in frame 4."],
                passed=True,
            )
        ),
    )

    assert report.blocking_reasons == ("A generated subtitle appears in frame 4.",)
    assert report.passed is False


def test_visual_qa_accepts_json_in_one_markdown_fence():
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    llm = _FakeLLM(f"```json\n{_response()}\n```")

    report = evaluate_visual_consistency(
        _image(b"approved-master"),
        _image(b"contact-sheet"),
        llm_client=llm,
    )

    assert report.passed is True


def test_visual_qa_fails_closed_for_malformed_json():
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    with pytest.raises(ValidationError, match="valid JSON"):
        evaluate_visual_consistency(
            _image(b"approved-master"),
            _image(b"contact-sheet"),
            llm_client=_FakeLLM("not JSON"),
        )


@pytest.mark.parametrize(
    "missing_field",
    [
        "identity_same_person",
        "apparent_age_consistent",
        "hair_consistent",
        "wardrobe_consistent",
        "room_consistent",
        "framing_stable",
        "no_artifacts",
        "confidence",
        "blocking_reasons",
        "observed_differences",
    ],
)
def test_visual_qa_fails_closed_when_a_required_field_is_missing(missing_field):
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    payload = json.loads(_response())
    payload.pop(missing_field)

    with pytest.raises(ValidationError, match="schema"):
        evaluate_visual_consistency(
            _image(b"approved-master"),
            _image(b"contact-sheet"),
            llm_client=_FakeLLM(json.dumps(payload)),
        )


def test_visual_qa_fails_closed_when_json_is_not_an_object():
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    with pytest.raises(ValidationError, match="object"):
        evaluate_visual_consistency(
            _image(b"approved-master"),
            _image(b"contact-sheet"),
            llm_client=_FakeLLM("[]"),
        )


def test_visual_qa_rejects_fields_outside_the_strict_schema():
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    payload = json.loads(_response())
    payload["freeform_analysis"] = "Unrequested prose."

    with pytest.raises(ValidationError, match="schema"):
        evaluate_visual_consistency(
            _image(b"approved-master"),
            _image(b"contact-sheet"),
            llm_client=_FakeLLM(json.dumps(payload)),
        )


@pytest.mark.parametrize(
    "component",
    [
        "identity_same_person",
        "apparent_age_consistent",
        "hair_consistent",
        "wardrobe_consistent",
        "room_consistent",
        "framing_stable",
        "no_artifacts",
    ],
)
@pytest.mark.parametrize("invalid_value", [1, "true"])
def test_visual_qa_rejects_non_boolean_component_values(component, invalid_value):
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    with pytest.raises(ValidationError, match="boolean"):
        evaluate_visual_consistency(
            _image(b"approved-master"),
            _image(b"contact-sheet"),
            llm_client=_FakeLLM(_response(**{component: invalid_value})),
        )


def test_visual_qa_validates_but_does_not_trust_optional_model_passed_value():
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    with pytest.raises(ValidationError, match="boolean"):
        evaluate_visual_consistency(
            _image(b"approved-master"),
            _image(b"contact-sheet"),
            llm_client=_FakeLLM(_response(passed="yes")),
        )


@pytest.mark.parametrize(
    "invalid_confidence",
    [True, "0.9", None, -0.01, 1.01, float("nan"), float("inf")],
)
def test_visual_qa_rejects_invalid_confidence(invalid_confidence):
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    with pytest.raises(ValidationError, match="confidence"):
        evaluate_visual_consistency(
            _image(b"approved-master"),
            _image(b"contact-sheet"),
            llm_client=_FakeLLM(_response(confidence=invalid_confidence)),
        )


@pytest.mark.parametrize("field", ["blocking_reasons", "observed_differences"])
@pytest.mark.parametrize("invalid_value", ["not a list", ["valid", 7], None])
def test_visual_qa_rejects_reason_or_difference_values_that_are_not_string_lists(
    field,
    invalid_value,
):
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    with pytest.raises(ValidationError, match="lists of strings"):
        evaluate_visual_consistency(
            _image(b"approved-master"),
            _image(b"contact-sheet"),
            llm_client=_FakeLLM(_response(**{field: invalid_value})),
        )


@pytest.mark.parametrize(
    ("master", "contact_sheet"),
    [
        (_image(b"master", "application/octet-stream"), _image(b"contact")),
        (_image(b"master"), _image(b"contact", "text/plain")),
        (_image(b""), _image(b"contact")),
        (_image(b"master"), _image(b"")),
    ],
)
def test_visual_qa_rejects_invalid_or_empty_input_images_before_calling_model(
    master,
    contact_sheet,
):
    from app.features.shot_production.visual_qa import evaluate_visual_consistency

    llm = _FakeLLM(_response())
    with pytest.raises(ValidationError, match="image"):
        evaluate_visual_consistency(master, contact_sheet, llm_client=llm)

    assert llm.calls == []
