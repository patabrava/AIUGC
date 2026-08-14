from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock
from time import sleep

import pytest

from app.core.errors import SuccessResponse, ValidationError


class FakeJobRepository:
    def __init__(self, count: int) -> None:
        self.lock = Lock()
        self.jobs = [
            {
                "id": f"job-{index}",
                "post_id": f"post-{index}",
                "expected_revision": None,
                "requested_by": "stress@test.invalid",
                "correlation_id": f"stress-{index}",
                "deadline_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=8)
                ).isoformat(),
            }
            for index in range(count)
        ]
        self.finished = []
        self.renewed = []
        self.persisted_job = None
        self.heartbeats = []

    def claim_scene_image_job(
        self, *, worker_id, lease_seconds, timeout_seconds=None
    ):
        with self.lock:
            if not self.jobs:
                return None
            job = self.jobs.pop(0)
            return {**job, "lease_token": f"lease-{job['id']}"}

    def finish_scene_image_job(self, **kwargs):
        with self.lock:
            self.finished.append(kwargs)
        return kwargs

    def renew_scene_image_job(self, **kwargs):
        with self.lock:
            self.renewed.append(kwargs)
        return {"lease_expires_at": "2099-01-01T00:00:00+00:00"}

    def get_scene_image_job(self, _post_id, **_kwargs):
        return self.persisted_job

    def probe_scene_image_queue(self, **_kwargs):
        return "semantic-scene-image-v2"

    def heartbeat_scene_image_worker(self, **kwargs):
        self.heartbeats.append(kwargs)
        return kwargs


def test_scene_image_worker_persists_one_successful_image(monkeypatch):
    from workers import semantic_scene_image_worker as module

    repo = FakeJobRepository(1)
    calls = []
    monkeypatch.setattr(
        module,
        "generate_candidates",
        lambda post_id, payload, request: (
            calls.append((post_id, payload.candidate_count, request.state.correlation_id))
            or SuccessResponse(data={"run_id": "run-1"})
        ),
    )

    result = module.SemanticSceneImageWorker(repo=repo, worker_id="worker-1").tick()

    assert result["action"] == "completed"
    assert calls == [("post-0", 1, "stress-0")]
    # Successful run persistence and queue completion are one database
    # transaction inside generate_candidates; the worker must not acknowledge
    # the same result a second time.
    assert repo.finished == []


def test_scene_image_worker_records_failure_without_requeueing(monkeypatch):
    from workers import semantic_scene_image_worker as module

    repo = FakeJobRepository(1)

    def fail(*_args, **_kwargs):
        raise RuntimeError("provider timeout")

    monkeypatch.setattr(module, "generate_candidates", fail)
    result = module.SemanticSceneImageWorker(repo=repo, worker_id="worker-1").tick()

    assert result["action"] == "failed"
    assert len(repo.finished) == 1
    assert repo.finished[0]["status"] == "failed"
    assert repo.finished[0]["error"]["message"] == "provider timeout"


def test_scene_image_worker_renews_lease_during_blocking_generation(monkeypatch):
    from workers import semantic_scene_image_worker as module

    repo = FakeJobRepository(1)

    def generate(*_args, **_kwargs):
        sleep(0.08)
        return SuccessResponse(data={"run_id": "run-heartbeat"})

    monkeypatch.setattr(module, "generate_candidates", generate)
    result = module.SemanticSceneImageWorker(
        repo=repo,
        worker_id="heartbeat-worker",
        lease_seconds=180,
        heartbeat_seconds=0.02,
    ).tick()

    assert result["action"] == "completed"
    assert len(repo.renewed) >= 2
    assert {renewal["job_id"] for renewal in repo.renewed} == {"job-0"}
    assert repo.finished == []


