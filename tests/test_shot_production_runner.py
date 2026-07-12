from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
from PIL import Image
import pytest

from app.adapters.deepgram_client import Word, WordLevelTranscript
from app.core.errors import ValidationError


SCRIPT = (
    "Jeder, der einen Rollstuhl nutzt, weiß genau: "
    "Normgerechte Rampen sind oft trotzdem eine echte Qual. "
    "Manchmal fühlt sich jeder Zentimeter Steigung wie ein unnötiger Kampf an. "
    "Das zehrt an den Kräften."
)


def _approved_png(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (90, 160))
    image.putdata(
        [
            (x * 255 // 89, y * 255 // 159, (x + y) % 256)
            for y in range(160)
            for x in range(90)
        ]
    )
    image.save(path, format="PNG")
    return sha256(path.read_bytes()).hexdigest()


def _script_input(path: Path, **overrides) -> None:
    payload = {
        "source": "app.features.topics.agents.generate_dialog_scripts",
        "target_length_tier": 16,
        "category": "problem_agitate_solution",
        "script": SCRIPT,
        "generator_output": {"problem_agitate_solution": [SCRIPT]},
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _initialize(tmp_path: Path):
    from app.features.shot_production.runner import initialize_pilot

    approved = tmp_path / "approved.png"
    approved_hash = _approved_png(approved)
    script_input = tmp_path / "script.json"
    _script_input(script_input)
    manifest = tmp_path / "run" / "manifest.json"
    payload = initialize_pilot(
        manifest_path=manifest,
        approved_frame_path=approved,
        expected_sha256=approved_hash,
        script_input_path=script_input,
        base_seed=240711,
    )
    return manifest, payload


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_initialize_pilot_fails_hash_before_any_provider_boundary(tmp_path):
    from app.features.shot_production.runner import initialize_pilot

    approved = tmp_path / "approved.png"
    _approved_png(approved)
    script_input = tmp_path / "script.json"
    _script_input(script_input)

    with pytest.raises(ValidationError, match="SHA-256"):
        initialize_pilot(
            manifest_path=tmp_path / "run" / "manifest.json",
            approved_frame_path=approved,
            expected_sha256="0" * 64,
            script_input_path=script_input,
            base_seed=1,
        )

    assert not (tmp_path / "run" / "manifest.json").exists()


def test_initialize_pilot_records_real_script_four_takes_and_complete_request_audit(tmp_path):
    manifest_path, payload = _initialize(tmp_path)

    assert payload == _read(manifest_path)
    assert payload["version"] == 2
    assert payload["status"] == "planned"
    assert payload["script"]["text"] == SCRIPT
    assert payload["script"]["source"] == "app.features.topics.agents.generate_dialog_scripts"
    assert payload["script"]["target_length_tier"] == 16
    assert [take["beat"]["text"] for take in payload["takes"]] == [
        "Jeder, der einen Rollstuhl nutzt, weiß genau:",
        "Normgerechte Rampen sind oft trotzdem eine echte Qual.",
        "Manchmal fühlt sich jeder Zentimeter Steigung wie ein unnötiger Kampf an.",
        "Das zehrt an den Kräften.",
    ]
    assert [take["duration_seconds"] for take in payload["takes"]] == [4, 6, 6, 4]
    assert [take["seed"] for take in payload["takes"]] == [240711, 240712, 240713, 240714]
    assert all(take["model"] == "veo-3.1-generate-001" for take in payload["takes"])
    assert all(take["aspect_ratio"] == "9:16" for take in payload["takes"])
    assert all(take["negative_prompt"] for take in payload["takes"])
    assert all(take["prompt"].count(take["beat"]["text"]) == 1 for take in payload["takes"])
    assert all(take["submission"] is None for take in payload["takes"])
    assert len(payload["request_contract_sha256"]) == 64
    assert len(payload["script"]["input_sha256"]) == 64
    assert payload["script"]["planned_provider_durations"] == [4, 6, 6, 4]
    assert len({take["shot"]["sha256"] for take in payload["takes"]}) == 4
    assert all(Path(take["shot"]["path"]).is_file() for take in payload["takes"])

    with pytest.raises(ValidationError, match="already exists"):
        _initialize(tmp_path)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"source": "manual"}, "app-generated"),
        ({"target_length_tier": 8}, "16-second"),
    ],
)
def test_initialize_pilot_rejects_unapproved_script_provenance_or_tier(tmp_path, override, message):
    from app.features.shot_production.runner import initialize_pilot

    approved = tmp_path / "approved.png"
    approved_hash = _approved_png(approved)
    script_input = tmp_path / "script.json"
    _script_input(script_input, **override)

    with pytest.raises(ValidationError, match=message):
        initialize_pilot(
            manifest_path=tmp_path / "run" / "manifest.json",
            approved_frame_path=approved,
            expected_sha256=approved_hash,
            script_input_path=script_input,
            base_seed=240711,
        )


