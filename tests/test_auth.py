"""Tests for auth schemas, config fields, and email allowlist logic."""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GEMINI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from app.features.auth.schemas import OTPRequestSchema, OTPVerifySchema


def test_otp_request_valid():
    schema = OTPRequestSchema(email="user@lippelift.de")
    assert schema.email == "user@lippelift.de"


def test_otp_request_invalid_email():
    import pytest
    with pytest.raises(Exception):
        OTPRequestSchema(email="not-an-email")


def test_otp_verify_valid():
    schema = OTPVerifySchema(email="user@lippelift.de", token="12345678")
    assert schema.token == "12345678"


def test_otp_verify_token_too_short():
    import pytest
    with pytest.raises(Exception):
        OTPVerifySchema(email="user@lippelift.de", token="12")


def test_otp_verify_token_too_long():
    import pytest
    with pytest.raises(Exception):
        OTPVerifySchema(email="user@lippelift.de", token="123456789")


def test_is_email_allowed_domain():
    from app.features.auth.queries import is_email_allowed
    assert is_email_allowed("anyone@lippelift.de") is True


def test_is_email_allowed_explicit():
    from app.features.auth.queries import is_email_allowed
    assert is_email_allowed("caposk817@gmail.com") is True


def test_is_email_allowed_case_insensitive():
    from app.features.auth.queries import is_email_allowed
    assert is_email_allowed("User@LippeLift.DE") is True


def test_is_email_allowed_rejected():
    from app.features.auth.queries import is_email_allowed
    assert is_email_allowed("hacker@evil.com") is False


def test_is_email_allowed_reviewer_email(monkeypatch):
    import app.core.config as config_module
    from app.features.auth.queries import is_email_allowed

    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("REVIEWER_LOGIN_EMAIL", "tiktok-review@lippelift.xyz")
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)

    assert is_email_allowed("tiktok-review@lippelift.xyz") is True


def test_session_cookie_roundtrip():
    from app.features.auth.middleware import encode_session_cookie, decode_session_cookie
    data = {"access_token": "abc123", "refresh_token": "def456"}
    secret = "test-secret-key-for-signing"
    encoded = encode_session_cookie(data, secret)
    decoded = decode_session_cookie(encoded, secret)
    assert decoded["access_token"] == "abc123"
    assert decoded["refresh_token"] == "def456"


def test_session_cookie_tampered():
    from app.features.auth.middleware import encode_session_cookie, decode_session_cookie
    data = {"access_token": "abc123", "refresh_token": "def456"}
    secret = "test-secret-key-for-signing"
    encoded = encode_session_cookie(data, secret)
    tampered = encoded[:-5] + "xxxxx"
    result = decode_session_cookie(tampered, secret)
    assert result is None


def test_session_cookie_wrong_secret():
    from app.features.auth.middleware import encode_session_cookie, decode_session_cookie
    data = {"access_token": "abc123", "refresh_token": "def456"}
    encoded = encode_session_cookie(data, "secret-one")
    result = decode_session_cookie(encoded, "secret-two")
    assert result is None


def test_local_request_detection_marks_localhost_as_bypassed():
    from starlette.requests import Request
    from app.features.auth.middleware import _is_local_request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/login",
        "raw_path": b"/auth/login",
        "query_string": b"",
        "headers": [(b"host", b"localhost:8000")],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 8000),
        "scheme": "http",
        "http_version": "1.1",
    }
    request = Request(scope)
    assert _is_local_request(request) is True


def test_local_request_detection_uses_host_header():
    from starlette.requests import Request
    from app.features.auth.middleware import _is_local_request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/login",
        "raw_path": b"/auth/login",
        "query_string": b"",
        "headers": [(b"host", b"localhost:8000")],
        "client": ("10.0.0.2", 12345),
        "server": ("10.0.0.2", 8000),
        "scheme": "http",
        "http_version": "1.1",
    }
    request = Request(scope)
    assert _is_local_request(request) is True


def test_local_request_detection_rejects_proxy_loopback_for_production_host():
    from starlette.requests import Request
    from app.features.auth.middleware import _is_local_request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/auth/login",
        "raw_path": b"/auth/login",
        "query_string": b"",
        "headers": [(b"host", b"aiugc-prod.srv1498567.hstgr.cloud")],
        "client": ("127.0.0.1", 12345),
        "server": ("aiugc-prod.srv1498567.hstgr.cloud", 80),
        "scheme": "http",
        "http_version": "1.1",
    }
    request = Request(scope)
    assert _is_local_request(request) is False


