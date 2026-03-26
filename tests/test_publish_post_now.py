"""Tests for POST /publish/posts/{post_id}/now — immediate publish."""

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from app.features.publish.schemas import PostNowRequest, SocialNetwork
from app.core.states import BatchState
from app.core.errors import ValidationError
from app.features.publish import handlers as publish_handlers


class TestPostNowRequestSchema:
    def test_valid_request_all_networks(self):
        req = PostNowRequest(
            post_id="post-1",
            publish_caption="Test caption",
            social_networks=[SocialNetwork.FACEBOOK, SocialNetwork.INSTAGRAM, SocialNetwork.TIKTOK],
        )
        assert req.post_id == "post-1"
        assert len(req.social_networks) == 3

    def test_valid_request_single_network(self):
        req = PostNowRequest(
            post_id="post-1",
            publish_caption="Test caption",
            social_networks=[SocialNetwork.TIKTOK],
        )
        assert req.social_networks == [SocialNetwork.TIKTOK]

    def test_rejects_empty_networks(self):
        with pytest.raises(Exception):
            PostNowRequest(
                post_id="post-1",
                publish_caption="Test caption",
                social_networks=[],
            )

    def test_rejects_duplicate_networks(self):
        with pytest.raises(Exception):
            PostNowRequest(
                post_id="post-1",
                publish_caption="Test caption",
                social_networks=[SocialNetwork.FACEBOOK, SocialNetwork.FACEBOOK],
            )


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

    def order(self, key):
        return self

    def limit(self, value):
        return self

    def lte(self, key, value):
        self.filters.append(("lte", key, value))
        return self

    def execute(self):
        rows = self.storage[self.table_name]
        matches = [row for row in rows if self._matches(row)]
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


def _make_storage(*, batch_state="S7_PUBLISH_PLAN", post_status="draft", networks=None):
    """Build standard fake DB storage for post-now tests."""
    return {
        "batches": [{"id": "batch-1", "state": batch_state, "meta_connection": {"page_id": "pg1", "page_access_token": "tok", "ig_user_id": "ig1"}, "updated_at": "2026-01-01T00:00:00Z"}],
        "posts": [{
            "id": "post-1",
            "batch_id": "batch-1",
            "video_url": "https://example.com/video.mp4",
            "seed_data": {},
            "scheduled_at": None,
            "publish_caption": "Test caption",
            "social_networks": networks or ["facebook"],
            "publish_status": post_status,
            "publish_results": {},
            "platform_ids": {},
        }],
    }


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestPublishPostNowEndpoint:
    def test_happy_path_facebook(self, monkeypatch):
        storage = _make_storage(networks=["facebook"])
        monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
        monkeypatch.setattr(publish_handlers, "_load_batch", lambda batch_id, fields="id,state,meta_connection": storage["batches"][0])
        monkeypatch.setattr(publish_handlers, "_effective_meta_connection", lambda batch_id, mc: mc)
        monkeypatch.setattr(publish_handlers, "_ensure_meta_targets_for_networks", lambda networks, mc: None)

        async def fake_tiktok_state():
            return {"status": "unavailable"}

        monkeypatch.setattr(publish_handlers, "get_tiktok_publish_state", fake_tiktok_state)

        async def fake_fb(post, mc):
            return "fb-remote-123"

        monkeypatch.setattr(publish_handlers, "_publish_facebook_video", fake_fb)

        result = _run(
            publish_handlers.publish_post_now("post-1", storage["posts"][0]["social_networks"])
        )
        assert result["publish_status"] == "published"
        assert result["publish_results"]["facebook"]["status"] == "published"

    def test_rejects_wrong_batch_state(self, monkeypatch):
        storage = _make_storage(batch_state="S6_QA")
        monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
        monkeypatch.setattr(publish_handlers, "_load_batch", lambda batch_id, fields="id,state,meta_connection": storage["batches"][0])

        with pytest.raises(ValidationError):
            _run(
                publish_handlers.publish_post_now("post-1", ["facebook"])
            )

    def test_rejects_already_published(self, monkeypatch):
        storage = _make_storage(post_status="published")
        monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))

        with pytest.raises(ValidationError):
            _run(
                publish_handlers.publish_post_now("post-1", ["facebook"])
            )

    def test_rejects_already_publishing(self, monkeypatch):
        storage = _make_storage(post_status="publishing")
        monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))

        with pytest.raises(ValidationError):
            _run(
                publish_handlers.publish_post_now("post-1", ["facebook"])
            )


class TestPostNowBatchCompletion:
    def test_last_post_published_completes_batch(self, monkeypatch):
        """When the last active post is published via Post Now, batch advances to S8_COMPLETE."""
        storage = {
            "batches": [{"id": "batch-1", "state": "S7_PUBLISH_PLAN", "meta_connection": {"page_id": "pg1", "page_access_token": "tok", "ig_user_id": "ig1"}, "updated_at": "2026-01-01T00:00:00Z"}],
            "posts": [
                {
                    "id": "post-1",
                    "batch_id": "batch-1",
                    "video_url": "https://example.com/video.mp4",
                    "seed_data": {},
                    "scheduled_at": None,
                    "publish_caption": "Caption 1",
                    "social_networks": ["facebook"],
                    "publish_status": "published",
                    "publish_results": {"facebook": {"status": "published"}},
                    "platform_ids": {"facebook": "fb-1"},
                },
                {
                    "id": "post-2",
                    "batch_id": "batch-1",
                    "video_url": "https://example.com/video2.mp4",
                    "seed_data": {},
                    "scheduled_at": None,
                    "publish_caption": "Caption 2",
                    "social_networks": ["facebook"],
                    "publish_status": "draft",
                    "publish_results": {},
                    "platform_ids": {},
                },
            ],
        }

        monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
        monkeypatch.setattr(publish_handlers, "_load_batch", lambda batch_id, fields="id,state,meta_connection": storage["batches"][0])
        monkeypatch.setattr(publish_handlers, "_effective_meta_connection", lambda batch_id, mc: mc)
        monkeypatch.setattr(publish_handlers, "_ensure_meta_targets_for_networks", lambda networks, mc: None)

        async def fake_tiktok():
            return {"status": "unavailable"}
        monkeypatch.setattr(publish_handlers, "get_tiktok_publish_state", fake_tiktok)

        async def fake_fb(post, mc):
            return "fb-remote-456"
        monkeypatch.setattr(publish_handlers, "_publish_facebook_video", fake_fb)

        result = _run(
            publish_handlers.publish_post_now("post-2", ["facebook"])
        )

        assert result["publish_status"] == "published"
        # Batch should have been advanced to S8_COMPLETE
        assert storage["batches"][0]["state"] == BatchState.S8_COMPLETE.value
