"""Tests for the batch arm endpoint schemas and handler."""

import pytest
from datetime import datetime, timedelta
from pydantic import ValidationError as PydanticValidationError

from app.features.publish.schemas import (
    SlotSpec,
    PostArmSpec,
    BatchArmRequest,
)


class TestSlotSpec:
    def test_valid_slot(self):
        slot = SlotSpec(day="mon", time="09:00")
        assert slot.day == "mon"
        assert slot.time == "09:00"

    def test_invalid_day_rejected(self):
        with pytest.raises(PydanticValidationError):
            SlotSpec(day="sunday", time="09:00")

    def test_invalid_time_format_rejected(self):
        with pytest.raises(PydanticValidationError):
            SlotSpec(day="mon", time="9am")


class TestPostArmSpec:
    def test_valid_post_arm(self):
        spec = PostArmSpec(post_id="abc", caption="Hello world")
        assert spec.post_id == "abc"
        assert spec.caption == "Hello world"
        assert spec.time_override is None
        assert spec.networks_override is None

    def test_with_overrides(self):
        spec = PostArmSpec(
            post_id="abc",
            caption="Hello",
            time_override="2026-03-24T16:00",
            networks_override=["instagram"],
        )
        assert spec.time_override == "2026-03-24T16:00"
        assert spec.networks_override == ["instagram"]

    def test_empty_caption_rejected(self):
        with pytest.raises(PydanticValidationError):
            PostArmSpec(post_id="abc", caption="")


class TestBatchArmRequest:
    def test_valid_request(self):
        req = BatchArmRequest(
            week_start="2026-03-23",
            slots=[
                SlotSpec(day="mon", time="09:00"),
                SlotSpec(day="tue", time="14:00"),
                SlotSpec(day="wed", time="11:00"),
                SlotSpec(day="thu", time="16:00"),
                SlotSpec(day="fri", time="12:00"),
            ],
            default_networks=["instagram", "facebook", "tiktok"],
            posts=[
                PostArmSpec(post_id="p1", caption="Caption 1"),
                PostArmSpec(post_id="p2", caption="Caption 2"),
            ],
        )
        assert len(req.slots) == 5
        assert len(req.posts) == 2

    def test_posts_within_30min_rejected(self):
        """Canon 7.3: minimum 30-minute gap between any two posts."""
        with pytest.raises(PydanticValidationError, match="30"):
            BatchArmRequest(
                week_start="2026-03-23",
                slots=[
                    SlotSpec(day="mon", time="09:00"),
                    SlotSpec(day="mon", time="09:15"),
                ],
                default_networks=["instagram"],
                posts=[
                    PostArmSpec(post_id="p1", caption="C1"),
                    PostArmSpec(post_id="p2", caption="C2"),
                ],
            )

    def test_empty_posts_rejected(self):
        with pytest.raises(PydanticValidationError):
            BatchArmRequest(
                week_start="2026-03-23",
                slots=[SlotSpec(day="mon", time="09:00")],
                default_networks=["instagram"],
                posts=[],
            )
