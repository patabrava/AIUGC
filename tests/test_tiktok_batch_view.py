"""Tests that the batch view exposes TikTok defaults and per-post settings."""

from app.features.batches.handlers import _build_publish_post_view


def test_publish_post_view_includes_tiktok_settings():
    post = {
        "id": "p-1",
        "post_type": "video",
        "topic_title": "Hello",
        "seed_data": {},
        "publish_results": {},
        "platform_ids": {},
        "tiktok_settings": {
            "title": "Hi",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": False,
            "allow_stitch": False,
            "commercial_disclosure": False,
            "your_brand": False,
            "branded_content": False,
        },
    }
    view = _build_publish_post_view(post)
    assert view["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert view["tiktokSettings"]["allow_comment"] is True


def test_publish_post_view_defaults_empty_tiktok_settings():
    post = {"id": "p-2", "post_type": "video", "topic_title": "Hi", "seed_data": {}}
    view = _build_publish_post_view(post)
    assert view["tiktokSettings"] == {}
