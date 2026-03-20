"""Regression tests for TikTok draft upload persistence."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from app.features.publish import tiktok
from app.features.publish.schemas import TikTokPublishRequest, TikTokUploadDraftRequest


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

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
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

        if self.operation == "insert":
            payload = deepcopy(self.payload)
            if "id" not in payload:
                payload["id"] = f"{self.table_name}-{len(rows) + 1}"
            rows.append(payload)
            return _FakeResponse([deepcopy(payload)])

        if self.operation == "update":
            updated = []
            for row in matches:
                row.update(deepcopy(self.payload))
                updated.append(deepcopy(row))
            return _FakeResponse(updated)

        return _FakeResponse([deepcopy(row) for row in matches])

    def _matches(self, row):
        for key, value in self.filters:
            if row.get(key) != value:
                return False
        return True


class _FakeRpc:
    def __init__(self, storage, name, params):
        self.storage = storage
        self.name = name
        self.params = params

    def execute(self):
        if self.name == "get_tiktok_connected_account_secret":
            return _FakeResponse(
                [
                    {
                        "id": "account-1",
                        "platform": "tiktok",
                        "open_id": "open-123",
                        "display_name": "Sandbox Creator",
                        "avatar_url": "",
                        "access_token_plain": "access-token",
                        "refresh_token_plain": "refresh-token",
                        "access_token_expires_at": "2099-03-17T10:00:00+00:00",
                        "refresh_token_expires_at": "2099-03-17T11:00:00+00:00",
                        "scope": "user.info.basic,video.upload,video.publish",
                        "environment": "sandbox",
                        "created_at": "2026-03-17T10:00:00+00:00",
                        "updated_at": "2026-03-17T10:00:00+00:00",
                    }
                ]
            )
        raise AssertionError(f"Unexpected RPC {self.name}")


class _FakeClient:
    def __init__(self, storage):
        self.storage = storage

    def table(self, table_name):
        return _FakeTable(self.storage, table_name)

    def rpc(self, name, params):
        return _FakeRpc(self.storage, name, params)


class _FakeSupabase:
    def __init__(self, storage):
        self.client = _FakeClient(storage)


def _settings():
    return SimpleNamespace(
        tiktok_client_key="client-key",
        tiktok_client_secret="client-secret",
        tiktok_redirect_uri="http://localhost:8000/api/auth/tiktok/callback",
        tiktok_environment="sandbox",
        tiktok_sandbox_account="@sandbox",
        token_encryption_key="encryption-secret",
        app_url="http://localhost:8000",
        privacy_policy_url="https://example.com/privacy",
        terms_url="https://example.com/terms",
    )


async def _download_stub(video_url: str):
    assert video_url == "https://cdn.example.com/video.mp4"
    return (b"video-bytes", "video/mp4")


async def _init_stub(access_token: str, video_size: int):
    assert access_token == "access-token"
    assert video_size == len(b"video-bytes")
    return {
        "publish_id": "publish-1",
        "upload_url": "https://upload.example.com",
        "chunk_size": video_size,
        "total_chunk_count": 1,
    }


async def _direct_init_stub(access_token: str, *, video_size: int, caption: str, privacy_level: str, disable_comment: bool, disable_duet: bool, disable_stitch: bool):
    assert access_token == "access-token"
    assert video_size == len(b"video-bytes")
    assert caption == "TikTok caption"
    assert privacy_level == "SELF_ONLY"
    assert disable_comment is False
    assert disable_duet is False
    assert disable_stitch is True
    return {
        "publish_id": "publish-direct-1",
        "upload_url": "https://upload.example.com/direct",
        "chunk_size": video_size,
        "total_chunk_count": 1,
    }


async def _upload_stub(upload_url: str, video_bytes: bytes, content_type: str, chunk_size: int, total_chunk_count: int):
    assert upload_url in {"https://upload.example.com", "https://upload.example.com/direct"}
    assert video_bytes == b"video-bytes"
    assert content_type == "video/mp4"
    assert chunk_size == len(b"video-bytes")
    assert total_chunk_count == 1


def test_upload_tiktok_draft_persists_job_and_post_result(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "topic_title": "Topic",
                "seed_data": {"script_review_status": "approved", "description": "Fallback caption"},
                "video_url": "https://cdn.example.com/video.mp4",
                "video_metadata": {"requested_seconds": 8},
                "publish_caption": "Local caption",
                "publish_results": {},
                "platform_ids": {},
            }
        ],
        "batches": [{"id": "batch-1", "state": "S7_PUBLISH_PLAN"}],
        "media_assets": [],
        "publish_jobs": [],
        "connected_accounts": [],
    }

    monkeypatch.setattr(tiktok, "get_settings", _settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_download_video_bytes", _download_stub)
    monkeypatch.setattr(tiktok, "_initialize_inbox_video_upload", _init_stub)
    monkeypatch.setattr(tiktok, "_poll_publish_status", lambda access_token, publish_id, *, post_mode: asyncio.sleep(0, result={"status": "SEND_TO_USER_INBOX"}))
    monkeypatch.setattr(tiktok, "_upload_video_chunks", _upload_stub)

    response = asyncio.run(
        tiktok.upload_tiktok_draft(TikTokUploadDraftRequest(post_id="post-1", caption="TikTok caption"))
    )

    job = response.data
    assert job["status"] == "awaiting_user_action"
    assert job["tiktok_publish_id"] == "publish-1"
    assert storage["media_assets"][0]["source_url"] == "https://cdn.example.com/video.mp4"
    assert storage["publish_jobs"][0]["caption"] == "TikTok caption"
    assert storage["posts"][0]["publish_results"]["tiktok"]["status"] == "awaiting_user_action"
    assert storage["posts"][0]["publish_results"]["tiktok"]["provider_status"] == "SEND_TO_USER_INBOX"


def test_publish_tiktok_direct_persists_published_post_result(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "topic_title": "Topic",
                "seed_data": {"script_review_status": "approved", "description": "Fallback caption"},
                "video_url": "https://cdn.example.com/video.mp4",
                "video_metadata": {"requested_seconds": 8},
                "publish_caption": "Local caption",
                "publish_results": {},
                "platform_ids": {},
                "social_networks": ["tiktok"],
                "publish_status": "pending",
            }
        ],
        "batches": [{"id": "batch-1", "state": "S7_PUBLISH_PLAN"}],
        "media_assets": [],
        "publish_jobs": [],
        "connected_accounts": [],
    }

    monkeypatch.setattr(tiktok, "get_settings", _settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_download_video_bytes", _download_stub)
    monkeypatch.setattr(tiktok, "_initialize_direct_post", _direct_init_stub)
    monkeypatch.setattr(
        tiktok,
        "_query_creator_info",
        lambda access_token: asyncio.sleep(
            0,
            result={
                "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"],
                "comment_disabled": False,
                "duet_disabled": False,
                "stitch_disabled": False,
                "max_video_post_duration_sec": 60,
            },
        ),
    )
    monkeypatch.setattr(
        tiktok,
        "_poll_publish_status",
        lambda access_token, publish_id, *, post_mode: asyncio.sleep(
            0,
            result={"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["tt-post-1"]},
        ),
    )
    monkeypatch.setattr(tiktok, "_upload_video_chunks", _upload_stub)

    response = asyncio.run(
        tiktok.publish_tiktok_direct(
            TikTokPublishRequest(
                post_id="post-1",
                caption="TikTok caption",
                privacy_level="SELF_ONLY",
                disable_comment=False,
                disable_duet=False,
                disable_stitch=True,
            )
        )
    )

    job = response.data
    assert job["status"] == "published"
    assert job["tiktok_publish_id"] == "publish-direct-1"
    assert storage["publish_jobs"][0]["post_mode"] == "direct"
    assert storage["posts"][0]["publish_results"]["tiktok"]["status"] == "published"
    assert storage["posts"][0]["publish_results"]["tiktok"]["provider_status"] == "PUBLISH_COMPLETE"
    assert storage["posts"][0]["platform_ids"]["tiktok"] == "tt-post-1"


def test_publish_tiktok_direct_allows_s8_complete_after_meta_publish(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "topic_title": "Topic",
                "seed_data": {"script_review_status": "approved", "description": "Fallback caption"},
                "video_url": "https://cdn.example.com/video.mp4",
                "video_metadata": {"requested_seconds": 8},
                "publish_caption": "Local caption",
                "publish_results": {
                    "facebook": {"status": "published"},
                    "instagram": {"status": "published"},
                },
                "platform_ids": {
                    "facebook": "fb-post-1",
                    "instagram": "ig-post-1",
                },
                "social_networks": ["facebook", "instagram", "tiktok"],
                "publish_status": "published",
            }
        ],
        "batches": [{"id": "batch-1", "state": "S8_COMPLETE"}],
        "media_assets": [],
        "publish_jobs": [],
        "connected_accounts": [],
    }

    monkeypatch.setattr(tiktok, "get_settings", _settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_download_video_bytes", _download_stub)
    monkeypatch.setattr(tiktok, "_initialize_direct_post", _direct_init_stub)
    monkeypatch.setattr(
        tiktok,
        "_query_creator_info",
        lambda access_token: asyncio.sleep(
            0,
            result={
                "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"],
                "comment_disabled": False,
                "duet_disabled": False,
                "stitch_disabled": False,
                "max_video_post_duration_sec": 60,
            },
        ),
    )
    monkeypatch.setattr(
        tiktok,
        "_poll_publish_status",
        lambda access_token, publish_id, *, post_mode: asyncio.sleep(
            0,
            result={"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["tt-post-1"]},
        ),
    )
    monkeypatch.setattr(tiktok, "_upload_video_chunks", _upload_stub)

    response = asyncio.run(
        tiktok.publish_tiktok_direct(
            TikTokPublishRequest(
                post_id="post-1",
                caption="TikTok caption",
                privacy_level="SELF_ONLY",
                disable_comment=False,
                disable_duet=False,
                disable_stitch=True,
            )
        )
    )

    assert response.data["status"] == "published"
    assert storage["posts"][0]["publish_results"]["tiktok"]["status"] == "published"
    assert storage["posts"][0]["platform_ids"]["tiktok"] == "tt-post-1"