class _SubmitClient:
    def __init__(self, *, fail_on_call: int | None = None):
        self.calls = []
        self.fail_on_call = fail_on_call

    def submit_image_video(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("simulated submit failure")
        return {
            "operation_id": f"operations/op-{kwargs['correlation_id'].split('_take_')[1].split('_')[0]}",
            "provider_model": kwargs["model"],
            "status": "submitted",
        }


def test_submission_persists_intent_and_blocks_ambiguous_paid_retry(tmp_path):
    from app.features.shot_production.runner import (
        reconcile_unknown_submission,
        reset_failed_take,
        submit_pending_takes,
    )

    manifest_path, _ = _initialize(tmp_path)
    first_client = _SubmitClient(fail_on_call=2)

    with pytest.raises(RuntimeError, match="simulated"):
        submit_pending_takes(manifest_path, first_client)

    after_failure = _read(manifest_path)
    assert after_failure["takes"][0]["operation"]["operation_id"] == "operations/op-0"
    assert after_failure["takes"][1]["operation"] is None
    assert after_failure["takes"][1]["submission"]["state"] == "unknown"
    assert after_failure["takes"][1]["submission"]["correlation_id"].endswith("_take_1_attempt_1")
    assert len(first_client.calls) == 2

    resumed_client = _SubmitClient()
    with pytest.raises(ValidationError, match="unresolved Vertex submission"):
        submit_pending_takes(manifest_path, resumed_client)
    assert resumed_client.calls == []

    with pytest.raises(ValidationError, match="reconciled"):
        reset_failed_take(
            manifest_path,
            index=1,
            reason="generic retry must not erase an ambiguous paid operation",
        )

    reconcile_unknown_submission(
        manifest_path,
        index=1,
        resolution="accepted",
        evidence="Recovered the accepted operation id from the provider request log.",
        operation_id="operations/recovered-1",
    )
    submit_pending_takes(manifest_path, resumed_client, max_inflight=4)
    completed = _read(manifest_path)

    assert [call["correlation_id"].split("_take_")[1].split("_")[0] for call in resumed_client.calls] == [
        "2",
        "3",
    ]
    assert [take["operation"]["operation_id"] for take in completed["takes"]] == [
        "operations/op-0",
        "operations/recovered-1",
        "operations/op-2",
        "operations/op-3",
    ]
    assert all("reference_images" not in call for call in first_client.calls + resumed_client.calls)
    assert all("video" not in call and "last_frame" not in call for call in first_client.calls + resumed_client.calls)

    no_op_client = _SubmitClient()
    submit_pending_takes(manifest_path, no_op_client, max_inflight=4)
    assert no_op_client.calls == []


def test_unknown_submission_requires_not_accepted_reconciliation_before_retry(tmp_path):
    from app.features.shot_production.runner import (
        reconcile_unknown_submission,
        reset_failed_take,
        submit_pending_takes,
    )

    manifest_path, _ = _initialize(tmp_path)
    with pytest.raises(RuntimeError, match="simulated"):
        submit_pending_takes(manifest_path, _SubmitClient(fail_on_call=1))

    with pytest.raises(ValidationError, match="reconciled"):
        reset_failed_take(manifest_path, index=0, reason="unsafe generic retry")

    reconcile_unknown_submission(
        manifest_path,
        index=0,
        resolution="not_accepted",
        evidence="Local configuration validation failed before the adapter issued any HTTP request.",
    )
    reset_failed_take(manifest_path, index=0, reason="confirmed pre-HTTP rejection")
    client = _SubmitClient()
    submit_pending_takes(manifest_path, client, max_inflight=1)

    saved = _read(manifest_path)["takes"][0]
    assert len(client.calls) == 1
    assert saved["attempt"] == 2
    assert saved["operation"]["operation_id"] == "operations/op-0"
    assert saved["attempt_history"][0]["submission"]["reconciliation"]["resolution"] == "not_accepted"


def test_pilot_run_lock_is_reentrant_but_rejects_a_second_process(tmp_path):
    from app.features.shot_production.runner import pilot_run_lock

    manifest_path, _ = _initialize(tmp_path)
    with pilot_run_lock(manifest_path):
        with pilot_run_lock(manifest_path):
            pass
        code = (
            "from pathlib import Path; "
            "from app.features.shot_production.runner import pilot_run_lock; "
            f"p=Path({str(manifest_path)!r}); "
            "ctx=pilot_run_lock(p); ctx.__enter__()"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

    assert result.returncode != 0
    assert "already active" in result.stderr


def test_atomic_manifest_write_fsyncs_file_and_parent_directory(monkeypatch, tmp_path):
    from app.features.shot_production.runner import _atomic_write_json

    real_fsync = os.fsync
    fsync_calls = []

    def recording_fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    path = tmp_path / "run" / "manifest.json"
    _atomic_write_json(path, {"version": 2})

    assert len(fsync_calls) == 2
    assert _read(path) == {"version": 2}


def test_submission_blocks_a_tampered_paid_request_contract(tmp_path):
    from app.features.shot_production.runner import submit_pending_takes

    manifest_path, _ = _initialize(tmp_path)
    payload = _read(manifest_path)
    payload["takes"][0]["prompt"] += " changed after approval"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    client = _SubmitClient()

    with pytest.raises(ValidationError, match="request contract changed"):
        submit_pending_takes(manifest_path, client)

    assert client.calls == []


def test_retry_refuses_to_orphan_a_nonfailed_paid_operation(tmp_path):
    from app.features.shot_production.runner import reset_failed_take, submit_pending_takes

    manifest_path, _ = _initialize(tmp_path)
    submit_pending_takes(manifest_path, _SubmitClient(), max_inflight=4)

    with pytest.raises(ValidationError, match="not in a retryable failed state"):
        reset_failed_take(manifest_path, index=0, reason="unsafe duplicate attempt")

    unchanged = _read(manifest_path)["takes"][0]
    assert unchanged["attempt"] == 1
    assert unchanged["operation"]["operation_id"] == "operations/op-0"


class _PollClient:
    def __init__(self):
        self.calls = []

    def check_operation_status(self, **kwargs):
        self.calls.append(kwargs["operation_id"])
        index = int(kwargs["operation_id"].rsplit("-", 1)[1])
        encoded = base64.b64encode(f"raw-{index}".encode()).decode()
        return {
            "done": True,
            "status": "completed",
            "video_uri": f"data:video/mp4;base64,{encoded}",
        }


class _WaveClient(_SubmitClient, _PollClient):
    def __init__(self):
        _SubmitClient.__init__(self)
        self.events = []

    def submit_image_video(self, **kwargs):
        index = int(kwargs["correlation_id"].split("_take_")[1].split("_")[0])
        self.events.append(("submit", index))
        return super().submit_image_video(**kwargs)

    def check_operation_status(self, **kwargs):
        index = int(kwargs["operation_id"].rsplit("-", 1)[1])
        self.events.append(("poll", index))
        encoded = base64.b64encode(f"raw-{index}".encode()).decode()
        return {
            "done": True,
            "status": "completed",
            "video_uri": f"data:video/mp4;base64,{encoded}",
        }


def test_generation_runs_in_two_operation_vertex_quota_waves(tmp_path):
    from app.features.shot_production.runner import generate_raw_takes_in_waves

    manifest_path, _ = _initialize(tmp_path)
    client = _WaveClient()

    generate_raw_takes_in_waves(
        manifest_path,
        client,
        max_inflight=2,
        sleep_fn=lambda _seconds: None,
        poll_interval_seconds=0,
        timeout_seconds=2,
    )

    assert client.events == [
        ("submit", 0),
        ("submit", 1),
        ("poll", 0),
        ("poll", 1),
        ("submit", 2),
        ("submit", 3),
        ("poll", 2),
        ("poll", 3),
    ]
    payload = _read(manifest_path)
    assert payload["status"] == "raw_completed"
    assert all(take["raw"] for take in payload["takes"])


def test_http_429_is_recorded_as_definitive_rejection_before_explicit_retry(tmp_path):
    from app.features.shot_production.runner import reset_failed_take, submit_pending_takes

    class _QuotaRejectedClient:
        def submit_image_video(self, **_kwargs):
            request = httpx.Request("POST", "https://vertex.example/predictLongRunning")
            response = httpx.Response(429, request=request, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
            raise httpx.HTTPStatusError("quota rejected", request=request, response=response)

    manifest_path, _ = _initialize(tmp_path)
    with pytest.raises(httpx.HTTPStatusError):
        submit_pending_takes(manifest_path, _QuotaRejectedClient())

    rejected = _read(manifest_path)["takes"][0]
    assert rejected["status"] == "submission_rejected"
    assert rejected["submission"]["state"] == "rejected"
    assert rejected["operation"] is None

    reset_failed_take(manifest_path, index=0, reason="Vertex explicitly rejected the request with HTTP 429")
    reset = _read(manifest_path)["takes"][0]
    assert reset["status"] == "planned"
    assert reset["attempt"] == 2


def test_poll_downloads_all_raw_takes_in_index_order_and_resumes(tmp_path):
    from app.features.shot_production.runner import poll_and_download_takes, submit_pending_takes

    manifest_path, _ = _initialize(tmp_path)
    submit_pending_takes(manifest_path, _SubmitClient(), max_inflight=4)
    client = _PollClient()
    poll_and_download_takes(
        manifest_path,
        client,
        sleep_fn=lambda _seconds: None,
        poll_interval_seconds=0,
        timeout_seconds=2,
    )

    payload = _read(manifest_path)
    assert client.calls == [f"operations/op-{index}" for index in range(4)]
    assert [Path(take["raw"]["path"]).read_bytes() for take in payload["takes"]] == [
        b"raw-0",
        b"raw-1",
        b"raw-2",
        b"raw-3",
    ]
    assert all(take["status"] == "completed" for take in payload["takes"])

    second_client = _PollClient()
    poll_and_download_takes(manifest_path, second_client, timeout_seconds=1)
    assert second_client.calls == []

    Path(payload["takes"][0]["raw"]["path"]).write_bytes(b"corrupt")
    repair_client = _PollClient()
    poll_and_download_takes(manifest_path, repair_client, timeout_seconds=1)
    repaired = _read(manifest_path)
    assert repair_client.calls == ["operations/op-0"]
    assert Path(repaired["takes"][0]["raw"]["path"]).read_bytes() == b"raw-0"


def _timed_transcript(text: str) -> WordLevelTranscript:
    words = []
    cursor = 0.2
    for raw in text.split():
        cleaned = raw.strip(".,:;!?")
        words.append(Word(word=cleaned, start=cursor, end=cursor + 0.28))
        cursor += 0.38
    return WordLevelTranscript(words=words, full_text=" ".join(word.word for word in words))


class _DeepgramByCall:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        return _timed_transcript(self.scripts[len(self.calls) - 1])


def _manifest_with_raw_takes(tmp_path: Path) -> Path:
    from app.features.shot_production.runner import submit_pending_takes

    manifest_path, _ = _initialize(tmp_path)
    submit_pending_takes(manifest_path, _SubmitClient(), max_inflight=4)
    payload = _read(manifest_path)
    raw_dir = manifest_path.parent / "raw"
    raw_dir.mkdir()
    for take in payload["takes"]:
        path = raw_dir / f"take-{take['index']}.mp4"
        video_bytes = f"clip-{take['index']}".encode()
        path.write_bytes(video_bytes)
        take["raw"] = {
            "path": str(path),
            "sha256": sha256(video_bytes).hexdigest(),
            "bytes": path.stat().st_size,
        }
        take["status"] = "completed"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


def test_take_transcription_records_real_word_windows_and_blocks_failed_take(tmp_path):
    from app.features.shot_production.runner import transcribe_and_validate_takes

    manifest_path = _manifest_with_raw_takes(tmp_path)
    beats = [take["beat"]["text"] for take in _read(manifest_path)["takes"]]
    transcribe_and_validate_takes(manifest_path, _DeepgramByCall(beats))
    payload = _read(manifest_path)
    assert all(take["transcript_qa"]["passed"] for take in payload["takes"])
    assert all(take["trim_window"]["start_seconds"] == 0.0 for take in payload["takes"])
    assert all(take["trim_window"]["source"] == "deepgram_word_window" for take in payload["takes"])

    failed_manifest = _manifest_with_raw_takes(tmp_path / "failed")
    wrong = list(beats)
    wrong[2] = "Völlig falscher Satz ohne erwartete Wörter."
    with pytest.raises(ValidationError, match="take indexes.*2"):
        transcribe_and_validate_takes(failed_manifest, _DeepgramByCall(wrong))
    failed = _read(failed_manifest)
    assert failed["takes"][2]["transcript_qa"]["passed"] is False
    assert "stitch" not in failed


def test_take_timing_migration_invalidates_cached_audio_and_delivery_without_retranscribing(tmp_path):
    from app.features.shot_production.runner import transcribe_and_validate_takes

    manifest_path = _manifest_with_raw_takes(tmp_path)
    beats = [take["beat"]["text"] for take in _read(manifest_path)["takes"]]
    transcribe_and_validate_takes(manifest_path, _DeepgramByCall(beats))
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["transcript_qa"].pop("first_word_start_seconds", None)
        take["trim_window"]["source"] = "deepgram_word_end"
    payload["voice_qa"] = {"passed": True}
    payload["stitch"] = {"sha256": "stale"}
    payload["seam_qa"] = {"passed": True}
    payload["caption"] = {"sha256": "stale"}
    payload["media_qa"] = {"passed": True}
    payload["upload"] = {"url": "https://example.test/stale.mp4"}
    payload["upload_verification"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    deepgram = _DeepgramByCall([])

    migrated = transcribe_and_validate_takes(manifest_path, deepgram)

    assert deepgram.calls == []
    assert all(take["trim_window"]["source"] == "deepgram_word_window" for take in migrated["takes"])
    for key in (
        "voice_qa",
        "stitch",
        "seam_qa",
        "caption",
        "media_qa",
        "upload",
        "upload_verification",
    ):
        assert key not in migrated


def _tiny_video(path: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=beige:s=90x160:r=24:d=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _valid_final_probe(duration: str = "16.0") -> dict:
    return {
        "streams": [
            {
                "index": 0,
                "codec_name": "h264",
                "codec_type": "video",
                "width": 720,
                "height": 1280,
                "r_frame_rate": "24/1",
            },
            {
                "index": 1,
                "codec_name": "aac",
                "codec_type": "audio",
                "sample_rate": "48000",
                "channels": 2,
                "r_frame_rate": "0/0",
            },
        ],
        "format": {
            "duration": duration,
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        },
    }


def test_contact_sheet_and_visual_gate_are_persisted_and_block_on_failure(tmp_path):
    from app.features.shot_production.runner import build_contact_sheet, run_visual_qa
    from app.features.shot_production.visual_qa import VisualQAReport

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    video = manifest_path.parent / "tiny.mp4"
    _tiny_video(video)
    video_sha = sha256(video.read_bytes()).hexdigest()
    for take in payload["takes"]:
        take["raw"]["path"] = str(video)
        take["raw"]["sha256"] = video_sha
        take["raw"]["bytes"] = video.stat().st_size
        take["transcript_qa"] = {"final_word_end_seconds": 0.8, "passed": True}
        take["trim_window"] = {"start_seconds": 0.0, "end_seconds": 1.0, "source": "deepgram_word_end"}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    contact = build_contact_sheet(manifest_path)
    assert Path(contact["path"]).is_file()
    with Image.open(contact["path"]) as sheet:
        assert sheet.width > sheet.height / 2

    calls = []

    def evaluator(master, sheet, **_kwargs):
        calls.append((master, sheet))
        return VisualQAReport(True, True, True, True, True, True, True, 0.95, (), (), True)

    report = run_visual_qa(manifest_path, evaluator=evaluator)
    assert report["passed"] is True
    assert calls[0][0]["image_bytes"] == Path(_read(manifest_path)["approved_master"]["path"]).read_bytes()
    assert calls[0][1]["image_bytes"] == Path(contact["path"]).read_bytes()

    Path(contact["path"]).write_bytes(b"corrupt contact sheet")
    rebuilt = build_contact_sheet(manifest_path)
    assert rebuilt["sha256"] != contact["sha256"] or Path(rebuilt["path"]).read_bytes() != b"corrupt contact sheet"

    failing_manifest = _manifest_with_raw_takes(tmp_path / "visual-fail")
    failing_payload = _read(failing_manifest)
    failing_payload["contact_sheet"] = contact
    failing_manifest.write_text(json.dumps(failing_payload), encoding="utf-8")

    def fail_evaluator(*_args, **_kwargs):
        return VisualQAReport(False, True, True, True, True, True, True, 0.99, ("face drift",), (), False)

    with pytest.raises(ValidationError, match="visual QA"):
        run_visual_qa(failing_manifest, evaluator=fail_evaluator)
    assert _read(failing_manifest)["visual_qa"]["passed"] is False


def test_voice_gate_extracts_full_takes_caches_by_contract_and_blocks_on_failure(tmp_path, monkeypatch):
    import app.features.shot_production.runner as runner_module
    from app.features.shot_production.runner import run_voice_qa
    from app.features.shot_production.voice_qa import VoiceQAReport

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for index, take in enumerate(payload["takes"]):
        take["transcript_qa"] = {"passed": True}
        take["trim_window"] = {
            "start_seconds": round(index * 0.1, 3),
            "end_seconds": round(1.0 + index * 0.1, 3),
            "source": "deepgram_word_window",
        }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    extraction_calls = []

    def extract_audio(source, destination, *, start_seconds, end_seconds):
        extraction_calls.append((source, destination, start_seconds, end_seconds))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"voice-{len(extraction_calls) - 1}".encode())

    evaluator_calls = []

    def evaluator(clips, **kwargs):
        evaluator_calls.append((clips, kwargs))
        return VoiceQAReport(
            same_speaker_across_takes=True,
            vocal_timbre_consistent=True,
            apparent_vocal_age_consistent=True,
            german_accent_consistent=True,
            evidence_sufficient=True,
            delivery_style_consistent=True,
            single_speaker_each_clip=True,
            no_music=True,
            no_background_voices=True,
            outlier_take_indexes=(),
            confidence=0.97,
            blocking_reasons=(),
            observed_differences=(),
            passed=True,
        )

    report = run_voice_qa(
        manifest_path,
        evaluator=evaluator,
        extract_audio_fn=extract_audio,
        model="gemini-2.5-flash",
    )

    assert report["passed"] is True
    assert [call[2:] for call in extraction_calls] == [
        (0.0, float(take["duration_seconds"])) for take in payload["takes"]
    ]
    assert [clip["media_bytes"] for clip in evaluator_calls[0][0]] == [
        b"voice-0",
        b"voice-1",
        b"voice-2",
        b"voice-3",
    ]
    assert evaluator_calls[0][1]["model"] == "gemini-2.5-flash"
    saved = _read(manifest_path)
    assert saved["voice_qa"]["passed"] is True
    assert saved["voice_qa"]["model"] == "gemini-2.5-flash"
    assert saved["voice_qa"]["rubric_version"] == "voice-continuity-v1"
    assert len(saved["voice_qa"]["clips"]) == 4
    assert all(Path(clip["path"]).is_file() for clip in saved["voice_qa"]["clips"])

    run_voice_qa(
        manifest_path,
        evaluator=evaluator,
        extract_audio_fn=extract_audio,
        model="gemini-2.5-flash",
    )
    assert len(extraction_calls) == 4
    assert len(evaluator_calls) == 1

    Path(saved["voice_qa"]["clips"][0]["path"]).write_bytes(b"corrupt")
    run_voice_qa(
        manifest_path,
        evaluator=evaluator,
        extract_audio_fn=extract_audio,
        model="gemini-2.5-flash",
    )
    assert len(extraction_calls) == 8
    assert len(evaluator_calls) == 2

    monkeypatch.setattr(runner_module, "VOICE_QA_RUBRIC_VERSION", "voice-continuity-v2")
    revised_report = run_voice_qa(
        manifest_path,
        evaluator=evaluator,
        extract_audio_fn=extract_audio,
        model="gemini-2.5-flash",
    )
    assert len(extraction_calls) == 12
    assert len(evaluator_calls) == 3
    assert revised_report["rubric_version"] == "voice-continuity-v2"

    failing_manifest = _manifest_with_raw_takes(tmp_path / "voice-fail")
    failing_payload = _read(failing_manifest)
    for take in failing_payload["takes"]:
        take["transcript_qa"] = {"passed": True}
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 1.0,
            "source": "deepgram_word_window",
        }
    failing_manifest.write_text(json.dumps(failing_payload), encoding="utf-8")

    def fail_evaluator(*_args, **_kwargs):
        return VoiceQAReport(
            same_speaker_across_takes=False,
            vocal_timbre_consistent=False,
            apparent_vocal_age_consistent=True,
            german_accent_consistent=True,
            evidence_sufficient=True,
            delivery_style_consistent=True,
            single_speaker_each_clip=True,
            no_music=True,
            no_background_voices=True,
            outlier_take_indexes=(2,),
            confidence=0.99,
            blocking_reasons=("Take 2 has a different vocal timbre.",),
            observed_differences=(),
            passed=False,
        )

    with pytest.raises(ValidationError, match="voice QA"):
        run_voice_qa(
            failing_manifest,
            evaluator=fail_evaluator,
            extract_audio_fn=extract_audio,
        )
    assert _read(failing_manifest)["voice_qa"]["passed"] is False


def test_default_voice_extractor_creates_mono_16khz_pcm_wav(tmp_path):
    from app.features.shot_production.runner import _extract_voice_clip

    source = tmp_path / "source.mp4"
    destination = tmp_path / "qa" / "voice.wav"
    _tiny_video(source)

    _extract_voice_clip(source, destination, start_seconds=0.0, end_seconds=1.0)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            str(destination),
        ],
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream == {"codec_name": "pcm_s16le", "sample_rate": "16000", "channels": 1}


def test_voice_failed_outlier_requires_explicit_targeted_retry_and_archives_report(tmp_path):
    from app.features.shot_production.runner import reset_failed_take

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["status"] = "transcribed"
        take["transcript_qa"] = {"passed": True}
    payload["status"] = "voice_qa_failed"
    payload["voice_qa"] = {
        "passed": False,
        "outlier_take_indexes": [2],
        "blocking_reasons": ["Take 2 has a different vocal timbre."],
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="retryable failed state"):
        reset_failed_take(manifest_path, index=1, reason="operator selected the wrong take")

    reset = reset_failed_take(
        manifest_path,
        index=2,
        reason="voice QA identified take 2 as the vocal outlier",
    )

    assert reset["takes"][2]["status"] == "planned"
    assert "voice_qa" not in reset
    assert reset["qa_failure_history"][-1]["stage"] == "voice_qa"
    assert reset["qa_failure_history"][-1]["selected_take_indexes"] == [2]


def test_batch_voice_retry_archives_one_report_and_plans_only_selected_outliers(tmp_path):
    from app.features.shot_production.runner import reset_voice_failed_takes

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    original_raw = [take["raw"] for take in payload["takes"]]
    for take in payload["takes"]:
        take["status"] = "transcribed"
        take["transcript_qa"] = {"passed": True}
    payload["status"] = "voice_qa_failed"
    payload["voice_qa"] = {
        "passed": False,
        "outlier_take_indexes": [1, 3],
        "blocking_reasons": ["Takes 1 and 3 do not match the reference vocal timbre."],
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    reset = reset_voice_failed_takes(
        manifest_path,
        indexes=[1, 3],
        reason="operator approved retry of both voice outliers",
    )

    assert reset["takes"][0]["raw"] == original_raw[0]
    assert reset["takes"][2]["raw"] == original_raw[2]
    assert reset["takes"][1]["status"] == "planned"
    assert reset["takes"][3]["status"] == "planned"
    assert reset["takes"][1]["raw"] is None
    assert reset["takes"][3]["raw"] is None
    voice_history = [
        item for item in reset["qa_failure_history"] if item["stage"] == "voice_qa"
    ]
    assert len(voice_history) == 1
    assert voice_history[0]["selected_take_indexes"] == [1, 3]


def test_compose_orders_takes_uses_trim_windows_and_captions_once(tmp_path):
    from app.features.shot_production.runner import compose_and_caption

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    beats = [take["beat"]["text"] for take in payload["takes"]]
    for take, beat in zip(payload["takes"], beats):
        take["transcript"] = {
            "full_text": beat,
            "words": [word.__dict__ for word in _timed_transcript(beat).words],
        }
        take["transcript_qa"] = {
            "passed": True,
            "first_word_start_seconds": 0.2,
            "final_word_end_seconds": 1.0,
        }
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 1.25,
            "source": "deepgram_word_window",
        }
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    stitch_calls = []

    def stitch_fn(**kwargs):
        stitch_calls.append(kwargs)
        return b"stitched-video", {"stitch_final_duration_s": 5.4, "stitch_segment_count": 4}

    caption_calls = []

    def caption_fn(**kwargs):
        caption_calls.append(kwargs)
        output = manifest_path.parent / "fake-caption-output.mp4"
        output.write_bytes(b"captioned-video")
        return str(output)

    result = compose_and_caption(
        manifest_path,
        _DeepgramByCall([SCRIPT]),
        stitch_fn=stitch_fn,
        caption_fn=caption_fn,
        probe_fn=lambda _path: _valid_final_probe(),
    )

    assert stitch_calls[0]["segment_videos"] == [b"clip-0", b"clip-1", b"clip-2", b"clip-3"]
    assert stitch_calls[0]["trim_windows"] == [take["trim_window"] for take in payload["takes"]]
    assert len(caption_calls) == 1
    expected_caption_text = " ".join(word.strip(".,:;!?") for word in SCRIPT.split())
    assert caption_calls[0]["transcript"].full_text == expected_caption_text
    assert Path(result["captioned_path"]).read_bytes() == b"captioned-video"
    saved = _read(manifest_path)
    assert saved["stitch"]["metadata"]["stitch_segment_count"] == 4
    assert saved["caption"]["sha256"]

    Path(saved["caption"]["captioned_path"]).write_bytes(b"corrupt caption")
    repaired = compose_and_caption(
        manifest_path,
        _DeepgramByCall([SCRIPT]),
        stitch_fn=stitch_fn,
        caption_fn=caption_fn,
        probe_fn=lambda _path: _valid_final_probe(),
    )
    assert len(stitch_calls) == 2
    assert len(caption_calls) == 2
    assert Path(repaired["captioned_path"]).read_bytes() == b"captioned-video"
    repair_history = _read(manifest_path)["composition_history"]
    assert repair_history[-1]["reason"] == "automatic rebuild of invalid cached caption delivery"


def test_compose_rejects_legacy_trim_windows_from_direct_callers(tmp_path):
    from app.features.shot_production.runner import compose_and_caption

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["transcript_qa"] = {"passed": True, "final_word_end_seconds": 1.0}
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 1.3,
            "source": "deepgram_word_end",
        }
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="current Deepgram speech windows"):
        compose_and_caption(manifest_path, _DeepgramByCall([SCRIPT]))


