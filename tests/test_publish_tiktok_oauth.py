"""Regression tests for the TikTok sandbox OAuth slice."""

from types import SimpleNamespace

from app.features.publish import tiktok


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeRpc:
    def __init__(self, storage, name, params):
        self.storage = storage
        self.name = name
        self.params = params

    def execute(self):
        if self.name == "upsert_tiktok_connected_account":
            row = {
                "id": "account-1",
                "user_id": None,
                "platform": "tiktok",
                "open_id": self.params["p_open_id"],
                "display_name": self.params["p_display_name"],
                "avatar_url": self.params["p_avatar_url"],
                "access_token": "ciphertext",
                "refresh_token": "ciphertext",
                "access_token_expires_at": self.params["p_access_token_expires_at"],
                "refresh_token_expires_at": self.params["p_refresh_token_expires_at"],
                "scope": self.params["p_scope"],
                "environment": self.params["p_environment"],
                "created_at": "2026-03-17T10:00:00+00:00",
                "updated_at": "2026-03-17T10:00:00+00:00",
            }
            self.storage["rpc_calls"].append((self.name, self.params))
            self.storage["connected_accounts"] = [row]
            if self.storage.get("rpc_returns_single_row"):
                return _FakeResponse(row)
            return _FakeResponse([row])
        raise AssertionError(f"Unexpected RPC {self.name}")


class _FakeTable:
    def __init__(self, storage, table_name):
        self.storage = storage
        self.table_name = table_name
        self.filters = []
        self.order_key = None
        self.order_desc = False
        self.limit_value = None

    def select(self, _fields):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, key, desc=False):
        self.order_key = key
        self.order_desc = desc
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        rows = [
            dict(row)
            for row in self.storage.get(self.table_name, [])
            if all(row.get(key) == value for key, value in self.filters)
        ]
        if self.order_key:
            rows = sorted(rows, key=lambda row: row.get(self.order_key) or "", reverse=self.order_desc)
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return _FakeResponse(rows)


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


class _ErrorResponse:
    def __init__(self, payload, status_code=400):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)
        self.is_error = True

    def json(self):
        return self._payload


class _ErrorClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def request(self, *args, **kwargs):
        return _ErrorResponse(
            {
                "error": "invalid_grant",
                "error_description": "redirect_uri mismatch",
                "log_id": "log-123",
            },
            status_code=400,
        )


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


def _production_settings_without_sandbox_account():
    return SimpleNamespace(
        tiktok_client_key="client-key",
        tiktok_client_secret="client-secret",
        tiktok_redirect_uri="http://localhost:8000/api/auth/tiktok/callback",
        tiktok_environment="production",
        tiktok_sandbox_account="",
        token_encryption_key="encryption-secret",
        app_url="http://localhost:8000",
        privacy_policy_url="https://example.com/privacy",
        terms_url="https://example.com/terms",
    )


async def _exchange_stub(code: str, code_verifier: str):
    assert code == "auth-code"
    assert code_verifier
    return {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_in": 3600,
        "refresh_expires_in": 7200,
        "scope": "user.info.basic,video.upload,video.publish",
        "open_id": "open-123",
    }


async def _profile_stub(access_token: str):
    assert access_token == "access-token"
    return {
        "open_id": "open-123",
        "display_name": "Sandbox Creator",
        "avatar_url": "https://example.com/avatar.jpg",
    }


def test_start_tiktok_oauth_builds_signed_pkce_redirect(monkeypatch):
    monkeypatch.setattr(tiktok, "get_settings", _settings)

    response = tiktok.start_tiktok_oauth.__wrapped__(batch_id="batch-1") if hasattr(tiktok.start_tiktok_oauth, "__wrapped__") else None
    if response is None:
        import asyncio

        response = asyncio.run(tiktok.start_tiktok_oauth(batch_id="batch-1"))

    location = response.headers["location"]
    assert location.startswith(tiktok.TIKTOK_AUTH_URL)
    assert "client_key=client-key" in location
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fapi%2Fauth%2Ftiktok%2Fcallback" in location
    assert "scope=user.info.basic%2Cvideo.upload%2Cvideo.publish" in location
    state = location.split("state=")[1].split("&")[0]
    payload = tiktok.decode_signed_state(state, _settings().token_encryption_key)
    assert payload["batch_id"] == "batch-1"
    assert payload["code_verifier"]


def test_tiktok_production_config_does_not_require_sandbox_handle(monkeypatch):
    monkeypatch.setattr(tiktok, "get_settings", _production_settings_without_sandbox_account)

    settings = tiktok._require_tiktok_settings()

    assert settings.tiktok_environment == "production"


