from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

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

SINGLE_TAKE_SCRIPT = (
    "Ein kompakter Homelift schafft Barrierefreiheit auf kleinem Raum und lässt sich oft ohne großen Umbau zuhause nachrüsten."
)

FIFTY_SECOND_SCRIPT = " ".join(
    [
        "Viele Menschen merken im Alltag erst spät, wie viel Kraft kleine Barrieren jeden einzelnen Tag tatsächlich kosten.",
        "Eine zu steile Rampe wirkt auf dem Papier vielleicht harmlos, verlangt aber bei jeder Nutzung volle Konzentration.",
        "Schon beim ersten Anstieg müssen Schultern, Arme und Hände gleichzeitig stabilisieren, lenken und das gesamte Gewicht bewegen.",
        "Wenn dann noch eine enge Kurve folgt, bleibt kaum Raum für einen sicheren und wirklich entspannten Bewegungsablauf.",
        "Das Problem fällt Außenstehenden oft nicht auf, weil wenige Zentimeter Unterschied von weitem völlig unbedeutend erscheinen können.",
        "Für Rollstuhlfahrer summiert sich diese zusätzliche Belastung jedoch über Wege, Termine und viele alltägliche Situationen hinweg.",
        "Darum sollten Rampen nicht nur normgerecht berechnet, sondern gemeinsam mit den Menschen vor Ort praktisch getestet werden.",
    ]
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
    script = str(overrides.pop("script", SCRIPT))
    payload = {
        "source": "app.features.topics.agents.generate_dialog_scripts",
        "target_length_tier": 16,
        "category": "problem_agitate_solution",
        "script": script,
        "generator_output": {"problem_agitate_solution": [script]},
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _initialize(tmp_path: Path, *, script: str = SCRIPT, target_length_tier: int = 16):
    from app.features.shot_production.runner import initialize_pilot

    approved = tmp_path / "approved.png"
    approved_hash = _approved_png(approved)
    script_input = tmp_path / "script.json"
    _script_input(script_input, script=script, target_length_tier=target_length_tier)
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


def test_initialize_pilot_records_minimum_two_shots_and_complete_request_audit(tmp_path):
    manifest_path, payload = _initialize(tmp_path)

    assert payload == _read(manifest_path)
    assert payload["version"] == 3
    assert payload["status"] == "planned"
    assert payload["script"]["text"] == SCRIPT
    assert payload["script"]["source"] == "app.features.topics.agents.generate_dialog_scripts"
    assert payload["script"]["target_length_tier"] == 16
    assert [take["beat"]["text"] for take in payload["takes"]] == [
        "Jeder, der einen Rollstuhl nutzt, weiß genau: Normgerechte Rampen sind oft trotzdem eine echte Qual.",
        "Manchmal fühlt sich jeder Zentimeter Steigung wie ein unnötiger Kampf an. Das zehrt an den Kräften.",
    ]
    assert [take["duration_seconds"] for take in payload["takes"]] == [8, 8]
    assert [take["seed"] for take in payload["takes"]] == [240711, 240712]
    assert all(take["model"] == "veo-3.1-generate-001" for take in payload["takes"])
    assert all(take["aspect_ratio"] == "9:16" for take in payload["takes"])
    assert all(take["negative_prompt"] for take in payload["takes"])
    assert all(take["prompt"].count(take["beat"]["text"]) == 1 for take in payload["takes"])
    assert all(take["submission"] is None for take in payload["takes"])
    assert len(payload["request_contract_sha256"]) == 64
    assert len(payload["script"]["input_sha256"]) == 64
    assert payload["script"]["planned_provider_durations"] == [8, 8]
    assert payload["script"]["planning_profile"] == "minimum-eight-second-shots-v1"
    assert payload["script"]["delivery_duration_seconds"] == {"requested": 16.0, "minimum": 14.5, "maximum": 16.5}
    assert len({take["shot"]["sha256"] for take in payload["takes"]}) == 2
    assert all(Path(take["shot"]["path"]).is_file() for take in payload["takes"])

    with pytest.raises(ValidationError, match="already exists"):
        _initialize(tmp_path)


def test_initialize_pilot_accepts_approved_manual_semantic_script(tmp_path):
    from app.features.shot_production.runner import initialize_pilot

    approved = tmp_path / "approved.png"
    approved_hash = _approved_png(approved)
    script_input = tmp_path / "script.json"
    _script_input(
        script_input,
        source="manual_semantic_ugc",
        creation_mode="manual_semantic_ugc",
        script_review_status="approved",
        target_length_tier=None,
        target_duration_seconds=16,
        generator_output=None,
    )
    manifest = tmp_path / "run" / "manifest.json"

    payload = initialize_pilot(
        manifest_path=manifest,
        approved_frame_path=approved,
        expected_sha256=approved_hash,
        script_input_path=script_input,
        base_seed=240711,
    )

    assert payload["script"]["source"] == "manual_semantic_ugc"
    assert payload["script"]["creation_mode"] == "manual_semantic_ugc"
    assert payload["script"]["script_review_status"] == "approved"
    assert payload["script"]["target_length_tier"] is None
    assert payload["script"]["target_duration_seconds"] == 16
    assert payload["script"]["delivery_duration_seconds"] == {
        "requested": 16.0,
        "minimum": 14.5,
        "maximum": 16.5,
    }


def test_initialize_pilot_accepts_dynamic_semantic_generator_provenance(tmp_path):
    from app.features.shot_production.runner import initialize_pilot

    approved = tmp_path / "approved.png"
    approved_hash = _approved_png(approved)
    script_input = tmp_path / "script.json"
    _script_input(
        script_input,
        source="app.features.topics.semantic_scripts.generate_semantic_script",
        creation_mode="semantic_ugc",
        target_length_tier=None,
        target_duration_seconds=16,
    )
    manifest = tmp_path / "run" / "manifest.json"

    payload = initialize_pilot(
        manifest_path=manifest,
        approved_frame_path=approved,
        expected_sha256=approved_hash,
        script_input_path=script_input,
        base_seed=240711,
    )

    assert payload["script"]["source"] == (
        "app.features.topics.semantic_scripts.generate_semantic_script"
    )
    assert payload["script"]["creation_mode"] == "semantic_ugc"
    assert payload["script"]["target_duration_seconds"] == 16
    assert payload["script"]["delivery_duration_seconds"]["requested"] == 16.0


def test_initialize_pilot_rejects_unapproved_manual_semantic_script(tmp_path):
    from app.features.shot_production.runner import initialize_pilot

    approved = tmp_path / "approved.png"
    approved_hash = _approved_png(approved)
    script_input = tmp_path / "script.json"
    _script_input(
        script_input,
        source="manual_semantic_ugc",
        creation_mode="manual_semantic_ugc",
        script_review_status="pending",
        target_length_tier=None,
        target_duration_seconds=16,
        generator_output=None,
    )

    with pytest.raises(ValidationError, match="approved manual semantic"):
        initialize_pilot(
            manifest_path=tmp_path / "run" / "manifest.json",
            approved_frame_path=approved,
            expected_sha256=approved_hash,
            script_input_path=script_input,
            base_seed=240711,
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"source": "manual"}, "app-generated"),
        ({"target_length_tier": 8}, "duration"),
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


def test_initialize_pilot_plans_seven_shots_for_fifty_second_script(tmp_path):
    from app.features.shot_production.runner import initialize_pilot

    approved = tmp_path / "approved.png"
    approved_hash = _approved_png(approved)
    script_input = tmp_path / "script.json"
    _script_input(script_input, script=FIFTY_SECOND_SCRIPT, target_length_tier=50)
    manifest = tmp_path / "run" / "manifest.json"

    payload = initialize_pilot(
        manifest_path=manifest,
        approved_frame_path=approved,
        expected_sha256=approved_hash,
        script_input_path=script_input,
        base_seed=240711,
    )

    assert len(payload["takes"]) == 7
    assert [take["index"] for take in payload["takes"]] == list(range(7))
    assert all(take["duration_seconds"] == 8 for take in payload["takes"])
    assert payload["script"]["delivery_duration_seconds"] == {"requested": 50.0, "minimum": 48.5, "maximum": 50.5}


def test_initialize_pilot_accepts_audited_revision_of_generator_output(tmp_path):
    from app.features.shot_production.runner import initialize_pilot

    original = SCRIPT
    original_beat = "Manchmal fühlt sich jeder Zentimeter Steigung wie ein unnötiger Kampf an."
    replacement_beat = "Manchmal wird schon eine leichte Steigung zu einem unnötigen Kampf."
    revised = original.replace(original_beat, replacement_beat)
    approved = tmp_path / "approved.png"
    approved_hash = _approved_png(approved)
    script_input = tmp_path / "script.json"
    script_input.write_text(
        json.dumps(
            {
                "source": "app.features.topics.agents.generate_dialog_scripts",
                "target_length_tier": 16,
                "category": "problem_agitate_solution",
                "script": revised,
                "original_script": original,
                "generator_output": {"problem_agitate_solution": [original]},
                "editorial_revisions": [
                    {
                        "take_index": 2,
                        "original_text": original_beat,
                        "replacement_text": replacement_beat,
                        "reason": "audited Veo pronunciation correction",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = initialize_pilot(
        manifest_path=tmp_path / "run" / "manifest.json",
        approved_frame_path=approved,
        expected_sha256=approved_hash,
        script_input_path=script_input,
        base_seed=240712,
    )

    assert payload["script"]["text"] == revised
    assert len(payload["takes"]) == 2


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

    assert resumed_client.calls == []
    assert [take["operation"]["operation_id"] for take in completed["takes"]] == [
        "operations/op-0",
        "operations/recovered-1",
    ]
    assert all("reference_images" not in call for call in first_client.calls + resumed_client.calls)
    assert all("video" not in call and "last_frame" not in call for call in first_client.calls + resumed_client.calls)

    no_op_client = _SubmitClient()
    submit_pending_takes(manifest_path, no_op_client, max_inflight=4)
    assert no_op_client.calls == []


def test_pilot_submission_pins_one_audio_720p_output_per_take(tmp_path):
    from app.features.shot_production.runner import submit_pending_takes

    manifest_path, _ = _initialize(tmp_path)
    client = _SubmitClient()

    submit_pending_takes(manifest_path, client, max_inflight=4)

    assert client.calls
    assert all(call["sample_count"] == 1 for call in client.calls)
    assert all(call["generate_audio"] is True for call in client.calls)
    assert all(call["resolution"] == "720p" for call in client.calls)


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("planning_profile", "tampered-planner-v9"),
        (
            "delivery_duration_seconds",
            {"requested": 16.0, "minimum": 4.0, "maximum": 99.0},
        ),
    ],
)
def test_submission_contract_binds_duration_planning_fields(tmp_path, field, value):
    from app.features.shot_production.runner import submit_pending_takes

    manifest_path, _ = _initialize(tmp_path)
    payload = _read(manifest_path)
    payload["script"][field] = value
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    client = _SubmitClient()

    with pytest.raises(ValidationError, match="planning contract|request contract changed"):
        submit_pending_takes(manifest_path, client)

    assert client.calls == []


def test_legacy_v2_manifest_keeps_its_original_hash_shape_and_remains_resumable(tmp_path):
    from app.features.shot_production.runner import (
        _canonical_sha256,
        _request_contract_payload,
        submit_pending_takes,
    )

    manifest_path, payload = _initialize(tmp_path)
    payload["version"] = 2
    payload["request_contract_sha256"] = _canonical_sha256(_request_contract_payload(payload))
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    client = _SubmitClient()

    submit_pending_takes(manifest_path, client, max_inflight=2)

    assert len(client.calls) == 2


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


def test_generation_runs_arbitrary_take_count_in_two_operation_vertex_quota_waves(tmp_path):
    from app.features.shot_production.runner import generate_raw_takes_in_waves

    manifest_path, _ = _initialize(
        tmp_path,
        script=FIFTY_SECOND_SCRIPT,
        target_length_tier=50,
    )
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
        ("submit", 4),
        ("submit", 5),
        ("poll", 4),
        ("poll", 5),
        ("submit", 6),
        ("poll", 6),
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
    assert client.calls == [f"operations/op-{index}" for index in range(len(payload["takes"]))]
    assert [Path(take["raw"]["path"]).read_bytes() for take in payload["takes"]] == [
        f"raw-{index}".encode() for index in range(len(payload["takes"]))
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


def _manifest_with_raw_takes(
    tmp_path: Path,
    *,
    script: str = SCRIPT,
    target_length_tier: int = 16,
) -> Path:
    from app.features.shot_production.runner import submit_pending_takes

    manifest_path, _ = _initialize(
        tmp_path,
        script=script,
        target_length_tier=target_length_tier,
    )
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
    wrong[1] = "Völlig falscher Satz ohne erwartete Wörter."
    with pytest.raises(ValidationError, match="take indexes.*1"):
        transcribe_and_validate_takes(failed_manifest, _DeepgramByCall(wrong))
    failed = _read(failed_manifest)
    assert failed["takes"][1]["transcript_qa"]["passed"] is False
    assert "stitch" not in failed


def test_failed_stored_transcript_can_be_reevaluated_without_a_paid_retry(tmp_path):
    from app.features.shot_production.runner import transcribe_and_validate_takes

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    payload["takes"][0]["beat"]["text"] = "Achte deshalb auf erreichbare Displays."
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    scripts = [take["beat"]["text"] for take in payload["takes"]]
    scripts[0] = "Völlig falscher Satz."

    with pytest.raises(ValidationError, match="take indexes.*0"):
        transcribe_and_validate_takes(manifest_path, _DeepgramByCall(scripts))

    failed = _read(manifest_path)
    numeric = _timed_transcript("8. deshalb auf erreichbare Displays.")
    failed["takes"][0]["transcript"] = {
        "full_text": numeric.full_text,
        "words": [
            {"word": word.word, "start": word.start, "end": word.end}
            for word in numeric.words
        ],
    }
    manifest_path.write_text(json.dumps(failed), encoding="utf-8")

    deepgram = _DeepgramByCall([])
    refreshed = transcribe_and_validate_takes(manifest_path, deepgram)

    assert deepgram.calls == []
    assert refreshed["takes"][0]["transcript_qa"]["passed"] is True
    assert refreshed["takes"][0]["transcript_qa"]["word_error_rate"] == 0.0
    assert refreshed["takes"][0]["trim_window"]["source"] == "deepgram_word_window"


def test_borderline_asr_failure_can_be_fail_closed_audio_adjudicated(tmp_path):
    from app.features.shot_production.runner import transcribe_and_validate_takes

    expected = (
        "Schnee und Eis machen Wege oft unpassierbar, doch Anlieger müssen "
        "Gehwege von 7 bis 20 Uhr räumen."
    )
    deepgram_text = (
        "Schnee und Eis machen Wege oft unpassierbar, doch Anleger müssen "
        "Gehwege von 7 bis 20 räumen."
    )
    independently_heard = (
        "Schnee und Eis machen Wege oft unpassierbar, doch Anlieger müssen "
        "Gehwege von 7 bis 20 räumen."
    )
    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    payload["takes"][0]["beat"]["text"] = expected
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    scripts = [deepgram_text, payload["takes"][1]["beat"]["text"]]
    calls = []
    deepgram_transcript = _timed_transcript(deepgram_text)
    adjudicated_transcript = _timed_transcript(independently_heard)

    def adjudicate(**kwargs):
        calls.append(kwargs)
        return adjudicated_transcript, {
            "source": "gemini_audio_borderline_v1",
            "confidence": 0.98,
        }

    result = transcribe_and_validate_takes(
        manifest_path,
        _DeepgramByCall(scripts),
        adjudicate_fn=adjudicate,
    )

    first = result["takes"][0]
    assert len(calls) == 1
    assert calls[0]["raw_path"] == Path(first["raw"]["path"])
    assert first["transcript_qa"]["passed"] is True
    assert first["transcript_qa"]["word_error_rate"] == pytest.approx(1 / 17)
    assert first["transcript"]["full_text"] == adjudicated_transcript.full_text
    assert first["transcript_adjudication"] == {
        "source": "gemini_audio_borderline_v1",
        "confidence": 0.98,
        "original_actual_text": deepgram_transcript.full_text,
        "original_word_error_rate": pytest.approx(2 / 17),
    }


def test_borderline_audio_adjudication_must_still_pass_the_existing_wer_limit(tmp_path):
    from app.features.shot_production.runner import transcribe_and_validate_takes

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    expected = payload["takes"][0]["beat"]["text"]
    words = expected.split()
    deepgram_text = " ".join([*words[:-3], "drei", "falsche", words[-1]])
    scripts = [deepgram_text, payload["takes"][1]["beat"]["text"]]

    def adjudicate(**_kwargs):
        return _timed_transcript(deepgram_text), {
            "source": "gemini_audio_borderline_v1",
            "confidence": 0.99,
        }

    with pytest.raises(ValidationError, match="take indexes.*0"):
        transcribe_and_validate_takes(
            manifest_path,
            _DeepgramByCall(scripts),
            adjudicate_fn=adjudicate,
        )

    failed = _read(manifest_path)["takes"][0]
    assert failed["transcript_qa"]["passed"] is False
    assert "transcript_adjudication" not in failed


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
        assert sheet.width == len(payload["takes"]) * 270
        assert sheet.height == 3 * 510

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
    ]
    assert evaluator_calls[0][1]["model"] == "gemini-2.5-flash"
    saved = _read(manifest_path)
    assert saved["voice_qa"]["passed"] is True
    assert saved["voice_qa"]["model"] == "gemini-2.5-flash"
    assert saved["voice_qa"]["rubric_version"] == "voice-continuity-v1"
    take_count = len(payload["takes"])
    assert len(saved["voice_qa"]["clips"]) == take_count
    assert all(Path(clip["path"]).is_file() for clip in saved["voice_qa"]["clips"])

    run_voice_qa(
        manifest_path,
        evaluator=evaluator,
        extract_audio_fn=extract_audio,
        model="gemini-2.5-flash",
    )
    assert len(extraction_calls) == take_count
    assert len(evaluator_calls) == 1

    Path(saved["voice_qa"]["clips"][0]["path"]).write_bytes(b"corrupt")
    run_voice_qa(
        manifest_path,
        evaluator=evaluator,
        extract_audio_fn=extract_audio,
        model="gemini-2.5-flash",
    )
    assert len(extraction_calls) == take_count * 2
    assert len(evaluator_calls) == 2

    monkeypatch.setattr(runner_module, "VOICE_QA_RUBRIC_VERSION", "voice-continuity-v2")
    revised_report = run_voice_qa(
        manifest_path,
        evaluator=evaluator,
        extract_audio_fn=extract_audio,
        model="gemini-2.5-flash",
    )
    assert len(extraction_calls) == take_count * 3
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
            outlier_take_indexes=(1,),
            confidence=0.99,
            blocking_reasons=("Take 1 has a different vocal timbre.",),
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


def test_voice_gate_persists_single_take_not_applicable_without_gemini(tmp_path):
    from app.features.shot_production.runner import run_voice_qa
    from app.features.shot_production.voice_qa import evaluate_voice_consistency

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    payload["takes"] = payload["takes"][:1]
    payload["takes"][0]["transcript_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    def extract_audio(_source, destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"single-voice")

    report = run_voice_qa(
        manifest_path,
        evaluator=evaluate_voice_consistency,
        extract_audio_fn=extract_audio,
        llm_client=SimpleNamespace(
            generate_gemini_text=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("Gemini must not be called for one take")
            )
        ),
    )

    assert report["passed"] is True
    assert report["status"] == "not_applicable"


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
        "outlier_take_indexes": [1],
        "blocking_reasons": ["Take 1 has a different vocal timbre."],
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="retryable failed state"):
        reset_failed_take(manifest_path, index=0, reason="operator selected the wrong take")

    reset = reset_failed_take(
        manifest_path,
        index=1,
        reason="voice QA identified take 1 as the vocal outlier",
    )

    assert reset["takes"][1]["status"] == "planned"
    assert "voice_qa" not in reset
    assert reset["qa_failure_history"][-1]["stage"] == "voice_qa"
    assert reset["qa_failure_history"][-1]["selected_take_indexes"] == [1]


def test_batch_voice_retry_archives_one_report_and_plans_only_selected_outliers(tmp_path):
    from app.features.shot_production.runner import reset_voice_failed_takes

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["status"] = "transcribed"
        take["transcript_qa"] = {"passed": True}
    payload["status"] = "voice_qa_failed"
    payload["voice_qa"] = {
        "passed": False,
        "outlier_take_indexes": [0, 1],
        "blocking_reasons": ["Takes 0 and 1 do not match the reference vocal timbre."],
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    reset = reset_voice_failed_takes(
        manifest_path,
        indexes=[0, 1],
        reason="operator approved retry of both voice outliers",
    )

    assert all(take["status"] == "planned" for take in reset["takes"])
    assert all(take["raw"] is None for take in reset["takes"])
    voice_history = [
        item for item in reset["qa_failure_history"] if item["stage"] == "voice_qa"
    ]
    assert len(voice_history) == 1
    assert voice_history[0]["selected_take_indexes"] == [0, 1]


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
        return b"stitched-video", {"stitch_final_duration_s": 15.0, "stitch_segment_count": 2}

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

    assert stitch_calls[0]["segment_videos"] == [b"clip-0", b"clip-1"]
    assert stitch_calls[0]["trim_windows"] == [take["trim_window"] for take in payload["takes"]]
    assert len(caption_calls) == 1
    expected_caption_text = " ".join(word.strip(".,:;!?") for word in SCRIPT.split())
    assert caption_calls[0]["transcript"].full_text == expected_caption_text
    assert Path(result["captioned_path"]).read_bytes() == b"captioned-video"
    saved = _read(manifest_path)
    assert saved["stitch"]["metadata"]["stitch_segment_count"] == 2
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


def test_compose_single_take_marks_seam_qa_not_applicable(tmp_path):
    from app.features.shot_production.runner import compose_and_caption

    manifest_path = _manifest_with_raw_takes(
        tmp_path,
        script=SINGLE_TAKE_SCRIPT,
        target_length_tier=8,
    )
    payload = _read(manifest_path)
    take = payload["takes"][0]
    take["transcript_qa"] = {
        "passed": True,
        "first_word_start_seconds": 0.2,
        "final_word_end_seconds": 6.8,
    }
    take["trim_window"] = {
        "start_seconds": 0.0,
        "end_seconds": 7.25,
        "source": "deepgram_word_window",
    }
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True, "status": "not_applicable"}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    def caption_fn(**_kwargs):
        output = manifest_path.parent / "single-take-captioned.mp4"
        output.write_bytes(b"captioned")
        return str(output)

    compose_and_caption(
        manifest_path,
        _DeepgramByCall([SINGLE_TAKE_SCRIPT]),
        stitch_fn=lambda **_kwargs: (
            b"stitched-video",
            {"stitch_final_duration_s": 8.0, "stitch_segment_count": 1},
        ),
        caption_fn=caption_fn,
        probe_fn=lambda _path: _valid_final_probe("8.0"),
    )

    saved = _read(manifest_path)
    assert saved["seam_qa"] == {
        "status": "not_applicable",
        "passed": True,
        "gaps_seconds": [],
    }


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


def test_composition_rederives_and_rejects_a_mutated_duration_envelope(tmp_path):
    from app.features.shot_production.runner import (
        _canonical_sha256,
        _request_contract_payload,
        compose_and_caption,
    )

    manifest_path, payload = _initialize(tmp_path)
    payload["script"]["delivery_duration_seconds"] = {
        "requested": 16.0,
        "minimum": 4.0,
        "maximum": 99.0,
    }
    payload["request_contract_sha256"] = _canonical_sha256(_request_contract_payload(payload))
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="duration planning contract"):
        compose_and_caption(manifest_path, _DeepgramByCall([]))


def test_upload_rejects_a_mutated_planning_profile_even_with_a_rehashed_manifest(tmp_path):
    from app.features.shot_production.runner import (
        _canonical_sha256,
        _request_contract_payload,
        upload_final,
    )

    manifest_path, payload = _initialize(tmp_path)
    payload["script"]["planning_profile"] = "tampered-planner-v9"
    payload["request_contract_sha256"] = _canonical_sha256(_request_contract_payload(payload))
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="duration planning contract"):
        upload_final(manifest_path)


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
        take["transcript_qa"] = {
            "passed": True,
            "first_word_start_seconds": 0.2,
            "final_word_end_seconds": 6.8,
        }
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 7.26,
            "source": "deepgram_word_window",
        }
    payload["takes"][1]["transcript_qa"]["first_word_start_seconds"] = 0.08
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    takes = tuple(
        PlannedTakeWindow(i, 0.0, 7.26, 0.0, 7.26, 0.0) for i in range(2)
    )
    seams = tuple(
        PlannedSeam(i, 7.26, 0.0, 0.04, 0.02, 0.16, 0.2, 0.0, False, ())
        for i in range(1)
    )
    plan = AcousticSeamPlan("test-v1", takes, seams, 0.8, 14.48)
    stitch_calls = []

    def stitch_fn(**kwargs):
        stitch_calls.append(kwargs)
        return b"stitched-video", {
            "stitch_segment_count": 2,
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

    def normalize_fn(_previous, _incoming, output, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"room-tone-bridged")

    def plan_fn(evidence, **_kwargs):
        assert evidence[1].provider_duration_seconds == pytest.approx(8.12)
        assert evidence[1].first_word_start_seconds == pytest.approx(0.20)
        assert evidence[1].final_word_end_seconds == pytest.approx(6.92)
        return plan

    compose_and_caption(
        manifest_path,
        _DeepgramByCall([SCRIPT]),
        acoustic_seams=True,
        analyze_audio_fn=lambda _path: (),
        plan_acoustic_fn=plan_fn,
        normalize_preroll_fn=normalize_fn,
        extract_seam_audio_fn=extract_fn,
        acoustic_evaluator=evaluator,
        stitch_fn=stitch_fn,
        caption_fn=caption_fn,
        probe_fn=lambda _path: _valid_final_probe("14.5"),
    )

    assert stitch_calls[0]["acoustic_plan"]["analyzer_version"] == "test-v1"
    assert stitch_calls[0]["segment_videos"][1] == b"room-tone-bridged"
    saved = _read(manifest_path)
    assert saved["acoustic_seam_qa"]["passed"] is True
    assert len(saved["acoustic_seam_qa"]["clips"]) == 1
    assert saved["acoustic_seam_plan"]["final_duration_seconds"] == 14.48
    assert saved["acoustic_preroll_normalization"][0]["take_index"] == 1


def test_acoustic_source_preparation_bridges_early_speech_with_previous_room_tone(tmp_path):
    from app.features.shot_production.runner import _prepare_acoustic_segment_sources

    manifest_path = _manifest_with_raw_takes(tmp_path)
    takes = sorted(_read(manifest_path)["takes"], key=lambda take: take["index"])
    for take in takes:
        take["transcript_qa"] = {
            "passed": True,
            "first_word_start_seconds": 0.2,
            "final_word_end_seconds": 6.8,
        }
    takes[1]["transcript_qa"]["first_word_start_seconds"] = 0.08
    calls = []

    def normalize(previous_path, incoming_path, output_path, **kwargs):
        calls.append((previous_path, incoming_path, output_path, kwargs))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"room-tone-bridged")

    paths, timing_offsets, records = _prepare_acoustic_segment_sources(
        takes,
        manifest_path.parent,
        normalize_fn=normalize,
    )

    assert paths[0] == Path(takes[0]["raw"]["path"])
    assert paths[1].read_bytes() == b"room-tone-bridged"
    assert timing_offsets == (0.0, 0.12)
    assert calls[0][0] == paths[0]
    assert calls[0][1] == Path(takes[1]["raw"]["path"])
    assert calls[0][3] == {
        "bridge_start_seconds": pytest.approx(6.9),
        "padding_seconds": pytest.approx(0.12),
    }
    assert records[0]["take_index"] == 1
    assert records[0]["padding_seconds"] == pytest.approx(0.12)
    assert records[0]["source_take_index"] == 0


