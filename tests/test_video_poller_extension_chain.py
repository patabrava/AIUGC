"""Tests for Veo extension chaining in the video poller."""
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone
import pytest
import httpx


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


def test_claim_video_poll_lease_uses_updated_at_compare_and_set(monkeypatch):
    from workers.video_poller import _claim_video_poll_lease

    captured = {}

    class FakeUpdateQuery:
        def __init__(self, row):
            self._row = row
            self._eq_calls = []

        def eq(self, column, value):
            self._eq_calls.append((column, value))
            captured["eq_calls"] = list(self._eq_calls)
            return self

        def execute(self):
            return MagicMock(data=[self._row])

    class FakeTable:
        def update(self, payload):
            captured["payload"] = payload
            return FakeUpdateQuery(
                {
                    "id": "post-claim",
                    "updated_at": "2026-03-27T09:30:01Z",
                    "video_metadata": payload["video_metadata"],
                }
            )

    fake_supabase = MagicMock()
    fake_supabase.client.table.return_value = FakeTable()

    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("workers.video_poller._poller_identity", lambda: "worker-a")

    post = {
        "id": "post-claim",
        "updated_at": "2026-03-27T09:30:00Z",
        "video_metadata": {},
    }

    claimed = _claim_video_poll_lease(post, "corr-claim")

    assert claimed is not None
    assert captured["eq_calls"] == [
        ("id", "post-claim"),
        ("updated_at", "2026-03-27T09:30:00Z"),
    ]
    metadata = captured["payload"]["video_metadata"]
    assert metadata["video_poll_lease_owner"] == "worker-a"
    assert metadata["last_polled_by"] == "worker-a"
    assert "video_poll_lease_expires_at" in metadata


