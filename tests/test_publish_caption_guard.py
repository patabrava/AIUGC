import asyncio
from copy import deepcopy
from types import SimpleNamespace

from app.features.publish import handlers as publish_handlers
from app.core.states import BatchState


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, storage, table_name):
        self.storage = storage
        self.table_name = table_name
        self.filters = []
        self.operation = "select"
        self.payload = None
        self.order_key = None
        self.limit_value = None

    def select(self, _fields):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def lte(self, key, value):
        self.filters.append(("lte", key, value))
        return self

    def order(self, key):
        self.order_key = key
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        rows = self.storage[self.table_name]
        matches = [row for row in rows if self._matches(row)]
        if self.order_key:
            matches = sorted(matches, key=lambda row: row.get(self.order_key))
        if self.limit_value is not None:
            matches = matches[: self.limit_value]

        if self.operation == "update":
            updated = []
            for row in matches:
                row.update(deepcopy(self.payload))
                updated.append(deepcopy(row))
            return _FakeResponse(updated)

        return _FakeResponse([deepcopy(row) for row in matches])

    def _matches(self, row):
        for operator, key, value in self.filters:
            current = row.get(key)
            if operator == "eq" and current != value:
                return False
            if operator == "lte" and current is not None and current > value:
                return False
        return True


class _FakeClient:
    def __init__(self, storage):
        self.storage = storage

    def table(self, table_name):
        return _FakeTable(self.storage, table_name)


class _FakeSupabase:
    def __init__(self, storage):
        self.client = _FakeClient(storage)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _build_storage():
    now = "2026-04-11T10:00:00"
    return {
        "batches": [
            {
                "id": "batch-1",
                "state": BatchState.S7_PUBLISH_PLAN.value,
                "meta_connection": {
                    "status": "connected",
                    "selected_page": {"id": "page-1", "access_token": "token-1"},
                    "selected_instagram": {"id": "ig-1"},
                },
            }
        ],
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "video_url": "https://cdn.example.com/video.mp4",
                "video_metadata": {},
                "seed_data": {
                    "post_type": "value",
                    "caption_review_required": True,
                    "script_review_status": "approved",
                },
                "scheduled_at": now,
                "publish_caption": "A useful caption",
                "social_networks": ["facebook"],
                "publish_status": "scheduled",
                "publish_results": {},
                "platform_ids": {},
            }
        ],
    }


def test_dispatch_due_posts_blocks_review_required_value_caption(monkeypatch):
    storage = _build_storage()
    monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(
        publish_handlers,
        "_load_batch",
        lambda batch_id, fields="id,meta_connection,state": storage["batches"][0],
    )
    monkeypatch.setattr(
        publish_handlers,
        "_effective_meta_connection",
        lambda batch_id, mc: mc,
    )
    monkeypatch.setattr(
        publish_handlers,
        "get_settings",
        lambda: SimpleNamespace(value_caption_block_on_publish=True),
    )

    called = {"facebook": 0}

    async def fake_publish_facebook_video(post, meta_connection):
        called["facebook"] += 1
        return "fb-1"

    monkeypatch.setattr(publish_handlers, "_publish_facebook_video", fake_publish_facebook_video)

    async def fake_tiktok_state():
        return {"status": "unavailable"}

    monkeypatch.setattr(publish_handlers, "get_tiktok_publish_state", fake_tiktok_state)

    result = _run(publish_handlers.dispatch_due_posts(limit=10, trigger="test"))

    assert result["processed"] == 1
    assert result["failed"] == 1
    assert called["facebook"] == 0
    assert storage["posts"][0]["publish_status"] == "scheduled"
    assert storage["posts"][0]["publish_results"]["dispatch"]["status"] == "blocked"


def test_dispatch_due_posts_allows_review_required_value_caption_when_guard_disabled(monkeypatch):
    storage = _build_storage()
    monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(
        publish_handlers,
        "_load_batch",
        lambda batch_id, fields="id,meta_connection,state": storage["batches"][0],
    )
    monkeypatch.setattr(
        publish_handlers,
        "_effective_meta_connection",
        lambda batch_id, mc: mc,
    )
    monkeypatch.setattr(
        publish_handlers,
        "get_settings",
        lambda: SimpleNamespace(value_caption_block_on_publish=False),
    )

    async def fake_publish_facebook_video(post, meta_connection):
        return "fb-1"

    monkeypatch.setattr(publish_handlers, "_publish_facebook_video", fake_publish_facebook_video)

    async def fake_tiktok_state():
        return {"status": "unavailable"}

    monkeypatch.setattr(publish_handlers, "get_tiktok_publish_state", fake_tiktok_state)

    result = _run(publish_handlers.dispatch_due_posts(limit=10, trigger="test"))

    assert result["processed"] == 1
    assert result["published"] == 1
    assert storage["posts"][0]["publish_status"] == "published"