def test_long_form_acoustic_planning_uses_cadence_floor_before_requesting_regeneration():
    from app.features.shot_production.runner import _plan_acoustic_delivery

    expected_plan = object()
    calls = []

    def plan_fn(_evidence, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ValidationError("Acoustic plan cannot satisfy the duration envelope.")
        return expected_plan

    plan, resolution = _plan_acoustic_delivery(
        (),
        {"requested": 32.0, "minimum": 30.5, "maximum": 32.5},
        plan_fn=plan_fn,
    )

    assert plan is expected_plan
    assert [call["min_duration_seconds"] for call in calls] == [30.5, 28.8]
    assert [call["max_duration_seconds"] for call in calls] == [32.5, 33.0]
    assert resolution == {
        "source": "long_form_acoustic_cadence_floor",
        "requested_seconds": 32.0,
        "approved_minimum_seconds": 30.5,
        "effective_minimum_seconds": 28.8,
        "approved_maximum_seconds": 32.5,
        "effective_maximum_seconds": 33.0,
        "post_word_crossfade_guard_seconds": 0.1,
    }


def test_forty_plus_second_acoustic_planning_uses_explicit_dense_speech_guard():
    from app.features.shot_production.runner import _plan_acoustic_delivery

    calls = []

    def plan_fn(_evidence, **kwargs):
        calls.append(kwargs)
        return object()

    _plan_acoustic_delivery(
        (),
        {"requested": 50.0, "minimum": 49.5, "maximum": 50.5},
        plan_fn=plan_fn,
    )

    assert calls == [{
        "min_duration_seconds": 49.5,
        "max_duration_seconds": 50.5,
        "min_post_word_crossfade_guard_seconds": 0.060,
    }]


def test_sixteen_second_acoustic_planning_allows_a_bounded_sentence_pause():
    from app.features.shot_production.runner import _plan_acoustic_delivery

    calls = []

    def plan_fn(_evidence, **kwargs):
        calls.append(kwargs)
        return object()

    _plan_acoustic_delivery(
        (),
        {"requested": 16.0, "minimum": 14.5, "maximum": 16.5},
        plan_fn=plan_fn,
    )

    assert calls == [{
        "min_duration_seconds": 14.5,
        "max_duration_seconds": 16.5,
        "max_seam_word_gap_seconds": 0.48,
    }]


def test_long_form_final_transcript_accepts_one_asr_stem_with_passed_take_consensus():
    from app.features.shot_production.runner import _accept_final_transcript_consensus

    final_qa = {
        "passed": False,
        "word_error_rate": 1 / 71,
        "expected_words": [f"word-{index}" for index in range(71)],
        "actual_words": [f"word-{index}" for index in range(70)] + ["stem"],
        "first_word_present": True,
        "last_word_present": True,
        "foreign_words": [],
    }
    takes = [
        {"transcript_qa": {"passed": True, "word_error_rate": 0.0}},
        {"transcript_qa": {"passed": True, "word_error_rate": 1 / 16}},
        {"transcript_qa": {"passed": True, "word_error_rate": 0.0}},
        {"transcript_qa": {"passed": True, "word_error_rate": 0.0}},
    ]

    accepted = _accept_final_transcript_consensus(
        final_qa,
        takes,
        acoustic_plan=object(),
        requested_duration_seconds=32.0,
    )

    assert accepted is True
    assert _accept_final_transcript_consensus(
        final_qa,
        takes,
        acoustic_plan=None,
        requested_duration_seconds=32.0,
    ) is False
    takes[1]["transcript_qa"]["passed"] = False
    assert _accept_final_transcript_consensus(
        final_qa,
        takes,
        acoustic_plan=object(),
        requested_duration_seconds=32.0,
    ) is False


def test_single_take_final_transcript_accepts_the_same_bounded_compound_asr_suffix_twice():
    from app.features.shot_production.runner import _accept_final_transcript_consensus

    expected = ["kopfsteinpflaster"] + [f"wort-{index}" for index in range(1, 16)]
    actual = ["steinpflaster"] + [f"wort-{index}" for index in range(1, 16)]
    final_qa = {
        "passed": False,
        "word_error_rate": 1 / 16,
        "expected_words": tuple(expected),
        "actual_words": tuple(actual),
        "first_word_present": False,
        "last_word_present": True,
        "foreign_words": [],
    }
    takes = [{
        "transcript_qa": {
            "passed": True,
            "word_error_rate": 1 / 16,
            "expected_words": expected,
            "actual_words": actual,
            "first_word_present": True,
            "last_word_present": True,
            "foreign_words": [],
        }
    }]

    assert _accept_final_transcript_consensus(
        final_qa,
        takes,
        acoustic_plan=None,
        requested_duration_seconds=8.0,
    ) is True


def test_sixteen_second_final_transcript_accepts_only_the_passed_take_consensus():
    from app.features.shot_production.runner import _accept_final_transcript_consensus

    first_expected = [f"erste-{index}" for index in range(15)] + ["uhr", "räumen"]
    first_actual = [f"erste-{index}" for index in range(15)] + ["räumen"]
    second_expected = [f"zweite-{index}" for index in range(16)] + ["ab"]
    second_actual = list(second_expected)
    final_qa = {
        "passed": False,
        "word_error_rate": 1 / 34,
        "expected_words": first_expected + second_expected,
        "actual_words": first_actual + second_actual,
        "first_word_present": True,
        "last_word_present": True,
        "foreign_words": [],
    }
    takes = [
        {
            "transcript_qa": {
                "passed": True,
                "word_error_rate": 1 / 17,
                "expected_words": first_expected,
                "actual_words": first_actual,
                "first_word_present": True,
                "last_word_present": True,
                "foreign_words": [],
            }
        },
        {
            "transcript_qa": {
                "passed": True,
                "word_error_rate": 0.0,
                "expected_words": second_expected,
                "actual_words": second_actual,
                "first_word_present": True,
                "last_word_present": True,
                "foreign_words": [],
            }
        },
    ]

    assert _accept_final_transcript_consensus(
        final_qa,
        takes,
        acoustic_plan=object(),
        requested_duration_seconds=16.0,
    ) is True

    final_qa["actual_words"] = [*first_actual, "new-word", *second_actual[1:]]
    assert _accept_final_transcript_consensus(
        final_qa,
        takes,
        acoustic_plan=object(),
        requested_duration_seconds=16.0,
    ) is False


def test_acoustic_plan_contract_rejects_seam_energy_delta_above_six_db():
    from app.features.shot_production.audio_seams import (
        AcousticSeamPlan,
        PlannedSeam,
        PlannedTakeWindow,
    )
    from app.features.shot_production.runner import _evaluate_acoustic_plan_contract

    plan = AcousticSeamPlan(
        "test-v1",
        (
            PlannedTakeWindow(0, 0.0, 7.26, 0.0, 7.26, 0.0),
            PlannedTakeWindow(1, 0.0, 7.26, 0.0, 7.26, 0.0),
        ),
        (PlannedSeam(0, 7.26, 0.0, 0.04, 0.02, 0.16, 6.01, 0.0, False, ()),),
        1.0,
        14.48,
    )

    assert _evaluate_acoustic_plan_contract(
        plan,
        {"stitch_audio_video_duration_delta_s": 0.02},
        fps=24.0,
    ) == ["seam_energy_delta_exceeded"]


def test_acoustic_plan_contract_defers_bounded_energy_fallback_to_perceptual_qa():
    from app.features.shot_production.audio_seams import (
        AcousticSeamPlan,
        PlannedSeam,
        PlannedTakeWindow,
    )
    from app.features.shot_production.runner import _evaluate_acoustic_plan_contract

    plan = AcousticSeamPlan(
        "test-v1",
        (
            PlannedTakeWindow(0, 0.0, 7.26, 0.0, 7.26, 0.0),
            PlannedTakeWindow(1, 0.0, 7.26, 0.0, 7.26, 0.0),
        ),
        (
            PlannedSeam(
                0,
                7.26,
                0.0,
                0.04,
                0.02,
                0.16,
                9.0,
                0.0,
                False,
                (),
                energy_fallback=True,
            ),
        ),
        1.0,
        14.48,
    )

    assert _evaluate_acoustic_plan_contract(
        plan,
        {"stitch_audio_video_duration_delta_s": 0.02},
        fps=24.0,
    ) == []


def test_acoustic_plan_contract_uses_the_approved_16_second_word_gap_ceiling():
    from app.features.shot_production.audio_seams import (
        AcousticSeamPlan,
        PlannedSeam,
        PlannedTakeWindow,
    )
    from app.features.shot_production.runner import _evaluate_acoustic_plan_contract

    plan = AcousticSeamPlan(
        "test-v1",
        (
            PlannedTakeWindow(0, 0.0, 6.50, 0.0, 6.50, 0.0),
            PlannedTakeWindow(1, 0.0, 7.96, 0.0, 7.96, 0.0),
        ),
        (
            PlannedSeam(
                0,
                6.50,
                0.0,
                0.04,
                0.02,
                0.48000000000000004,
                4.0,
                0.0,
                False,
                (),
            ),
        ),
        1.0,
        14.42,
    )

    assert _evaluate_acoustic_plan_contract(
        plan,
        {"stitch_audio_video_duration_delta_s": 0.02},
        fps=24.0,
        max_seam_word_gap_seconds=0.48,
    ) == []


def test_acoustic_retry_map_targets_only_takes_adjacent_to_failed_seams():
    from app.features.shot_production.runner import _acoustic_retry_map

    mapping, take_indexes = _acoustic_retry_map([2, 5], take_count=7)

    assert mapping == [
        {"seam_index": 2, "adjacent_take_indexes": [2, 3]},
        {"seam_index": 5, "adjacent_take_indexes": [5, 6]},
    ]
    assert take_indexes == [2, 3, 5, 6]


def test_reset_failed_take_supports_a_localized_acoustic_seam_qa_failure(tmp_path):
    from app.features.shot_production.runner import reset_failed_take

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["status"] = "transcribed"
        take["transcript_qa"] = {"passed": True}
    payload["status"] = "acoustic_seam_qa_failed"
    payload["acoustic_seam_qa"] = {
        "passed": False,
        "failed_seam_indexes": [0],
        "seam_retry_map": [
            {"seam_index": 0, "adjacent_take_indexes": [0, 1]},
        ],
        "recommended_retry_take_indexes": [0, 1],
        "blocking_reasons": ["breath restarts at seam 0"],
    }
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    reset = reset_failed_take(
        manifest_path,
        index=1,
        reason="incoming take restarts the breath at seam 0",
        retry_guidance="Continue the prior breath and cadence without a fresh inhale.",
    )

    assert reset["takes"][1]["status"] == "planned"
    assert reset["qa_failure_history"][-1]["stage"] == "acoustic_seam_qa"
    assert reset["qa_failure_history"][-1]["report"]["failed_seam_indexes"] == [0]


def test_composition_persists_failed_seam_verdict_and_adjacent_retry_indexes(tmp_path):
    from app.features.shot_production.acoustic_qa import (
        AcousticQAReport,
        AcousticSeamVerdict,
    )
    from app.features.shot_production.audio_seams import (
        AcousticSeamPlan,
        PlannedSeam,
        PlannedTakeWindow,
    )
    from app.features.shot_production.runner import compose_and_caption

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["transcript_qa"] = {
            "passed": True,
            "first_word_start_seconds": 0.2,
            "final_word_end_seconds": 6.8,
        }
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 7.26,
            "source": "deepgram_word_window",
        }
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    plan = AcousticSeamPlan(
        "test-v1",
        tuple(PlannedTakeWindow(i, 0.0, 7.26, 0.0, 7.26, 0.0) for i in range(2)),
        (PlannedSeam(0, 7.26, 0.0, 0.04, 0.02, 0.16, 0.2, 0.0, False, ()),),
        0.8,
        14.48,
    )

    def extract_fn(_source, destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"wav")

    report = AcousticQAReport(
        False,
        True,
        True,
        True,
        True,
        True,
        True,
        0.96,
        ("seam 0 restarts on an inhale",),
        (),
        False,
        (AcousticSeamVerdict(0, False, ("restarts on an inhale",)),),
    )

    with pytest.raises(ValidationError, match="acoustic seam QA failed"):
        compose_and_caption(
            manifest_path,
            _DeepgramByCall([SCRIPT]),
            acoustic_seams=True,
            analyze_audio_fn=lambda _path: (),
            plan_acoustic_fn=lambda _evidence, **_kwargs: plan,
            extract_seam_audio_fn=extract_fn,
            acoustic_evaluator=lambda _clips, **_kwargs: report,
            stitch_fn=lambda **_kwargs: (
                b"stitched-video",
                {
                    "stitch_segment_count": 2,
                    "stitch_fps": 24.0,
                    "stitch_audio_video_duration_delta_s": 0.02,
                },
            ),
        )

    saved = _read(manifest_path)
    assert saved["status"] == "acoustic_seam_qa_failed"
    assert saved["acoustic_seam_qa"]["failed_seam_indexes"] == [0]
    assert saved["acoustic_seam_qa"]["seam_retry_map"] == [
        {"seam_index": 0, "adjacent_take_indexes": [0, 1]},
    ]
    assert saved["acoustic_seam_qa"]["recommended_retry_take_indexes"] == [0, 1]