def test_claim_video_poll_lease_skips_when_other_worker_owns_active_lease(monkeypatch):
    from workers.video_poller import _claim_video_poll_lease

    future_expiry = (
        datetime.now(timezone.utc) + timedelta(seconds=60)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    fake_supabase = MagicMock()
    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("workers.video_poller._poller_identity", lambda: "worker-b")

    post = {
        "id": "post-skip",
        "updated_at": "2026-03-27T09:30:00Z",
        "video_metadata": {
            "video_poll_lease_owner": "worker-a",
            "video_poll_lease_expires_at": future_expiry,
        },
    }

    claimed = _claim_video_poll_lease(post, "corr-skip")

    assert claimed is None
    fake_supabase.client.table.assert_not_called()


def test_process_video_operation_skips_foreign_environment(monkeypatch):
    from workers.video_poller import process_video_operation

    claimed = []
    handled = []

    monkeypatch.setattr("workers.video_poller._poller_environment", lambda: "development")
    monkeypatch.setattr("workers.video_poller._poller_scope", lambda: "localhost")
    monkeypatch.setattr(
        "workers.video_poller._claim_video_poll_lease",
        lambda post, correlation_id: claimed.append((post["id"], correlation_id)),
    )
    monkeypatch.setattr(
        "workers.video_poller._handle_vertex_ai_video",
        lambda post, operation_id, correlation_id: handled.append((post["id"], operation_id, correlation_id)),
    )

    process_video_operation(
        {
            "id": "post-foreign-env",
            "video_provider": "vertex_ai",
            "video_operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-lite-generate-001/operations/op-1",
            "video_metadata": {
                "poller_scope": "lippelift.xyz",
            },
        }
    )

    assert claimed == []
    assert handled == []


def test_handle_vertex_ai_video_surfaces_provider_error(monkeypatch):
    from workers.video_poller import _handle_vertex_ai_video
    from app.core.errors import FlowForgeException

    class FakeVertexClient:
        def check_operation_status(self, *, operation_id, correlation_id):
            return {
                "operation_id": operation_id,
                "done": True,
                "status": "failed",
                "video_uri": None,
                "provider": "vertex_ai",
                "error": {
                    "code": 3,
                    "message": "Veo could not generate videos because the input image violates Vertex AI's usage guidelines.",
                },
            }

    monkeypatch.setattr("workers.video_poller.get_vertex_ai_client", lambda: FakeVertexClient())

    post = {
        "id": "post-vertex-error",
        "video_provider": "vertex_ai",
        "video_operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/op-1",
        "video_metadata": {},
    }

    with pytest.raises(FlowForgeException) as exc_info:
        _handle_vertex_ai_video(post, post["video_operation_id"], "corr-vertex-error")

    assert exc_info.value.status_code == 503
    assert exc_info.value.code.value == "third_party_fail"
    assert "usage guidelines" in exc_info.value.message
    assert exc_info.value.details["provider_error"]["code"] == 3


def test_clear_expired_poller_leases_removes_only_expired_entries(monkeypatch):
    from workers.video_poller import _clear_expired_poller_leases

    expired = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    active = (
        datetime.now(timezone.utc) + timedelta(seconds=60)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    updates = []

    class FakeUpdate:
        def __init__(self, payload, post_id):
            self.payload = payload
            self.post_id = post_id

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            updates.append((self.post_id, self.payload))
            return MagicMock(data=[])

    class FakeTable:
        def __init__(self):
            self._selected = False
            self._status_values = None

        def select(self, *args, **kwargs):
            self._selected = True
            return self

        def in_(self, column, values):
            self._status_values = values
            return self

        def execute(self):
            return MagicMock(
                data=[
                    {
                        "id": "expired-post",
                        "video_metadata": {
                            "video_poll_lease_owner": "worker-old",
                            "video_poll_lease_expires_at": expired,
                            "video_poll_lease_acquired_at": expired,
                        },
                    },
                    {
                        "id": "active-post",
                        "video_metadata": {
                            "video_poll_lease_owner": "worker-old",
                            "video_poll_lease_expires_at": active,
                            "video_poll_lease_acquired_at": active,
                        },
                    },
                ]
            )

        def update(self, payload):
            return FakeUpdate(payload, getattr(self, "_current_id", None))

    class FakeSupabase:
        def __init__(self):
            self.client = MagicMock()
            self.table_obj = FakeTable()
            self.client.table.side_effect = lambda name: self.table_obj

    fake_supabase = FakeSupabase()

    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: fake_supabase)
    monkeypatch.setattr("workers.video_poller.get_pollable_video_statuses", lambda: ["submitted", "processing"])

    _clear_expired_poller_leases()

    assert updates, "expected expired leases to be cleared"
    payload = updates[0][1]
    assert payload["video_metadata"]["last_poll_recovery"] == "startup_expired_lease_cleanup"
    assert "video_poll_lease_owner" not in payload["video_metadata"]
    assert "video_poll_lease_expires_at" not in payload["video_metadata"]


def test_acquire_poller_lock_exits_when_lock_held(monkeypatch):
    import builtins
    import io
    from workers.video_poller import _acquire_poller_lock

    fake_handle = io.StringIO()

    class DummyFcntl:
        def flock(self, fd, mode):
            raise OSError("locked")

    monkeypatch.setitem(__import__("sys").modules, "fcntl", DummyFcntl())
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: fake_handle)

    assert _acquire_poller_lock() is False


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


def test_submit_extension_hop_downloads_video_and_uses_extension_api(monkeypatch):
    """Extension hop must call the REST submit_video_extension path."""
    from app.core.config import get_settings
    from workers.video_poller import _submit_extension_hop

    settings = get_settings()
    monkeypatch.setattr(settings, "veo_disable_local_quota_guard", False)
    monkeypatch.setattr(settings, "veo_disable_all_quota_controls", False)

    previous_video_data = {"video_uri": "gs://bucket/base.mp4", "mime_type": "video/mp4"}
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
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
            "requested_size": "720x1280",
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_extension.return_value = {
        "operation_id": "op-ext-1",
        "status": "submitted",
    }
    mock_prompt_audit = MagicMock()
    mock_consume_quota = MagicMock()
    mock_ensure_slot = MagicMock()

    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller.record_prompt_audit", mock_prompt_audit), \
         patch("workers.video_poller.consume_quota", mock_consume_quota), \
         patch("workers.video_poller.ensure_immediate_submit_slot", mock_ensure_slot):
        _submit_extension_hop(post, correlation_id="test-corr", previous_video_data=previous_video_data)

    # Must use submit_video_extension with video_uri via REST.
    mock_veo.submit_video_extension.assert_called_once()
    mock_veo.submit_video_generation.assert_not_called()
    call_kwargs = mock_veo.submit_video_extension.call_args[1]
    assert "Zweiter Satz." in call_kwargs["prompt"]
    assert call_kwargs["video_uri"] == "gs://bucket/base.mp4"
    assert call_kwargs["aspect_ratio"] == "9:16"
    assert call_kwargs["duration_seconds"] == 7
    assert call_kwargs["negative_prompt"] is not None

    update_call = mock_supabase.client.table.return_value.update
    assert update_call.called
    update_data = update_call.call_args[0][0]
    assert update_data["video_operation_id"] == "op-ext-1"
    meta = update_data["video_metadata"]
    assert meta["veo_extension_hops_completed"] == 1
    assert meta["veo_current_segment_index"] == 1
    assert "op-ext-1" in meta["operation_ids"]
    assert meta["chain_status"] == "extending"
    mock_ensure_slot.assert_called_once()
    mock_consume_quota.assert_not_called()
    mock_prompt_audit.assert_called_once()
    audit_kwargs = mock_prompt_audit.call_args.kwargs
    assert audit_kwargs["prompt_path"] == "veo_extension_hop"
    assert audit_kwargs["requested_seconds"] == 7
    assert audit_kwargs["operation_id"] == "op-ext-1"
    assert audit_kwargs["negative_prompt"] is not None


