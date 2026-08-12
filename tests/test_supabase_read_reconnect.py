from types import SimpleNamespace

import httpx
import pytest

from app.adapters import supabase_client


def test_read_reconnects_after_shared_transport_corruption(monkeypatch):
    failed_client = object()
    healthy_client = object()
    adapter = SimpleNamespace(client=failed_client)
    reconnects = []

    def reconnect(*, failed_client):
        reconnects.append(failed_client)
        adapter.client = healthy_client
        return healthy_client

    adapter.reconnect = reconnect
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: adapter)
    monkeypatch.setattr(supabase_client.time, "sleep", lambda _delay: None)

    def read(client):
        if client is failed_client:
            raise httpx.RemoteProtocolError(
                "peer closed connection without response",
                request=httpx.Request("GET", "https://example.test/rest/v1/runs"),
            )
        return {"ok": True}

    assert supabase_client.execute_supabase_read("detail_projection", read) == {"ok": True}
    assert reconnects == [failed_client]


def test_read_reconnects_explicit_adapter_after_timeout(monkeypatch):
    failed_client = object()
    healthy_client = object()
    adapter = SimpleNamespace(client=failed_client)
    reconnects = []
    calls = []

    def reconnect(*, failed_client):
        reconnects.append(failed_client)
        adapter.client = healthy_client
        return healthy_client

    adapter.reconnect = reconnect
    monkeypatch.setattr(supabase_client.time, "sleep", lambda _delay: None)

    def read(client):
        calls.append(client)
        if client is failed_client:
            raise httpx.ReadTimeout(
                "database read timed out",
                request=httpx.Request("GET", "https://example.test/rest/v1/batches"),
            )
        return {"ok": True}

    assert supabase_client.execute_supabase_read(
        "batch_detail",
        read,
        adapter=adapter,
    ) == {"ok": True}
    assert calls == [failed_client, healthy_client]
    assert reconnects == [failed_client]


def test_read_does_not_reconnect_for_contract_errors(monkeypatch):
    client = object()
    adapter = SimpleNamespace(
        client=client,
        reconnect=lambda **_kwargs: pytest.fail("must not reconnect"),
    )
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: adapter)

    with pytest.raises(ValueError, match="invalid projection"):
        supabase_client.execute_supabase_read(
            "detail_projection",
            lambda _client: (_ for _ in ()).throw(ValueError("invalid projection")),
        )


def test_explicit_transaction_client_is_retried_without_replacement(monkeypatch):
    explicit_client = object()
    adapter = SimpleNamespace(
        client=object(),
        reconnect=lambda **_kwargs: pytest.fail("must not replace explicit client"),
    )
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: adapter)
    monkeypatch.setattr(supabase_client.time, "sleep", lambda _delay: None)
    calls = []

    def read(client):
        calls.append(client)
        if len(calls) == 1:
            raise httpx.ReadError(
                "temporary read failure",
                request=httpx.Request("GET", "https://example.test/rest/v1/runs"),
            )
        return "recovered"

    assert (
        supabase_client.execute_supabase_read(
            "transaction_read",
            read,
            client=explicit_client,
        )
        == "recovered"
    )
    assert calls == [explicit_client, explicit_client]


@pytest.mark.parametrize(
    ("reader_name", "row"),
    [
        (
            "get_scene_image_job",
            {"id": "job-1", "post_id": "post-1", "status": "queued"},
        ),
        (
            "get_scene_image_worker_heartbeat",
            {
                "worker_id": "semantic-scene-image-v2-test",
                "last_seen_at": "2026-08-05T00:00:00+00:00",
                "metadata": {"queue_probe_status": "ok"},
            },
        ),
    ],
)
def test_scene_image_detail_reads_reconnect_the_shared_client(
    monkeypatch, reader_name, row
):
    from app.features.semantic_videos import queries

    class FluentClient:
        def __init__(self, *, broken: bool):
            self.broken = broken

        def table(self, _name):
            return self

        def select(self, _fields):
            return self

        def eq(self, _field, _value):
            return self

        def order(self, _field, **_kwargs):
            return self

        def limit(self, _count):
            return self

        def execute(self):
            if self.broken:
                raise httpx.RemoteProtocolError(
                    "peer closed connection without response",
                    request=httpx.Request(
                        "GET", "https://example.test/rest/v1/scene-image"
                    ),
                )
            return SimpleNamespace(data=[row])

    failed_client = FluentClient(broken=True)
    healthy_client = FluentClient(broken=False)
    adapter = SimpleNamespace(client=failed_client)
    reconnects = []

    def reconnect(*, failed_client):
        reconnects.append(failed_client)
        adapter.client = healthy_client
        return healthy_client

    adapter.reconnect = reconnect
    monkeypatch.setattr(supabase_client, "get_supabase", lambda: adapter)
    monkeypatch.setattr(supabase_client.time, "sleep", lambda _delay: None)

    reader = getattr(queries, reader_name)
    result = reader("post-1") if reader_name == "get_scene_image_job" else reader()

    assert result == row
    assert reconnects == [failed_client]