def test_acoustic_duration_failure_persists_provider_diagnostic_retry_indexes(tmp_path):
    from app.features.shot_production.runner import compose_and_caption, reset_failed_take

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["status"] = "transcribed"
        take["transcript_qa"] = {
            "passed": True,
            "first_word_start_seconds": 0.5,
            "final_word_end_seconds": 6.2,
        }
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 6.45,
            "source": "deepgram_word_window",
        }
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    def fail_plan(_evidence, **_kwargs):
        raise ValidationError(
            "Acoustic plan cannot satisfy the duration envelope.",
            {
                "required_seconds": 0.4,
                "total_available_seconds": 0.2,
                "under_capacity_take_indexes": [0, 1],
            },
        )

    with pytest.raises(ValidationError, match="cannot satisfy"):
        compose_and_caption(
            manifest_path,
            _DeepgramByCall([]),
            acoustic_seams=True,
            analyze_audio_fn=lambda _path: (),
            plan_acoustic_fn=fail_plan,
        )

    failed = _read(manifest_path)
    assert failed["status"] == "acoustic_plan_failed"
    assert failed["acoustic_plan_failure"]["recommended_retry_take_indexes"] == [0, 1]
    reset = reset_failed_take(
        manifest_path,
        index=1,
        reason="final take ended too early for the delivery duration",
        retry_guidance="Use natural measured pacing and place the final spoken word near 7.0 seconds.",
    )
    assert reset["takes"][1]["status"] == "planned"
    assert reset["qa_failure_history"][-1]["stage"] == "acoustic_plan"


