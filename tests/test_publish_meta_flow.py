"""Regression tests for the Meta publish planning and dispatch slice."""

import asyncio
from copy import deepcopy

from app.core.states import BatchState
from app.core.errors import ValidationError
from app.features.publish import handlers as publish_handlers
from app.features.publish.schemas import ConfirmPublishRequest


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
            if operator == "lte" and current > value:
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


def test_confirm_publish_arms_dispatch_without_completing_batch(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "publish_status": "pending",
                "publish_results": {"facebook": {"status": "failed"}},
                "platform_ids": {"facebook": "old"},
            },
            {
                "id": "post-2",
                "publish_status": "pending",
                "publish_results": {"instagram": {"status": "failed"}},
                "platform_ids": {"instagram": "old"},
            },
        ]
    }

    monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(
        publish_handlers,
        "_load_batch",
        lambda batch_id, fields="id,state,meta_connection": {
            "id": batch_id,
            "state": BatchState.S7_PUBLISH_PLAN.value,
            "meta_connection": {
                "selected_page": {"id": "page-1", "access_token": "page-token"},
                "selected_instagram": {"id": "ig-1"},
                "user_access_token": "user-token",
            },
        },
    )
    monkeypatch.setattr(
        publish_handlers,
        "get_post_schedules",
        lambda batch_id: [
            {
                "id": "post-1",
                "scheduled_at": "2026-03-17T12:00:00",
                "publish_caption": "Caption one",
                "social_networks": ["facebook"],
            },
            {
                "id": "post-2",
                "scheduled_at": "2026-03-17T12:15:00",
                "publish_caption": "Caption two",
                "social_networks": ["instagram", "facebook"],
            },
        ],
    )

    response = asyncio.run(
        publish_handlers.confirm_publish(
            "batch-1",
            ConfirmPublishRequest(batch_id="batch-1", confirm=True),
        )
    )

    assert response.total_posts == 2
    assert response.published_count == 0
    assert response.failed_count == 0
    assert [post["publish_status"] for post in storage["posts"]] == ["scheduled", "scheduled"]
    assert storage["posts"][0]["publish_results"] == {}
    assert storage["posts"][1]["platform_ids"] == {}


def test_dispatch_due_posts_records_network_results_individually(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "video_url": "https://cdn.example.com/video.mp4",
                "seed_data": {"script_review_status": "approved"},
                "scheduled_at": "2026-03-17T08:00:00",
                "publish_caption": "Shared caption",
                "social_networks": ["facebook", "instagram"],
                "publish_status": "scheduled",
                "publish_results": {},
                "platform_ids": {},
            }
        ]
    }

    touched_batches = []
    monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(
        publish_handlers,
        "_load_batch",
        lambda batch_id, fields="id,state,meta_connection": {
            "id": batch_id,
            "state": BatchState.S7_PUBLISH_PLAN.value,
            "meta_connection": {
                "selected_page": {"id": "page-1", "access_token": "page-token"},
                "selected_instagram": {"id": "ig-1"},
                "user_access_token": "user-token",
            },
        },
    )
    monkeypatch.setattr(publish_handlers, "_publish_facebook_video", lambda post, meta_connection: asyncio.sleep(0, result="fb-remote-1"))

    async def _fail_instagram(_post, _meta_connection):
        raise ValidationError("Instagram rejected the selected media.")

    monkeypatch.setattr(publish_handlers, "_publish_instagram_reel", _fail_instagram)
    monkeypatch.setattr(
        publish_handlers,
        "_reconcile_completed_batches",
        lambda batch_ids: touched_batches.extend(batch_ids),
    )

    result = asyncio.run(publish_handlers.dispatch_due_posts())

    assert result["processed"] == 1
    assert result["published"] == 0
    assert result["failed"] == 1
    assert touched_batches == ["batch-1"]

    post = storage["posts"][0]
    assert post["publish_status"] == "failed"
    assert post["platform_ids"] == {"facebook": "fb-remote-1"}
    assert post["publish_results"]["facebook"]["status"] == "published"
    assert post["publish_results"]["instagram"]["status"] == "failed"
    assert post["publish_results"]["instagram"]["error_code"] == "validation_error"
