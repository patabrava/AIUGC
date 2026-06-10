"""Unit tests for segmented-route duration profiles and selection.

Hermetic: stubs ``video_profiles.get_settings`` so the tests do not require a populated ``.env``.
"""

import pytest

import app.core.video_profiles as vp


class _StubSettings:
    def __init__(self, *, segmented: bool, efficient: bool = True):
        self.veo_enable_segmented_route = segmented
        self.veo_enable_efficient_long_route = efficient


@pytest.fixture
def segmented_enabled(monkeypatch):
    monkeypatch.setattr(vp, "get_settings", lambda: _StubSettings(segmented=True))


@pytest.fixture
def segmented_disabled(monkeypatch):
    monkeypatch.setattr(vp, "get_settings", lambda: _StubSettings(segmented=False))


def test_segment_count_for_tier():
    assert vp.segment_count_for_tier(8) == 1
    assert vp.segment_count_for_tier(16) == 2
    assert vp.segment_count_for_tier(32) == 4
    assert vp.segment_count_for_tier(48) == 6
    assert vp.segment_count_for_tier(64) == 8


def test_segmented_profiles_selected_when_enabled(segmented_enabled):
    profile_16 = vp.get_duration_profile(16)
    assert profile_16.route == vp.VEO_SEGMENTED_VIDEO_ROUTE
    assert profile_16.veo_base_seconds == vp.SEGMENTED_SEGMENT_SECONDS
    assert profile_16.provider_target_seconds == 16  # 2 x 8s
    # cost units == segment count (1 + hops), keeping it comparable to the extend route.
    assert vp.get_profile_request_cost_units(profile_16) == 2

    profile_32 = vp.get_duration_profile(32)
    assert profile_32.route == vp.VEO_SEGMENTED_VIDEO_ROUTE
    assert vp.get_profile_request_cost_units(profile_32) == 4
    assert profile_32.provider_target_seconds == 32


def test_tier_8_stays_short_route_when_segmented(segmented_enabled):
    assert vp.get_duration_profile(8).route == vp.SHORT_VIDEO_ROUTE


def test_default_route_unchanged_when_flag_off(segmented_disabled):
    # With segmented off, tier 16/32 keep the extend route.
    assert vp.get_duration_profile(16).route == vp.VEO_EXTENDED_VIDEO_ROUTE
    assert vp.get_duration_profile(32).route == vp.VEO_EXTENDED_VIDEO_ROUTE


def test_creation_mode_profile_prefers_segmented(segmented_enabled):
    profile = vp.get_duration_profile_for_creation_mode(16, "character_consistency")
    assert profile.route == vp.VEO_SEGMENTED_VIDEO_ROUTE
