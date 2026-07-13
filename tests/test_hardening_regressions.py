from __future__ import annotations

import os

import pytest
from types import SimpleNamespace

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.video_profiles import VEO_SEGMENTED_VIDEO_ROUTE
import app.core.video_profiles as video_profiles
from app.main import app
import app.main as main_module
import app.adapters.supabase_client as supabase_client_module
from app.features.qa.handlers import _active_posts_ready_for_publish


def test_sensitive_setting_defaults_do_not_ship_live_values():
    assert Settings.model_fields["supabase_key"].default == ""
    assert Settings.model_fields["supabase_service_key"].default == ""
    assert Settings.model_fields["cron_secret"].default == ""


def test_settings_load_gemini_api_key_from_canonical_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "canonical-gemini-key")

    settings = Settings()

    assert settings.gemini_api_key == "canonical-gemini-key"


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
    monkeypatch.setattr(
        main_module,
        "find_recoverable_stalled_batch_ids",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(main_module, "recover_stalled_topic_research_runs", lambda **kwargs: [])

    async def _run():
        async with main_module.lifespan(app):
            return True

    import asyncio

    assert asyncio.run(_run()) is True
    assert calls["count"] == 0


def test_app_lifespan_can_disable_startup_database_recovery(monkeypatch):
    calls = {"batch_recovery": 0, "topic_recovery": 0, "cron_monitoring": 0}

    def _called(name):
        calls[name] += 1
        raise AssertionError(f"{name} should be disabled")

    monkeypatch.setenv("DISABLE_STARTUP_RECOVERY_CHECKS", "true")
    monkeypatch.setenv("DISABLE_BACKGROUND_SCHEDULERS", "true")
    monkeypatch.setattr(
        main_module,
        "find_recoverable_stalled_batch_ids",
        lambda **kwargs: _called("batch_recovery"),
    )
    monkeypatch.setattr(main_module, "recover_stalled_topic_research_runs", lambda **kwargs: _called("topic_recovery"))
    monkeypatch.setattr(main_module, "get_topic_research_cron_monitoring", lambda: _called("cron_monitoring"))

    async def _run():
        async with main_module.lifespan(app):
            return True

    import asyncio

    assert asyncio.run(_run()) is True
    assert calls == {"batch_recovery": 0, "topic_recovery": 0, "cron_monitoring": 0}


def test_startup_recovery_schedules_semantic_discovery_on_main_event_loop(monkeypatch):
    import asyncio
    import gc
    import threading
    import warnings
    from datetime import datetime, timezone

    from app.features.topics import handlers as topic_handlers

    batch_id = "semantic-threaded-recovery"
    batch = {
        "id": batch_id,
        "brand": "AYRA",
        "state": "S1_SETUP",
        "creation_mode": "semantic_ugc",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "post_type_counts": {"value": 1, "lifestyle": 0, "product": 0},
        "target_duration_seconds": 50,
        "target_length_tier": None,
    }
    scan_thread_ids = []
    discovery_thread_ids = []
    discovery_loops = []

    def fake_list_batches(*, archived, limit, offset):
        scan_thread_ids.append(threading.get_ident())
        return [batch], 1

    monkeypatch.setattr(topic_handlers, "list_batches", fake_list_batches)
    monkeypatch.setattr(topic_handlers, "get_batch_by_id", lambda _batch_id: batch)
    monkeypatch.setattr(topic_handlers, "get_posts_by_batch", lambda _batch_id: [])
    monkeypatch.setattr(topic_handlers, "get_seeding_progress", lambda _batch_id: None)
    monkeypatch.setattr(main_module, "recover_stalled_topic_research_runs", lambda **kwargs: [])
    monkeypatch.setattr(main_module, "get_topic_research_cron_monitoring", lambda: {})

    async def fake_discover_topics_for_batch(recovered_batch_id):
        discovery_thread_ids.append(threading.get_ident())
        discovery_loops.append(asyncio.get_running_loop())
        return {
            "batch_id": recovered_batch_id,
            "posts_created": 1,
            "state": "S2_SEEDED",
            "topics": [],
        }

    monkeypatch.setattr(
        topic_handlers,
        "discover_topics_for_batch",
        fake_discover_topics_for_batch,
    )

    async def _run():
        running_loop = asyncio.get_running_loop()
        main_thread_id = threading.get_ident()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            await main_module._run_startup_recovery_checks()
            await asyncio.sleep(0)
            gc.collect()
        return running_loop, main_thread_id, caught

    topic_handlers._DISCOVERY_TASKS.pop(batch_id, None)
    try:
        running_loop, main_thread_id, caught = asyncio.run(_run())
    finally:
        topic_handlers._DISCOVERY_TASKS.pop(batch_id, None)

    unawaited = [
        warning
        for warning in caught
        if issubclass(warning.category, RuntimeWarning)
        and "was never awaited" in str(warning.message)
    ]
    assert scan_thread_ids and all(
        thread_id != main_thread_id for thread_id in scan_thread_ids
    )
    assert discovery_thread_ids == [main_thread_id]
    assert discovery_loops == [running_loop]
    assert unawaited == []


def test_database_dependency_errors_render_html_for_browser_gets():
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/batches",
        "headers": [(b"accept", b"text/html")],
    }
    request = Request(scope)

    response = main_module._database_unavailable_response(request, "supabase.co 522")

    assert response.status_code == 503
    assert "Studio database is recovering" in response.body.decode()


