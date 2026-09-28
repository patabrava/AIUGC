"""Deterministic, non-production defaults for app settings during tests."""

import os

import pytest


_TEST_SETTINGS = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_KEY": "test-key",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "CLOUDFLARE_R2_ACCOUNT_ID": "test-account",
    "CLOUDFLARE_R2_ACCESS_KEY_ID": "test-access",
    "CLOUDFLARE_R2_SECRET_ACCESS_KEY": "test-secret",
    "CLOUDFLARE_R2_BUCKET_NAME": "test-bucket",
    "CLOUDFLARE_R2_PUBLIC_BASE_URL": "https://example.r2.dev",
    "CRON_SECRET": "test-cron-secret",
}


def pytest_configure() -> None:
    for key, value in _TEST_SETTINGS.items():
        os.environ.setdefault(key, value)


@pytest.fixture(autouse=True)
def reset_cached_settings(monkeypatch):
    """Build each Settings snapshot from that test's own environment."""
    import app.core.config as config_module

    monkeypatch.setattr(config_module, "_settings", None)
