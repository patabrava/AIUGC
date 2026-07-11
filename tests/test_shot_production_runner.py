from __future__ import annotations

import base64
from hashlib import sha256
import json
from pathlib import Path
import subprocess

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
    from app.features.shot_production.runner import reset_failed_take, submit_pending_takes

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

    reset_failed_take(
        manifest_path,
        index=1,
        reason="operator confirmed no recoverable operation id; explicit retry approved",
    )
    submit_pending_takes(manifest_path, resumed_client, max_inflight=4)
    completed = _read(manifest_path)

    assert [call["correlation_id"].split("_take_")[1].split("_")[0] for call in resumed_client.calls] == [
        "1",
        "2",
        "3",
    ]
    assert [take["operation"]["operation_id"] for take in completed["takes"]] == [
        "operations/op-0",
        "operations/op-1",
        "operations/op-2",
        "operations/op-3",
    ]
    assert all("reference_images" not in call for call in first_client.calls + resumed_client.calls)
    assert all("video" not in call and "last_frame" not in call for call in first_client.calls + resumed_client.calls)

    no_op_client = _SubmitClient()
    submit_pending_takes(manifest_path, no_op_client, max_inflight=4)
    assert no_op_client.calls == []


def test_pilot_run_lock_rejects_a_second_cli_for_the_same_manifest(tmp_path):
    from app.features.shot_production.runner import pilot_run_lock

    manifest_path, _ = _initialize(tmp_path)
    with pilot_run_lock(manifest_path):
        with pytest.raises(ValidationError, match="already active"):
            with pilot_run_lock(manifest_path):
                raise AssertionError("a second runner must never enter the critical section")


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
    assert all(take["trim_window"]["source"] == "deepgram_word_end" for take in payload["takes"])

    failed_manifest = _manifest_with_raw_takes(tmp_path / "failed")
    wrong = list(beats)
    wrong[2] = "Völlig falscher Satz ohne erwartete Wörter."
    with pytest.raises(ValidationError, match="take indexes.*2"):
        transcribe_and_validate_takes(failed_manifest, _DeepgramByCall(wrong))
    failed = _read(failed_manifest)
    assert failed["takes"][2]["transcript_qa"]["passed"] is False
    assert "stitch" not in failed


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
        take["transcript_qa"] = {"passed": True, "final_word_end_seconds": 1.0}
        take["trim_window"] = {"start_seconds": 0.0, "end_seconds": 1.35, "source": "deepgram_word_end"}
    payload["visual_qa"] = {"passed": True}
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
    )
    assert len(stitch_calls) == 2
    assert len(caption_calls) == 2
    assert Path(repaired["captioned_path"]).read_bytes() == b"captioned-video"


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
    payload["caption"] = {"path": "old-caption"}
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
    assert "visual_qa" not in reset and "stitch" not in reset and "caption" not in reset

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