def test_tiktok_callback_persists_connected_account(monkeypatch):
    import asyncio

    storage = {"rpc_calls": [], "connected_accounts": []}
    monkeypatch.setattr(tiktok, "get_settings", _settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_exchange_code_for_tokens", _exchange_stub)
    monkeypatch.setattr(tiktok, "_fetch_user_profile", _profile_stub)

    state = tiktok.build_signed_state(
        _settings().token_encryption_key,
        batch_id="batch-1",
        code_verifier="code-verifier",
    )

    response = asyncio.run(tiktok.tiktok_oauth_callback(code="auth-code", state=state))

    assert response.headers["location"] == "/batches/batch-1"
    assert storage["rpc_calls"]
    _, params = storage["rpc_calls"][0]
    assert params["p_open_id"] == "open-123"
    assert params["p_display_name"] == "Sandbox Creator"
    assert params["p_access_token_plain"] == "access-token"


def test_tiktok_callback_accepts_single_row_rpc_payload(monkeypatch):
    import asyncio

    storage = {"rpc_calls": [], "connected_accounts": [], "rpc_returns_single_row": True}
    monkeypatch.setattr(tiktok, "get_settings", _settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_exchange_code_for_tokens", _exchange_stub)
    monkeypatch.setattr(tiktok, "_fetch_user_profile", _profile_stub)

    state = tiktok.build_signed_state(
        _settings().token_encryption_key,
        batch_id="batch-1",
        code_verifier="code-verifier",
    )

    response = asyncio.run(tiktok.tiktok_oauth_callback(code="auth-code", state=state))

    assert response.headers["location"] == "/batches/batch-1"
    assert storage["connected_accounts"][0]["open_id"] == "open-123"


def test_tiktok_public_account_uses_latest_reconnect_row(monkeypatch):
    storage = {
        "connected_accounts": [
            {
                "id": "expired-account",
                "platform": "tiktok",
                "open_id": "old-open",
                "display_name": "Expired Login",
                "environment": "sandbox",
                "scope": "user.info.basic,video.upload,video.publish",
                "access_token_expires_at": "2026-04-02T16:44:03+00:00",
                "refresh_token_expires_at": "2027-04-01T16:44:03+00:00",
                "updated_at": "2026-04-01T16:44:03+00:00",
            },
            {
                "id": "fresh-account",
                "platform": "tiktok",
                "open_id": "fresh-open",
                "display_name": "Fresh Login",
                "environment": "sandbox",
                "scope": "user.info.basic,video.upload,video.publish",
                "access_token_expires_at": "2099-05-06T11:45:02.37894+00:00",
                "refresh_token_expires_at": "2099-05-05T11:45:02.378959+00:00",
                "updated_at": "2026-05-05T11:45:02+00:00",
            },
        ]
    }
    monkeypatch.setattr(tiktok, "get_settings", _settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))

    account = tiktok.get_tiktok_public_account()

    assert account["id"] == "fresh-account"
    assert account["status"] == "connected"


def test_tiktok_publish_state_refreshes_expired_public_access_token(monkeypatch):
    import asyncio

    storage = {
        "connected_accounts": [
            {
                "id": "account-1",
                "platform": "tiktok",
                "open_id": "open-123",
                "display_name": "Sandbox Creator",
                "environment": "sandbox",
                "scope": "user.info.basic,video.upload,video.publish",
                "access_token_expires_at": "2026-04-02T16:44:03+00:00",
                "refresh_token_expires_at": "2099-04-01T16:44:03+00:00",
                "updated_at": "2026-04-01T16:44:03+00:00",
            }
        ]
    }

    async def _load_refreshed_secret():
        return {
            "id": "account-1",
            "platform": "tiktok",
            "open_id": "open-123",
            "display_name": "Sandbox Creator",
            "environment": "sandbox",
            "scope": "user.info.basic,video.upload,video.publish",
            "access_token_plain": "fresh-access-token",
            "refresh_token_plain": "refresh-token",
            "access_token_expires_at": "2099-04-02T16:44:03+00:00",
            "refresh_token_expires_at": "2099-04-01T16:44:03+00:00",
            "updated_at": "2026-04-01T16:44:03+00:00",
        }

    async def _creator_info(access_token):
        assert access_token == "fresh-access-token"
        return {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 60}

    monkeypatch.setattr(tiktok, "get_settings", _settings)
    monkeypatch.setattr(tiktok, "get_supabase", lambda: _FakeSupabase(storage))
    monkeypatch.setattr(tiktok, "_load_tiktok_account_secret", _load_refreshed_secret)
    monkeypatch.setattr(tiktok, "_query_creator_info", _creator_info)

    state = asyncio.run(tiktok.get_tiktok_publish_state())

    assert state["status"] == "connected"
    assert state["draft_ready"] is True
    assert "access_token_plain" not in state
    assert "refresh_token_plain" not in state


def test_tiktok_readiness_marks_sandbox_as_draft_only():
    readiness = tiktok._derive_tiktok_readiness(
        {
            "status": "connected",
            "environment": "sandbox",
            "scope": "user.info.basic,video.upload,video.publish",
        },
        {
            "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"],
            "max_video_post_duration_sec": 60,
        },
    )

    assert readiness["publish_ready"] is False
    assert readiness["draft_ready"] is True
    assert readiness["readiness_status"] == "draft_ready"
    assert "draft upload" in readiness["readiness_reason"]


def test_tiktok_request_handles_oauth_error_payload(monkeypatch):
    import asyncio

    monkeypatch.setattr(tiktok.httpx, "AsyncClient", lambda *args, **kwargs: _ErrorClient())

    async def run():
        try:
            await tiktok._tiktok_request("POST", "/v2/oauth/token/", data={})
        except Exception as exc:
            return exc
        raise AssertionError("Expected TikTok request to raise")

    exc = asyncio.run(run())
    assert exc.code.value == "third_party_fail"
    assert "redirect_uri mismatch" in exc.message
    assert exc.details["log_id"] == "log-123"