@pytest.mark.parametrize(
    "final_text",
    [
        SCRIPT.replace("echte Qual", "echte Belastung"),
        SCRIPT.replace("echte Qual", "echte große Qual"),
        SCRIPT.replace("echte Qual", "Qual"),
    ],
)
def test_compose_requires_exact_final_dialogue_without_substitution_insertion_or_deletion(
    tmp_path,
    final_text,
):
    from app.features.shot_production.runner import compose_and_caption

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["transcript_qa"] = {
            "passed": True,
            "first_word_start_seconds": 0.2,
            "final_word_end_seconds": 1.0,
        }
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 1.25,
            "source": "deepgram_word_window",
        }
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="Final stitched transcript QA failed"):
        compose_and_caption(
            manifest_path,
            _DeepgramByCall([final_text]),
            stitch_fn=lambda **_kwargs: (b"stitched-video", {"stitch_segment_count": 4}),
        )

    saved = _read(manifest_path)
    assert saved["status"] == "final_transcript_failed"
    assert saved["final_transcript_qa"]["word_error_rate"] > 0


def test_compose_requires_passed_voice_qa(tmp_path):
    from app.features.shot_production.runner import compose_and_caption

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["transcript_qa"] = {"passed": True}
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 1.0,
            "source": "deepgram_word_window",
        }
    payload["visual_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="voice QA"):
        compose_and_caption(manifest_path, _DeepgramByCall([SCRIPT]))


def test_compose_acoustic_mode_plans_crossfades_and_requires_acoustic_gate(tmp_path):
    from app.features.shot_production.acoustic_qa import AcousticQAReport
    from app.features.shot_production.audio_seams import (
        AcousticSeamPlan,
        PlannedSeam,
        PlannedTakeWindow,
    )
    from app.features.shot_production.runner import compose_and_caption

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["duration_seconds"] = 4.0
        take["transcript_qa"] = {
            "passed": True,
            "first_word_start_seconds": 0.2,
            "final_word_end_seconds": 3.4,
        }
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 3.65,
            "source": "deepgram_word_window",
        }
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    takes = tuple(
        PlannedTakeWindow(i, 0.0, 3.65, 0.0, 3.65, 0.0) for i in range(4)
    )
    seams = tuple(
        PlannedSeam(i, 3.65, 0.0, 0.04, 0.02, 0.16, 0.2, 0.0, False, ())
        for i in range(3)
    )
    plan = AcousticSeamPlan("test-v1", takes, seams, 0.8, 14.48)
    stitch_calls = []

    def stitch_fn(**kwargs):
        stitch_calls.append(kwargs)
        return b"stitched-video", {
            "stitch_segment_count": 4,
            "stitch_audio_video_duration_delta_s": 0.02,
        }

    def extract_fn(_source, destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"wav")

    def evaluator(_clips, **_kwargs):
        return AcousticQAReport(True, True, True, True, True, True, True, 0.96, (), (), True)

    def caption_fn(**_kwargs):
        output = manifest_path.parent / "captioned-acoustic.mp4"
        output.write_bytes(b"captioned")
        return str(output)

    compose_and_caption(
        manifest_path,
        _DeepgramByCall([SCRIPT]),
        acoustic_seams=True,
        analyze_audio_fn=lambda _path: (),
        plan_acoustic_fn=lambda _evidence: plan,
        extract_seam_audio_fn=extract_fn,
        acoustic_evaluator=evaluator,
        stitch_fn=stitch_fn,
        caption_fn=caption_fn,
        probe_fn=lambda _path: _valid_final_probe("14.5"),
    )

    assert stitch_calls[0]["acoustic_plan"]["analyzer_version"] == "test-v1"
    saved = _read(manifest_path)
    assert saved["acoustic_seam_qa"]["passed"] is True
    assert len(saved["acoustic_seam_qa"]["clips"]) == 3
    assert saved["acoustic_seam_plan"]["final_duration_seconds"] == 14.48


