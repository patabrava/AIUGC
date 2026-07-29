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
