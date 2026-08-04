from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep

from app.core.errors import SuccessResponse


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
            }
            for index in range(count)
        ]
        self.finished = []

    def claim_scene_image_job(self, *, worker_id, lease_seconds):
        with self.lock:
            if not self.jobs:
                return None
            job = self.jobs.pop(0)
            return {**job, "lease_token": f"lease-{job['id']}"}

    def finish_scene_image_job(self, **kwargs):
        with self.lock:
            self.finished.append(kwargs)
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
    assert len(repo.finished) == 1
    assert repo.finished[0]["status"] == "completed"
    assert repo.finished[0]["run_id"] == "run-1"


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
        assert len(repo.finished) == script_count
        assert peak <= 2
