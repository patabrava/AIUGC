"""Regression guards for app-wide latency and resource containment."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.errors import SuccessResponse
from app.features.batches import handlers as batch_handlers
from app.features.posts import handlers as post_handlers
from app.features.publish import tiktok
from app.main import app, live_check


class _JsonReviewRequest:
    headers = {"content-type": "application/json"}

    async def json(self):
        return {"action": "approved"}


def test_script_review_database_work_does_not_starve_the_event_loop(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_review(**_kwargs):
        started.set()
        assert release.wait(timeout=2)
        return SuccessResponse(data={"script_review_status": "approved"})

    monkeypatch.setattr(post_handlers, "_update_post_script_review_sync", blocking_review)

    async def scenario():
        review = asyncio.create_task(
            post_handlers.update_post_script_review("post-1", _JsonReviewRequest())
        )
        assert await asyncio.to_thread(started.wait, 1)
        liveness = await asyncio.wait_for(live_check(), timeout=0.1)
        assert liveness["status"] == "alive"
        release.set()
        return await review

    response = asyncio.run(scenario())
    assert response.data["script_review_status"] == "approved"


def test_batch_list_database_wait_does_not_starve_liveness(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_list(**_kwargs):
        started.set()
        assert release.wait(timeout=2)
        return [], 0

    monkeypatch.setattr(batch_handlers, "list_batches", blocking_list)
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/batches",
        "raw_path": b"/batches",
        "query_string": b"",
        "headers": [(b"accept", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
        "http_version": "1.1",
    })

    async def scenario():
        listing = asyncio.create_task(batch_handlers.list_batches_endpoint(request))
        assert await asyncio.to_thread(started.wait, 1)
        liveness = await asyncio.wait_for(live_check(), timeout=0.1)
        assert liveness["status"] == "alive"
        release.set()
        return await listing

    response = asyncio.run(scenario())
    assert response.data.total == 0


def test_batch_progress_prefers_sse_and_uses_polling_only_as_fallback():
    source = Path("templates/batches/list.html").read_text()

    assert "stopPolling(batchId);\n                setStreamState(batchId, { streamState: 'live' });" in source
    assert "setStreamState(batchId, { streamState: 'reconnecting' });\n                startPolling" in source
    assert "if (!startStream(batchId, Number(expectedPosts), brand))" in source
    assert "startPolling(batchId, Number(expectedPosts), brand);\n            startStream" not in source


def test_tiktok_batch_view_readiness_uses_short_sanitized_cache(monkeypatch):
    calls = 0
    tiktok.invalidate_tiktok_publish_state_cache()

    async def live_state():
        nonlocal calls
        calls += 1
        return {"status": "connected", "publish_ready": True}

    monkeypatch.setattr(tiktok, "get_tiktok_publish_state", live_state)

    async def scenario():
        first = await tiktok.get_cached_tiktok_publish_state()
        second = await tiktok.get_cached_tiktok_publish_state()
        second["status"] = "mutated"
        third = await tiktok.get_cached_tiktok_publish_state()
        return first, third

    first, third = asyncio.run(scenario())
    assert calls == 1
    assert first["status"] == "connected"
    assert third["status"] == "connected"


def test_liveness_exposes_server_timing_and_all_deploy_files_enforce_it():
    response = TestClient(app).get("/livez")

    assert response.status_code == 200
    assert response.headers["server-timing"].startswith("app;dur=")
    for compose_file in (
        "docker-compose.yml",
        "docker-compose.yaml",
        "docker-compose.production.yml",
        "docker-compose.hostinger-runtime.yaml",
    ):
        source = Path(compose_file).read_text()
        assert "healthcheck:" in source
        assert "http://127.0.0.1:8000/livez" in source


def test_batch_detail_avoids_duplicate_summary_query():
    source = Path("app/features/batches/handlers.py").read_text()
    helper = source.split("def _load_batch_detail_records", 1)[1].split(
        "@router.get(\"/{batch_id}\"", 1
    )[0]

    assert "posts_data = get_posts_by_batch(batch_id)" in helper
    assert "get_batch_posts_summary" not in helper