def test_submit_extension_hop_uses_vertex_doc_shape(monkeypatch):
    """Vertex extension should use gcsUri input and 7s hop duration."""
    from workers.video_poller import _submit_extension_hop
    from app.core.config import get_settings

    previous_video_data = {"video_uri": "gs://bucket/base.mp4", "mime_type": "video/mp4"}
    post = {
        "id": "post-vertex-doc",
        "video_provider": "vertex_ai",
        "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 1,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Erster Satz.", "Zweiter Satz."],
            "veo_segments_total": 2,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "submitted",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
            "requested_size": "720x1280",
        },
    }

    mock_vertex = MagicMock()
    mock_vertex.submit_video_extension.return_value = {
        "operation_id": "op-ext-vertex",
        "status": "submitted",
    }
    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    settings = get_settings()
    monkeypatch.setattr(settings, "vertex_ai_output_gcs_uri", "gs://bucket/output/")

    with patch("workers.video_poller.get_vertex_ai_client", return_value=mock_vertex), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller.ensure_immediate_submit_slot", return_value={"ok": True}):
        _submit_extension_hop(post, correlation_id="test-vertex-doc", previous_video_data=previous_video_data)

    payload = mock_vertex.submit_video_extension.call_args.kwargs
    assert payload["video_uri"] == "gs://bucket/base.mp4"
    assert payload["video_mime_type"] == "video/mp4"
    assert payload["duration_seconds"] == 7
    assert payload["output_gcs_uri"] == "gs://bucket/output/"


def test_submit_extension_hop_stages_vertex_data_uri_to_gcs(monkeypatch):
    """Vertex extension must stage data URI sources into GCS before submit."""
    from workers.video_poller import _submit_extension_hop
    from app.core.config import get_settings

    previous_video_data = {
        "video_uri": "data:video/mp4;base64,AAAA",
        "mime_type": "video/mp4",
    }
    post = {
        "id": "post-vertex-stage",
        "video_provider": "vertex_ai",
        "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 1,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Erster Satz.", "Zweiter Satz."],
            "veo_segments_total": 2,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "submitted",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
            "requested_size": "720x1280",
        },
    }

    mock_vertex = MagicMock()
    mock_vertex.submit_video_extension.return_value = {
        "operation_id": "op-ext-stage",
        "status": "submitted",
    }
    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    settings = get_settings()
    monkeypatch.setattr(settings, "vertex_ai_output_gcs_uri", "gs://bucket/output/")
    monkeypatch.setattr("workers.video_poller._stage_vertex_video_bytes_to_gcs", lambda **kwargs: "gs://bucket/output/vertex-input/post-vertex-stage/1.mp4")

    with patch("workers.video_poller.get_vertex_ai_client", return_value=mock_vertex), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller.ensure_immediate_submit_slot", return_value={"ok": True}):
        _submit_extension_hop(post, correlation_id="test-vertex-stage", previous_video_data=previous_video_data)

    payload = mock_vertex.submit_video_extension.call_args.kwargs
    assert payload["video_uri"] == "gs://bucket/output/vertex-input/post-vertex-stage/1.mp4"


