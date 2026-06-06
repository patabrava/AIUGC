"""Tests for POST /publish/posts/{post_id}/now — immediate publish."""

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
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

    def test_accepts_valid_tiktok_settings_for_post_now(self):
        req = PostNowRequest(
            post_id="post-1",
            publish_caption="Test caption",
            social_networks=[SocialNetwork.TIKTOK],
            tiktok_settings={
                "title": "TikTok title",
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "allow_comment": True,
                "allow_duet": False,
                "allow_stitch": False,
                "commercial_disclosure": False,
                "your_brand": False,
                "branded_content": False,
                "consent_acknowledged": True,
            },
        )

        assert req.tiktok_settings.consent_acknowledged is True

    def test_rejects_tiktok_settings_without_consent(self):
        with pytest.raises(Exception) as excinfo:
            PostNowRequest(
                post_id="post-1",
                publish_caption="Test caption",
                social_networks=[SocialNetwork.TIKTOK],
                tiktok_settings={
                    "title": "TikTok title",
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "consent_acknowledged": False,
                },
            )

        assert "consent_acknowledged" in str(excinfo.value)


def test_post_now_route_passes_tiktok_settings(monkeypatch):
    import app.core.config as config_module
    from app.main import app

    captured = {}

    async def fake_publish_post_now(post_id, social_networks, *, publish_caption=None, tiktok_settings=None):
        captured["post_id"] = post_id
        captured["social_networks"] = social_networks
        captured["publish_caption"] = publish_caption
        captured["tiktok_settings"] = tiktok_settings
        return {"post_id": post_id, "publish_status": "published", "publish_results": {}, "platform_ids": {}}

    monkeypatch.setattr(publish_handlers, "publish_post_now", fake_publish_post_now)
    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BYPASS_AUTH_IN_DEVELOPMENT", "true")

    client = TestClient(app, base_url="http://localhost")
    response = client.post(
        "/publish/posts/post-1/now",
        json={
            "post_id": "post-1",
            "publish_caption": "Test caption",
            "social_networks": ["tiktok"],
            "tiktok_settings": {
                "title": "TikTok title",
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "allow_comment": True,
                "allow_duet": False,
                "allow_stitch": False,
                "commercial_disclosure": False,
                "your_brand": False,
                "branded_content": False,
                "consent_acknowledged": True,
            },
        },
    )

    assert response.status_code == 200, response.text
    assert captured["post_id"] == "post-1"
    assert captured["social_networks"] == ["tiktok"]
    assert captured["tiktok_settings"]["consent_acknowledged"] is True


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
        "batches": [{"id": "batch-1", "state": batch_state, "meta_connection": {"status": "connected", "selected_page": {"id": "pg1", "access_token": "tok"}, "selected_instagram": {"id": "ig1"}}, "updated_at": "2026-01-01T00:00:00Z"}],
        "posts": [{
            "id": "post-1",
            "batch_id": "batch-1",
            "video_url": "https://example.com/video.mp4",
            "video_metadata": {},
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

    def test_tiktok_draft_upload_is_recorded(self, monkeypatch):
        storage = _make_storage(networks=["tiktok"])
        monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
        monkeypatch.setattr(publish_handlers, "_load_batch", lambda batch_id, fields="id,state,meta_connection": storage["batches"][0])
        monkeypatch.setattr(publish_handlers, "_effective_meta_connection", lambda batch_id, mc: mc)
        monkeypatch.setattr(publish_handlers, "_ensure_meta_targets_for_networks", lambda networks, mc: None)

        async def fake_tiktok_state():
            return {
                "status": "connected",
                "environment": "production",
                "publish_ready": True,
                "creator_info": {"privacy_level_options": ["SELF_ONLY"]},
            }

        monkeypatch.setattr(publish_handlers, "get_tiktok_publish_state", fake_tiktok_state)

        async def fake_tiktok_publish(post_id, *, caption=None):
            return {
                "id": "job-1",
                "status": "submitted",
                "tiktok_publish_id": "tt-publish-1",
                "response_payload_json": {
                    "provider_status": "SEND_TO_USER_INBOX",
                    "publicaly_available_post_id": [],
                },
                "error_message": "",
            }

        monkeypatch.setattr(publish_handlers, "upload_tiktok_draft_for_post", fake_tiktok_publish)

        result = _run(publish_handlers.publish_post_now("post-1", ["tiktok"]))

        assert result["publish_status"] == "publishing"
        assert result["platform_ids"] == {}
        assert result["publish_results"]["tiktok"]["status"] == "awaiting_user_action"
        assert result["publish_results"]["tiktok"]["post_mode"] == "draft"

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
            "batches": [{"id": "batch-1", "state": "S7_PUBLISH_PLAN", "meta_connection": {"status": "connected", "selected_page": {"id": "pg1", "access_token": "tok"}, "selected_instagram": {"id": "ig1"}}, "updated_at": "2026-01-01T00:00:00Z"}],
            "posts": [
                {
                    "id": "post-1",
                    "batch_id": "batch-1",
                    "video_url": "https://example.com/video.mp4",
                    "video_metadata": {},
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
                    "video_metadata": {},
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


class TestPostNowTikTokRouting:
    """When TikTok readiness is publish_ready and settings are provided, call direct-post; else draft."""

    def _settings(self):
        return {
            "title": "Hi",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": False,
            "allow_stitch": False,
            "commercial_disclosure": False,
            "your_brand": False,
            "branded_content": False,
        }

    def test_post_now_routes_to_direct_when_publish_ready(self, monkeypatch):
        storage = _make_storage(networks=["tiktok"])
        monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
        monkeypatch.setattr(publish_handlers, "_load_batch", lambda batch_id, fields="id,state,meta_connection": storage["batches"][0])
        monkeypatch.setattr(publish_handlers, "_effective_meta_connection", lambda batch_id, mc: mc)
        monkeypatch.setattr(publish_handlers, "_ensure_meta_targets_for_networks", lambda networks, mc: None)

        captured = {}

        async def fake_state():
            return {"readiness_status": "publish_ready", "publish_ready": True}

        async def fake_direct(post_id, **kwargs):
            captured["called"] = "direct"
            captured["post_id"] = post_id
            captured["kwargs"] = kwargs
            return {
                "id": "job-1",
                "status": "published",
                "post_mode": "direct",
                "response_payload_json": {
                    "publicaly_available_post_id": ["123"],
                    "provider_status": "PUBLISH_COMPLETE",
                },
                "tiktok_publish_id": "p1",
                "error_message": "",
                "post_id": post_id,
            }

        async def fake_draft(post_id, caption=None):
            captured["called"] = "draft"
            return {}

        monkeypatch.setattr(publish_handlers, "get_tiktok_publish_state", fake_state)
        monkeypatch.setattr(publish_handlers, "publish_tiktok_direct_for_post", fake_direct)
        monkeypatch.setattr(publish_handlers, "upload_tiktok_draft_for_post", fake_draft)

        result = _run(
            publish_handlers.publish_post_now(
                "post-1",
                ["tiktok"],
                publish_caption="c",
                tiktok_settings=self._settings(),
            )
        )

        assert captured["called"] == "direct"
        assert captured["kwargs"]["title"] == "Hi"
        assert captured["kwargs"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
        assert result["publish_results"]["tiktok"]["post_mode"] == "direct"
        assert result["publish_results"]["tiktok"]["status"] == "published"

    def test_post_now_falls_back_to_draft_when_not_publish_ready(self, monkeypatch):
        storage = _make_storage(networks=["tiktok"])
        monkeypatch.setattr(publish_handlers, "get_supabase", lambda: _FakeSupabase(storage))
        monkeypatch.setattr(publish_handlers, "_load_batch", lambda batch_id, fields="id,state,meta_connection": storage["batches"][0])
        monkeypatch.setattr(publish_handlers, "_effective_meta_connection", lambda batch_id, mc: mc)
        monkeypatch.setattr(publish_handlers, "_ensure_meta_targets_for_networks", lambda networks, mc: None)

        captured = {}

        async def fake_state():
            return {"readiness_status": "draft_ready", "publish_ready": False}

        async def fake_direct(post_id, **kwargs):
            captured["called"] = "direct"
            return {}

        async def fake_draft(post_id, caption=None):
            captured["called"] = "draft"
            return {
                "id": "job-2",
                "status": "submitted",
                "post_mode": "draft",
                "response_payload_json": {
                    "publicaly_available_post_id": [],
                    "provider_status": "SEND_TO_USER_INBOX",
                },
                "tiktok_publish_id": "p2",
                "error_message": "",
                "post_id": post_id,
            }

        monkeypatch.setattr(publish_handlers, "get_tiktok_publish_state", fake_state)
        monkeypatch.setattr(publish_handlers, "publish_tiktok_direct_for_post", fake_direct)
        monkeypatch.setattr(publish_handlers, "upload_tiktok_draft_for_post", fake_draft)

        result = _run(
            publish_handlers.publish_post_now(
                "post-1",
                ["tiktok"],
                publish_caption="c",
                tiktok_settings=None,
            )
        )

        assert captured["called"] == "draft"
        assert result["publish_results"]["tiktok"]["post_mode"] == "draft"
