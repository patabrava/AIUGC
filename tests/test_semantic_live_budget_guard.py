from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from app.core.errors import ValidationError


APPROVED_BEAT = (
    "Jeder, der einen Rollstuhl nutzt, weiß genau: Normgerechte Rampen sind oft trotzdem eine echte Qual."
)


def _inputs(tmp_path: Path) -> tuple[Path, str, Path]:
    frame = tmp_path / "approved.png"
    frame.write_bytes(b"approved-frame")
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps({"script": f"{APPROVED_BEAT} Das ist der zweite Satz."}, ensure_ascii=False),
        encoding="utf-8",
    )
    return frame, sha256(frame.read_bytes()).hexdigest(), script


def _plan(tmp_path: Path, **overrides):
    from scripts.run_semantic_ugc_live_smoke import build_live_plan

    frame, digest, script = _inputs(tmp_path)
    values = {
        "approved_frame_path": frame,
        "expected_sha256": digest,
        "script_input_path": script,
        "output_dir": tmp_path / "proof",
        "max_budget_usd": "17.70",
        "max_submissions": 1,
        "output_count": 1,
        "retry_requested": False,
        "image_generation_collaborators": [],
    }
    values.update(overrides)
    return build_live_plan(**values)


def test_one_eight_second_full_model_audio_plan_costs_exactly_3_20(tmp_path):
    from scripts.run_semantic_ugc_live_smoke import validate_live_plan

    plan = _plan(tmp_path)
    validate_live_plan(plan)

    assert plan["model"] == "veo-3.1-generate-001"
    assert plan["duration_seconds"] == 8
    assert plan["aspect_ratio"] == "9:16"
    assert plan["generate_audio"] is True
    assert plan["output_count"] == 1
    assert plan["planned_take_count"] == 1
    assert plan["estimated_cost_usd"] == "3.20"
    assert plan["approved_beat"] == APPROVED_BEAT
    assert 14 <= plan["approved_beat_word_count"] <= 18


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"planned_take_count": 2}, "one planned take"),
        ({"output_count": 2}, "one output"),
        ({"approved_master_sha256": ""}, "approved master hash"),
        ({"max_budget_usd": "3.19"}, "budget"),
        ({"retry_requested": True}, "retry"),
        ({"image_generation_collaborators": ["gemini-image"]}, "image-generation"),
        ({"duration_seconds": 6}, "eight-second"),
        ({"model": "veo-3.1-fast-generate-001"}, "full Veo"),
    ],
)
def test_live_guard_rejects_any_contract_that_can_expand_spend(tmp_path, updates, message):
    from scripts.run_semantic_ugc_live_smoke import validate_live_plan

    plan = _plan(tmp_path)
    plan.update(updates)

    with pytest.raises(ValidationError, match=message):
        validate_live_plan(plan)


def test_live_guard_rejects_estimated_cost_above_absolute_17_70_cap(tmp_path):
    from scripts.run_semantic_ugc_live_smoke import validate_live_plan

    plan = _plan(tmp_path)
    plan["estimated_cost_usd"] = "17.71"
    plan["max_budget_usd"] = "99.00"

    with pytest.raises(ValidationError, match="absolute budget"):
        validate_live_plan(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_count", 2),
        ("duration_seconds", 6),
        ("model", "veo-3.1-fast-generate-001"),
        ("generate_audio", False),
        ("resolution", "1080p"),
        ("aspect_ratio", "16:9"),
        ("approved_master_sha256", "0" * 64),
    ],
)
def test_live_guard_rejects_rehashed_inner_request_contract_tampering(
    tmp_path, field, value
):
    from scripts.run_semantic_ugc_live_smoke import _canonical_sha256, validate_live_plan

    plan = _plan(tmp_path)
    plan["request_contract"][field] = value
    plan["request_sha256"] = _canonical_sha256(plan["request_contract"])

    with pytest.raises(ValidationError, match="request contract"):
        validate_live_plan(plan)


def test_intent_is_persisted_before_vertex_and_second_submission_is_blocked(tmp_path):
    from scripts.run_semantic_ugc_live_smoke import initialize_manifest, submit_once

    class FakeVertex:
        def __init__(self):
            self.submit_calls = 0

        def submit_image_video(self, **kwargs):
            self.submit_calls += 1
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert manifest["ledger"]["submission_attempts"] == 1
            assert manifest["submission"]["state"] == "intent_persisted"
            return {
                "operation_id": "projects/test/models/veo-3.1-generate-001/operations/one",
                "provider_model": "veo-3.1-generate-001",
                "status": "submitted",
            }

    plan = _plan(tmp_path)
    manifest_path = initialize_manifest(plan)
    vertex = FakeVertex()

    submitted = submit_once(manifest_path, vertex)
    assert submitted["submission"]["state"] == "accepted"
    assert vertex.submit_calls == 1

    with pytest.raises(ValidationError, match="second paid submission"):
        submit_once(manifest_path, vertex)
    assert vertex.submit_calls == 1


def test_dry_run_never_instantiates_vertex_or_other_paid_collaborators(tmp_path):
    from scripts.run_semantic_ugc_live_smoke import execute_live_proof

    plan = _plan(tmp_path)
    called = []

    result = execute_live_proof(
        plan,
        confirm_paid_plan=False,
        vertex_factory=lambda: called.append("vertex"),
    )

    assert result["status"] == "pending_paid_confirmation"
    assert result["ledger"]["submission_attempts"] == 0
    assert called == []