def test_submit_extension_hop_reuses_existing_veo_seed():
    """Extension hops must reuse the base Veo seed when one exists."""
    from workers.video_poller import _submit_extension_hop

    previous_video_data = {"video_uri": "gs://bucket/base.mp4", "mime_type": "video/mp4"}
    post = {
        "id": "post-seed",
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
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
            "requested_size": "720x1280",
            "veo_seed": 123456789,
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_extension.return_value = {
        "operation_id": "op-ext-seed",
        "status": "submitted",
    }
    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller.record_prompt_audit", MagicMock()), \
         patch("workers.video_poller.consume_quota", MagicMock()), \
         patch("workers.video_poller.ensure_immediate_submit_slot", MagicMock()):
        _submit_extension_hop(post, correlation_id="test-seed", previous_video_data=previous_video_data)

    call_kwargs = mock_veo.submit_video_extension.call_args[1]
    assert call_kwargs["seed"] == 123456789


def test_submit_extension_hop_consumes_quota_when_reservation_key_exists():
    """A successful extension hop must record one consumed quota unit."""
    from workers.video_poller import _submit_extension_hop

    previous_video_data = {"video_uri": "gs://bucket/base.mp4", "mime_type": "video/mp4"}
    post = {
        "id": "post-123-quota",
        "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
        "video_metadata": {
            "quota_reservation_key": "res-chain",
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
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
            "requested_size": "720x1280",
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_extension.return_value = {
        "operation_id": "op-ext-2",
        "status": "submitted",
    }
    mock_prompt_audit = MagicMock()
    mock_consume_quota = MagicMock()
    mock_ensure_slot = MagicMock()

    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller.record_prompt_audit", mock_prompt_audit), \
         patch("workers.video_poller.consume_quota", mock_consume_quota), \
         patch("workers.video_poller.ensure_immediate_submit_slot", mock_ensure_slot):
        _submit_extension_hop(post, correlation_id="test-corr-quota", previous_video_data=previous_video_data)

    mock_consume_quota.assert_called_once_with(
        reservation_key="res-chain",
        operation_id="op-ext-2",
        units=1,
    )


def test_submit_extension_hop_bypasses_quota_controls_for_control_testing(monkeypatch):
    """Controlled test runs must bypass extension-hop quota checks entirely."""
    from app.core.config import get_settings
    from workers.video_poller import _submit_extension_hop

    settings = get_settings()
    monkeypatch.setattr(settings, "veo_disable_all_quota_controls", True)

    previous_video_data = {"video_uri": "gs://bucket/base.mp4", "mime_type": "video/mp4"}
    post = {
        "id": "post-bypass",
        "seed_data": {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
        "video_metadata": {
            "quota_reservation_key": "res-chain",
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
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
            "requested_size": "720x1280",
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_extension.return_value = {
        "operation_id": "op-ext-bypass",
        "status": "submitted",
    }
    mock_prompt_audit = MagicMock()
    mock_consume_quota = MagicMock()
    mock_ensure_slot = MagicMock()

    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller.record_prompt_audit", mock_prompt_audit), \
         patch("workers.video_poller.consume_quota", mock_consume_quota), \
         patch("workers.video_poller.ensure_immediate_submit_slot", mock_ensure_slot):
        _submit_extension_hop(post, correlation_id="test-bypass", previous_video_data=previous_video_data)

    mock_ensure_slot.assert_not_called()
    mock_consume_quota.assert_called_once_with(
        reservation_key="res-chain",
        operation_id="op-ext-bypass",
        units=1,
    )
    mock_prompt_audit.assert_called_once()


def test_submit_extension_hop_raises_when_fewer_segments_than_hops():
    """Under-segmented chains must fail instead of repeating the last line."""
    from workers.video_poller import _submit_extension_hop

    previous_video_data = {"video_uri": "gs://bucket/base.mp4"}
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
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
            "requested_size": "720x1280",
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_extension.return_value = {"operation_id": "op-ext-1", "status": "submitted"}
    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase):
        with pytest.raises(Exception, match="ran out of distinct dialogue segments"):
            _submit_extension_hop(post, correlation_id="test-corr", previous_video_data=previous_video_data)

    mock_veo.submit_video_extension.assert_not_called()


def test_submit_extension_hop_defers_retryable_processed_video_error():
    """Retryable Veo extension readiness 400s must stay in extended processing."""
    from workers.video_poller import _submit_extension_hop

    request = httpx.Request(
        "POST",
        "https://generativelanguage.googleapis.com/v1beta/models/veo-3.1-generate-preview:predictLongRunning",
    )
    response = httpx.Response(
        400,
        json={
            "error": {
                "code": 400,
                "message": "Input video must be a video that was generated by VEO that has been processed.",
                "status": "INVALID_ARGUMENT",
            }
        },
        request=request,
    )

    previous_video_data = {"video_uri": "https://generativelanguage.googleapis.com/v1beta/files/base:download?alt=media"}
    post = {
        "id": "post-retryable",
        "seed_data": {"script": "Erster Satz. Zweiter Satz."},
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 1,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Erster Satz.", "Zweiter Satz."],
            "veo_segments_total": 2,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "submitted",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
        },
    }

    mock_veo = MagicMock()
    mock_veo.submit_video_extension.side_effect = httpx.HTTPStatusError(
        "bad request",
        request=request,
        response=response,
    )
    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller.ensure_immediate_submit_slot", return_value={"ok": True}):
        _submit_extension_hop(post, correlation_id="corr-retryable", previous_video_data=previous_video_data)

    update_payload = mock_supabase.client.table.return_value.update.call_args[0][0]
    assert update_payload["video_status"] == "extended_processing"
    metadata = update_payload["video_metadata"]
    assert metadata["chain_status"] == "waiting_for_extension_input_processing"
    assert metadata["veo_extension_input_retry_count"] == 1
    assert "veo_extension_retry_after" in metadata
    assert metadata["provider_status_code"] == 400
    assert "generated by VEO" in metadata["veo_extension_last_retryable_error"]


def test_submit_extension_hop_respects_retry_backoff_window():
    """Deferred extension retries must not hammer the provider before retry_after."""
    from workers.video_poller import _submit_extension_hop

    future_retry_after = (
        datetime.now(timezone.utc) + timedelta(seconds=45)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    previous_video_data = {"video_uri": "https://generativelanguage.googleapis.com/v1beta/files/base:download?alt=media"}
    post = {
        "id": "post-backoff",
        "seed_data": {"script": "Erster Satz. Zweiter Satz."},
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 1,
            "veo_extension_hops_completed": 0,
            "veo_segments": ["Erster Satz.", "Zweiter Satz."],
            "veo_segments_total": 2,
            "veo_current_segment_index": 0,
            "operation_ids": ["op-base"],
            "chain_status": "waiting_for_extension_input_processing",
            "generated_seconds": 4,
            "veo_base_seconds": 4,
            "veo_extension_seconds": 7,
            "requested_aspect_ratio": "9:16",
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
            "veo_extension_retry_after": future_retry_after,
        },
    }

    mock_veo = MagicMock()
    mock_supabase = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase):
        _submit_extension_hop(post, correlation_id="corr-backoff", previous_video_data=previous_video_data)

    mock_veo.submit_video_extension.assert_not_called()
    mock_supabase.client.table.assert_not_called()


def test_handle_veo_video_chains_when_hops_remaining():
    """When a VEO op completes but hops remain, download + extend via extension API."""
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
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
            "requested_size": "720x1280",
        },
    }

    mock_veo = MagicMock()
    mock_veo.check_operation_status.return_value = {
        "done": True,
        "video_data": {"video_uri": "gs://bucket/video.mp4"},
    }
    mock_veo.submit_video_extension.return_value = {
        "operation_id": "op-ext-1",
        "status": "submitted",
    }

    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch("workers.video_poller.ensure_immediate_submit_slot", return_value={"ok": True}), \
         patch("workers.video_poller._store_completed_video") as mock_store:
        _handle_veo_video(post, "op-base", "corr-chain")

    mock_store.assert_not_called()
    # Must use extension API with video_uri via REST.
    mock_veo.submit_video_extension.assert_called_once()
    assert mock_veo.submit_video_extension.call_args[1]["video_uri"] == "gs://bucket/video.mp4"
    assert mock_veo.submit_video_extension.call_args[1]["aspect_ratio"] == "9:16"
    mock_veo.submit_video_generation.assert_not_called()


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


def test_handle_vertex_ai_video_chains_when_hops_remaining():
    """Vertex AI completion must also advance to the next extension hop."""
    from workers.video_poller import _handle_vertex_ai_video

    post = {
        "id": "post-vertex-chain",
        "video_provider": "vertex_ai",
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
            "provider_aspect_ratio": "9:16",
            "requested_resolution": "720p",
        },
    }

    mock_vertex = MagicMock()
    mock_vertex.check_operation_status.return_value = {
        "done": True,
        "video_uri": "gs://bucket/video.mp4",
        "status": "completed",
    }

    with patch("workers.video_poller.get_vertex_ai_client", return_value=mock_vertex), \
         patch("workers.video_poller._submit_extension_hop") as mock_submit_extension, \
         patch("workers.video_poller.get_supabase"):
        _handle_vertex_ai_video(post, "op-base", "corr-vertex-chain")

    mock_submit_extension.assert_called_once()
    mock_vertex.check_operation_status.assert_called_once()




