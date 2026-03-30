"""Tests for auth schemas, config fields, and email allowlist logic."""

import os

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
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
    schema = OTPVerifySchema(email="user@lippelift.de", token="123456")
    assert schema.token == "123456"


def test_otp_verify_token_too_short():
    import pytest
    with pytest.raises(Exception):
        OTPVerifySchema(email="user@lippelift.de", token="12")


def test_otp_verify_token_too_long():
    import pytest
    with pytest.raises(Exception):
        OTPVerifySchema(email="user@lippelift.de", token="1234567")


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
