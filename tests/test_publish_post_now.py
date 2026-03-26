"""Tests for POST /publish/posts/{post_id}/now — immediate publish."""

import pytest
from app.features.publish.schemas import PostNowRequest, SocialNetwork


class TestPostNowRequestSchema:
    def test_valid_request_all_networks(self):
        req = PostNowRequest(
            post_id="post-1",
            publish_caption="Test caption",
            social_networks=[SocialNetwork.FACEBOOK, SocialNetwork.INSTAGRAM, SocialNetwork.TIKTOK],
        )
        assert req.post_id == "post-1"
        assert len(req.social_networks) == 3

    def test_valid_request_single_network(self):
        req = PostNowRequest(
            post_id="post-1",
            publish_caption="Test caption",
            social_networks=[SocialNetwork.TIKTOK],
        )
        assert req.social_networks == [SocialNetwork.TIKTOK]

    def test_rejects_empty_networks(self):
        with pytest.raises(Exception):
            PostNowRequest(
                post_id="post-1",
                publish_caption="Test caption",
                social_networks=[],
            )

    def test_rejects_duplicate_networks(self):
        with pytest.raises(Exception):
            PostNowRequest(
                post_id="post-1",
                publish_caption="Test caption",
                social_networks=[SocialNetwork.FACEBOOK, SocialNetwork.FACEBOOK],
            )
