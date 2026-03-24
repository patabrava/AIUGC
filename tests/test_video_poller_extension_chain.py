"""Tests for Veo extension chaining in the video poller."""
from unittest.mock import patch, MagicMock


def test_poll_pending_videos_includes_extended_statuses(monkeypatch):
    """poll_pending_videos must query for extended statuses too."""
    captured_statuses = {}

    class FakeTable:
        def __init__(self, name):
            self._name = name
        def select(self, *a, **kw):
            return self
        def in_(self, col, values):
            captured_statuses[col] = values
            return self
        def eq(self, *a, **kw):
            return self
        def execute(self):
            return MagicMock(data=[])

    class FakeSupabase:
        client = MagicMock()

    fake_sb = FakeSupabase()
    fake_sb.client.table = lambda name: FakeTable(name)
    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: fake_sb)

    from workers.video_poller import poll_pending_videos
    poll_pending_videos()

    assert "video_status" in captured_statuses
    queried = captured_statuses["video_status"]
    assert "extended_submitted" in queried
    assert "extended_processing" in queried
    assert "submitted" in queried
    assert "processing" in queried


from workers.video_poller import _needs_extension_hop


def test_needs_extension_hop_returns_true_when_hops_remaining():
    metadata = {
        "video_pipeline_route": "veo_extended",
        "veo_extension_hops_target": 4,
        "veo_extension_hops_completed": 1,
    }
    assert _needs_extension_hop(metadata) is True


def test_needs_extension_hop_returns_false_when_all_hops_done():
    metadata = {
        "video_pipeline_route": "veo_extended",
        "veo_extension_hops_target": 2,
        "veo_extension_hops_completed": 2,
    }
    assert _needs_extension_hop(metadata) is False


def test_needs_extension_hop_returns_false_for_short_route():
    metadata = {"video_pipeline_route": "short", "veo_extension_hops_target": 0, "veo_extension_hops_completed": 0}
    assert _needs_extension_hop(metadata) is False


def test_needs_extension_hop_returns_false_for_missing_metadata():
    assert _needs_extension_hop({}) is False
    assert _needs_extension_hop(None) is False