@pytest.mark.parametrize(
    ("probe", "reason"),
    [
        (_valid_final_probe("13.9"), "duration_out_of_range"),
        ({"probe_error": "bad file"}, "probe_failed"),
        (
            {
                "streams": [{"codec_type": "video", "codec_name": "vp9", "width": 720, "height": 1280}],
                "format": {"duration": "16.0"},
            },
            "video_must_be_h264",
        ),
        (
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 720,
                        "height": 1280,
                    },
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
                "format": {"duration": "16.0", "format_name": "matroska,webm"},
            },
            "container_must_be_mp4",
        ),
    ],
)
def test_final_media_probe_fails_closed_for_invalid_delivery_contract(probe, reason):
    from app.features.shot_production.runner import evaluate_final_media_probe

    report = evaluate_final_media_probe(probe)

    assert report["passed"] is False
    assert reason in report["failure_reasons"]


def test_upload_persists_remote_head_verification_and_rechecks_without_reupload(tmp_path):
    from app.features.shot_production.runner import upload_final

    class FakeStorage:
        def __init__(self, *, verified):
            self.verified = verified
            self.upload_calls = []
            self.verify_calls = []

        def prepare_video_upload(self, **kwargs):
            return {
                "storage_provider": "fake_r2",
                "storage_key": "videos/content-addressed-final.mp4",
                "url": "https://cdn.example.test/videos/content-addressed-final.mp4",
                "file_path": "videos/content-addressed-final.mp4",
                "size": kwargs["expected_size"],
                "sha256": kwargs["expected_sha256"],
                "file_type": "video/mp4",
            }

        def upload_video(self, **kwargs):
            self.upload_calls.append(kwargs)
            return {
                "storage_key": kwargs["object_key"],
                "url": "https://cdn.example.test/videos/content-addressed-final.mp4",
                "size": len(kwargs["video_bytes"]),
                "sha256": sha256(kwargs["video_bytes"]).hexdigest(),
            }

        def verify_video_upload(self, **kwargs):
            self.verify_calls.append(kwargs)
            if not self.upload_calls:
                return {
                    "passed": False,
                    "failure_reasons": ["not_found"],
                    "storage_key": kwargs["storage_key"],
                }
            return {
                "passed": self.verified,
                "failure_reasons": [] if self.verified else ["sha256_mismatch"],
                "storage_key": kwargs["storage_key"],
            }

    def ready_manifest(root: Path) -> Path:
        manifest_path = _manifest_with_raw_takes(root)
        payload = _read(manifest_path)
        captioned = manifest_path.parent / "final-captioned.mp4"
        captioned.write_bytes(b"captioned-video")
        payload["caption"] = {
            "captioned_path": str(captioned),
            "sha256": sha256(captioned.read_bytes()).hexdigest(),
            "bytes": captioned.stat().st_size,
        }
        payload["seam_qa"] = {"passed": True}
        payload["media_qa"] = {"passed": True}
        payload["voice_qa"] = {"passed": True}
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        return manifest_path

    manifest_path = ready_manifest(tmp_path / "pass")
    storage = FakeStorage(verified=True)
    upload = upload_final(manifest_path, storage)
    assert upload["url"].startswith("https://cdn.example.test/")
    saved = _read(manifest_path)
    assert saved["upload_verification"]["passed"] is True
    assert saved["upload_intent"]["storage_key"] == "videos/content-addressed-final.mp4"
    assert len(storage.upload_calls) == 1
    assert storage.upload_calls[0]["object_key"] == "videos/content-addressed-final.mp4"
    assert len(storage.verify_calls) == 2

    failed_manifest = ready_manifest(tmp_path / "recover")
    recovering_storage = FakeStorage(verified=False)
    with pytest.raises(ValidationError, match="remote verification"):
        upload_final(failed_manifest, recovering_storage)
    failed = _read(failed_manifest)
    assert failed["upload"]
    assert failed["upload_verification"]["passed"] is False
    assert len(recovering_storage.upload_calls) == 1

    recovering_storage.verified = True
    recovered = upload_final(failed_manifest, recovering_storage)
    assert recovered["url"].startswith("https://cdn.example.test/")
    assert len(recovering_storage.upload_calls) == 1
    assert len(recovering_storage.verify_calls) == 3
    assert _read(failed_manifest)["status"] == "uploaded"