def test_handle_veo_video_downloads_bytes_when_postprocess_needed():
    """Portrait fallback must bypass URL ingest and download bytes for the final crop step."""
    from workers.video_poller import _handle_veo_video

    post = {
        "id": "post-final-crop",
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 2,
            "chain_status": "extending",
            "operation_ids": ["op-base", "op-ext-1", "op-ext-2"],
            "postprocess_crop_aspect_ratio": "9:16",
            "requested_size": "720x1280",
        },
    }

    mock_veo = MagicMock()
    mock_veo.check_operation_status.return_value = {
        "done": True,
        "video_data": {"video_uri": "gs://bucket/final.mp4"},
    }
    mock_veo.download_video.return_value = b"final-video"

    mock_settings = MagicMock()
    mock_settings.use_url_based_upload = True

    with patch("workers.video_poller.get_veo_client", return_value=mock_veo), \
         patch("workers.video_poller.get_settings", return_value=mock_settings), \
         patch("workers.video_poller._store_completed_video") as mock_store:
        _handle_veo_video(post, "op-ext-2", "corr-final-crop")

    mock_veo.get_video_download_url.assert_not_called()
    mock_veo.download_video.assert_called_once_with(
        video_uri="gs://bucket/final.mp4",
        correlation_id="corr-final-crop",
    )
    mock_store.assert_called_once()
    assert mock_store.call_args.kwargs["video_source"] == b"final-video"


