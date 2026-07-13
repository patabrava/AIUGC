from __future__ import annotations

import pytest

from tests.test_semantic_video_worker import FakeRepo, FakeVertex, _worker


@pytest.mark.parametrize(
    "failure",
    [
        "missing_approval",
        "stale_request_hash",
        "budget_exhausted",
        "quota_exhausted",
        "submission_cap_exhausted",
        "lease_fenced",
    ],
)
def test_paid_reservation_failure_makes_zero_provider_calls(failure):
    repo = FakeRepo(take_count=1)
    repo.reserve_error = RuntimeError(failure)
    vertex = FakeVertex()
    worker = _worker(repo, vertex)

    with pytest.raises(RuntimeError, match=failure):
        worker.tick("run-1")

    assert vertex.submit_calls == []
    assert not any(event[0] == "intent" for event in repo.events)


def test_worker_never_exceeds_approved_submission_count():
    repo = FakeRepo(take_count=1)
    vertex = FakeVertex()
    worker = _worker(repo, vertex)

    worker.tick("run-1")
    worker.tick("run-1")

    assert len(vertex.submit_calls) == 1


def test_existing_unknown_operation_is_never_polled_or_resubmitted():
    repo = FakeRepo(take_count=1)
    repo.takes[0]["submission_state"] = "submission_unknown"
    vertex = FakeVertex()
    worker = _worker(repo, vertex)

    result = worker.tick("run-1")

    assert result.action == "blocked_unknown_submission"
    assert vertex.submit_calls == []
    assert vertex.poll_calls == []


def test_reserved_but_not_intended_take_resumes_without_a_second_quota_reservation():
    repo = FakeRepo(take_count=1)
    repo.takes[0]["submission_state"] = "reserved"
    vertex = FakeVertex()
    worker = _worker(repo, vertex)

    result = worker.tick("run-1")

    assert result.action == "submitted"
    assert len(vertex.submit_calls) == 1
    assert not any(event[0] == "reserve" for event in repo.events)
    assert any(event[0] == "intent" for event in repo.events)