import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_send_otp_success():
    from app.features.auth.queries import send_otp

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}

    with patch("app.features.auth.queries.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        result = await send_otp("user@lippelift.de")
        assert result is True


@pytest.mark.asyncio
async def test_send_otp_rate_limited():
    from app.features.auth.queries import send_otp

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.json.return_value = {"msg": "Rate limit exceeded"}

    with patch("app.features.auth.queries.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        with pytest.raises(Exception) as exc_info:
            await send_otp("user@lippelift.de")
        assert "Rate limit" in str(exc_info.value) or exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_verify_otp_success():
    from app.features.auth.queries import verify_otp

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "jwt-token",
        "refresh_token": "refresh-token",
        "user": {"email": "user@lippelift.de"},
    }

    with patch("app.features.auth.queries.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        result = await verify_otp("user@lippelift.de", "12345678")
        assert result["access_token"] == "jwt-token"
        assert result["refresh_token"] == "refresh-token"


@pytest.mark.asyncio
async def test_verify_otp_invalid_code():
    from app.features.auth.queries import verify_otp

    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.json.return_value = {"msg": "Token has expired or is invalid"}

    with patch("app.features.auth.queries.httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        result = await verify_otp("user@lippelift.de", "00000000")
        assert result is None


@pytest.mark.asyncio
async def test_local_auth_bypass_skips_supabase_calls(monkeypatch):
    import app.core.config as config_module
    from app.features.auth.queries import send_otp, verify_otp, get_user_from_token

    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BYPASS_AUTH_IN_DEVELOPMENT", "true")

    assert await send_otp("user@lippelift.de") is True
    session = await verify_otp("user@lippelift.de", "12345678")
    assert session["user"]["email"] == "user@lippelift.de"
    assert await get_user_from_token(session["access_token"]) == {"email": "user@lippelift.de"}


@pytest.mark.asyncio
async def test_production_proxy_loopback_still_requires_login(monkeypatch):
    import app.core.config as config_module
    from starlette.requests import Request
    from app.features.auth.middleware import require_auth

    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_URL", "https://aiugc-prod.srv1498567.hstgr.cloud")
    monkeypatch.delenv("BYPASS_AUTH_IN_DEVELOPMENT", raising=False)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/batches",
        "raw_path": b"/batches",
        "query_string": b"",
        "headers": [(b"host", b"aiugc-prod.srv1498567.hstgr.cloud")],
        "client": ("127.0.0.1", 12345),
        "server": ("aiugc-prod.srv1498567.hstgr.cloud", 443),
        "scheme": "https",
        "http_version": "1.1",
    }
    request = Request(scope)

    response = await require_auth(request)

    assert response is not None
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_reviewer_login_sets_session_cookie(monkeypatch):
    import app.core.config as config_module
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_URL", "https://lippelift.xyz")
    monkeypatch.setenv("REVIEWER_LOGIN_EMAIL", "tiktok-review@lippelift.xyz")
    monkeypatch.setenv("REVIEWER_LOGIN_TOKEN", "review-secret-token")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-encryption-key")

    client = TestClient(app, base_url="https://lippelift.xyz")
    response = client.get("/auth/review?token=review-secret-token", allow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/batches"
    assert "ff_session=" in response.headers.get("set-cookie", "")


def test_reviewer_email_direct_login_from_send_otp(monkeypatch):
    import app.core.config as config_module
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_URL", "https://lippelift.xyz")
    monkeypatch.setenv("REVIEWER_LOGIN_EMAIL", "tiktok-review@lippelift.xyz")
    monkeypatch.setenv("REVIEWER_LOGIN_TOKEN", "review-secret-token")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "test-token-encryption-key")

    client = TestClient(app, base_url="https://lippelift.xyz")
    response = client.post(
        "/auth/send-otp",
        data={"email": "tiktok-review@lippelift.xyz"},
        allow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/batches"
    assert "ff_session=" in response.headers.get("set-cookie", "")


def test_reviewer_login_rejects_invalid_token(monkeypatch):
    import app.core.config as config_module
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_URL", "https://lippelift.xyz")
    monkeypatch.setenv("REVIEWER_LOGIN_EMAIL", "tiktok-review@lippelift.xyz")
    monkeypatch.setenv("REVIEWER_LOGIN_TOKEN", "review-secret-token")

    client = TestClient(app, base_url="https://lippelift.xyz")
    response = client.get("/auth/review?token=wrong-token")

    assert response.status_code == 403
    assert "Invalid reviewer access link." in response.text


def test_reviewer_login_requires_configuration(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_URL", "https://lippelift.xyz")
    monkeypatch.setenv("REVIEWER_LOGIN_EMAIL", "")
    monkeypatch.setenv("REVIEWER_LOGIN_TOKEN", "")

    client = TestClient(app, base_url="https://lippelift.xyz")
    response = client.get("/auth/review?token=anything")

    assert response.status_code == 503
    assert "Reviewer login is not configured yet." in response.text
