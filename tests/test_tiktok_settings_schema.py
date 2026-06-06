"""Tests for TikTok settings schemas used by the batch UI."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.features.publish.schemas import TikTokBatchDefaults, TikTokPostSettings


def test_post_settings_accepts_required_fields():
    settings = TikTokPostSettings(
        title="Title",
        privacy_level="PUBLIC_TO_EVERYONE",
        allow_comment=True,
        allow_duet=False,
        allow_stitch=False,
        commercial_disclosure=True,
        your_brand=True,
        branded_content=False,
        consent_acknowledged=True,
    )
    assert settings.title == "Title"


def test_post_settings_rejects_invalid_privacy_level():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(title="Title", privacy_level="UNKNOWN")


def test_post_settings_rejects_disclosure_without_subtype():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(
            title="Title",
            privacy_level="PUBLIC_TO_EVERYONE",
            commercial_disclosure=True,
        )


def test_post_settings_rejects_branded_content_with_private_visibility():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(
            title="Title",
            privacy_level="SELF_ONLY",
            commercial_disclosure=True,
            branded_content=True,
            consent_acknowledged=True,
        )


def test_post_settings_persist_consent_acknowledged():
    settings = TikTokPostSettings(
        title="Creator title",
        privacy_level="PUBLIC_TO_EVERYONE",
        consent_acknowledged=True,
    )

    payload = settings.model_dump()

    assert payload["consent_acknowledged"] is True


def test_post_settings_require_consent_for_direct_post_settings():
    with pytest.raises(ValueError) as excinfo:
        TikTokPostSettings(
            title="Creator title",
            privacy_level="PUBLIC_TO_EVERYONE",
            consent_acknowledged=False,
        )

    assert "consent_acknowledged" in str(excinfo.value)


def test_batch_defaults_allow_unset_privacy_level():
    defaults = TikTokBatchDefaults(title_template="Template")
    assert defaults.privacy_level is None