def test_store_completed_video_applies_crop_postprocess():
    """Final storage must upload the post-processed portrait bytes when crop metadata is present."""
    from workers.video_poller import _store_completed_video

    mock_storage = MagicMock()
    mock_storage.upload_video.return_value = {
        "storage_provider": "cloudflare_r2",
        "storage_key": "videos/post.mp4",
        "url": "https://cdn.example.com/post.mp4",
        "thumbnail_url": None,
        "file_path": "videos/post.mp4",
        "size": 12,
    }
    mock_supabase = MagicMock()
    mock_supabase.client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    with patch("workers.video_poller.get_storage_client", return_value=mock_storage), \
         patch("workers.video_poller.get_supabase", return_value=mock_supabase), \
         patch(
             "workers.video_poller._maybe_postprocess_video_bytes",
             return_value=(
                 b"cropped-video",
                 {
                     "postprocess_crop_applied": True,
                     "postprocess_crop_output_size": "720x1280",
                 },
             ),
         ) as mock_postprocess, \
         patch(
             "workers.video_poller._trim_tail",
             return_value=(b"cropped-video", {}),
         ):
        _store_completed_video(
            post_id="post-crop",
            provider="veo_3_1",
            video_source=b"raw-video",
            correlation_id="corr-store",
            provider_metadata={"video_uri": "gs://bucket/final.mp4"},
            existing_metadata={
                "postprocess_crop_aspect_ratio": "9:16",
                "requested_size": "720x1280",
            },
        )

    mock_postprocess.assert_called_once()
    mock_storage.upload_video.assert_called_once_with(
        video_bytes=b"cropped-video",
        file_name="post_post-crop.mp4",
        correlation_id="corr-store",
    )
    update_payload = mock_supabase.client.table.return_value.update.call_args[0][0]
    assert update_payload["video_metadata"]["postprocess_crop_applied"] is True
    assert update_payload["video_metadata"]["postprocess_crop_output_size"] == "720x1280"


