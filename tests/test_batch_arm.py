"""Tests for the batch arm endpoint schemas and handler."""

import asyncio
import pytest
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

from pydantic import ValidationError as PydanticValidationError

from app.core.states import BatchState
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


# -- Fake Supabase helpers (same pattern as test_publish_meta_flow.py) --

class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, storage, table_name):
        self.storage = storage
        self.table_name = table_name
        self.filters = []
        self.operation = "select"
        self.payload = None

    def select(self, _fields):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append(("eq", key, value))
        return self

    def execute(self):
        rows = self.storage[self.table_name]
        matches = [r for r in rows if all(r.get(k) == v for _, k, v in self.filters)]
        if self.operation == "update" and self.payload:
            for m in matches:
                m.update(self.payload)
        return _FakeResponse(matches)


class _FakeClient:
    def __init__(self, storage):
        self.storage = storage

    def table(self, name):
        return _FakeTable(self.storage, name)


def _make_storage(batch_id="b1", num_posts=5):
    posts = []
    for i in range(num_posts):
        posts.append({
            "id": f"p{i+1}",
            "batch_id": batch_id,
            "video_url": f"https://cdn.example.com/video_{i+1}.mp4",
            "publish_status": "pending",
            "scheduled_at": None,
            "publish_caption": None,
            "social_networks": None,
            "seed_data": {"caption": f"Generated caption {i+1}"},
        })
    return {
        "batches": [{"id": batch_id, "state": "S7_PUBLISH_PLAN"}],
        "posts": posts,
    }


class TestBatchArmHandler:
    def test_arm_sets_scheduled_at_for_all_posts(self):
        storage = _make_storage()
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        result = asyncio.get_event_loop().run_until_complete(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2026-03-23",
                    slots=[
                        SlotSpec(day="mon", time="09:00"),
                        SlotSpec(day="tue", time="14:00"),
                        SlotSpec(day="wed", time="11:00"),
                        SlotSpec(day="thu", time="16:00"),
                        SlotSpec(day="fri", time="12:00"),
                    ],
                    default_networks=["instagram", "facebook"],
                    posts=[
                        PostArmSpec(post_id=f"p{i+1}", caption=f"Caption {i+1}")
                        for i in range(5)
                    ],
                ),
                db=client,
            )
        )
        assert result["ok"] is True
        assert result["armed_count"] == 5
        for post in storage["posts"]:
            assert post["scheduled_at"] is not None
            assert post["publish_status"] == "scheduled"
            assert post["social_networks"] == ["instagram", "facebook"]

    def test_arm_accepts_single_post_and_single_slot(self):
        storage = _make_storage(num_posts=1)
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        result = asyncio.get_event_loop().run_until_complete(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2026-03-23",
                    slots=[SlotSpec(day="mon", time="09:00")],
                    default_networks=["instagram"],
                    posts=[PostArmSpec(post_id="p1", caption="Caption 1")],
                ),
                db=client,
            )
        )
        assert result["ok"] is True
        assert result["armed_count"] == 1
        assert storage["posts"][0]["scheduled_at"] is not None
        assert storage["posts"][0]["publish_status"] == "scheduled"
        assert storage["posts"][0]["social_networks"] == ["instagram"]

    def test_arm_rejects_batch_not_in_s7(self):
        storage = _make_storage()
        storage["batches"][0]["state"] = "S6_QA"
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        with pytest.raises(Exception, match="S7_PUBLISH_PLAN"):
            asyncio.get_event_loop().run_until_complete(
                arm_batch_dispatch(
                    batch_id="b1",
                    request=BatchArmRequest(
                        week_start="2026-03-23",
                        slots=[SlotSpec(day="mon", time="09:00")],
                        default_networks=["instagram"],
                        posts=[PostArmSpec(post_id="p1", caption="Cap")],
                    ),
                    db=client,
                )
            )

    def test_arm_rejects_post_without_video(self):
        storage = _make_storage(num_posts=1)
        storage["posts"][0]["video_url"] = None
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        with pytest.raises(Exception, match="video"):
            asyncio.get_event_loop().run_until_complete(
                arm_batch_dispatch(
                    batch_id="b1",
                    request=BatchArmRequest(
                        week_start="2026-03-23",
                        slots=[SlotSpec(day="mon", time="09:00")],
                        default_networks=["instagram"],
                        posts=[PostArmSpec(post_id="p1", caption="Cap")],
                    ),
                    db=client,
                )
            )

    def test_arm_respects_time_override(self):
        storage = _make_storage(num_posts=1)
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        result = asyncio.get_event_loop().run_until_complete(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2026-03-23",
                    slots=[SlotSpec(day="mon", time="09:00")],
                    default_networks=["instagram"],
                    posts=[PostArmSpec(post_id="p1", caption="Cap", time_override="2026-03-25T18:00")],
                ),
                db=client,
            )
        )
        scheduled = storage["posts"][0]["scheduled_at"]
        assert "2026-03-25" in scheduled
        assert result["armed_count"] == 1

    def test_arm_respects_networks_override(self):
        storage = _make_storage(num_posts=1)
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        asyncio.get_event_loop().run_until_complete(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2026-03-23",
                    slots=[SlotSpec(day="mon", time="09:00")],
                    default_networks=["instagram", "facebook", "tiktok"],
                    posts=[PostArmSpec(post_id="p1", caption="Cap", networks_override=["tiktok"])],
                ),
                db=client,
            )
        )
        assert storage["posts"][0]["social_networks"] == ["tiktok"]
