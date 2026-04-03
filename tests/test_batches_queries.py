import httpx
from types import SimpleNamespace

from app.features.batches import queries as batch_queries


class _RetryingQuery:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def execute(self):
        self.calls += 1
        if self.calls < 3:
            raise httpx.ReadError(
                "temporarily unavailable",
                request=httpx.Request("GET", "https://example.test/rest/v1/batches"),
            )
        return SimpleNamespace(data=self.rows, count=len(self.rows))


class _FakeClient:
    def __init__(self, batches, posts):
        self._queries = {
            "batches": _RetryingQuery(batches),
            "posts": _RetryingQuery(posts),
        }

    def table(self, name):
        return self._queries[name]


def test_get_batch_by_id_retries_transient_request_errors(monkeypatch):
    fake_adapter = SimpleNamespace(
        client=_FakeClient(
            batches=[{"id": "batch-1", "brand": "Test Batch"}],
            posts=[],
        )
    )
    sleeps = []
    monkeypatch.setattr(batch_queries, "get_supabase", lambda: fake_adapter)
    monkeypatch.setattr(batch_queries.time, "sleep", lambda seconds: sleeps.append(seconds))

    batch = batch_queries.get_batch_by_id("batch-1")

    assert batch["id"] == "batch-1"
    assert sleeps == [0.15, 0.35]


def test_get_batch_posts_summary_retries_transient_request_errors(monkeypatch):
    fake_adapter = SimpleNamespace(
        client=_FakeClient(
            batches=[],
            posts=[
                {"id": "post-1", "batch_id": "batch-1", "post_type": "value"},
                {"id": "post-2", "batch_id": "batch-1", "post_type": "lifestyle"},
            ],
        )
    )
    sleeps = []
    monkeypatch.setattr(batch_queries, "get_supabase", lambda: fake_adapter)
    monkeypatch.setattr(batch_queries.time, "sleep", lambda seconds: sleeps.append(seconds))

    summary = batch_queries.get_batch_posts_summary("batch-1")

    assert summary == {"posts_count": 2, "posts_by_state": {"value": 1, "lifestyle": 1}}
    assert sleeps == [0.15, 0.35]
