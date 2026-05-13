"""Tests for the TikTok settings persistence endpoints."""

from fastapi.testclient import TestClient


def test_save_post_tiktok_settings_round_trip(monkeypatch):
    import app.core.config as config_module
    from app.features.publish import handlers
    from app.main import app

    captured = {}

    def fake_update(post_id, payload):
        captured["post_id"] = post_id
        captured["payload"] = payload
        return {"id": post_id, "tiktok_settings": payload["tiktok_settings"]}

    monkeypatch.setattr(handlers, "_update_post_tiktok_settings_row", fake_update)
    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BYPASS_AUTH_IN_DEVELOPMENT", "true")

    client = TestClient(app, base_url="http://localhost")
    response = client.put(
        "/publish/posts/post-1/tiktok-settings",
        json={
            "title": "Hello",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": False,
            "allow_stitch": False,
            "commercial_disclosure": True,
            "your_brand": True,
            "branded_content": False,
        },
    )

    assert response.status_code == 200, response.text
    assert captured["post_id"] == "post-1"
    assert captured["payload"]["tiktok_settings"]["title"] == "Hello"


def test_save_batch_tiktok_defaults_round_trip(monkeypatch):
    import app.core.config as config_module
    from app.features.publish import handlers
    from app.main import app

    captured = {}

    def fake_update(batch_id, payload):
        captured["batch_id"] = batch_id
        captured["payload"] = payload
        return {"id": batch_id, "tiktok_defaults": payload["tiktok_defaults"]}

    monkeypatch.setattr(handlers, "_update_batch_tiktok_defaults_row", fake_update)
    monkeypatch.setattr(config_module, "_settings", None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BYPASS_AUTH_IN_DEVELOPMENT", "true")

    client = TestClient(app, base_url="http://localhost")
    response = client.put(
        "/publish/batches/batch-1/tiktok-defaults",
        json={
            "title_template": "Template",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": True,
            "allow_stitch": True,
            "commercial_disclosure": False,
            "your_brand": False,
            "branded_content": False,
        },
    )

    assert response.status_code == 200, response.text
    assert captured["batch_id"] == "batch-1"
    assert captured["payload"]["tiktok_defaults"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