def test_upload_resume_reconciles_committed_put_after_lost_response_without_second_upload(tmp_path):
    from app.features.shot_production.runner import upload_final

    class AcceptedThenLostStorage:
        def __init__(self):
            self.remote_exists = False
            self.upload_calls = 0

        def prepare_video_upload(self, **kwargs):
            return {
                "storage_provider": "fake_r2",
                "storage_key": "videos/content-addressed-final.mp4",
                "url": "https://cdn.example.test/videos/content-addressed-final.mp4",
                "file_path": "videos/content-addressed-final.mp4",
                "size": kwargs["expected_size"],
                "sha256": kwargs["expected_sha256"],
                "file_type": "video/mp4",
            }

        def upload_video(self, **_kwargs):
            self.upload_calls += 1
            self.remote_exists = True
            raise TimeoutError("R2 committed the object but the response was lost")

        def verify_video_upload(self, **kwargs):
            return {
                "passed": self.remote_exists,
                "failure_reasons": [] if self.remote_exists else ["not_found"],
                "storage_key": kwargs["storage_key"],
            }

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    captioned = manifest_path.parent / "final-captioned.mp4"
    captioned.write_bytes(b"captioned-video")
    payload["caption"] = {
        "captioned_path": str(captioned),
        "sha256": sha256(captioned.read_bytes()).hexdigest(),
        "bytes": captioned.stat().st_size,
    }
    payload["seam_qa"] = {"passed": True}
    payload["media_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    storage = AcceptedThenLostStorage()

    with pytest.raises(TimeoutError, match="response was lost"):
        upload_final(manifest_path, storage)
    after_loss = _read(manifest_path)
    assert after_loss["upload_intent"]["storage_key"] == "videos/content-addressed-final.mp4"
    assert "upload" not in after_loss

    recovered = upload_final(manifest_path, storage)

    assert storage.upload_calls == 1
    assert recovered["url"] == "https://cdn.example.test/videos/content-addressed-final.mp4"
    assert _read(manifest_path)["upload_verification"]["passed"] is True


def test_invalidate_composition_preserves_passed_takes_and_archives_delivery(tmp_path):
    from app.features.shot_production.runner import invalidate_composition

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["status"] = "transcribed"
        take["transcript_qa"] = {"passed": True}
        take["trim_window"] = {"start_seconds": 0.0, "end_seconds": 1.0}
    payload["visual_qa"] = {"passed": True}
    old_stitch = manifest_path.parent / "stitched.mp4"
    old_caption = manifest_path.parent / "final-captioned.mp4"
    old_stitch.write_bytes(b"old-stitched-video")
    old_caption.write_bytes(b"old-captioned-video")
    payload["stitch"] = {
        "path": str(old_stitch),
        "sha256": sha256(old_stitch.read_bytes()).hexdigest(),
        "metadata": {"stitch_segment_count": 4},
        "probe": {"format": {"duration": "16.04"}},
    }
    payload["final_transcript"] = {"full_text": SCRIPT, "words": []}
    payload["final_transcript_qa"] = {"passed": True, "word_error_rate": 0.0}
    payload["seam_qa"] = {"passed": False, "gaps_seconds": [0.94, 0.981, 1.1]}
    payload["caption"] = {
        "captioned_path": str(old_caption),
        "sha256": sha256(old_caption.read_bytes()).hexdigest(),
        "bytes": old_caption.stat().st_size,
    }
    payload["media_qa"] = {"passed": True, "duration_seconds": 16.04}
    payload["upload_intent"] = {"storage_key": "videos/old-captioned.mp4"}
    payload["upload"] = {"url": "https://example.test/old.mp4"}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    reset = invalidate_composition(
        manifest_path,
        reason="rebuild trim windows to enforce seam-gap acceptance",
    )

    assert reset["status"] == "recompose_planned"
    assert all(take["raw"] for take in reset["takes"])
    assert all(take["transcript_qa"]["passed"] for take in reset["takes"])
    assert reset["visual_qa"]["passed"] is True
    assert "stitch" not in reset and "caption" not in reset and "upload" not in reset
    assert "upload_intent" not in reset
    archived = reset["composition_history"][-1]
    assert archived["snapshot"]["seam_qa"]["gaps_seconds"] == [0.94, 0.981, 1.1]
    assert archived["snapshot"]["final_transcript"]["full_text"] == SCRIPT
    archived_stitch = Path(archived["artifacts"]["stitch"]["path"])
    archived_caption = Path(archived["artifacts"]["caption"]["path"])
    assert archived_stitch.read_bytes() == b"old-stitched-video"
    assert archived_caption.read_bytes() == b"old-captioned-video"
    old_stitch.write_bytes(b"replacement-stitch")
    old_caption.write_bytes(b"replacement-caption")
    assert archived_stitch.read_bytes() == b"old-stitched-video"
    assert archived_caption.read_bytes() == b"old-captioned-video"


def test_repair_failed_seam_windows_tightens_only_failed_cut_and_moves_pause_to_outro(tmp_path):
    from app.features.shot_production.runner import repair_failed_seam_windows

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    timings = [
        (0.56, 3.54, 0.0, 3.79),
        (0.40, 4.02, 0.15, 4.27),
        (0.56, 4.50, 0.31, 4.75),
        (0.56, 2.50, 0.31, 2.75),
    ]
    for take, (first_start, final_end, trim_start, trim_end) in zip(payload["takes"], timings):
        take["status"] = "transcribed"
        take["transcript_qa"] = {
            "passed": True,
            "first_word_start_seconds": first_start,
            "final_word_end_seconds": final_end,
        }
        take["trim_window"] = {
            "start_seconds": trim_start,
            "end_seconds": trim_end,
            "source": "deepgram_word_window",
        }
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    payload["final_transcript_qa"] = {"passed": True, "word_error_rate": 0.0}
    payload["final_transcript"] = {"full_text": SCRIPT, "words": []}
    payload["seam_qa"] = {
        "passed": False,
        "gaps_seconds": [0.54, 0.38, 0.78],
        "failed_seam_indexes": [2],
        "max_allowed_seconds": 0.6,
    }
    stitched = manifest_path.parent / "stitched.mp4"
    stitched.write_bytes(b"failed-seam-stitch")
    payload["stitch"] = {
        "path": str(stitched),
        "sha256": sha256(stitched.read_bytes()).hexdigest(),
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    original_windows = [dict(take["trim_window"]) for take in payload["takes"]]
    original_total = sum(
        window["end_seconds"] - window["start_seconds"] for window in original_windows
    )

    repaired = repair_failed_seam_windows(
        manifest_path,
        reason="final hard cut measured 0.78 seconds of silence",
        target_gap_seconds=0.45,
    )

    new_windows = [take["trim_window"] for take in repaired["takes"]]
    assert new_windows[0] == original_windows[0]
    assert new_windows[1] == original_windows[1]
    assert new_windows[2]["start_seconds"] == original_windows[2]["start_seconds"]
    assert new_windows[2]["end_seconds"] == pytest.approx(4.59)
    assert new_windows[3]["start_seconds"] == pytest.approx(0.48)
    assert new_windows[3]["end_seconds"] == pytest.approx(3.08)
    repaired_total = sum(
        window["end_seconds"] - window["start_seconds"] for window in new_windows
    )
    assert repaired_total == pytest.approx(original_total)
    assert repaired["status"] == "seam_repair_planned"
    assert "seam_qa" not in repaired and "stitch" not in repaired
    assert repaired["composition_history"][-1]["snapshot"]["seam_qa"]["gaps_seconds"] == [
        0.54,
        0.38,
        0.78,
    ]
    audit = repaired["seam_repair_history"][-1]
    assert audit["failed_seam_indexes"] == [2]
    assert audit["duration_compensation_seconds"] == pytest.approx(0.33)
    assert audit["target_gap_seconds"] == 0.45


def test_visual_qa_rechecks_approved_master_hash(tmp_path):
    from app.features.shot_production.runner import build_contact_sheet, run_visual_qa

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    video = manifest_path.parent / "tiny.mp4"
    _tiny_video(video)
    for take in payload["takes"]:
        take["raw"]["path"] = str(video)
        take["raw"]["sha256"] = sha256(video.read_bytes()).hexdigest()
        take["raw"]["bytes"] = video.stat().st_size
        take["transcript_qa"] = {"final_word_end_seconds": 0.8, "passed": True}
        take["trim_window"] = {"start_seconds": 0.0, "end_seconds": 1.0, "source": "deepgram_word_end"}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    build_contact_sheet(manifest_path)
    master_path = Path(payload["approved_master"]["path"])
    master_path.write_bytes(b"not the approved master")

    with pytest.raises(ValidationError, match="approved master changed"):
        run_visual_qa(manifest_path, evaluator=lambda *_args, **_kwargs: None)


def test_reset_failed_take_archives_only_that_take_and_invalidates_downstream(tmp_path):
    from app.features.shot_production.runner import reset_failed_take

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    payload["takes"][2]["transcript_qa"] = {"passed": False}
    payload["takes"][2]["status"] = "transcript_failed"
    payload["visual_qa"] = {"passed": False}
    payload["stitch"] = {"path": "old"}
    payload["seam_qa"] = {"passed": True}
    payload["caption"] = {"path": "old-caption"}
    payload["media_qa"] = {"passed": True}
    payload["upload_verification"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    original_contract = payload["request_contract_sha256"]
    original_seed = payload["takes"][2]["seed"]
    reset_failed_take(
        manifest_path,
        index=2,
        reason="transcript merged two distinct German words",
        retry_guidance=(
            "Pronounce every written word distinctly. Keep Zentimeter and Steigung as two separate words."
        ),
    )
    reset = _read(manifest_path)
    take = reset["takes"][2]
    assert take["attempt"] == 2
    assert len(take["attempt_history"]) == 1
    assert take["attempt_history"][0]["reason"] == "transcript merged two distinct German words"
    assert take["seed"] == original_seed + 1000
    assert "Keep Zentimeter and Steigung as two separate words." in take["prompt"]
    assert reset["request_contract_history"][-1]["sha256"] == original_contract
    assert reset["request_contract_sha256"] != original_contract
    assert take["operation"] is None
    assert take["submission"] is None
    assert take["raw"] is None
    assert take["transcript_qa"] is None
    assert reset["takes"][0]["raw"] is not None
    for key in (
        "visual_qa",
        "voice_qa",
        "stitch",
        "seam_qa",
        "caption",
        "media_qa",
        "upload_verification",
    ):
        assert key not in reset

    retry_client = _SubmitClient()
    from app.features.shot_production.runner import submit_pending_takes

    submit_pending_takes(manifest_path, retry_client, max_inflight=2)
    assert len(retry_client.calls) == 1
    assert retry_client.calls[0]["seed"] == original_seed + 1000


def test_revise_failed_beat_preserves_other_takes_and_exact_duration_plan(tmp_path):
    from app.features.shot_production.runner import revise_failed_beat, submit_pending_takes

    manifest_path = _manifest_with_raw_takes(tmp_path)
    before = _read(manifest_path)
    before["takes"][2]["status"] = "transcript_failed"
    before["takes"][2]["transcript_qa"] = {"passed": False, "word_error_rate": 0.18}
    before["status"] = "transcript_failed"
    manifest_path.write_text(json.dumps(before), encoding="utf-8")
    replacement = "Manchmal wird schon eine leichte Steigung zu einem unnötigen Kampf."

    revised = revise_failed_beat(
        manifest_path,
        index=2,
        replacement_text=replacement,
        reason="Repeated Veo delivery merged the original phrase into the wrong German compound",
    )

    assert revised["script"]["text"] == (
        "Jeder, der einen Rollstuhl nutzt, weiß genau: "
        "Normgerechte Rampen sind oft trotzdem eine echte Qual. "
        f"{replacement} Das zehrt an den Kräften."
    )
    assert revised["script"]["source"] == "app.features.topics.agents.generate_dialog_scripts"
    assert revised["script"]["source_payload"]["script"] == SCRIPT
    assert revised["script"]["editorial_revisions"][-1]["original_text"] == before["takes"][2]["beat"]["text"]
    assert [take["duration_seconds"] for take in revised["takes"]] == [4, 6, 6, 4]
    assert [take["beat"]["text"] for take in revised["takes"]][2] == replacement
    assert revised["takes"][2]["attempt"] == 2
    assert revised["takes"][2]["seed"] == before["takes"][2]["seed"] + 1000
    assert replacement in revised["takes"][2]["prompt"]
    assert before["takes"][2]["beat"]["text"] not in revised["takes"][2]["prompt"]
    assert revised["takes"][2]["raw"] is None
    assert revised["takes"][2]["operation"] is None
    assert revised["takes"][0]["raw"] == before["takes"][0]["raw"]
    assert revised["takes"][1]["raw"] == before["takes"][1]["raw"]
    assert revised["takes"][3]["raw"] == before["takes"][3]["raw"]
    assert revised["request_contract_sha256"] != before["request_contract_sha256"]

    client = _SubmitClient()
    submit_pending_takes(manifest_path, client, max_inflight=2)
    assert len(client.calls) == 1
    assert replacement in client.calls[0]["prompt"]


def test_reset_visual_failed_takes_retries_only_selected_indexes_as_one_batch(tmp_path):
    from app.features.shot_production.runner import reset_visual_failed_takes

    manifest_path = _manifest_with_raw_takes(tmp_path)
    before = _read(manifest_path)
    for take in before["takes"]:
        take["status"] = "transcribed"
        take["transcript_qa"] = {"passed": True}
    before["status"] = "visual_qa_failed"
    before["visual_qa"] = {
        "passed": False,
        "no_artifacts": False,
        "blocking_reasons": ["Baked-in gibberish text appears in takes 1 and 2."],
    }
    manifest_path.write_text(json.dumps(before), encoding="utf-8")
    guidance = "Keep every frame completely free of on-screen text, captions, logos, and watermarks."

    reset = reset_visual_failed_takes(
        manifest_path,
        indexes=[1, 2],
        reason="manual contact-sheet review found baked-in generated subtitle artifacts",
        retry_guidance=guidance,
    )

    assert reset["takes"][0]["raw"] == before["takes"][0]["raw"]
    assert reset["takes"][3]["raw"] == before["takes"][3]["raw"]
    for index in (1, 2):
        assert reset["takes"][index]["status"] == "planned"
        assert reset["takes"][index]["raw"] is None
        assert reset["takes"][index]["operation"] is None
        assert reset["takes"][index]["seed"] == before["takes"][index]["seed"] + 1000
        assert guidance in reset["takes"][index]["prompt"]
    assert "visual_qa" not in reset
