from hashlib import sha256
import json

import pytest

from app.core.errors import ValidationError


def test_edit_metrics_derive_true_shot_distribution_and_density():
    from app.features.shot_production.reference_comparison import derive_edit_metrics

    metrics = derive_edit_metrics(14.5, [7.25])

    assert metrics["cut_count"] == 1
    assert metrics["cut_density_per_second"] == pytest.approx(1 / 14.5)
    assert metrics["shot_durations_seconds"] == pytest.approx([7.25, 7.25])
    assert metrics["mean_shot_duration_seconds"] == pytest.approx(7.25)
    assert metrics["shot_duration_cv"] == pytest.approx(0.0)


def test_comparison_marks_two_shot_candidate_closer_than_four_take_control():
    from app.features.shot_production.reference_comparison import compare_edit_profiles

    report = compare_edit_profiles(
        reference=derive(53.823855, [9.566667, 19.233333, 27.433333, 37.0, 46.766667]),
        control=derive(14.5, [3.666667, 7.375, 11.458333]),
        candidate=derive(14.5, [7.25]),
    )

    assert report["candidate"]["cut_count"] == 1
    assert report["closer_to_reference_than_control"] is True
    assert report["candidate_reference_distance"] < report["control_reference_distance"]
    assert report["candidate_two_shot_gate"] == {"passed": True, "failure_reasons": []}


def derive(duration, cuts):
    from app.features.shot_production.reference_comparison import derive_edit_metrics

    return derive_edit_metrics(duration, cuts)


@pytest.mark.parametrize(
    ("duration", "cuts"),
    [
        (0.0, []),
        (10.0, [5.0, 4.0]),
        (10.0, [0.0]),
        (10.0, [10.0]),
    ],
)
def test_edit_metrics_reject_invalid_duration_or_cut_timeline(duration, cuts):
    from app.features.shot_production.reference_comparison import derive_edit_metrics

    with pytest.raises(ValidationError):
        derive_edit_metrics(duration, cuts)