def test_full_32s_chain_lifecycle():
    """Simulate a complete 32s chain: base + 4 extension hops."""
    from workers.video_poller import _needs_extension_hop

    metadata = {
        "video_pipeline_route": "veo_extended",
        "veo_extension_hops_target": 4,
        "veo_extension_hops_completed": 0,
        "generated_seconds": 4,
        "veo_base_seconds": 4,
        "veo_extension_seconds": 7,
    }

    assert _needs_extension_hop(metadata) is True

    for hop in range(1, 5):
        metadata["veo_extension_hops_completed"] = hop
        metadata["generated_seconds"] = 4 + (hop * 7)

        if hop < 4:
            assert _needs_extension_hop(metadata) is True, f"Hop {hop} should still need more"
        else:
            assert _needs_extension_hop(metadata) is False, f"Hop {hop} should be done"

    assert metadata["generated_seconds"] == 32
    assert metadata["veo_extension_hops_completed"] == 4


def test_process_video_operation_releases_quota_and_freezes_after_provider_429(monkeypatch):
    from app.core.config import get_settings
    from workers.video_poller import process_video_operation

    settings = get_settings()
    monkeypatch.setattr(settings, "veo_disable_local_quota_guard", False)
    monkeypatch.setattr(settings, "veo_disable_all_quota_controls", False)

    response = httpx.Response(
        status_code=429,
        request=httpx.Request("POST", "https://example.com"),
        text='{"error":{"message":"quota exhausted"}}',
    )
    error = httpx.HTTPStatusError("quota exhausted", request=response.request, response=response)

    post = {
        "id": "post-429",
        "video_operation_id": "op-429",
        "video_provider": "veo_3_1",
        "updated_at": "2026-03-31T10:00:00Z",
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 1,
            "quota_reservation_key": "res-429",
        },
    }

    captured_update = {}

    class FakeUpdate:
        def __init__(self, payload):
            captured_update["payload"] = payload

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            return MagicMock()

    class FakeTable:
        def update(self, payload):
            return FakeUpdate(payload)

    fake_supabase = MagicMock()
    fake_supabase.client.table.return_value = FakeTable()

    monkeypatch.setattr("workers.video_poller._claim_video_poll_lease", lambda post, correlation_id: post)
    monkeypatch.setattr("workers.video_poller._handle_veo_video", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: fake_supabase)
    freeze_mock = MagicMock()
    release_mock = MagicMock()
    monkeypatch.setattr("workers.video_poller.maybe_freeze_after_provider_429", freeze_mock)
    monkeypatch.setattr("workers.video_poller._release_post_quota_reservation", release_mock)

    process_video_operation(post)

    freeze_mock.assert_called_once()
    release_mock.assert_called_once_with(
        post,
        reason=str(error),
        final_status="failed",
        error_code="provider_429",
    )
    assert captured_update["payload"]["video_status"] == "failed"


def test_process_video_operation_suppresses_freeze_when_controls_bypassed(monkeypatch):
    from app.core.config import get_settings
    from workers.video_poller import process_video_operation

    settings = get_settings()
    monkeypatch.setattr(settings, "veo_disable_all_quota_controls", True)

    response = httpx.Response(
        status_code=429,
        request=httpx.Request("POST", "https://example.com"),
        text='{"error":{"message":"quota exhausted"}}',
    )
    error = httpx.HTTPStatusError("quota exhausted", request=response.request, response=response)

    post = {
        "id": "post-429-bypass",
        "video_operation_id": "op-429-bypass",
        "video_provider": "veo_3_1",
        "updated_at": "2026-03-31T10:00:00Z",
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 1,
            "quota_reservation_key": "res-429-bypass",
        },
    }

    captured_update = {}

    class FakeUpdate:
        def __init__(self, payload):
            captured_update["payload"] = payload

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            return MagicMock()

    class FakeTable:
        def update(self, payload):
            return FakeUpdate(payload)

    fake_supabase = MagicMock()
    fake_supabase.client.table.return_value = FakeTable()

    monkeypatch.setattr("workers.video_poller._claim_video_poll_lease", lambda post, correlation_id: post)
    monkeypatch.setattr("workers.video_poller._handle_veo_video", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: fake_supabase)
    freeze_mock = MagicMock()
    release_mock = MagicMock()
    monkeypatch.setattr("workers.video_poller.maybe_freeze_after_provider_429", freeze_mock)
    monkeypatch.setattr("workers.video_poller._release_post_quota_reservation", release_mock)

    process_video_operation(post)

    freeze_mock.assert_not_called()
    release_mock.assert_called_once_with(
        post,
        reason=str(error),
        final_status="failed",
        error_code="provider_429",
    )
    assert captured_update["payload"]["video_status"] == "failed"


