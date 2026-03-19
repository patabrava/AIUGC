"""Regression tests for the Meta publish planning and dispatch slice."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from fastapi import HTTPException

from app.core.states import BatchState
from app.core.errors import ValidationError
from app.features.batches import handlers as batch_handlers
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


class _FakeRequest:
    def __init__(self, *, headers=None, form_data=None):
        self.headers = headers or {}
        self._form_data = form_data or {}

    async def form(self):
        return self._form_data


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
            _FakeRequest(headers={}),
            ConfirmPublishRequest(batch_id="batch-1", confirm=True),
        )
    )

    assert response.total_posts == 2
    assert response.published_count == 0
    assert response.failed_count == 0
    assert [post["publish_status"] for post in storage["posts"]] == ["scheduled", "scheduled"]
    assert storage["posts"][0]["publish_results"] == {}
    assert storage["posts"][1]["platform_ids"] == {}


def test_confirm_publish_accepts_htmx_form_payload(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "publish_status": "pending",
                "publish_results": {},
                "platform_ids": {},
            }
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
            },
        },
    )
    monkeypatch.setattr(
        publish_handlers,
        "get_post_schedules",
        lambda batch_id: [
            {
                "id": "post-1",
                "scheduled_at": "2026-03-20T02:00:00+00:00",
                "publish_caption": "Caption one",
                "social_networks": ["facebook", "instagram"],
            }
        ],
    )

    response = asyncio.run(
        publish_handlers.confirm_publish(
            "batch-1",
            _FakeRequest(
                headers={"content-type": "application/x-www-form-urlencoded"},
                form_data={"batch_id": "batch-1", "confirm": "true"},
            ),
            None,
        )
    )

    assert response.total_posts == 1
    assert response.results[0].success is True
    assert storage["posts"][0]["publish_status"] == "scheduled"


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


def test_dispatch_due_posts_includes_tiktok_direct_post(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "video_url": "https://cdn.example.com/video.mp4",
                "seed_data": {"script_review_status": "approved"},
                "scheduled_at": "2026-03-17T08:00:00",
                "publish_caption": "Shared caption",
                "social_networks": ["facebook", "instagram", "tiktok"],
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
    monkeypatch.setattr(publish_handlers, "_publish_instagram_reel", lambda post, meta_connection: asyncio.sleep(0, result="ig-remote-1"))
    monkeypatch.setattr(
        publish_handlers,
        "get_tiktok_publish_state",
        lambda: asyncio.sleep(
            0,
            result={
                "status": "connected",
                "publish_ready": True,
                "creator_info": {
                    "privacy_level_options": ["SELF_ONLY"],
                    "comment_disabled": False,
                    "duet_disabled": False,
                    "stitch_disabled": False,
                },
            },
        ),
    )

    async def _fake_tiktok_publish(post_id, *, caption=None, privacy_level, disable_comment, disable_duet, disable_stitch):
        assert post_id == "post-1"
        assert caption == "Shared caption"
        assert privacy_level == "SELF_ONLY"
        return {
            "id": "job-1",
            "status": "published",
            "tiktok_publish_id": "tt-publish-1",
            "response_payload_json": {
                "provider_status": "PUBLISH_COMPLETE",
                "publicaly_available_post_id": ["tt-post-1"],
            },
            "error_message": "",
        }

    monkeypatch.setattr(publish_handlers, "publish_tiktok_direct_for_post", _fake_tiktok_publish)
    monkeypatch.setattr(
        publish_handlers,
        "_reconcile_completed_batches",
        lambda batch_ids: touched_batches.extend(batch_ids),
    )

    result = asyncio.run(publish_handlers.dispatch_due_posts())

    assert result["processed"] == 1
    assert result["published"] == 1
    assert result["failed"] == 0
    assert touched_batches == ["batch-1"]

    post = storage["posts"][0]
    assert post["publish_status"] == "published"
    assert post["platform_ids"]["tiktok"] == "tt-post-1"
    assert post["publish_results"]["tiktok"]["status"] == "published"
    assert post["publish_results"]["tiktok"]["provider_status"] == "PUBLISH_COMPLETE"


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
    assert "scope=pages_show_list%2Cpages_read_engagement%2Cpages_manage_posts%2Cbusiness_management%2Cinstagram_basic%2Cinstagram_content_publish" in location


def test_meta_callback_uses_assigned_pages_when_accounts_is_empty(monkeypatch):
    import asyncio

    captured = {}
    monkeypatch.setattr(
        publish_handlers,
        "get_settings",
        lambda: SimpleNamespace(
            meta_app_id="meta-app-id",
            meta_app_secret="meta-secret",
            meta_redirect_uri="https://example.com/publish/meta/callback",
        ),
    )
    monkeypatch.setattr(
        publish_handlers,
        "_set_workspace_meta_connection",
        lambda meta_connection, source_batch_id=None: captured.update(meta_connection=deepcopy(meta_connection), source_batch_id=source_batch_id),
    )

    async def _fake_meta_request(method, url, *, params=None, data=None):
        if url.endswith("/oauth/access_token"):
            return {"access_token": "user-token", "expires_in": 3600}
        if url.endswith("/me"):
            return {"id": "user-1", "name": "Camilo"}
        if url.endswith("/me/accounts"):
            return {"data": []}
        if url.endswith("/me/assigned_pages"):
            return {
                "data": [
                    {
                        "id": "page-1",
                        "name": "Lippe Lift",
                        "tasks": ["ADVERTISE", "CREATE_CONTENT"],
                        "access_token": "page-token",
                        "instagram_business_account": {"id": "ig-1", "username": "lippe_test"},
                    }
                ]
            }
        raise AssertionError(f"Unexpected Meta URL: {url}")

    monkeypatch.setattr(publish_handlers, "_meta_request", _fake_meta_request)

    state = publish_handlers._build_meta_state("batch-1", "meta-secret")
    response = asyncio.run(publish_handlers.meta_oauth_callback(code="auth-code", state=state))

    assert response.status_code == 302
    assert captured["source_batch_id"] == "batch-1"
    assert captured["meta_connection"]["status"] == "connected"
    assert len(captured["meta_connection"]["available_pages"]) == 1
    assert captured["meta_connection"]["available_pages"][0]["id"] == "page-1"
    assert captured["meta_connection"]["available_pages"][0]["instagram_business_account"]["id"] == "ig-1"
    assert captured["meta_connection"]["selected_page"]["id"] == "page-1"
    assert captured["meta_connection"]["selected_instagram"]["id"] == "ig-1"


def test_meta_callback_accepts_connected_instagram_account(monkeypatch):
    import asyncio

    captured = {}
    monkeypatch.setattr(
        publish_handlers,
        "get_settings",
        lambda: SimpleNamespace(
            meta_app_id="meta-app-id",
            meta_app_secret="meta-secret",
            meta_redirect_uri="https://example.com/publish/meta/callback",
        ),
    )
    monkeypatch.setattr(
        publish_handlers,
        "_set_workspace_meta_connection",
        lambda meta_connection, source_batch_id=None: captured.update(meta_connection=deepcopy(meta_connection), source_batch_id=source_batch_id),
    )

    async def _fake_meta_request(method, url, *, params=None, data=None):
        if url.endswith("/oauth/access_token"):
            return {"access_token": "user-token", "expires_in": 3600}
        if url.endswith("/me"):
            return {"id": "user-1", "name": "Camilo"}
        if url.endswith("/me/accounts"):
            return {
                "data": [
                    {
                        "id": "page-1",
                        "name": "Lippe Lift",
                        "tasks": ["ADVERTISE", "CREATE_CONTENT"],
                        "access_token": "page-token",
                        "connected_instagram_account": {"id": "ig-1", "username": "lippe_test"},
                    }
                ]
            }
        if url.endswith("/me/assigned_pages"):
            return {"data": []}
        raise AssertionError(f"Unexpected Meta URL: {url}")

    monkeypatch.setattr(publish_handlers, "_meta_request", _fake_meta_request)

    state = publish_handlers._build_meta_state("batch-1", "meta-secret")
    response = asyncio.run(publish_handlers.meta_oauth_callback(code="auth-code", state=state))

    assert response.status_code == 302
    assert captured["meta_connection"]["status"] == "connected"
    assert captured["meta_connection"]["available_pages"][0]["instagram_business_account"]["id"] == "ig-1"
    assert captured["meta_connection"]["selected_page"]["id"] == "page-1"
    assert captured["meta_connection"]["selected_instagram"]["id"] == "ig-1"


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
            "publish_ready": False,
            "readiness_status": "page_selection_required",
            "readiness_reason": "Select a Page target.",
            "user": {"name": "Operator"},
            "user_access_token": "secret",
            "selected_page": {"id": "page-1", "name": "Page Name", "access_token": "page-secret"},
            "selected_instagram": {"id": "ig-1", "username": "brand"},
        },
    )
    monkeypatch.setattr(
        publish_handlers,
        "get_tiktok_publish_state",
        lambda: asyncio.sleep(
            0,
            result={
                "status": "connected",
                "publish_ready": True,
                "draft_ready": True,
                "readiness_status": "publish_ready",
                "readiness_reason": "Ready to post.",
                "display_name": "Sandbox Creator",
                "open_id": "open-123",
            },
        ),
    )

    response = asyncio.run(publish_handlers.get_accounts_status())

    assert response.data["providers"]["meta"]["connected"] is True
    assert response.data["providers"]["meta"]["publish_ready"] is True
    assert response.data["providers"]["tiktok"]["connected"] is True
    assert response.data["providers"]["tiktok"]["publish_ready"] is True
    assert response.data["tiktok_connection"]["display_name"] == "Sandbox Creator"
    assert "user_access_token" not in response.data["meta_connection"]


def test_meta_publish_readiness_reports_missing_publishable_page():
    readiness = publish_handlers._meta_publish_readiness(
        {
            "status": "connected",
            "available_pages": [],
            "selected_page": {},
            "selected_instagram": {},
        }
    )

    assert readiness["publish_ready"] is False
    assert readiness["readiness_status"] == "missing_instagram_business"


def test_effective_meta_connection_auto_selects_only_publishable_page(monkeypatch):
    monkeypatch.setattr(
        publish_handlers,
        "_get_workspace_meta_connection",
        lambda preferred_batch_id=None: {},
    )

    resolved = publish_handlers._effective_meta_connection(
        "batch-1",
        {
            "status": "connected",
            "available_pages": [
                {
                    "id": "page-1",
                    "name": "Lippe Lift",
                    "access_token": "page-token",
                    "instagram_business_account": {"id": "ig-1", "username": "lippe_test"},
                }
            ],
            "selected_page": {},
            "selected_instagram": {},
        },
    )

    assert resolved["selected_page"]["id"] == "page-1"
    assert resolved["selected_instagram"]["id"] == "ig-1"


def test_batch_meta_connection_sanitizer_preserves_publish_readiness():
    sanitized = batch_handlers._sanitize_meta_connection(
        {
            "status": "connected",
            "user_access_token": "user-secret",
            "selected_page": {
                "id": "page-1",
                "name": "Page Name",
                "access_token": "page-secret",
            },
            "selected_instagram": {"id": "ig-1", "username": "brand"},
            "available_pages": [
                {
                    "id": "page-1",
                    "name": "Page Name",
                    "access_token": "page-secret",
                    "instagram_business_account": {"id": "ig-1", "username": "brand"},
                }
            ],
        }
    )

    assert sanitized["publish_ready"] is True
    assert sanitized["readiness_status"] == "publish_ready"
    assert sanitized["readiness_reason"] == "Facebook and Instagram are ready to publish from this workspace."
    assert "user_access_token" not in sanitized
    assert "access_token" not in sanitized["selected_page"]


def test_post_schedule_request_accepts_utc_iso_timestamp():
    request = PostScheduleRequest(
        post_id="post-1",
        scheduled_at="2026-03-20T02:00:00Z",
        publish_caption="Caption",
        social_networks=[SocialNetwork.FACEBOOK],
    )

    assert request.scheduled_at.isoformat() == "2026-03-20T02:00:00+00:00"


def test_update_post_schedule_request_accepts_utc_iso_timestamp():
    from app.features.publish.schemas import UpdatePostScheduleRequest

    request = UpdatePostScheduleRequest(
        scheduled_at="2026-03-20T02:00:00Z",
        publish_caption="Caption",
        social_networks=[SocialNetwork.INSTAGRAM],
    )

    assert request.scheduled_at.isoformat() == "2026-03-20T02:00:00+00:00"


def test_derive_publish_status_does_not_treat_tiktok_inbox_as_published():
    status = publish_handlers._derive_publish_status(
        ["tiktok"],
        {"tiktok": {"status": "awaiting_user_action"}},
    )

    assert status == "publishing"


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
