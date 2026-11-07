import asyncio
from threading import Event

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.features.batches import handlers as batch_handlers


def _batch_records(batch_id):
    state = "S7_PUBLISH_PLAN" if batch_id == "publish" else "S2_SEEDED"
    batch = {
        "id": batch_id,
        "brand": "Workflow fixture",
        "state": state,
        "creation_mode": "semantic_ugc",
        "post_type_counts": {},
        "archived": False,
        "created_at": "2026-11-07T12:00:00Z",
        "updated_at": "2026-11-07T12:00:00Z",
    }
    return batch, {"posts_count": 0, "posts_by_state": {}}, [], {}, {}, []


def test_creator_readiness_is_only_loaded_for_publish_html_or_json(monkeypatch):
    app = FastAPI()
    app.include_router(batch_handlers.router)
    monkeypatch.setattr(batch_handlers, "_load_batch_detail_records", _batch_records)
    calls = []

    async def readiness():
        calls.append(True)
        return {"readiness_status": "reconnect_required"}

    monkeypatch.setattr(batch_handlers, "get_tiktok_publish_state", readiness)
    with TestClient(app) as client:
        before_publish = client.get("/batches/scripts", headers={"Accept": "text/html"})
        assert before_publish.status_code == 200
        assert not calls
        publish = client.get("/batches/publish", headers={"Accept": "text/html"})
        assert publish.status_code == 200
        assert "reconnect_required" in publish.text
        assert len(calls) == 1
        api = client.get("/batches/scripts")
        assert api.json()["data"]["tiktok_connection"] == {
            "readiness_status": "reconnect_required"
        }
        assert len(calls) == 2


def test_batch_projection_leaves_the_event_loop_available(monkeypatch):
    app = FastAPI()
    app.include_router(batch_handlers.router)
    monkeypatch.setattr(batch_handlers, "_load_batch_detail_records", _batch_records)
    original_view = batch_handlers._build_batch_detail_view
    entered = Event()
    release = Event()
    completed = Event()

    def slow_projection(batch):
        entered.set()
        release.wait(timeout=2)
        completed.set()
        return original_view(batch)

    monkeypatch.setattr(batch_handlers, "_build_batch_detail_view", slow_projection)

    @app.get("/probe")
    async def probe():
        return {"available": True}

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(
                client.get("/batches/scripts", headers={"Accept": "text/html"})
            )
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                result = await client.get("/probe")
                assert result.json() == {"available": True}
                assert not completed.is_set(), "Batch projection blocked the event loop"
            finally:
                release.set()
                assert (await pending).status_code == 200

    asyncio.run(exercise())
