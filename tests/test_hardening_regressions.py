from __future__ import annotations

import os

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
import app.main as main_module
import app.adapters.supabase_client as supabase_client_module
from app.features.qa.handlers import _active_posts_ready_for_publish


def test_sensitive_setting_defaults_do_not_ship_live_values():
    assert Settings.model_fields["supabase_key"].default == ""
    assert Settings.model_fields["supabase_service_key"].default == ""
    assert Settings.model_fields["google_ai_api_key"].default == ""
    assert Settings.model_fields["cron_secret"].default == ""


def test_http_exception_is_normalized_into_shared_error_envelope():
    client = TestClient(app, base_url="http://localhost")

    response = client.put("/posts/test-post-id/script", data={"script_text": ""})

    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["status"] == 422
    assert body["code"] == "validation_error"
    assert "script_text" in body["message"]


def test_removed_posts_do_not_block_qa_advancement():
    posts = [
        {"id": "removed-post", "qa_pass": False, "seed_data": {"script_review_status": "removed"}},
        {"id": "active-post", "qa_pass": True, "seed_data": {"script_review_status": "approved"}},
    ]

    assert _active_posts_ready_for_publish(posts) is True


def test_app_lifespan_does_not_eagerly_create_supabase_client(monkeypatch):
    calls = {"count": 0}

    def _fail_if_called():
        calls["count"] += 1
        raise AssertionError("get_supabase should not be called during startup")

    monkeypatch.setattr(main_module, "get_supabase", _fail_if_called)
    monkeypatch.setattr(main_module, "recover_stalled_batches", lambda **kwargs: [])
    monkeypatch.setattr(main_module, "recover_stalled_topic_research_runs", lambda **kwargs: [])

    async def _run():
        async with main_module.lifespan(app):
            return True

    import asyncio

    assert asyncio.run(_run()) is True
    assert calls["count"] == 0


def test_app_lifespan_logs_google_ai_context_fingerprint(monkeypatch):
    from app.core.config import fingerprint_secret

    recorded = []

    class FakeLogger:
        def info(self, event, **data):
            recorded.append((event, data))

        def warning(self, event, **data):
            recorded.append((event, data))

        def exception(self, event, **data):
            recorded.append((event, data))

    monkeypatch.setattr(main_module, "logger", FakeLogger())
    monkeypatch.setattr(main_module, "recover_stalled_batches", lambda **kwargs: [])
    monkeypatch.setattr(main_module, "recover_stalled_topic_research_runs", lambda **kwargs: [])

    async def _run():
        async with main_module.lifespan(app):
            return True

    import asyncio

    assert asyncio.run(_run()) is True

    startup_events = [data for event, data in recorded if event == "application_startup"]
    assert startup_events, "application_startup log was not emitted"
    startup = startup_events[0]
    assert startup["google_ai_api_key_present"] is True
    assert startup["google_ai_api_key_fingerprint"] == fingerprint_secret("test-google-key")
    assert startup["google_ai_project_id"] == "unset"


def test_google_ai_context_fingerprint_is_stable_and_redacted():
    from app.core.config import fingerprint_secret, google_ai_context_fingerprint

    class DummySettings:
        google_ai_api_key = "alpha-key"
        google_ai_project_id = "project-123"

    first = google_ai_context_fingerprint(DummySettings())
    second = google_ai_context_fingerprint(DummySettings())

    assert first == second
    assert first["google_ai_api_key_present"] is True
    assert first["google_ai_api_key_fingerprint"] == fingerprint_secret("alpha-key")
    assert first["google_ai_api_key_fingerprint"] != "alpha-key"
    assert first["google_ai_project_id"] == "project-123"


def test_google_ai_key_alignment_detector_flags_mismatch():
    class DummySettings:
        gemini_api_key = "gemini-key"
        google_ai_api_key = "google-key"

    assert Settings.google_ai_keys_aligned(DummySettings()) is False


def test_supabase_adapter_uses_valid_service_key(monkeypatch):
    captured = {}

    class DummySettings:
        supabase_url = "https://example.supabase.co"
        supabase_key = "ey.public.payload"
        supabase_service_key = "ey.service.payload"

    def _fake_create_client(*, supabase_url, supabase_key):
        captured["url"] = supabase_url
        captured["key"] = supabase_key
        return object()

    monkeypatch.setattr(supabase_client_module, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(supabase_client_module, "create_client", _fake_create_client)
    supabase_client_module.SupabaseAdapter._instance = None
    supabase_client_module.SupabaseAdapter._client = None

    adapter = supabase_client_module.SupabaseAdapter()

    assert adapter.client is not None
    assert captured == {
        "url": "https://example.supabase.co",
        "key": "ey.service.payload",
    }


def test_supabase_adapter_falls_back_when_service_key_is_malformed(monkeypatch):
    captured = {}

    class DummySettings:
        supabase_url = "https://example.supabase.co"
        supabase_key = "ey.public.payload"
        supabase_service_key = "ey.bad.payloadey.bad.payload"

    def _fake_create_client(*, supabase_url, supabase_key):
        captured["url"] = supabase_url
        captured["key"] = supabase_key
        return object()

    monkeypatch.setattr(supabase_client_module, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(supabase_client_module, "create_client", _fake_create_client)
    supabase_client_module.SupabaseAdapter._instance = None
    supabase_client_module.SupabaseAdapter._client = None

    adapter = supabase_client_module.SupabaseAdapter()

    assert adapter.client is not None
    assert captured == {
        "url": "https://example.supabase.co",
        "key": "ey.public.payload",
    }
