from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import io
import json
import threading
from types import SimpleNamespace

from PIL import Image
import pytest

from app.core.errors import StateTransitionError, ThirdPartyError, ValidationError


def _png_bytes() -> bytes:
    image = Image.new("RGB", (90, 160))
    image.putdata(
        [
            (x * 255 // 89, y * 255 // 159, (x + y) % 256)
            for y in range(160)
            for x in range(90)
        ]
    )
    target = io.BytesIO()
    image.save(target, format="PNG")
    return target.getvalue()


def _takes(count: int = 7) -> tuple[bytes, list[dict]]:
    from app.features.shot_production.shot_deck import derive_shot_deck

    master = _png_bytes()
    master_hash = sha256(master).hexdigest()
    deck = derive_shot_deck(
        approved_master_bytes=master,
        expected_sha256=master_hash,
        mime_type="image/png",
        shot_count=count,
    )
    takes = []
    for index, shot in enumerate(deck):
        takes.append(
            {
                "id": f"take-{index}",
                "run_id": "run-1",
                "take_index": index,
                "attempt": 1,
                "provider_duration_seconds": 8,
                "provider_model": "veo-3.1-generate-001",
                "seed": 1000 + index,
                "request_hash": f"request-{index}",
                "submission_state": "planned",
                "operation_id": None,
                "shot_transform": {
                    "index": shot.index,
                    "name": shot.name,
                    "crop_box": list(shot.crop_box),
                    "width": shot.width,
                    "height": shot.height,
                    "mime_type": shot.mime_type,
                    "source_sha256": shot.source_sha256,
                    "output_sha256": shot.output_sha256,
                },
                "request_contract": {
                    "prompt": f"Prompt {index}",
                    "negative_prompt": "No identity drift.",
                    "aspect_ratio": "9:16",
                    "provider_duration_seconds": 8,
                    "provider_model": "veo-3.1-generate-001",
                    "resolution": "1080p",
                    "seed": 1000 + index,
                    "shot_sha256": shot.output_sha256,
                },
            }
        )
    return master, takes


def _actor_reference_snapshot(image_bytes: bytes) -> dict:
    digest = sha256(image_bytes).hexdigest()
    return {
        "actor_references": [
            {
                "role": "actor_front",
                "storage_uri": "https://storage/actor-front.png",
                "mime_type": "image/png",
                "byte_length": len(image_bytes),
                "sha256": digest,
            },
            {
                "role": "actor_three_quarter",
                "storage_uri": "https://storage/actor-three-quarter.png",
                "mime_type": "image/png",
                "byte_length": len(image_bytes),
                "sha256": digest,
            },
        ]
    }


class FakeRepo:
    def __init__(self, *, stage: str = "generating", take_count: int = 7):
        from app.features.semantic_videos.visual_contract import (
            SCENE_IDENTITY_COMPONENT_FIELDS,
            build_actor_reference_fingerprint,
            build_scene_plate_generation_contract,
            build_visual_contract,
        )

        master, takes = _takes(take_count)
        actor_references = [
            {
                "role": "actor_front",
                "storage_uri": "https://storage/actor-front.png",
                "mime_type": "image/png",
                "byte_length": len(master),
                "sha256": sha256(master).hexdigest(),
            },
            {
                "role": "actor_three_quarter",
                "storage_uri": "https://storage/actor-three-quarter.png",
                "mime_type": "image/png",
                "byte_length": len(master),
                "sha256": sha256(master).hexdigest(),
            },
        ]
        actor_fingerprint = build_actor_reference_fingerprint(actor_references)
        generation_contract = build_scene_plate_generation_contract(
            actor_reference_fingerprint=actor_fingerprint
        )
        visual_reference = {
            "scene_key": "garden_patio_a",
            "scene_description": "the exact supplied garden patio",
            "wardrobe_key": "grey_cardigan",
            "wardrobe_description": "light-grey cardigan over a plain white top",
            "location_reference": {
                "scene_key": "garden_patio_a",
                "sha256": "3" * 64,
            },
        }
        visual_contract = build_visual_contract(visual_reference)
        identity_gate = {
            "status": "passed",
            "passed": True,
            "evaluator_model": generation_contract["identity_evaluator_model"],
            "evaluator_contract_version": generation_contract[
                "identity_evaluator_contract_version"
            ],
            "evaluated_actor_reference_fingerprint": actor_fingerprint,
            "candidate_sha256": sha256(master).hexdigest(),
            "component_results": {
                field: True for field in SCENE_IDENTITY_COMPONENT_FIELDS
            },
            "confidence": 0.99,
            "blocking_reasons": [],
            "observed_differences": [],
            "evaluated_at": "2026-07-26T00:00:00+00:00",
        }
        self.master = master
        self.run = {
            "id": "run-1",
            "post_id": "post-1",
            "batch_id": "batch-1",
            "stage": stage,
            "requested_duration_seconds": take_count * 8,
            "plan_hash": "a" * 64,
            "master_hash": sha256(master).hexdigest(),
            "master_snapshot": {
                "storage_uri": "https://storage/master.png",
                "sha256": sha256(master).hexdigest(),
                "byte_length": len(master),
                "mime_type": "image/png",
                "provider_model": generation_contract["model"],
                "visual_contract_hash": visual_contract["contract_hash"],
                "generation_contract_hash": generation_contract["contract_hash"],
                "actor_reference_fingerprint": actor_fingerprint,
                "identity_gate_result": identity_gate,
                "identity_attestation": True,
                "attestation_version": "semantic-actor-identity-v1",
                "approved_by": "operator@example.com",
                "approved_at": "2026-07-26T00:00:00+00:00",
            },
            "reference_snapshot": {
                **visual_reference,
                "actor_references": actor_references,
                "actor_reference_fingerprint": actor_fingerprint,
                "scene_plate_generation_contract": generation_contract,
                "visual_contract": visual_contract,
            },
            "plan_snapshot": {
                "generation_contract_hash": generation_contract["contract_hash"],
                "actor_reference_fingerprint": actor_fingerprint,
                "visual_contract_hash": visual_contract["contract_hash"],
            },
            "artifact_prefix": "semantic-videos/batch-1/post-1",
            "lease_owner": "worker-1",
            "lease_token": "lease-1",
            "max_submission_count": take_count,
            "max_estimated_cost_usd": f"{take_count * 3.2:.2f}",
        }
        self.takes = takes
        self.events: list[tuple] = []
        self.reserve_error: Exception | None = None
        self.renew_error: Exception | None = None
        self.release_error: Exception | None = None
        self.renewed = threading.Event()
        self.claimable = True

    def claim_run(self, *, run_id, worker_id, lease_seconds):
        self.events.append(("claim", run_id, worker_id, lease_seconds))
        return deepcopy(self.run) if self.claimable else None

    def list_attempts(self, run_id):
        assert run_id == self.run["id"]
        return deepcopy(self.takes)

    def renew_run(self, *, run_id, worker_id, lease_token, lease_seconds):
        self.events.append(("renew", run_id, worker_id, lease_token, lease_seconds))
        self.renewed.set()
        if self.renew_error:
            raise self.renew_error
        return deepcopy(self.run)

    def reserve_submission(self, *, run_id, take_id, worker_id, lease_token):
        self.events.append(("reserve", take_id))
        if self.reserve_error:
            raise self.reserve_error
        take = self._take(take_id)
        if take["submission_state"] != "planned":
            raise RuntimeError("not planned")
        take["submission_state"] = "reserved"
        return deepcopy(take)

    def persist_submission_intent(self, *, run_id, take_id, worker_id, lease_token, request_hash):
        take = self._take(take_id)
        assert take["submission_state"] == "reserved"
        assert take["request_hash"] == request_hash
        take["submission_state"] = "intent_persisted"
        self.events.append(("intent", take_id))
        return deepcopy(take)

    def persist_accepted_operation(
        self, *, run_id, take_id, worker_id, lease_token, operation_id, provider_model
    ):
        take = self._take(take_id)
        assert take["submission_state"] == "intent_persisted"
        take.update(submission_state="submitted", operation_id=operation_id)
        self.events.append(("accepted", take_id, operation_id, provider_model))
        return deepcopy(take)

    def persist_submission_unknown(self, *, run_id, take_id, worker_id, lease_token, error):
        take = self._take(take_id)
        take["submission_state"] = "submission_unknown"
        self.events.append(("unknown", take_id, error["code"]))
        return deepcopy(take)

    def persist_provider_failure(self, *, run_id, take_id, worker_id, lease_token, error):
        take = self._take(take_id)
        take["submission_state"] = "failed"
        self.run["stage"] = "retry_approval_required"
        self.events.append(("provider_failed", take_id, error["code"]))
        return deepcopy(self.run)

    def persist_completed_take(
        self,
        *,
        run_id,
        take_id,
        worker_id,
        lease_token,
        provider_video_uri,
        raw_artifact_uri,
        raw_artifact_sha256,
    ):
        take = self._take(take_id)
        assert take["submission_state"] == "submitted"
        take.update(
            submission_state="completed",
            provider_video_uri=provider_video_uri,
            raw_artifact_uri=raw_artifact_uri,
            raw_artifact_sha256=raw_artifact_sha256,
        )
        self.events.append(("completed_take", take_id, raw_artifact_uri))
        return deepcopy(take)

    def advance_stage(self, *, run_id, worker_id, lease_token, expected_stage, next_stage, artifacts):
        assert self.run["stage"] == expected_stage
        self.run["stage"] = next_stage
        self.events.append(("advance", expected_stage, next_stage, deepcopy(artifacts)))
        return deepcopy(self.run)

    def require_retry_approval(
        self, *, run_id, worker_id, lease_token, expected_stage, failed_take_indexes, evidence
    ):
        self.run["stage"] = "retry_approval_required"
        self.events.append(("retry_required", tuple(failed_take_indexes), deepcopy(evidence)))
        return deepcopy(self.run)

    def complete_run(
        self,
        *,
        run_id,
        worker_id,
        lease_token,
        final_video_uri,
        final_video_sha256,
        final_caption_uri,
        final_caption_sha256,
        artifact_manifest,
    ):
        self.run["stage"] = "completed"
        self.events.append(("complete_run", final_video_uri, final_caption_uri, deepcopy(artifact_manifest)))
        return deepcopy(self.run)

    def release_run(self, *, run_id, worker_id, lease_token):
        self.events.append(("release", run_id, worker_id, lease_token))
        if self.release_error:
            raise self.release_error

    def persist_worker_exception(
        self, *, run_id, worker_id, lease_token, stage, error
    ):
        self.run["error"] = deepcopy(error)
        self.events.append(("worker_exception", stage, deepcopy(error)))
        return deepcopy(self.run)

    def _take(self, take_id):
        return next(take for take in self.takes if take["id"] == take_id)


class FakeVertex:
    def __init__(self):
        self.submit_calls: list[dict] = []
        self.poll_calls: list[dict] = []
        self.submit_error: Exception | None = None
        self.poll_results: dict[str, dict] = {}

    def submit_image_video(self, **kwargs):
        self.submit_calls.append(deepcopy(kwargs))
        if self.submit_error:
            raise self.submit_error
        return {"operation_id": f"operation-{len(self.submit_calls)}"}

    def check_operation_status(self, **kwargs):
        self.poll_calls.append(deepcopy(kwargs))
        return deepcopy(
            self.poll_results.get(
                kwargs["operation_id"],
                {"done": False, "status": "processing", "video_uri": None},
            )
        )


class FakeStorage:
    def __init__(self, master: bytes):
        self.master = master
        self.upload_calls: list[dict] = []

    def download_video(self, *, video_url, correlation_id):
        assert video_url in {
            "https://storage/master.png",
            "https://storage/actor-front.png",
            "https://storage/actor-three-quarter.png",
        }
        return self.master

    def upload_video(self, **kwargs):
        self.upload_calls.append(deepcopy(kwargs))
        return {
            "url": f"https://storage/{kwargs['object_key']}",
            "storage_key": kwargs["object_key"],
            "sha256": sha256(kwargs["video_bytes"]).hexdigest(),
            "size": len(kwargs["video_bytes"]),
        }


class FakeStages:
    def __init__(self, result=None):
        self.result = result or {"passed": True, "artifacts": {}}
        self.calls = []

    def run_stage(self, *, stage, run, takes):
        self.calls.append((stage, deepcopy(run), deepcopy(takes)))
        return deepcopy(self.result)


def _worker(repo: FakeRepo, vertex: FakeVertex | None = None, stages: FakeStages | None = None):
    from workers.semantic_video_worker import SemanticVideoWorker

    return SemanticVideoWorker(
        repo=repo,
        vertex=vertex or FakeVertex(),
        storage=FakeStorage(repo.master),
        stage_runner=stages or FakeStages(),
        video_loader=lambda uri: f"video:{uri}".encode(),
        worker_id="worker-1",
        max_inflight=2,
    )


def test_worker_persists_intent_before_each_provider_call_and_acceptance_immediately_after():
    repo = FakeRepo()
    vertex = FakeVertex()
    worker = _worker(repo, vertex)

    result = worker.tick("run-1")

    assert result.action == "submitted"
    assert repo.events[0] == ("claim", "run-1", "worker-1", 120)
    assert len(vertex.submit_calls) == 2
    assert all(call["sample_count"] == 1 for call in vertex.submit_calls)
    assert all(call["generate_audio"] is True for call in vertex.submit_calls)
    assert all(call["resolution"] == "1080p" for call in vertex.submit_calls)
    assert [event[0] for event in repo.events] == [
        "claim",
        "reserve",
        "intent",
        "accepted",
        "reserve",
        "intent",
        "accepted",
        "release",
    ]


@pytest.mark.parametrize(
    "tamper",
    [
        "legacy_generation_contract",
        "changed_actor_reference",
        "failed_identity_gate",
        "missing_human_attestation",
    ],
)
def test_worker_blocks_invalid_identity_evidence_before_any_paid_call(tamper):
    repo = FakeRepo(take_count=1)
    if tamper == "legacy_generation_contract":
        repo.run["reference_snapshot"].pop("scene_plate_generation_contract")
    elif tamper == "changed_actor_reference":
        repo.run["reference_snapshot"]["actor_references"][0]["sha256"] = "f" * 64
    elif tamper == "failed_identity_gate":
        repo.run["master_snapshot"]["identity_gate_result"]["passed"] = False
    else:
        repo.run["master_snapshot"]["identity_attestation"] = False
    vertex = FakeVertex()
    worker = _worker(repo, vertex)

    with pytest.raises((ValidationError, StateTransitionError)):
        worker.tick("run-1")

    assert vertex.submit_calls == []
    assert not any(event[0] in {"reserve", "intent", "accepted"} for event in repo.events)


def test_worker_processes_fifty_second_run_in_bounded_submission_waves():
    repo = FakeRepo()
    vertex = FakeVertex()
    worker = _worker(repo, vertex)

    for expected_total in (2, 4, 6, 7):
        worker.tick("run-1")
        assert len(vertex.submit_calls) == expected_total
        for take in repo.takes:
            if take["submission_state"] == "submitted":
                take["submission_state"] = "completed"

    assert [call["duration_seconds"] for call in vertex.submit_calls] == [8] * 7


def test_worker_persists_a_fenced_runtime_exception_before_releasing_the_lease():
    repo = FakeRepo(take_count=1)
    repo.reserve_error = RuntimeError("production-only failure")
    worker = _worker(repo)

    with pytest.raises(RuntimeError, match="production-only failure"):
        worker.tick("run-1")

    diagnostic = next(event for event in repo.events if event[0] == "worker_exception")
    assert diagnostic[1] == "generating"
    assert diagnostic[2] == {
        "code": "RuntimeError",
        "message": "production-only failure",
        "worker_id": "worker-1",
    }
    assert repo.events[-1][0] == "release"


def test_identity_qa_provider_failure_becomes_advisory_without_paid_retry():
    from workers.semantic_video_worker import SemanticVideoWorker, WorkerTickResult

    repo = FakeRepo(stage="identity_qa", take_count=2)
    for take in repo.takes:
        take["submission_state"] = "completed"

    class UnavailableIdentityQA:
        def run_stage(self, *, stage, run, takes):
            assert stage == "identity_qa"
            raise ThirdPartyError(
                "Vertex Gemini generateContent failed",
                {"status_code": 503, "model": "gemini-2.5-flash"},
            )

    worker = SemanticVideoWorker(
        repo=repo,
        vertex=FakeVertex(),
        storage=FakeStorage(repo.master),
        stage_runner=UnavailableIdentityQA(),
        video_loader=lambda uri: f"video:{uri}".encode(),
        worker_id="worker-1",
        max_inflight=2,
        lease_seconds=120,
    )

    result = worker.tick("run-1")

    assert result == WorkerTickResult(
        run_id="run-1",
        stage="voice_qa",
        action="stage_advanced_with_qa_advisory",
    )
    advance_event = next(event for event in repo.events if event[0] == "advance")
    assert advance_event[1:3] == ("identity_qa", "voice_qa")
    assert advance_event[3]["qa_advisory"] == {
        "required": True,
        "stage": "identity_qa",
        "failed_take_indexes": [0, 1],
        "message": "Vertex Gemini generateContent failed",
        "paid_retry_required": False,
    }
    assert advance_event[3]["qa_failure"] is None
    assert not any(event[0] == "retry_required" for event in repo.events)
    assert not any(event[0] == "worker_exception" for event in repo.events)
    assert repo.events[-1][0] == "release"


def test_worker_renews_the_short_lease_while_a_stage_is_blocking():
    from workers.semantic_video_worker import SemanticVideoWorker

    repo = FakeRepo(stage="transcript_qa", take_count=1)

    class RenewalWaitingStage:
        def run_stage(self, *, stage, run, takes):
            assert repo.renewed.wait(timeout=1.0), "lease heartbeat did not renew"
            return {"passed": True, "artifacts": {}}

    worker = SemanticVideoWorker(
        repo=repo,
        vertex=FakeVertex(),
        storage=FakeStorage(repo.master),
        stage_runner=RenewalWaitingStage(),
        video_loader=lambda uri: f"video:{uri}".encode(),
        worker_id="worker-1",
        max_inflight=2,
        lease_seconds=120,
        heartbeat_seconds=0.01,
    )

    result = worker.tick("run-1")

    assert result.action == "stage_advanced"
    event_names = [event[0] for event in repo.events]
    assert event_names.index("renew") < event_names.index("advance")
    assert event_names[-1] == "release"
    assert next(event for event in repo.events if event[0] == "renew") == (
        "renew",
        "run-1",
        "worker-1",
        "lease-1",
        120,
    )


def test_worker_best_effort_release_does_not_mask_the_original_failure():
    repo = FakeRepo(take_count=1)
    repo.reserve_error = RuntimeError("production-only failure")
    repo.release_error = RuntimeError("lease release unavailable")
    worker = _worker(repo)

    with pytest.raises(RuntimeError, match="production-only failure"):
        worker.tick("run-1")

    assert repo.events[-1][0] == "release"


def test_worker_polls_an_accepted_operation_without_resubmitting():
    repo = FakeRepo(take_count=1)
    take = repo.takes[0]
    take.update(submission_state="submitted", operation_id="existing-operation")
    vertex = FakeVertex()
    worker = _worker(repo, vertex)

    result = worker.tick("run-1")

    assert result.action == "polling"
    assert vertex.submit_calls == []
    assert [call["operation_id"] for call in vertex.poll_calls] == ["existing-operation"]


def test_worker_persists_ambiguous_submission_as_unknown_and_never_retries():
    repo = FakeRepo(take_count=1)
    vertex = FakeVertex()
    vertex.submit_error = TimeoutError("response lost")
    worker = _worker(repo, vertex)

    first = worker.tick("run-1")
    second = worker.tick("run-1")

    assert first.action == "submission_unknown"
    assert second.action == "blocked_unknown_submission"
    assert len(vertex.submit_calls) == 1
    assert repo.takes[0]["submission_state"] == "submission_unknown"


def test_worker_uploads_checksum_addressed_raw_artifact_after_poll_completion():
    repo = FakeRepo(take_count=1)
    take = repo.takes[0]
    take.update(submission_state="submitted", operation_id="operation-1")
    vertex = FakeVertex()
    vertex.poll_results["operation-1"] = {
        "done": True,
        "status": "completed",
        "video_uri": "gs://bucket/generated.mp4",
    }
    storage = FakeStorage(repo.master)
    from workers.semantic_video_worker import SemanticVideoWorker

    worker = SemanticVideoWorker(
        repo=repo,
        vertex=vertex,
        storage=storage,
        stage_runner=FakeStages(),
        video_loader=lambda _uri: b"raw-video-bytes",
        worker_id="worker-1",
    )

    result = worker.tick("run-1")

    digest = sha256(b"raw-video-bytes").hexdigest()
    assert result.action == "raw_completed"
    assert storage.upload_calls[0]["object_key"].endswith(f"/{digest}.mp4")
    assert repo.takes[0]["raw_artifact_sha256"] == digest


def test_worker_never_persists_inline_video_bytes_as_provider_uri():
    repo = FakeRepo(take_count=1)
    take = repo.takes[0]
    take.update(submission_state="submitted", operation_id="operation-inline-1")
    vertex = FakeVertex()
    vertex.poll_results["operation-inline-1"] = {
        "done": True,
        "status": "completed",
        "video_uri": "data:video/mp4;base64,AAAA",
    }
    worker = _worker(repo, vertex)

    result = worker.tick("run-1")

    assert result.action == "raw_completed"
    assert repo.takes[0]["provider_video_uri"] == "vertex-operation://operation-inline-1"
    assert not repo.takes[0]["provider_video_uri"].startswith("data:")


def test_worker_provider_operation_failure_stops_and_requires_retry_approval():
    repo = FakeRepo(take_count=1)
    repo.takes[0].update(submission_state="submitted", operation_id="operation-1")
    vertex = FakeVertex()
    vertex.poll_results["operation-1"] = {
        "done": True,
        "status": "failed",
        "video_uri": None,
        "error": {"code": 13, "message": "generation failed"},
    }
    worker = _worker(repo, vertex)

    result = worker.tick("run-1")

    assert result.action == "provider_failed"
    assert repo.run["stage"] == "retry_approval_required"
    assert len(vertex.poll_calls) == 1
    assert vertex.submit_calls == []


def test_worker_multitake_evaluator_qa_failure_is_advisory_and_never_auto_retries():
    repo = FakeRepo(stage="identity_qa", take_count=2)
    stages = FakeStages(
        {
            "passed": False,
            "failed_take_indexes": [0],
            "artifacts": {"identity": {"score": 0.42}},
        }
    )
    vertex = FakeVertex()
    worker = _worker(repo, vertex, stages)

    result = worker.tick("run-1")
    second = worker.tick("run-1")

    assert result.action == "stage_advanced_with_qa_advisory"
    assert result.stage == "voice_qa"
    assert second.action == "stage_advanced_with_qa_advisory"
    assert vertex.submit_calls == []
    assert not any(event[0] == "retry_required" for event in repo.events)
    advisory = next(
        event[3]["qa_advisory"]
        for event in repo.events
        if event[0] == "advance" and event[1] == "identity_qa"
    )
    assert advisory == {
        "required": True,
        "stage": "identity_qa",
        "failed_take_indexes": [0],
        "message": "Automated QA recommends manual review.",
        "paid_retry_required": False,
    }


def test_worker_still_blocks_when_composition_qa_cannot_produce_delivery():
    repo = FakeRepo(stage="acoustic_qa", take_count=2)
    stages = FakeStages(
        {
            "passed": False,
            "failed_take_indexes": [0],
            "artifacts": {"qa_failure": {"message": "Composition is not playable."}},
        }
    )
    worker = _worker(repo, FakeVertex(), stages)

    result = worker.tick("run-1")

    assert result.action == "retry_approval_required"
    assert any(event[0] == "retry_required" for event in repo.events)


def test_worker_delivers_single_paid_eight_second_take_when_qa_is_advisory():
    repo = FakeRepo(stage="identity_qa", take_count=1)
    raw_hash = "d" * 64
    repo.takes[0].update(
        submission_state="completed",
        raw_artifact_uri="https://storage/paid-8s.mp4",
        raw_artifact_sha256=raw_hash,
    )
    stages = FakeStages(
        {
            "passed": False,
            "failed_take_indexes": [0],
            "artifacts": {
                "qa_failure": {
                    "stage": "identity_qa",
                    "message": "Automated identity score was below the advisory threshold.",
                }
            },
        }
    )
    vertex = FakeVertex()
    worker = _worker(repo, vertex, stages)

    result = worker.tick("run-1")

    assert result.action == "completed_with_qa_advisory"
    assert repo.run["stage"] == "completed"
    assert vertex.submit_calls == []
    assert not any(event[0] == "retry_required" for event in repo.events)
    advances = [event[1:3] for event in repo.events if event[0] == "advance"]
    assert advances == [
        ("identity_qa", "voice_qa"),
        ("voice_qa", "acoustic_qa"),
        ("acoustic_qa", "composing"),
        ("composing", "uploading"),
    ]
    completion = next(event for event in repo.events if event[0] == "complete_run")
    assert completion[1] == "https://storage/paid-8s.mp4"
    assert completion[2] == "https://storage/paid-8s.mp4"
    assert completion[3]["qa_advisory"]["paid_retry_required"] is False
    assert completion[3]["delivery"]["mode"] == "single_paid_take_manual_review"


def test_worker_final_captioned_artifact_completes_post_directly():
    repo = FakeRepo(stage="uploading", take_count=1)
    stages = FakeStages(
        {
            "passed": True,
            "artifacts": {"delivery": {"duration_seconds": 8.0}},
            "final_video_uri": "https://storage/final.mp4",
            "final_video_sha256": "b" * 64,
            "final_caption_uri": "https://storage/final-captioned.mp4",
            "final_caption_sha256": "c" * 64,
        }
    )
    worker = _worker(repo, FakeVertex(), stages)

    result = worker.tick("run-1")

    assert result.action == "completed"
    completion = next(event for event in repo.events if event[0] == "complete_run")
    assert completion[2] == "https://storage/final-captioned.mp4"
    assert not any(event[0] == "advance" for event in repo.events)


def test_production_stage_runner_projects_only_checksum_verified_durable_delivery():
    from workers.semantic_video_worker import ProductionStageRunner

    runner = ProductionStageRunner(storage=SimpleNamespace())
    run = {
        "id": "run-1",
        "stage": "uploading",
        "artifact_manifest": {
            "delivery": {
                "passed": True,
                "raw": {"url": "https://cdn/final.mp4", "sha256": "b" * 64},
                "captioned": {
                    "url": "https://cdn/final-captioned.mp4",
                    "sha256": "c" * 64,
                },
            }
        },
    }

    result = runner.run_stage(stage="uploading", run=run, takes=[])

    assert result == {
        "passed": True,
        "artifacts": run["artifact_manifest"],
        "final_video_uri": "https://cdn/final.mp4",
        "final_video_sha256": "b" * 64,
        "final_caption_uri": "https://cdn/final-captioned.mp4",
        "final_caption_sha256": "c" * 64,
    }


def test_production_stage_runner_rejects_unverified_delivery_projection():
    from workers.semantic_video_worker import ProductionStageRunner

    runner = ProductionStageRunner(storage=SimpleNamespace())
    with pytest.raises(Exception, match="delivery"):
        runner.run_stage(
            stage="uploading",
            run={"id": "run-1", "artifact_manifest": {"delivery": {"passed": False}}},
            takes=[],
        )


def test_production_stage_runner_materializes_canonical_exact_16s_contract(tmp_path):
    from workers.semantic_video_worker import ProductionStageRunner

    master, takes = _takes(2)
    raw_payloads = {}
    for index, take in enumerate(takes):
        raw_bytes = f"raw-take-{index}".encode()
        raw_url = f"https://storage/raw-{index}.mp4"
        raw_payloads[raw_url] = raw_bytes
        take.update(
            submission_state="completed",
            raw_artifact_uri=raw_url,
            raw_artifact_sha256=sha256(raw_bytes).hexdigest(),
        )

    master_url = "https://storage/master.png"

    class ManifestStorage:
        def download_video(self, *, video_url, correlation_id):
            del correlation_id
            if video_url in {
                master_url,
                "https://storage/actor-front.png",
                "https://storage/actor-three-quarter.png",
            }:
                return master
            return raw_payloads[video_url]

    run = {
        "id": "run-16s",
        "created_at": "2026-07-20T12:00:00+00:00",
        "updated_at": "2026-07-20T12:00:00+00:00",
        "requested_duration_seconds": 16,
        "master_hash": sha256(master).hexdigest(),
        "master_snapshot": {
            "storage_uri": master_url,
            "sha256": sha256(master).hexdigest(),
            "byte_length": len(master),
            "mime_type": "image/png",
        },
        "reference_snapshot": _actor_reference_snapshot(master),
        "script_hash": sha256(b"script").hexdigest(),
        "script_snapshot": {"text": "Ein exakter Testtext fuer zwei Takes."},
    }

    runner = ProductionStageRunner(storage=ManifestStorage(), work_root=tmp_path)
    manifest_path = runner._materialize_manifest(run, takes)  # noqa: SLF001
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["script"]["delivery_duration_seconds"] == {
        "requested": 16.0,
        "minimum": 16.0 - (1.0 / 24.0),
        "maximum": 16.0 + (1.0 / 24.0),
    }


@pytest.mark.parametrize(
    ("creation_mode", "source"),
    [
        ("manual_semantic_ugc", "manual_semantic_ugc"),
        (
            "semantic_ugc",
            "app.features.topics.semantic_scripts.generate_semantic_script",
        ),
    ],
)
def test_production_stage_runner_preserves_semantic_script_provenance(
    tmp_path,
    creation_mode,
    source,
):
    from workers.semantic_video_worker import ProductionStageRunner

    master, takes = _takes(1)
    raw_bytes = b"raw-take"
    raw_url = "https://storage/raw.mp4"
    takes[0].update(
        submission_state="completed",
        raw_artifact_uri=raw_url,
        raw_artifact_sha256=sha256(raw_bytes).hexdigest(),
    )
    master_url = "https://storage/master.png"

    class ManifestStorage:
        def download_video(self, *, video_url, correlation_id):
            del correlation_id
            return (
                master
                if video_url
                in {
                    master_url,
                    "https://storage/actor-front.png",
                    "https://storage/actor-three-quarter.png",
                }
                else raw_bytes
            )

    run = {
        "id": f"run-{creation_mode}",
        "requested_duration_seconds": 16,
        "master_hash": sha256(master).hexdigest(),
        "master_snapshot": {
            "storage_uri": master_url,
            "sha256": sha256(master).hexdigest(),
            "byte_length": len(master),
            "mime_type": "image/png",
        },
        "reference_snapshot": _actor_reference_snapshot(master),
        "script_hash": sha256(b"script").hexdigest(),
        "script_snapshot": {
            "text": "Ein exakter manueller Testtext.",
            "source": source,
            "creation_mode": creation_mode,
            "script_review_status": "approved",
            "target_duration_seconds": 16,
        },
    }

    runner = ProductionStageRunner(storage=ManifestStorage(), work_root=tmp_path)
    manifest_path = runner._materialize_manifest(run, takes)  # noqa: SLF001
    script = json.loads(manifest_path.read_text(encoding="utf-8"))["script"]

    assert script["source"] == source
    assert script["creation_mode"] == creation_mode
    assert script["script_review_status"] == "approved"
    assert script["target_duration_seconds"] == 16
    assert "target_length_tier" not in script