def test_batch_query_timeouts_fail_fast_without_retry():
    import httpx
    from app.core.errors import ThirdPartyError
    from app.features.batches.queries import _execute_with_retry

    calls = {"count": 0}

    def _timeout():
        calls["count"] += 1
        raise httpx.ReadTimeout("The read operation timed out")

    with pytest.raises(ThirdPartyError) as exc_info:
        _execute_with_retry("list_batches", _timeout)

    assert calls["count"] == 1
    assert "Database unavailable" in exc_info.value.message


def test_health_check_caches_database_probe(monkeypatch):
    calls = {"count": 0}

    class FakeSupabase:
        def health_check(self):
            calls["count"] += 1
            return True

    monkeypatch.setattr(main_module, "get_supabase", lambda: FakeSupabase())
    main_module._health_db_cache.update({"checked_at": 0.0, "healthy": True, "error": None})

    import asyncio

    first = asyncio.run(main_module.health_check())
    second = asyncio.run(main_module.health_check())

    assert first["checks"]["database"] == "ok"
    assert second["checks"]["database"] == "ok"
    assert calls["count"] == 1


def test_health_check_times_out_blocking_database_probe(monkeypatch):
    import asyncio
    import time

    class SlowSupabase:
        def health_check(self):
            time.sleep(0.2)
            return True

    monkeypatch.setattr(main_module, "get_supabase", lambda: SlowSupabase())
    monkeypatch.setattr(main_module, "_HEALTH_DB_TIMEOUT_SECONDS", 0.01)
    main_module._health_db_cache.update({"checked_at": 0.0, "healthy": True, "error": None})

    response = asyncio.run(main_module.health_check())

    assert response.status_code == 503
    assert "timed out" in response.body.decode()


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
    monkeypatch.setattr(
        main_module,
        "find_recoverable_stalled_batch_ids",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(main_module, "recover_stalled_topic_research_runs", lambda **kwargs: [])
    monkeypatch.setattr(main_module.settings, "gemini_api_key", "test-google-key")

    async def _run():
        async with main_module.lifespan(app):
            return True

    import asyncio

    assert asyncio.run(_run()) is True

    startup_events = [data for event, data in recorded if event == "application_startup"]
    assert startup_events, "application_startup log was not emitted"
    startup = startup_events[0]
    assert startup["gemini_api_key_present"] is True
    assert startup["gemini_api_key_fingerprint"] == fingerprint_secret("test-google-key")
    assert startup["vertex_ai_project_id"] == "unset"
    assert startup["video_routes"]["tier_32_route"] in {"veo_extended", VEO_SEGMENTED_VIDEO_ROUTE}


def test_livez_exposes_non_secret_video_route_fingerprint(monkeypatch):
    settings = SimpleNamespace(
        environment="production",
        veo_enable_segmented_route=True,
        veo_enable_efficient_long_route=True,
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(video_profiles, "get_settings", lambda: settings)

    import asyncio

    response = asyncio.run(main_module.live_check())

    assert response["status"] == "alive"
    assert response["environment"] == "production"
    assert response["video_routes"] == {
        "segmented_route_enabled": True,
        "tier_16_route": VEO_SEGMENTED_VIDEO_ROUTE,
        "tier_32_route": VEO_SEGMENTED_VIDEO_ROUTE,
    }


def test_google_ai_context_fingerprint_is_stable_and_redacted():
    from app.core.config import fingerprint_secret, google_ai_context_fingerprint

    class DummySettings:
        gemini_provider = "vertex"
        gemini_deep_research_provider = "vertex_grounded"
        gemini_api_fallback_enabled = False
        gemini_api_key = "alpha-key"
        vertex_ai_project_id = "project-123"
        vertex_ai_location = "us-central1"
        vertex_grounded_research_location = "global"
        google_application_credentials = ""
        google_application_credentials_json = ""

    first = google_ai_context_fingerprint(DummySettings())
    second = google_ai_context_fingerprint(DummySettings())

    assert first == second
    assert first["gemini_api_key_present"] is True
    assert first["gemini_api_key_fingerprint"] == fingerprint_secret("alpha-key")
    assert first["gemini_api_key_fingerprint"] != "alpha-key"
    assert first["vertex_ai_project_id"] == "project-123"


def test_supabase_adapter_uses_valid_service_key(monkeypatch):
    captured = {}

    class DummySettings:
        supabase_url = "https://example.supabase.co"
        supabase_key = "ey.public.payload"
        supabase_service_key = "ey.service.payload"

    class _FakeQuery:
        def select(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            return object()

    class _FakeClient:
        def table(self, *_args, **_kwargs):
            return _FakeQuery()

    def _fake_create_client(*, supabase_url, supabase_key, **_kwargs):
        captured["url"] = supabase_url
        captured["key"] = supabase_key
        captured["postgrest_timeout"] = _kwargs["options"].postgrest_client_timeout
        return _FakeClient()

    monkeypatch.setattr(supabase_client_module, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(supabase_client_module, "_probe_supabase_api_key", lambda **_kwargs: True)
    monkeypatch.setattr(supabase_client_module, "create_client", _fake_create_client)
    supabase_client_module.SupabaseAdapter._instance = None
    supabase_client_module.SupabaseAdapter._client = None

    adapter = supabase_client_module.SupabaseAdapter()

    assert adapter.client is not None
    assert captured == {
        "url": "https://example.supabase.co",
        "key": "ey.service.payload",
        "postgrest_timeout": supabase_client_module._SUPABASE_POSTGREST_TIMEOUT_SECONDS,
    }


def test_supabase_adapter_falls_back_when_service_key_is_malformed(monkeypatch):
    captured = {}

    class DummySettings:
        supabase_url = "https://example.supabase.co"
        supabase_key = "ey.public.payload"
        supabase_service_key = "not-a-jwt-token"

    class _FakeQuery:
        def select(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            return object()

    class _FakeClient:
        def table(self, *_args, **_kwargs):
            return _FakeQuery()

    def _fake_create_client(*, supabase_url, supabase_key, **_kwargs):
        captured["url"] = supabase_url
        captured["key"] = supabase_key
        return _FakeClient()

    monkeypatch.setattr(supabase_client_module, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(supabase_client_module, "_probe_supabase_api_key", lambda **_kwargs: True)
    monkeypatch.setattr(supabase_client_module, "create_client", _fake_create_client)
    supabase_client_module.SupabaseAdapter._instance = None
    supabase_client_module.SupabaseAdapter._client = None

    adapter = supabase_client_module.SupabaseAdapter()

    assert adapter.client is not None
    assert captured == {
        "url": "https://example.supabase.co",
        "key": "ey.public.payload",
    }


def test_supabase_adapter_falls_back_to_public_key_after_auth_probe_failure(monkeypatch):
    captured = {"created_keys": [], "probed_keys": []}

    class DummySettings:
        supabase_url = "https://example.supabase.co"
        supabase_key = "ey.public.payload"
        supabase_service_key = "ey.service.payload"

    class _FakeClient:
        def __init__(self, key: str):
            captured["created_keys"].append(key)

    def _fake_create_client(*, supabase_url, supabase_key, **_kwargs):
        captured["url"] = supabase_url
        return _FakeClient(supabase_key)

    def _fake_probe(*, candidate, **_kwargs):
        captured["probed_keys"].append(candidate)
        return candidate != "ey.service.payload"

    monkeypatch.setattr(supabase_client_module, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(supabase_client_module, "_probe_supabase_api_key", _fake_probe)
    monkeypatch.setattr(supabase_client_module, "create_client", _fake_create_client)
    supabase_client_module.SupabaseAdapter._instance = None
    supabase_client_module.SupabaseAdapter._client = None

    adapter = supabase_client_module.SupabaseAdapter()

    assert adapter.client is not None
    assert captured["url"] == "https://example.supabase.co"
    assert captured["probed_keys"] == ["ey.service.payload", "ey.public.payload"]
    assert captured["created_keys"] == ["ey.public.payload"]


def test_supabase_adapter_does_not_block_client_creation_on_transient_probe_failure(monkeypatch):
    captured = {}

    class DummySettings:
        supabase_url = "https://example.supabase.co"
        supabase_key = "ey.public.payload"
        supabase_service_key = "ey.service.payload"

    class _FakeClient:
        pass

    def _fake_create_client(*, supabase_url, supabase_key, **_kwargs):
        captured["url"] = supabase_url
        captured["key"] = supabase_key
        return _FakeClient()

    def _degraded_probe(**_kwargs):
        raise supabase_client_module._SupabaseProbeTransientError("PostgREST timed out")

    monkeypatch.setattr(supabase_client_module, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(supabase_client_module, "_probe_supabase_api_key", _degraded_probe)
    monkeypatch.setattr(supabase_client_module, "create_client", _fake_create_client)
    supabase_client_module.SupabaseAdapter._instance = None
    supabase_client_module.SupabaseAdapter._client = None

    adapter = supabase_client_module.SupabaseAdapter()

    assert adapter.client is not None
    assert captured == {
        "url": "https://example.supabase.co",
        "key": "ey.service.payload",
    }


def test_supabase_adapter_normalizes_wrapped_supabase_keys(monkeypatch):
    captured = {}

    class DummySettings:
        supabase_url = "https://example.supabase.co"
        supabase_key = '"ey.public.payload"'
        supabase_service_key = "  'ey.service.payload'  "

    class _FakeQuery:
        def select(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def execute(self):
            return object()

    class _FakeClient:
        def table(self, *_args, **_kwargs):
            return _FakeQuery()

    def _fake_create_client(*, supabase_url, supabase_key, **_kwargs):
        captured["url"] = supabase_url
        captured["key"] = supabase_key
        return _FakeClient()

    monkeypatch.setattr(supabase_client_module, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(supabase_client_module, "_probe_supabase_api_key", lambda **_kwargs: True)
    monkeypatch.setattr(supabase_client_module, "create_client", _fake_create_client)
    supabase_client_module.SupabaseAdapter._instance = None
    supabase_client_module.SupabaseAdapter._client = None

    adapter = supabase_client_module.SupabaseAdapter()

    assert adapter.client is not None
    assert captured == {
        "url": "https://example.supabase.co",
        "key": "ey.service.payload",
    }
