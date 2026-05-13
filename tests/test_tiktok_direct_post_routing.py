"""Tests for TikTok direct-post routing and fail-closed defaults."""

import pytest

from app.features.publish import tiktok
from app.core.errors import ValidationError


def test_default_privacy_level_constant_removed():
    assert not hasattr(tiktok, "DEFAULT_PRIVACY_LEVEL"), (
        "DEFAULT_PRIVACY_LEVEL must not exist — privacy must be user-selected."
    )


def test_build_post_info_requires_title(monkeypatch):
    with pytest.raises(ValidationError):
        tiktok._build_tiktok_post_info(
            title="",
            privacy_level="PUBLIC_TO_EVERYONE",
            disable_comment=True,
            disable_duet=True,
            disable_stitch=True,
            brand_content_toggle=False,
            brand_organic_toggle=False,
        )


def test_build_post_info_passes_brand_toggles():
    info = tiktok._build_tiktok_post_info(
        title="Hello world",
        privacy_level="PUBLIC_TO_EVERYONE",
        disable_comment=True,
        disable_duet=True,
        disable_stitch=True,
        brand_content_toggle=True,
        brand_organic_toggle=False,
    )
    assert info["title"] == "Hello world"
    assert info["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert info["disable_comment"] is True
    assert info["brand_content_toggle"] is True
    assert info["brand_organic_toggle"] is False


from fastapi.testclient import TestClient


def test_save_post_tiktok_settings_round_trip(monkeypatch):
    from app.main import app
    from app.features.publish import handlers

    captured = {}

    def fake_update(post_id, payload):
        captured["payload"] = payload
        captured["post_id"] = post_id
        return {"id": post_id, "tiktok_settings": payload["tiktok_settings"]}

    monkeypatch.setattr(handlers, "_update_post_tiktok_settings_row", fake_update)
    client = TestClient(app)
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
    assert captured["payload"]["tiktok_settings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert captured["payload"]["tiktok_settings"]["your_brand"] is True
