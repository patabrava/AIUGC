"""Regression tests for the Meta publish planning and dispatch slice."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from fastapi import HTTPException

from app.core.states import BatchState
from app.core.errors import ValidationError
from app.features.publish import handlers as publish_handlers
from app.features.publish.schemas import ConfirmPublishRequest, PostScheduleRequest, SocialNetwork


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
    monkeypatch.setattr(publish_handlers, "_list_batch_rows", lambda fields="id,meta_connection": [])
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
    monkeypatch.setattr(publish_handlers, "_list_batch_rows", lambda fields="id,meta_connection": [])
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


def test_workspace_meta_connection_prefers_connected_state(monkeypatch):
    monkeypatch.setattr(
        publish_handlers,
        "_list_batch_rows",
        lambda fields="id,meta_connection": [
            {
                "id": "batch-1",
                "meta_connection": {
                    "status": "disconnected",
                    "updated_at": "2026-03-17T10:00:00",
                },
            },
            {
                "id": "batch-2",
                "meta_connection": {
                    "status": "connected",
                    "updated_at": "2026-03-17T12:00:00",
                    "selected_page": {"id": "page-1"},
                },
            },
        ],
    )

    resolved = publish_handlers._effective_meta_connection("batch-1", {})

    assert resolved["status"] == "connected"
    assert resolved["selected_page"]["id"] == "page-1"


def test_schedule_post_rejects_missing_video_before_save(monkeypatch):
    monkeypatch.setattr(
        publish_handlers,
        "_load_post",
        lambda post_id, fields="id,batch_id,video_url": {
            "id": post_id,
            "batch_id": "batch-1",
            "video_url": None,
        },
    )

    request = PostScheduleRequest(
        post_id="post-1",
        scheduled_at=datetime.utcnow() + timedelta(hours=2),
        publish_caption="Caption",
        social_networks=[SocialNetwork.FACEBOOK],
    )

    try:
        asyncio.run(publish_handlers.schedule_post("post-1", request))
        assert False, "schedule_post should reject missing video"
    except HTTPException as exc:
        assert exc.status_code == 422
        assert exc.detail == "Generate the video before saving a publish schedule."


def test_connect_meta_account_allows_pre_s7_batches(monkeypatch):
    monkeypatch.setattr(
        publish_handlers,
        "_load_batch",
        lambda batch_id, fields="id,state,meta_connection": {
            "id": batch_id,
            "state": BatchState.S1_SETUP.value,
        },
    )
    monkeypatch.setattr(
        publish_handlers,
        "_require_meta_settings",
        lambda: SimpleNamespace(
            meta_app_id="meta-app-id",
            meta_app_secret="meta-app-secret",
            meta_redirect_uri="https://example.com/publish/meta/callback",
        ),
    )

    response = asyncio.run(publish_handlers.connect_meta_account("batch-1"))

    assert response.status_code == 302
    location = response.headers["location"]
    assert "client_id=meta-app-id" in location
    assert "redirect_uri=https%3A%2F%2Fexample.com%2Fpublish%2Fmeta%2Fcallback" in location
    assert "scope=instagram_business_basic%2Cinstagram_business_content_publish" in location


def test_connect_meta_account_can_resolve_batch_for_navbar(monkeypatch):
    monkeypatch.setattr(
        publish_handlers,
        "_list_batch_rows",
        lambda fields="id,updated_at,created_at": [
            {"id": "batch-old", "updated_at": "2026-03-17T10:00:00"},
            {"id": "batch-new", "updated_at": "2026-03-18T09:00:00"},
        ],
    )
    monkeypatch.setattr(
        publish_handlers,
        "_require_meta_settings",
        lambda: SimpleNamespace(
            meta_app_id="meta-app-id",
            meta_app_secret="meta-app-secret",
            meta_redirect_uri="https://example.com/publish/meta/callback",
        ),
    )

    response = asyncio.run(publish_handlers.connect_meta_account())

    assert response.status_code == 302
    assert "client_id=meta-app-id" in response.headers["location"]


def test_get_meta_status_returns_sanitized_workspace_state(monkeypatch):
    monkeypatch.setattr(
        publish_handlers,
        "_get_workspace_meta_connection",
        lambda preferred_batch_id=None: {
            "status": "connected",
            "user": {"name": "Operator"},
            "user_access_token": "secret",
            "selected_page": {"id": "page-1", "name": "Page Name", "access_token": "page-secret"},
            "selected_instagram": {"id": "ig-1", "username": "brand"},
            "available_pages": [
                {"id": "page-1", "name": "Page Name", "access_token": "page-secret"},
            ],
        },
    )

    response = asyncio.run(publish_handlers.get_meta_status())

    assert response.data["is_connected"] is True
    assert response.data["has_selected_target"] is True
    assert response.data["meta_connection"]["user"]["name"] == "Operator"
    assert "user_access_token" not in response.data["meta_connection"]


def test_get_accounts_status_includes_meta_and_tiktok(monkeypatch):
    monkeypatch.setattr(
        publish_handlers,
        "_get_workspace_meta_connection",
        lambda preferred_batch_id=None: {
            "status": "connected",
            "user": {"name": "Operator"},
            "user_access_token": "secret",
            "selected_page": {"id": "page-1", "name": "Page Name", "access_token": "page-secret"},
            "selected_instagram": {"id": "ig-1", "username": "brand"},
        },
    )
    monkeypatch.setattr(
        publish_handlers,
        "get_tiktok_public_account",
        lambda: {
            "status": "connected",
            "display_name": "Sandbox Creator",
            "open_id": "open-123",
        },
    )

    response = asyncio.run(publish_handlers.get_accounts_status())

    assert response.data["providers"]["meta"]["connected"] is True
    assert response.data["providers"]["tiktok"]["connected"] is True
    assert response.data["tiktok_connection"]["display_name"] == "Sandbox Creator"
    assert "user_access_token" not in response.data["meta_connection"]


def test_publish_instagram_reel_uses_selected_page_token(monkeypatch):
    calls = []

    async def _fake_meta_request(method, url, *, params=None, data=None):
        calls.append({"method": method, "url": url, "params": params, "data": data})
        if url.endswith("/media"):
            return {"id": "container-1"}
        if url.endswith("/media_publish"):
            return {"id": "ig-media-1"}
        if url.endswith("/container-1"):
            return {"status_code": "FINISHED"}
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(publish_handlers, "_meta_request", _fake_meta_request)

    remote_id = asyncio.run(
        publish_handlers._publish_instagram_reel(
            {
                "video_url": "https://cdn.example.com/reel.mp4",
                "publish_caption": "Caption",
            },
            {
                "selected_page": {"id": "page-1", "access_token": "page-token"},
                "selected_instagram": {"id": "ig-1"},
                "user_access_token": "user-token",
            },
        )
    )

    assert remote_id == "ig-media-1"
    assert calls[0]["data"]["access_token"] == "page-token"
    assert calls[1]["params"]["access_token"] == "page-token"
    assert calls[2]["data"]["access_token"] == "page-token"
    assert calls[0]["url"].endswith("/ig-1/media")
    assert calls[2]["url"].endswith("/ig-1/media_publish")
