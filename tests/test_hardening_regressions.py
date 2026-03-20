from __future__ import annotations

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

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.features.qa.handlers import _active_posts_ready_for_publish


def test_sensitive_setting_defaults_do_not_ship_live_values():
    assert Settings.model_fields["supabase_key"].default == ""
    assert Settings.model_fields["supabase_service_key"].default == ""
    assert Settings.model_fields["google_ai_api_key"].default == ""
    assert Settings.model_fields["cron_secret"].default == ""


def test_http_exception_is_normalized_into_shared_error_envelope():
    client = TestClient(app)

    response = client.put("/posts/test-post-id/script", data={"script_text": ""})

    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["status"] == 422
    assert body["code"] == "validation_error"
    assert "script_text" in body["message"]


def test_removed_posts_do_not_block_qa_advancement():
    posts = [
        {"id": "removed-post", "qa_pass": False, "seed_data": {"script_review_status": "removed"}},
        {"id": "active-post", "qa_pass": True, "seed_data": {"script_review_status": "approved"}},
    ]

    assert _active_posts_ready_for_publish(posts) is True

