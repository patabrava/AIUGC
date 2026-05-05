"""Regression tests for TikTok draft upload persistence."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from app.features.publish import tiktok
from app.features.publish.schemas import TikTokPublishRequest, TikTokUploadDraftRequest
from app.core.errors import ThirdPartyError, ValidationError


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
            if self.storage.get("tiktok_secret"):
                return _FakeResponse([deepcopy(self.storage["tiktok_secret"])])
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
        if self.name == "upsert_tiktok_connected_account":
            self.storage.setdefault("rpc_calls", []).append((self.name, deepcopy(self.params)))
            existing = deepcopy(self.storage.get("tiktok_secret") or {})
            row = {
                **existing,
                "id": existing.get("id", "account-1"),
                "platform": "tiktok",
                "open_id": self.params["p_open_id"],
                "display_name": self.params["p_display_name"],
                "avatar_url": self.params["p_avatar_url"],
                "access_token_plain": self.params["p_access_token_plain"],
                "refresh_token_plain": self.params["p_refresh_token_plain"],
                "access_token_expires_at": self.params["p_access_token_expires_at"],
                "refresh_token_expires_at": self.params["p_refresh_token_expires_at"],
                "scope": self.params["p_scope"],
                "environment": self.params["p_environment"],
            }
            self.storage["tiktok_secret"] = row
            return _FakeResponse([deepcopy(row)])
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


class _ProxyResponse:
    def __init__(self, content=b"video-bytes", content_type="video/mp4", status_code=200):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    @property
    def is_error(self):
        return self.status_code >= 400


class _ProxyClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        assert url == "https://cdn.example.com/video.mp4"
        return self.response


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


def _production_settings():
    return SimpleNamespace(
        tiktok_client_key="client-key",
        tiktok_client_secret="client-secret",
        tiktok_redirect_uri="http://localhost:8000/api/auth/tiktok/callback",
        tiktok_environment="production",
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


async def _init_url_stub(access_token: str, video_url: str):
    assert access_token == "access-token"
    assert video_url == "http://localhost:8000/tiktok/drafts/post-1/video.mp4"
    return {
        "publish_id": "publish-1",
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


async def _unexpected_upload_call(*args, **kwargs):
    raise AssertionError("Draft upload should use PULL_FROM_URL without chunk upload.")


def test_tiktok_status_refresh_derives_networks_from_results_when_selection_missing():
    status = tiktok._derive_post_publish_status(
        [],
        {
            "facebook": {"status": "published"},
            "instagram": {"status": "published"},
            "tiktok": {"status": "publishing"},
        },
    )

    assert status == "publishing"


def test_tiktok_token_expiry_parser_accepts_supabase_fractional_timestamp():
    parsed = tiktok._parse_tiktok_token_expiry("2027-04-29T16:11:35.5637+00:00")

    assert parsed.isoformat() == "2027-04-29T16:11:35.563700+00:00"


def test_load_tiktok_secret_refreshes_expired_access_token(monkeypatch):
    storage = {
        "tiktok_secret": {
            "id": "account-1",
            "platform": "tiktok",
            "open_id": "open-123",
            "display_name": "Sandbox Creator",
            "avatar_url": "",
            "access_token_plain": "expired-access-token",
            "refresh_token_plain": "valid-refresh-token",
            "access_token_expires_at": "2026-04-02T16:44:03+00:00",
            "refresh_token_expires_at": "2099-04-01T16:44:03+00:00",
            "scope": "user.info.basic,video.upload,video.publish",
            "environment": "sandbox",
        },
        "rpc_calls": [],
    }

    async def _refresh_stub(method, path, *, headers=None, params=None, data=None, json_body=None):
        assert method == "POST"
        assert path == "/v2/oauth/token/"
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "valid-refresh-token"
        return {
            "access_token": "fresh-access-token",
            "refresh_token": "rotated-refresh-token",
            "expires_in": 86400,
            "refresh_expires_in": 31536000,
            "scope": "user.info.basic,video.upload,video.publish",
        }

    monkeypatch.setattr(tiktok, "get_settings", _settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_tiktok_request", _refresh_stub)

    account = asyncio.run(tiktok._load_tiktok_account_secret())

    assert account["access_token_plain"] == "fresh-access-token"
    assert account["refresh_token_plain"] == "rotated-refresh-token"
    assert storage["tiktok_secret"]["access_token_plain"] == "fresh-access-token"
    assert storage["rpc_calls"][0][0] == "upsert_tiktok_connected_account"


def test_upload_tiktok_draft_persists_job_and_post_result(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "topic_title": "Topic",
                "seed_data": {"script_review_status": "approved", "description": "Fallback caption"},
                "video_url": "https://cdn.example.com/video.mp4",
                "video_metadata": {"requested_seconds": 8, "size_bytes": len(b"video-bytes")},
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

    monkeypatch.setattr(tiktok, "get_settings", _production_settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_initialize_inbox_video_pull_from_url", _init_url_stub)
    monkeypatch.setattr(tiktok, "_poll_publish_status", lambda access_token, publish_id, *, post_mode: asyncio.sleep(0, result={"status": "SEND_TO_USER_INBOX"}))
    monkeypatch.setattr(tiktok, "_download_video_bytes", _unexpected_upload_call)
    monkeypatch.setattr(tiktok, "_upload_video_chunks", _unexpected_upload_call)

    response = asyncio.run(
        tiktok.upload_tiktok_draft(TikTokUploadDraftRequest(post_id="post-1", caption="TikTok caption"))
    )

    job = response.data
    assert job["status"] == "submitted"
    assert job["tiktok_publish_id"] == "publish-1"
    assert storage["media_assets"][0]["source_url"] == "https://cdn.example.com/video.mp4"
    assert storage["media_assets"][0]["file_size"] == len(b"video-bytes")
    assert storage["publish_jobs"][0]["caption"] == "TikTok caption"
    assert storage["posts"][0]["publish_results"]["tiktok"]["status"] == "awaiting_user_action"
    assert storage["posts"][0]["publish_results"]["tiktok"]["provider_status"] == "SEND_TO_USER_INBOX"


def test_tiktok_draft_proxy_route_serves_video_from_public_domain(monkeypatch):
    post_id = "123e4567-e89b-12d3-a456-426614174000"
    storage = {
        "posts": [
            {
                "id": post_id,
                "batch_id": "batch-1",
                "topic_title": "Topic",
                "seed_data": {"script_review_status": "approved"},
                "video_url": "https://cdn.example.com/video.mp4",
                "video_metadata": {"requested_seconds": 8},
                "publish_caption": "Local caption",
                "publish_results": {},
                "platform_ids": {},
            }
        ],
        "batches": [{"id": "batch-1", "state": "S8_COMPLETE"}],
        "media_assets": [],
        "publish_jobs": [],
        "connected_accounts": [],
    }

    monkeypatch.setattr(tiktok, "get_settings", _production_settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok.httpx, "AsyncClient", lambda *args, **kwargs: _ProxyClient(_ProxyResponse()))

    response = asyncio.run(tiktok.serve_tiktok_draft_video(f"post-{post_id}"))

    assert response.media_type == "video/mp4"


def test_upload_tiktok_draft_allows_s8_complete_after_meta_publish(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "topic_title": "Topic",
                "seed_data": {"script_review_status": "approved", "description": "Fallback caption"},
                "video_url": "https://cdn.example.com/video.mp4",
                "video_metadata": {"requested_seconds": 8, "size_bytes": len(b"video-bytes")},
                "publish_caption": "Local caption",
                "publish_results": {
                    "facebook": {"status": "published"},
                    "instagram": {"status": "published"},
                },
                "platform_ids": {
                    "facebook": "fb-post-1",
                    "instagram": "ig-post-1",
                },
            }
        ],
        "batches": [{"id": "batch-1", "state": "S8_COMPLETE"}],
        "media_assets": [],
        "publish_jobs": [],
        "connected_accounts": [],
    }

    monkeypatch.setattr(tiktok, "get_settings", _production_settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_initialize_inbox_video_pull_from_url", _init_url_stub)
    monkeypatch.setattr(tiktok, "_poll_publish_status", lambda access_token, publish_id, *, post_mode: asyncio.sleep(0, result={"status": "SEND_TO_USER_INBOX"}))
    monkeypatch.setattr(tiktok, "_download_video_bytes", _unexpected_upload_call)
    monkeypatch.setattr(tiktok, "_upload_video_chunks", _unexpected_upload_call)

    response = asyncio.run(
        tiktok.upload_tiktok_draft(TikTokUploadDraftRequest(post_id="post-1", caption="TikTok caption"))
    )

    assert response.data["status"] == "submitted"
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

    monkeypatch.setattr(tiktok, "get_settings", _production_settings)
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

    monkeypatch.setattr(tiktok, "get_settings", _production_settings)
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


def test_publish_tiktok_direct_surfaces_private_account_restriction(monkeypatch):
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
        "batches": [{"id": "batch-1", "state": "S8_COMPLETE"}],
        "media_assets": [],
        "publish_jobs": [],
        "connected_accounts": [],
    }

    async def _restricted_init(*args, **kwargs):
        raise ThirdPartyError(
            "Please review our integration guidelines at https://developers.tiktok.com/doc/content-sharing-guidelines/",
            details={
                "status_code": 403,
                "error": {
                    "code": "unaudited_client_can_only_post_to_private_accounts",
                    "message": "Please review our integration guidelines at https://developers.tiktok.com/doc/content-sharing-guidelines/",
                    "log_id": "log-1",
                },
                "url": "https://open.tiktokapis.com/v2/post/publish/video/init/",
                "log_id": None,
            },
        )

    monkeypatch.setattr(tiktok, "get_settings", _production_settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_download_video_bytes", _download_stub)
    monkeypatch.setattr(tiktok, "_initialize_direct_post", _restricted_init)
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

    try:
        asyncio.run(
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
        raise AssertionError("Expected ValidationError")
    except ValidationError as exc:
        assert "private account" in str(exc) or "blocked" in str(exc)
        assert storage["publish_jobs"][0]["status"] == "failed"
        assert storage["posts"][0]["publish_results"]["tiktok"]["status"] == "failed"


def test_publish_tiktok_direct_blocks_sandbox_environment(monkeypatch):
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
        "batches": [{"id": "batch-1", "state": "S8_COMPLETE"}],
        "media_assets": [],
        "publish_jobs": [],
        "connected_accounts": [],
    }

    monkeypatch.setattr(tiktok, "get_settings", _settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_download_video_bytes", _download_stub)

    try:
        asyncio.run(
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
        raise AssertionError("Expected ValidationError")
    except ValidationError as exc:
        assert "draft-only" in str(exc)
        assert storage["publish_jobs"] == []
