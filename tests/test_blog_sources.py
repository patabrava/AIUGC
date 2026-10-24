from __future__ import annotations

import http.client
import socket
from unittest.mock import MagicMock

import pytest

from app.features.blog import sources


@pytest.mark.parametrize("url", [
    "https://example.com/community-peer-support-lokale-gruppen-foren-mentoring",
    "https://sub.example.org/paper", "https://example.net/", "https://source.invalid/",
    "http://localhost/", "http://service.local/", "file:///etc/passwd",
    "https://user:secret@journal.de/", "https://journal.de:8443/", "https://journal.de:0/",
])
def test_placeholder_or_unsafe_url_is_omitted_without_network(monkeypatch, url):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("unexpected DNS"))
    assert sources.filter_blog_sources([{"title": "Quelle", "url": url}]) == []


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "224.0.0.1"])
def test_non_public_dns_destinations_are_never_contacted(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", (address, 443))])
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("unsafe connection"))
    assert sources.filter_blog_sources([{"title": "Quelle", "url": "https://journal.de/"}]) == []


@pytest.mark.parametrize("head_status,get_status,omitted", [
    (404, 404, True), (410, 410, True), (404, 200, False), (405, 200, False),
    (403, 200, False), (405, 404, True), (404, 403, False), (501, 200, False),
    (401, None, False), (429, None, False), (503, None, False), (200, None, False),
])
def test_missing_is_confirmed_with_get_and_temporary_failures_are_retained(monkeypatch, head_status, get_status, omitted):
    calls = []

    def probe(url, method, timeout):
        calls.append(method)
        assert 0 < timeout <= 8
        return (head_status if method == "HEAD" else get_status), None

    monkeypatch.setattr(sources, "_request_status", probe)
    refs = [{"title": "Medikationsplan", "url": "https://www.kvwl.de/praxis/verordnungen/arzneimittel/medikationsplan"}]
    assert sources.filter_blog_sources(refs) == ([] if omitted else refs)
    assert calls == (["HEAD", "GET"] if get_status is not None else ["HEAD"])


@pytest.mark.parametrize("error", [socket.gaierror(), TimeoutError(), http.client.RemoteDisconnected()])
def test_unconfirmed_transport_failure_keeps_citation(monkeypatch, error):
    def unavailable(*args):
        raise error

    monkeypatch.setattr(sources, "_request_status", unavailable)
    refs = [{"title": "Studie", "url": "https://journal.de/study"}]
    assert sources.filter_blog_sources(refs) == refs


def test_redirects_are_revalidated_before_connecting(monkeypatch):
    connection = MagicMock()
    response = connection.getresponse.return_value
    response.status = 302
    response.getheader.return_value = "http://169.254.169.254/metadata"
    monkeypatch.setattr(http.client, "HTTPConnection", lambda *args, **kwargs: connection)
    connect = MagicMock()
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(sources.ssl, "create_default_context", MagicMock())
    # Resolve the redirected private literal as private, not the initial public host.
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *args, **kwargs: [(2, 1, 6, "", (host if host.startswith("169.") else "8.8.8.8", 443))])
    assert sources.filter_blog_sources([{"title": "Quelle", "url": "https://journal.de/"}]) == []
    assert connect.call_count == 1


def test_request_pins_ip_keeps_tls_hostname_and_does_not_read_body(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))])
    connection = MagicMock()
    connection.getresponse.return_value.status = 200
    connection.getresponse.return_value.getheader.return_value = None
    constructor = MagicMock(return_value=connection)
    monkeypatch.setattr(http.client, "HTTPConnection", constructor)
    connect = MagicMock()
    monkeypatch.setattr(socket, "create_connection", connect)
    tls = MagicMock()
    monkeypatch.setattr(sources.ssl, "create_default_context", lambda: tls)
    assert sources._request_status("https://journal.de/paper?lang=de#abstract", "GET", 2) == (200, None)
    connect.assert_called_once_with(("8.8.8.8", 443), timeout=2)
    tls.wrap_socket.assert_called_once_with(connect.return_value, server_hostname="journal.de")
    assert connection.request.call_args.args == ("GET", "/paper?lang=de")
    connection.getresponse.return_value.read.assert_not_called()
    connection.close.assert_called_once()


def test_redirect_loop_is_bounded_and_not_misreported_as_missing(monkeypatch):
    probe = MagicMock(return_value=(302, "/loop"))
    monkeypatch.setattr(sources, "_request_status", probe)
    refs = [{"title": "Quelle", "url": "https://journal.de/loop"}]
    assert sources.filter_blog_sources(refs) == refs
    assert probe.call_count == 5


def test_checks_unique_urls_once_and_keeps_url_less_names(monkeypatch):
    probe = MagicMock(return_value=(200, None))
    monkeypatch.setattr(sources, "_request_status", probe)
    assert sources.filter_blog_sources([
        {"title": "Studie", "url": "https://journal.de/study"},
        {"title": "Studie Anhang", "url": "https://journal.de/study"},
        {"title": "Gedrucktes Buch"},
    ]) == [
        {"title": "Studie", "url": "https://journal.de/study"},
        {"title": "Studie Anhang", "url": "https://journal.de/study"},
        {"title": "Gedrucktes Buch", "url": ""},
    ]
    assert probe.call_count == 1