def test_committed_lease_renewal_with_lost_response_is_reconciled(monkeypatch):
    from workers import semantic_scene_image_worker as module

    class AckLossRepository(FakeJobRepository):
        def claim_scene_image_job(self, *, worker_id, lease_seconds, **_kwargs):
            job = super().claim_scene_image_job(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            job.update(
                {
                    "status": "processing",
                    "worker_id": worker_id,
                    "lease_expires_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
                    ).isoformat(),
                }
            )
            self.persisted_job = job
            return job

        def renew_scene_image_job(self, **_kwargs):
            self.persisted_job["lease_expires_at"] = (
                datetime.now(timezone.utc) + timedelta(minutes=4)
            ).isoformat()
            raise TimeoutError("renewal response was lost after commit")

    repo = AckLossRepository(1)

    def generate(_post_id, _payload, request):
        request.state.scene_image_lease_guard.assert_active()
        sleep(0.04)
        request.state.scene_image_lease_guard.assert_active()
        repo.persisted_job.update({"status": "completed", "run_id": "run-1"})
        return SuccessResponse(data={"run_id": "run-1"})

    monkeypatch.setattr(module, "generate_candidates", generate)
    result = module.SemanticSceneImageWorker(
        repo=repo,
        worker_id="ack-loss-worker",
        lease_seconds=180,
        heartbeat_seconds=0.01,
    ).tick()

    assert result["action"] == "completed"
    assert result["run_id"] == "run-1"
    assert repo.finished == []


def test_ambiguous_lease_reconciliation_requires_a_safe_provider_window():
    from workers import semantic_scene_image_worker as module

    unsafe_expiry = datetime.now(timezone.utc) + timedelta(seconds=100)
    guard = module._SceneImageLeaseGuard(lease_expires_at=unsafe_expiry)

    assert guard.reconcile(
        unsafe_expiry.isoformat(),
        minimum_remaining_seconds=150,
    ) is False
    with pytest.raises(ValidationError, match="too short for a provider attempt"):
        guard.assert_provider_window(150)


def test_ambiguous_lease_reconciliation_accepts_an_advanced_expiry():
    from workers import semantic_scene_image_worker as module

    initial_expiry = datetime.now(timezone.utc) + timedelta(seconds=100)
    renewed_expiry = datetime.now(timezone.utc) + timedelta(seconds=180)
    guard = module._SceneImageLeaseGuard(lease_expires_at=initial_expiry)

    assert guard.reconcile(
        renewed_expiry.isoformat(),
        minimum_remaining_seconds=150,
    ) is True
    guard.assert_provider_window(150)


def test_scene_image_worker_reconciles_atomic_completion_before_recording_failure(
    monkeypatch,
):
    from workers import semantic_scene_image_worker as module

    repo = FakeJobRepository(1)
    repo.persisted_job = {
        "id": "job-0",
        "post_id": "post-0",
        "status": "completed",
        "run_id": "run-already-committed",
    }
    monkeypatch.setattr(
        module,
        "generate_candidates",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("response lost after commit")
        ),
    )

    result = module.SemanticSceneImageWorker(
        repo=repo, worker_id="reconcile-worker"
    ).tick()

    assert result == {
        "action": "completed",
        "job_id": "job-0",
        "run_id": "run-already-committed",
    }
    assert repo.finished == []


def test_scene_image_worker_does_not_start_provider_work_near_deadline(monkeypatch):
    from workers import semantic_scene_image_worker as module

    repo = FakeJobRepository(1)
    repo.jobs[0]["deadline_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=239)
    ).isoformat()
    provider_calls = []
    monkeypatch.setattr(
        module,
        "generate_candidates",
        lambda *_args, **_kwargs: provider_calls.append("started"),
    )

    result = module.SemanticSceneImageWorker(
        repo=repo, worker_id="near-deadline-worker"
    ).tick()

    assert result["action"] == "failed"
    assert result["error"]["code"] == "insufficient_execution_budget"
    assert provider_calls == []
    assert repo.finished[0]["status"] == "failed"


def test_scene_image_worker_lease_loss_stops_the_next_provider_stage(monkeypatch):
    from workers import semantic_scene_image_worker as module

    class RenewalFailureRepository(FakeJobRepository):
        def renew_scene_image_job(self, **kwargs):
            raise RuntimeError("database renewal unavailable")

    repo = RenewalFailureRepository(1)
    provider_stages = []

    def generate(_post_id, _payload, request):
        request.state.scene_image_lease_guard.assert_active()
        provider_stages.append("first")
        sleep(0.04)
        request.state.scene_image_lease_guard.assert_active()
        provider_stages.append("second")
        return SuccessResponse(data={"run_id": "should-not-complete"})

    monkeypatch.setattr(module, "generate_candidates", generate)
    result = module.SemanticSceneImageWorker(
        repo=repo,
        worker_id="lost-lease-worker",
        lease_seconds=180,
        heartbeat_seconds=0.01,
    ).tick()

    assert result["action"] == "failed"
    assert provider_stages == ["first"]
    assert repo.finished[0]["status"] == "failed"


