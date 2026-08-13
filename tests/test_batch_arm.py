"""Tests for the batch arm endpoint schemas and handler."""

import asyncio
import pytest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
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
        assert spec.blog_scheduled_at is None

    def test_with_overrides(self):
        spec = PostArmSpec(
            post_id="abc",
            caption="Hello",
            time_override="2036-03-25T16:00",
            networks_override=["instagram"],
        )
        assert spec.time_override == "2036-03-25T16:00"
        assert spec.networks_override == ["instagram"]

    def test_empty_caption_rejected(self):
        with pytest.raises(PydanticValidationError):
            PostArmSpec(post_id="abc", caption="")


class TestBatchArmRequest:
    def test_valid_request(self):
        req = BatchArmRequest(
            week_start="2036-03-24",
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
                week_start="2036-03-24",
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

    def test_past_schedule_rejected_before_dispatch(self):
        with pytest.raises(PydanticValidationError, match="future"):
            BatchArmRequest(
                week_start="2020-03-23",
                slots=[SlotSpec(day="mon", time="09:00")],
                default_networks=["instagram"],
                posts=[PostArmSpec(post_id="p1", caption="Caption")],
            )

    def test_empty_posts_rejected(self):
        with pytest.raises(PydanticValidationError):
            BatchArmRequest(
                week_start="2036-03-24",
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
        self.rpc_calls = []

    def table(self, name):
        return _FakeTable(self.storage, name)

    def rpc(self, function_name, payload):
        client = self

        class _Rpc:
            def execute(self):
                client.rpc_calls.append((function_name, deepcopy(payload)))
                assert function_name == "arm_batch_content_schedule"
                schedules = payload["p_schedules"]
                for schedule in schedules:
                    post = next(row for row in client.storage["posts"] if row["id"] == schedule["post_id"])
                    post.update({
                        "scheduled_at": schedule["scheduled_at"],
                        "publish_caption": schedule["publish_caption"],
                        "social_networks": schedule["networks"],
                        "publish_status": "scheduled",
                    })
                    if post.get("blog_enabled"):
                        blog_content = deepcopy(post.get("blog_content") or {})
                        blog_content["publication_date"] = schedule["blog_scheduled_at"]
                        post.update({
                            "blog_status": "scheduled",
                            "blog_scheduled_at": schedule["blog_scheduled_at"],
                            "blog_content": blog_content,
                        })
                return _FakeResponse({
                    "armed_count": len(schedules),
                    "scheduled_blog_count": sum(
                        1 for schedule in schedules if schedule.get("blog_scheduled_at")
                    ),
                })

        return _Rpc()


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
        result = asyncio.run(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2036-03-24",
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
        assert len(client.rpc_calls) == 1
        assert client.rpc_calls[0][0] == "arm_batch_content_schedule"

    def test_arm_one_eight_second_video_and_ready_blog_atomically(self):
        storage = _make_storage(num_posts=1)
        storage["posts"][0].update({
            "video_metadata": {"duration_seconds": 8.0},
            "blog_enabled": True,
            "blog_status": "draft",
            "blog_content": {
                "name": "Was bei Einsamkeit wirklich hilft",
                "body_html": "<p>Ein vollständiger Blogentwurf.</p>",
                "preview_image_url": "https://cdn.example.com/blog-preview.jpg",
            },
        })
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        result = asyncio.run(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2036-03-24",
                    timezone="Europe/Berlin",
                    slots=[SlotSpec(day="mon", time="10:00")],
                    default_networks=["instagram"],
                    posts=[
                        PostArmSpec(
                            post_id="p1",
                            caption="Ein kurzer, geprüfter Social Caption.",
                            blog_scheduled_at="2036-03-24T12:00:00+01:00",
                        )
                    ],
                ),
                db=client,
            )
        )

        post = storage["posts"][0]
        assert result["ok"] is True
        assert result["armed_count"] == 1
        assert result["scheduled_blog_count"] == 1
        assert post["video_metadata"]["duration_seconds"] == 8.0
        assert post["publish_status"] == "scheduled"
        assert post["blog_status"] == "scheduled"
        assert post["scheduled_at"] is not None
        assert post["blog_scheduled_at"] is not None
        assert client.rpc_calls[0][0] == "arm_batch_content_schedule"

    def test_arm_accepts_single_post_and_single_slot(self):
        storage = _make_storage(num_posts=1)
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        result = asyncio.run(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2036-03-24",
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

    def test_arm_schedules_ready_blog_with_social_in_same_rpc(self):
        storage = _make_storage(num_posts=1)
        storage["posts"][0].update({
            "blog_enabled": True,
            "blog_status": "draft",
            "blog_content": {
                "body_html": "<p>Ready article</p>",
                "preview_image_url": "https://cdn.example.com/blog.webp",
            },
            "blog_scheduled_at": None,
        })
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        result = asyncio.run(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2036-03-24",
                    slots=[SlotSpec(day="mon", time="09:00")],
                    default_networks=["instagram"],
                    posts=[PostArmSpec(
                        post_id="p1",
                        caption="Caption 1",
                        blog_scheduled_at="2036-03-27T09:00:00+01:00",
                    )],
                ),
                db=client,
            )
        )

        post = storage["posts"][0]
        assert result["scheduled_blog_count"] == 1
        assert post["publish_status"] == "scheduled"
        assert post["blog_status"] == "scheduled"
        assert post["blog_scheduled_at"].startswith("2036-03-27T08:00:00")
        assert post["blog_content"]["publication_date"] == post["blog_scheduled_at"]

    def test_arm_rejects_enabled_blog_without_generated_image(self):
        storage = _make_storage(num_posts=1)
        storage["posts"][0].update({
            "blog_enabled": True,
            "blog_status": "draft",
            "blog_content": {"body_html": "<p>Ready article</p>"},
        })
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        with pytest.raises(Exception, match="blog image"):
            asyncio.run(
                arm_batch_dispatch(
                    batch_id="b1",
                    request=BatchArmRequest(
                        week_start="2036-03-24",
                        slots=[SlotSpec(day="mon", time="09:00")],
                        default_networks=["instagram"],
                        posts=[PostArmSpec(
                            post_id="p1",
                            caption="Caption 1",
                            blog_scheduled_at="2036-03-27T09:00:00+01:00",
                        )],
                    ),
                    db=client,
                )
            )

    def test_arm_rejects_batch_not_in_s7(self):
        storage = _make_storage()
        storage["batches"][0]["state"] = "S6_QA"
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        with pytest.raises(Exception, match="S7_PUBLISH_PLAN"):
            asyncio.run(
                arm_batch_dispatch(
                    batch_id="b1",
                    request=BatchArmRequest(
                        week_start="2036-03-24",
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
            asyncio.run(
                arm_batch_dispatch(
                    batch_id="b1",
                    request=BatchArmRequest(
                        week_start="2036-03-24",
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
        result = asyncio.run(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2036-03-24",
                    slots=[SlotSpec(day="mon", time="09:00")],
                    default_networks=["instagram"],
                    posts=[PostArmSpec(post_id="p1", caption="Cap", time_override="2036-03-26T18:00")],
                ),
                db=client,
            )
        )
        scheduled = storage["posts"][0]["scheduled_at"]
        assert "2036-03-26" in scheduled
        assert result["armed_count"] == 1

    def test_arm_respects_networks_override(self):
        storage = _make_storage(num_posts=1)
        # Provide complete TikTok settings so arm validation passes for the override.
        storage["posts"][0]["tiktok_settings"] = {
            "title": "x",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": False,
            "allow_duet": False,
            "allow_stitch": False,
            "commercial_disclosure": False,
            "your_brand": False,
            "branded_content": False,
            "consent_acknowledged": True,
        }
        client = _FakeClient(storage)

        from app.features.publish.arm import arm_batch_dispatch
        asyncio.run(
            arm_batch_dispatch(
                batch_id="b1",
                request=BatchArmRequest(
                    week_start="2036-03-24",
                    slots=[SlotSpec(day="mon", time="09:00")],
                    default_networks=["instagram", "facebook", "tiktok"],
                    posts=[PostArmSpec(post_id="p1", caption="Cap", networks_override=["tiktok"])],
                ),
                db=client,
            )
        )
        assert storage["posts"][0]["social_networks"] == ["tiktok"]


def test_atomic_batch_arm_migration_validates_and_updates_in_one_statement():
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase/migrations/20260812010000_atomic_batch_publish_arm.sql"
    ).read_text()

    assert "CREATE OR REPLACE FUNCTION public.arm_batch_publish_schedule" in migration
    assert "FOR UPDATE;" in migration
    assert "matched_count <> expected_count" in migration
    assert "UPDATE public.posts AS post" in migration
    assert "TO service_role" in migration


def test_unified_final_schedule_migration_arms_social_and_blog_atomically():
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase/migrations/20260812220000_unified_final_publish_schedule.sql"
    ).read_text()

    assert "CREATE OR REPLACE FUNCTION public.arm_batch_content_schedule" in migration
    assert "blog_scheduled_at TIMESTAMPTZ" in migration
    assert "post.blog_content ->> 'preview_image_url'" in migration
    assert "blog_status = CASE" in migration
    assert "publish_status = 'scheduled'" in migration
    assert "NOTIFY pgrst, 'reload schema'" in migration


def test_arm_rejects_tiktok_post_without_settings(monkeypatch):
    """Arm must fail if any TikTok-targeted post is missing required TikTok settings."""
    from app.features.publish import arm

    posts = [
        {
            "id": "p-1",
            "social_networks": ["tiktok"],
            "tiktok_settings": {},
        },
    ]
    monkeypatch.setattr(arm, "_load_batch_posts_for_arm", lambda batch_id: posts, raising=False)
    with pytest.raises(Exception) as excinfo:
        arm._validate_tiktok_settings_present(posts)
    assert "TikTok settings" in str(excinfo.value)


def test_arm_accepts_tiktok_post_with_complete_settings(monkeypatch):
    from app.features.publish import arm

    posts = [
        {
            "id": "p-1",
            "social_networks": ["tiktok"],
            "tiktok_settings": {
                "title": "x",
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "allow_comment": False,
                "allow_duet": False,
                "allow_stitch": False,
                "commercial_disclosure": False,
                "your_brand": False,
                "branded_content": False,
                "consent_acknowledged": True,
            },
        },
    ]
    arm._validate_tiktok_settings_present(posts)  # must not raise