def test_transcript_safe_planning_failure_persists_adjacent_retry_indexes(tmp_path):
    from app.features.shot_production.runner import compose_and_caption, reset_failed_take

    manifest_path = _manifest_with_raw_takes(tmp_path)
    payload = _read(manifest_path)
    for take in payload["takes"]:
        take["status"] = "transcribed"
        take["transcript_qa"] = {
            "passed": True,
            "first_word_start_seconds": 0.5,
            "final_word_end_seconds": 6.8,
        }
        take["trim_window"] = {
            "start_seconds": 0.0,
            "end_seconds": 7.0,
            "source": "deepgram_word_window",
        }
    payload["visual_qa"] = {"passed": True}
    payload["voice_qa"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    def fail_plan(_evidence, **_kwargs):
        raise ValidationError(
            "No transcript-safe acoustic seam candidate exists.",
            {"seam_index": 0, "rejected_candidate_count": 252},
        )

    with pytest.raises(ValidationError, match="transcript-safe"):
        compose_and_caption(
            manifest_path,
            _DeepgramByCall([]),
            acoustic_seams=True,
            analyze_audio_fn=lambda _path: (),
            plan_acoustic_fn=fail_plan,
        )

    failed = _read(manifest_path)
    assert failed["status"] == "acoustic_plan_failed"
    assert failed["acoustic_plan_failure"]["failed_seam_indexes"] == [0]
    assert failed["acoustic_plan_failure"]["seam_retry_map"] == [
        {"seam_index": 0, "adjacent_take_indexes": [0, 1]},
    ]
    assert failed["acoustic_plan_failure"]["recommended_retry_take_indexes"] == [0, 1]

    reset = reset_failed_take(
        manifest_path,
        index=1,
        reason="incoming take begins inside a retained breath",
        retry_guidance="Begin cleanly without an inhale before the first scripted word.",
    )
    assert reset["takes"][1]["status"] == "planned"
    assert reset["qa_failure_history"][-1]["stage"] == "acoustic_plan"


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


def test_final_media_probe_accepts_one_frame_below_minimum_duration():
    from app.features.shot_production.runner import evaluate_final_media_probe

    report = evaluate_final_media_probe(
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
            "format": {"duration": "14.458333", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        },
        min_duration_seconds=14.5,
        max_duration_seconds=16.5,
    )

    assert report["passed"] is True


def test_upload_persists_remote_head_verification_and_rechecks_without_reupload(tmp_path):
    from app.features.shot_production.runner import upload_final

    class FakeStorage:
        def __init__(self, *, verified):
            self.verified = verified
            self.prepare_calls = []
            self.upload_calls = []
            self.verify_calls = []

        def prepare_video_upload(self, **kwargs):
            self.prepare_calls.append(kwargs)
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
    assert storage.prepare_calls[0]["file_name"].endswith("-minimum-shots-captioned.mp4")
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
        "gaps_seconds": [0.78],
        "failed_seam_indexes": [0],
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
    assert new_windows[0]["start_seconds"] == original_windows[0]["start_seconds"]
    assert new_windows[0]["end_seconds"] == pytest.approx(3.63)
    assert new_windows[1]["start_seconds"] == pytest.approx(0.32)
    assert new_windows[1]["end_seconds"] == pytest.approx(4.60)
    repaired_total = sum(
        window["end_seconds"] - window["start_seconds"] for window in new_windows
    )
    assert repaired_total == pytest.approx(original_total)
    assert repaired["status"] == "seam_repair_planned"
    assert "seam_qa" not in repaired and "stitch" not in repaired
    assert repaired["composition_history"][-1]["snapshot"]["seam_qa"]["gaps_seconds"] == [
        0.78,
    ]
    audit = repaired["seam_repair_history"][-1]
    assert audit["failed_seam_indexes"] == [0]
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
    payload["takes"][1]["transcript_qa"] = {"passed": False}
    payload["takes"][1]["status"] = "transcript_failed"
    payload["visual_qa"] = {"passed": False}
    payload["stitch"] = {"path": "old"}
    payload["seam_qa"] = {"passed": True}
    payload["caption"] = {"path": "old-caption"}
    payload["media_qa"] = {"passed": True}
    payload["upload_verification"] = {"passed": True}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    original_contract = payload["request_contract_sha256"]
    original_seed = payload["takes"][1]["seed"]
    reset_failed_take(
        manifest_path,
        index=1,
        reason="transcript merged two distinct German words",
        retry_guidance=(
            "Pronounce every written word distinctly. Keep Zentimeter and Steigung as two separate words."
        ),
    )
    reset = _read(manifest_path)
    take = reset["takes"][1]
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
    before["takes"][1]["status"] = "transcript_failed"
    before["takes"][1]["transcript_qa"] = {"passed": False, "word_error_rate": 0.18}
    before["status"] = "transcript_failed"
    manifest_path.write_text(json.dumps(before), encoding="utf-8")
    replacement = "Manchmal wird schon eine leichte Steigung zu einem unnötigen Kampf. Das zehrt an den Kräften."

    revised = revise_failed_beat(
        manifest_path,
        index=1,
        replacement_text=replacement,
        reason="Repeated Veo delivery merged the original phrase into the wrong German compound",
    )

    assert revised["script"]["text"] == (
        "Jeder, der einen Rollstuhl nutzt, weiß genau: "
        "Normgerechte Rampen sind oft trotzdem eine echte Qual. "
        f"{replacement}"
    )
    assert revised["script"]["source"] == "app.features.topics.agents.generate_dialog_scripts"
    assert revised["script"]["source_payload"]["script"] == SCRIPT
    assert revised["script"]["editorial_revisions"][-1]["original_text"] == before["takes"][1]["beat"]["text"]
    assert [take["duration_seconds"] for take in revised["takes"]] == [8, 8]
    assert [take["beat"]["text"] for take in revised["takes"]][1] == replacement
    assert revised["takes"][1]["attempt"] == 2
    assert revised["takes"][1]["seed"] == before["takes"][1]["seed"] + 1000
    assert replacement in revised["takes"][1]["prompt"]
    assert before["takes"][1]["beat"]["text"] not in revised["takes"][1]["prompt"]
    assert revised["takes"][1]["raw"] is None
    assert revised["takes"][1]["operation"] is None
    assert revised["takes"][0]["raw"] == before["takes"][0]["raw"]
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
        indexes=[0, 1],
        reason="manual contact-sheet review found baked-in generated subtitle artifacts",
        retry_guidance=guidance,
    )

    for index in (0, 1):
        assert reset["takes"][index]["status"] == "planned"
        assert reset["takes"][index]["raw"] is None
        assert reset["takes"][index]["operation"] is None
        assert reset["takes"][index]["seed"] == before["takes"][index]["seed"] + 1000
        assert guidance in reset["takes"][index]["prompt"]
    assert "visual_qa" not in reset