def test_scene_image_worker_rejects_a_lease_shorter_than_provider_lifecycle():
    from workers import semantic_scene_image_worker as module

    with pytest.raises(ValidationError, match="between 180 and 300"):
        module.SemanticSceneImageWorker(
            repo=FakeJobRepository(0),
            lease_seconds=120,
            heartbeat_seconds=30,
        )

    worker = module.SemanticSceneImageWorker(repo=FakeJobRepository(0))
    assert (
        worker.lease_seconds - worker.heartbeat_seconds
        >= module.MAX_PROVIDER_CALL_WALLCLOCK_SECONDS
        + module.LEASE_RECLAIM_SAFETY_SECONDS
    )


def test_scene_image_worker_reports_a_claim_probe_failure():
    from workers import semantic_scene_image_worker as module

    class BrokenClaimRepository(FakeJobRepository):
        def claim_scene_image_job(self, **_kwargs):
            raise RuntimeError("claim RPC missing")

    observations = []
    worker = module.SemanticSceneImageWorker(
        repo=BrokenClaimRepository(0), worker_id="broken-claim-worker"
    )

    with pytest.raises(RuntimeError, match="claim RPC missing"):
        worker.tick(
            on_claim_probe=lambda success, error: observations.append(
                (success, error)
            )
        )

    assert observations == [(False, "RuntimeError")]


def test_process_heartbeat_marks_queue_probe_failure_while_claims_are_busy():
    from workers import semantic_scene_image_worker as module

    class ProbeFailureRepository(FakeJobRepository):
        def probe_scene_image_queue(self, **_kwargs):
            raise RuntimeError("queue probe blocked")

    repo = ProbeFailureRepository(0)
    worker = module.SemanticSceneImageWorker(repo=repo, worker_id="probe-worker")
    worker.provider_auth_probe = lambda: None

    metadata = module._publish_process_heartbeat(
        worker,
        active_count=2,
        concurrency=2,
    )

    assert metadata["active"] == 2
    assert metadata["queue_probe_status"] == "error"
    assert metadata["queue_probe_error_class"] == "RuntimeError"
    assert metadata["provider_auth_probe_status"] == "ok"
    assert repo.heartbeats[0]["metadata"] == metadata


def test_process_heartbeat_blocks_claim_readiness_when_provider_auth_is_missing():
    from workers import semantic_scene_image_worker as module

    repo = FakeJobRepository(0)

    def missing_credentials():
        raise ValidationError("No Google Cloud credentials found.")

    worker = module.SemanticSceneImageWorker(
        repo=repo,
        worker_id="missing-auth-worker",
        provider_auth_probe=missing_credentials,
    )

    metadata = module._publish_process_heartbeat(
        worker,
        active_count=0,
        concurrency=2,
    )

    assert metadata["queue_probe_status"] == "ok"
    assert metadata["provider_auth_probe_status"] == "error"
    assert metadata["provider_auth_probe_error_class"] == "ValidationError"
    assert repo.jobs == []


def test_ten_variable_batch_runs_generate_every_script_once_with_concurrency_two(
    monkeypatch,
):
    from workers import semantic_scene_image_worker as module

    batch_sizes = [3, 4, 5, 6, 7, 1, 2, 3, 7, 4]
    for run_number, script_count in enumerate(batch_sizes, start=1):
        repo = FakeJobRepository(script_count)
        active = 0
        peak = 0
        generated = []
        generation_lock = Lock()

        def generate(post_id, payload, _request):
            nonlocal active, peak
            with generation_lock:
                active += 1
                peak = max(peak, active)
            try:
                sleep(0.002)
                generated.append((post_id, payload.candidate_count))
                return SuccessResponse(data={"run_id": f"run-{run_number}-{post_id}"})
            finally:
                with generation_lock:
                    active -= 1

        monkeypatch.setattr(module, "generate_candidates", generate)
        worker = module.SemanticSceneImageWorker(
            repo=repo,
            worker_id=f"stress-worker-{run_number}",
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: worker.tick(), range(script_count)))

        assert all(result["action"] == "completed" for result in results)
        assert len(generated) == script_count
        assert len({post_id for post_id, _count in generated}) == script_count
        assert {count for _post_id, count in generated} == {1}
        assert repo.finished == []
        assert peak <= 2