def test_process_video_operation_defers_vertex_polling_errors(monkeypatch):
    from workers.video_poller import process_video_operation

    post = {
        "id": "post-vertex-defer",
        "video_operation_id": "op-vertex-defer",
        "video_provider": "vertex_ai",
        "updated_at": "2026-03-31T10:00:00Z",
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 1,
            "veo_extension_hops_completed": 0,
            "chain_status": "submitted",
        },
    }

    response = httpx.Response(
        status_code=503,
        request=httpx.Request("POST", "https://example.com"),
        text="temporary outage",
    )
    error = httpx.HTTPStatusError("temporary outage", request=response.request, response=response)

    captured_update = {}

    class FakeUpdate:
        def __init__(self, payload):
            captured_update["payload"] = payload

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            return MagicMock()

    class FakeTable:
        def update(self, payload):
            return FakeUpdate(payload)

    fake_supabase = MagicMock()
    fake_supabase.client.table.return_value = FakeTable()

    monkeypatch.setattr("workers.video_poller._claim_video_poll_lease", lambda post, correlation_id: post)
    monkeypatch.setattr("workers.video_poller._handle_vertex_ai_video", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: fake_supabase)

    process_video_operation(post)

    assert captured_update["payload"]["video_status"] == "processing"
    assert captured_update["payload"]["video_metadata"]["provider_status_code"] == 503
    assert captured_update["payload"]["video_metadata"]["error_type"] == "HTTPStatusError"


def test_process_video_operation_marks_vertex_provider_failures_as_failed(monkeypatch):
    from app.core.errors import ErrorCode, FlowForgeException
    from workers.video_poller import process_video_operation

    post = {
        "id": "post-vertex-provider-failed",
        "video_operation_id": "op-vertex-provider-failed",
        "video_provider": "vertex_ai",
        "updated_at": "2026-03-31T10:00:00Z",
        "video_metadata": {
            "video_pipeline_route": "veo_extended",
            "veo_extension_hops_target": 1,
            "veo_extension_hops_completed": 0,
            "chain_status": "submitted",
        },
    }

    provider_error = {
        "code": 3,
        "message": "Veo could not generate videos because the input image violates Vertex AI's usage guidelines. Support codes: 15236754",
    }
    error = FlowForgeException(
        code=ErrorCode.THIRD_PARTY_FAIL,
        message=provider_error["message"],
        details={"provider_error": provider_error},
        status_code=503,
    )

    captured_update = {}

    class FakeUpdate:
        def __init__(self, payload):
            captured_update["payload"] = payload

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            return MagicMock()

    class FakeTable:
        def update(self, payload):
            return FakeUpdate(payload)

    fake_supabase = MagicMock()
    fake_supabase.client.table.return_value = FakeTable()

    monkeypatch.setattr("workers.video_poller._claim_video_poll_lease", lambda post, correlation_id: post)
    monkeypatch.setattr("workers.video_poller._handle_vertex_ai_video", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: fake_supabase)

    process_video_operation(post)

    assert captured_update["payload"]["video_status"] == "failed"
    assert captured_update["payload"]["video_metadata"]["error_type"] == "FlowForgeException"
    assert "usage guidelines" in captured_update["payload"]["video_metadata"]["error"]
    assert captured_update["payload"]["video_metadata"]["error_details"]["provider_error"]["code"] == 3


def test_process_video_operation_skips_lease_when_operation_data_missing(monkeypatch):
    from workers.video_poller import process_video_operation

    post = {
        "id": "post-missing-op",
        "updated_at": "2026-03-31T10:00:00Z",
        "video_metadata": {},
    }

    lease_mock = MagicMock()
    handle_mock = MagicMock()
    monkeypatch.setattr("workers.video_poller._claim_video_poll_lease", lease_mock)
    monkeypatch.setattr("workers.video_poller._handle_vertex_ai_video", handle_mock)
    monkeypatch.setattr("workers.video_poller.get_supabase", lambda: MagicMock())

    process_video_operation(post)

    lease_mock.assert_not_called()
    handle_mock.assert_not_called()
