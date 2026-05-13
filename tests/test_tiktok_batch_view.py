"""Tests for the batch detail TikTok view payload."""

from app.features.batches.handlers import _build_batch_detail_view, _build_publish_post_view


def test_publish_post_view_includes_caption_video_and_tiktok_settings():
    post = {
        "id": "post-1",
        "post_type": "video",
        "topic_title": "Hello",
        "seed_data": {},
        "video_url": "https://example.com/video.mp4",
        "video_metadata": {"caption_video_url": "https://example.com/caption.mp4", "duration_seconds": 12},
        "publish_results": {},
        "platform_ids": {},
        "tiktok_settings": {
            "title": "Hello TikTok",
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

    assert view["captionVideoUrl"] == "https://example.com/caption.mp4"
    assert view["videoMetadata"]["duration_seconds"] == 12
    assert view["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"


def test_batch_detail_view_propagates_tiktok_defaults():
    batch_detail = {
        "state": "S7_PUBLISH_PLAN",
        "posts": [],
        "tiktok_defaults": {
            "title_template": "Batch title",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": False,
            "allow_stitch": False,
            "commercial_disclosure": False,
            "your_brand": False,
            "branded_content": False,
        },
        "meta_connection": {},
        "tiktok_connection": {"avatar_url": "https://example.com/avatar.png", "creator_info": {}},
    }

    view = _build_batch_detail_view(batch_detail)

    assert view["tiktok_defaults"]["title_template"] == "Batch title"
    assert view["tiktok_publish_state"]["avatar_url"] == "https://example.com/avatar.png"