def _write_valid_candidate_manifest(path, candidate_path):
    candidate_bytes = candidate_path.read_bytes()
    candidate_sha256 = sha256(candidate_bytes).hexdigest()
    script_text = "Jeder weiß genau: Normgerechte Rampen kosten Kraft."
    payload = {
        "run_id": "proof-run",
        "status": "uploaded",
        "script": {"text": script_text},
        "caption": {
            "captioned_path": str(candidate_path.resolve()),
            "sha256": candidate_sha256,
            "bytes": len(candidate_bytes),
        },
        "final_transcript_qa": {
            "expected_text": script_text,
            "actual_text": script_text,
            "word_error_rate": 0.0,
            "passed": True,
            "failure_reasons": [],
        },
        "seam_qa": {
            "gaps_seconds": [0.38],
            "passed": True,
            "failure_reasons": [],
        },
        "acoustic_seam_qa": {
            "passed": True,
            "deterministic_passed": True,
            "blocking_reasons": [],
            "deterministic_failure_reasons": [],
        },
        "visual_qa": {"passed": True, "blocking_reasons": []},
        "voice_qa": {"passed": True, "blocking_reasons": []},
        "media_qa": {"passed": True, "failure_reasons": []},
        "upload": {
            "storage_provider": "cloudflare_r2",
            "storage_key": "proof/candidate.mp4",
            "url": "https://example.invalid/proof/candidate.mp4",
            "sha256": candidate_sha256,
            "size": len(candidate_bytes),
        },
        "upload_verification": {
            "passed": True,
            "failure_reasons": [],
            "expected_sha256": candidate_sha256,
            "actual_sha256": candidate_sha256,
            "expected_size": len(candidate_bytes),
            "actual_size": len(candidate_bytes),
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _proof_metrics_for(path):
    name = path.name
    if name == "reference.mp4":
        return derive(53.823855, [9.566667, 19.233333, 27.433333, 37.0, 46.766667])
    if name == "control.mp4":
        return derive(14.5, [3.666667, 7.375, 11.458333])
    return derive(14.5, [7.25])


def test_artifact_bound_report_records_candidate_manifest_and_delivery_evidence(tmp_path):
    from app.features.shot_production.reference_comparison import (
        build_artifact_bound_report,
    )

    reference = tmp_path / "reference.mp4"
    control = tmp_path / "control.mp4"
    candidate = tmp_path / "candidate.mp4"
    manifest = tmp_path / "manifest.json"
    reference.write_bytes(b"reference-video")
    control.write_bytes(b"control-video")
    candidate.write_bytes(b"candidate-video")
    payload = _write_valid_candidate_manifest(manifest, candidate)

    report = build_artifact_bound_report(
        reference_path=reference,
        control_path=control,
        candidate_path=candidate,
        candidate_manifest_path=manifest,
        probe_fn=_proof_metrics_for,
    )

    assert report["artifacts"]["reference"]["sha256"] == sha256(b"reference-video").hexdigest()
    assert report["artifacts"]["control"]["bytes"] == len(b"control-video")
    assert report["artifacts"]["candidate"]["sha256"] == payload["caption"]["sha256"]
    assert report["candidate_manifest"]["artifact"]["bytes"] == manifest.stat().st_size
    assert report["candidate_manifest"]["run_id"] == "proof-run"
    assert report["candidate_evidence"]["final_transcript_qa"] == {
        "script_text": payload["script"]["text"],
        "expected_text": payload["script"]["text"],
        "actual_text": payload["script"]["text"],
        "word_error_rate": 0.0,
        "passed": True,
        "failure_reasons": [],
    }
    assert report["candidate_evidence"]["seam_gaps_seconds"] == [0.38]
    assert report["candidate_evidence"]["verdicts"] == {
        "acoustic": {"passed": True, "deterministic_passed": True},
        "visual": {"passed": True},
        "voice": {"passed": True},
        "media": {"passed": True},
    }
    delivery = report["candidate_evidence"]["delivery"]
    assert delivery["local_candidate"]["sha256"] == payload["caption"]["sha256"]
    assert delivery["upload"]["sha256"] == payload["caption"]["sha256"]
    assert delivery["upload"]["size"] == len(b"candidate-video")
    assert delivery["upload"]["passed"] is True
    assert delivery["remote_verification"]["actual_sha256"] == payload["caption"]["sha256"]
    assert delivery["remote_verification"]["actual_size"] == len(b"candidate-video")
    assert delivery["remote_verification"]["passed"] is True
    assert report["editorial_comparison"]["candidate_two_shot_gate"]["passed"] is True
    assert report["evidence_gate"] == {"passed": True, "failure_reasons": []}
    assert report["proof_gate"] == {"passed": True, "failure_reasons": []}


def test_artifact_bound_report_fails_closed_on_hash_path_and_missing_qa_evidence(tmp_path):
    from app.features.shot_production.reference_comparison import (
        build_artifact_bound_report,
    )

    reference = tmp_path / "reference.mp4"
    control = tmp_path / "control.mp4"
    candidate = tmp_path / "candidate.mp4"
    manifest = tmp_path / "manifest.json"
    reference.write_bytes(b"reference-video")
    control.write_bytes(b"control-video")
    candidate.write_bytes(b"candidate-video")
    payload = _write_valid_candidate_manifest(manifest, candidate)
    payload["caption"]["captioned_path"] = str((tmp_path / "different.mp4").resolve())
    payload["caption"]["sha256"] = "0" * 64
    payload["final_transcript_qa"]["word_error_rate"] = 0.25
    payload.pop("voice_qa")
    payload["upload_verification"]["passed"] = False
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    report = build_artifact_bound_report(
        reference_path=reference,
        control_path=control,
        candidate_path=candidate,
        candidate_manifest_path=manifest,
        probe_fn=_proof_metrics_for,
    )

    reasons = set(report["evidence_gate"]["failure_reasons"])
    assert {
        "candidate_caption_path_mismatch",
        "candidate_caption_sha256_mismatch",
        "final_transcript_wer_nonzero",
        "voice_qa_missing",
        "remote_verification_failed",
    } <= reasons
    assert report["evidence_gate"]["passed"] is False
    assert report["proof_gate"]["passed"] is False


def test_comparison_cli_requires_manifest_and_returns_nonzero_for_failed_proof(
    tmp_path, monkeypatch
):
    from scripts import compare_semantic_ugc_reference as cli

    base_args = [
        "--reference", str(tmp_path / "reference.mp4"),
        "--control", str(tmp_path / "control.mp4"),
        "--candidate", str(tmp_path / "candidate.mp4"),
    ]
    with pytest.raises(SystemExit) as exc_info:
        cli.main(base_args)
    assert exc_info.value.code == 2

    monkeypatch.setattr(
        cli,
        "build_artifact_bound_report",
        lambda **_kwargs: {
            "proof_gate": {"passed": False, "failure_reasons": ["voice_qa_missing"]}
        },
    )
    result = cli.main(
        [*base_args, "--candidate-manifest", str(tmp_path / "manifest.json")]
    )

    assert result == 1
