"""Unit tests for the segmented-route pure orchestration logic."""

import pytest

import app.core.video_profiles as vp
from app.features.videos import segmented_pipeline as sp


class _StubSettings:
    veo_enable_segmented_route = True
    veo_enable_efficient_long_route = True


@pytest.fixture
def segmented_profile(monkeypatch):
    monkeypatch.setattr(vp, "get_settings", lambda: _StubSettings())
    return vp.get_duration_profile(16)  # 2 segments


def test_plan_segment_submissions_shape(segmented_profile):
    subs = sp.plan_segment_submissions(
        profile=segmented_profile,
        segments=["Beat eins.", "Beat zwei."],
        seed=12345,
        character="38yo German woman",
        scene="stairwell",
    )
    assert [s.index for s in subs] == [0, 1]
    assert all(s.duration_seconds == 8 for s in subs)
    assert all(s.seed == 12345 for s in subs)  # same seed across all segments
    assert all("38yo German woman" in s.prompt for s in subs)  # identity re-anchored each segment


def test_plan_rejects_wrong_segment_count(segmented_profile):
    with pytest.raises(ValueError):
        sp.plan_segment_submissions(
            profile=segmented_profile,
            segments=["only one"],  # tier 16 expects 2
            seed=1,
        )


def test_plan_rejects_non_segmented_profile(monkeypatch):
    monkeypatch.setattr(vp, "get_settings", lambda: type("S", (), {
        "veo_enable_segmented_route": False, "veo_enable_efficient_long_route": True})())
    extend_profile = vp.get_duration_profile(16)
    with pytest.raises(ValueError):
        sp.plan_segment_submissions(profile=extend_profile, segments=["a", "b"], seed=1)


def _metadata(ops, count=2):
    return {
        "video_pipeline_route": vp.VEO_SEGMENTED_VIDEO_ROUTE,
        "veo_segment_count": count,
        "veo_segment_ops": ops,
    }


def test_build_initial_segment_ops():
    ops = sp.build_initial_segment_ops(["op-a", "op-b"])
    assert [o["index"] for o in ops] == [0, 1]
    assert all(o["status"] == sp.SEGMENT_STATUS_SUBMITTED for o in ops)
    assert all(o["video_uri"] is None for o in ops)


def test_record_segment_result_is_pure_and_targeted():
    meta = _metadata(sp.build_initial_segment_ops(["op-a", "op-b"]))
    new_ops = sp.record_segment_result(
        meta, operation_id="op-b", status=sp.SEGMENT_STATUS_COMPLETED, video_uri="gs://x/b.mp4"
    )
    # original metadata untouched (purity)
    assert meta["veo_segment_ops"][1]["status"] == sp.SEGMENT_STATUS_SUBMITTED
    assert new_ops[0]["status"] == sp.SEGMENT_STATUS_SUBMITTED  # op-a unchanged
    assert new_ops[1]["status"] == sp.SEGMENT_STATUS_COMPLETED
    assert new_ops[1]["video_uri"] == "gs://x/b.mp4"


def test_stitch_ready_only_when_all_complete():
    ops = sp.build_initial_segment_ops(["op-a", "op-b"])
    meta = _metadata(ops)
    assert sp.segment_stitch_ready(meta) is False  # nothing complete

    ops = sp.record_segment_result(meta, operation_id="op-a", status=sp.SEGMENT_STATUS_COMPLETED, video_uri="a.mp4")
    meta = _metadata(ops)
    assert sp.segment_stitch_ready(meta) is False  # one still pending

    ops = sp.record_segment_result(meta, operation_id="op-b", status=sp.SEGMENT_STATUS_COMPLETED, video_uri="b.mp4")
    meta = _metadata(ops)
    assert sp.segment_stitch_ready(meta) is True


def test_stitch_not_ready_if_any_failed():
    ops = sp.build_initial_segment_ops(["op-a", "op-b"])
    meta = _metadata(ops)
    ops = sp.record_segment_result(meta, operation_id="op-a", status=sp.SEGMENT_STATUS_COMPLETED, video_uri="a.mp4")
    meta = _metadata(ops)
    ops = sp.record_segment_result(meta, operation_id="op-b", status=sp.SEGMENT_STATUS_FAILED)
    meta = _metadata(ops)
    assert sp.any_segment_failed(meta) is True
    assert sp.segment_stitch_ready(meta) is False


def test_completed_uri_missing_blocks_stitch():
    """A segment marked completed but without a video_uri must not be considered ready."""
    ops = [
        {"index": 0, "operation_id": "op-a", "status": sp.SEGMENT_STATUS_COMPLETED, "video_uri": "a.mp4"},
        {"index": 1, "operation_id": "op-b", "status": sp.SEGMENT_STATUS_COMPLETED, "video_uri": None},
    ]
    assert sp.segment_stitch_ready(_metadata(ops)) is False


def test_ordered_uris_sorted_by_index():
    ops = [
        {"index": 1, "operation_id": "op-b", "status": "completed", "video_uri": "b.mp4"},
        {"index": 0, "operation_id": "op-a", "status": "completed", "video_uri": "a.mp4"},
    ]
    assert sp.ordered_completed_segment_uris(_metadata(ops)) == ["a.mp4", "b.mp4"]


def test_is_segmented_route_guard():
    assert sp.is_segmented_route({"video_pipeline_route": vp.VEO_SEGMENTED_VIDEO_ROUTE}) is True
    assert sp.is_segmented_route({"video_pipeline_route": vp.VEO_EXTENDED_VIDEO_ROUTE}) is False
    assert sp.is_segmented_route(None) is False
