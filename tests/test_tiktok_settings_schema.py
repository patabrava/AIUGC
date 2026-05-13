"""Tests for TikTok Content Posting API request schemas."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.features.publish.schemas import (
    TikTokPostSettings,
    TikTokBatchDefaults,
    TikTokPublishRequest,
)


def test_settings_requires_privacy_level():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(title="hi")


def test_settings_accepts_valid_privacy_level():
    settings = TikTokPostSettings(
        title="hello",
        privacy_level="PUBLIC_TO_EVERYONE",
        allow_comment=True,
        allow_duet=False,
        allow_stitch=False,
    )
    assert settings.privacy_level == "PUBLIC_TO_EVERYONE"


def test_settings_rejects_unknown_privacy_level():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(title="hi", privacy_level="UNKNOWN_LEVEL")


def test_settings_rejects_branded_with_private():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(
            title="hi",
            privacy_level="SELF_ONLY",
            commercial_disclosure=True,
            branded_content=True,
        )


def test_settings_rejects_disclosure_without_subtype():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(
            title="hi",
            privacy_level="PUBLIC_TO_EVERYONE",
            commercial_disclosure=True,
            your_brand=False,
            branded_content=False,
        )


def test_settings_title_required_nonblank():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(title="   ", privacy_level="PUBLIC_TO_EVERYONE")


def test_publish_request_requires_settings_fields():
    with pytest.raises(PydanticValidationError):
        TikTokPublishRequest(post_id="abc")


def test_publish_request_round_trips():
    request = TikTokPublishRequest(
        post_id="post-1",
        title="Title",
        privacy_level="PUBLIC_TO_EVERYONE",
        allow_comment=True,
        allow_duet=False,
        allow_stitch=False,
        commercial_disclosure=True,
        your_brand=True,
        branded_content=False,
    )
    assert request.brand_organic_toggle is True
    assert request.brand_content_toggle is False


def test_batch_defaults_allows_unset_privacy():
    defaults = TikTokBatchDefaults()
    assert defaults.privacy_level is None
