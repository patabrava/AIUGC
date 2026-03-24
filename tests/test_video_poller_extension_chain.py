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


def test_submit_extension_hop_advances_segment_and_submits():
    """Extension hop must advance segment index and submit next VEO generation."""
    from workers.video_poller import _submit_extension_hop

    post = {
        "id": "post-123",
        "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Erster Satz.", "Zweiter Satz.", "Dritter Satz."],
            "veo_segments_total": 3,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "submitted",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "requested_resolution": "720p",
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_generation.return_value = {
        "operation_id": "op-ext-1",
        "status": "submitted",
    }

    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase):
        _submit_extension_hop(post, correlation_id="test-corr")

    mock_veo.submit_video_generation.assert_called_once()
    call_kwargs = mock_veo.submit_video_generation.call_args[1]
    assert "Zweiter Satz." in call_kwargs["prompt"]

    update_call = mock_supabase.client.table.return_value.update
    assert update_call.called
    update_data = update_call.call_args[0][0]
    assert update_data["video_operation_id"] == "op-ext-1"
    meta = update_data["video_metadata"]
    assert meta["veo_extension_hops_completed"] == 1
    assert meta["veo_current_segment_index"] == 1
    assert "op-ext-1" in meta["operation_ids"]
    assert meta["chain_status"] == "extending"


def test_submit_extension_hop_reuses_last_segment_when_fewer_segments_than_hops():
    """If segments list is shorter than hops, reuse the last segment."""
    from workers.video_poller import _submit_extension_hop

    post = {
        "id": "post-short-segs",
        "seed_data": {"script": "Nur ein Satz."},
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 4,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Nur ein Satz."],
            "veo_segments_total": 1,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "submitted",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "requested_resolution": "720p",
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_generation.return_value = {"operation_id": "op-ext-1", "status": "submitted"}
    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase):
        _submit_extension_hop(post, correlation_id="test-corr")

    call_kwargs = mock_veo.submit_video_generation.call_args[1]
    assert "Nur ein Satz." in call_kwargs["prompt"]


def test_handle_veo_video_chains_when_hops_remaining():
    """When a VEO op completes but hops remain, submit next extension."""
    from workers.video_poller import _handle_veo_video

    post = {
        "id": "post-chain",
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Satz eins.", "Satz zwei.", "Satz drei."],
            "veo_segments_total": 3,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "submitted",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "requested_resolution": "720p",
        },
    }

    mock_veo = MagicMock()
    mock_veo.check_operation_status.return_value = {
        "done": True,
        "video_data": {"video_uri": "gs://bucket/video.mp4"},
    }
    mock_veo.submit_video_generation.return_value = {
        "operation_id": "op-ext-1",
        "status": "submitted",
    }

    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller._store_completed_video") as mock_store:
        _handle_veo_video(post, "op-base", "corr-chain")

    mock_store.assert_not_called()
    mock_veo.submit_video_generation.assert_called_once()


def test_handle_veo_video_completes_when_all_hops_done():
    """When final hop completes, store the video normally."""
    from workers.video_poller import _handle_veo_video

    post = {
        "id": "post-final",
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 2,
            "chain_status": "extending",
            "operation_ids": ["op-base", "op-ext-1", "op-ext-2"],
        },
    }

    mock_veo = MagicMock()
    mock_veo.check_operation_status.return_value = {
        "done": True,
        "video_data": {"video_uri": "gs://bucket/final.mp4"},
    }
    mock_veo.get_video_download_url.return_value = "https://storage.example.com/final.mp4"

    mock_settings = MagicMock()
    mock_settings.use_url_based_upload = True

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_settings", return_value=mock_settings), \
         patch("workers.video_poller._store_completed_video") as mock_store:
        _handle_veo_video(post, "op-ext-2", "corr-final")

    mock_store.assert_called_once()
