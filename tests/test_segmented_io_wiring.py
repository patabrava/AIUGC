"""Unit tests for the segmented-route IO wiring (prompt fan-out + poller orchestration).

These cover the glue that cannot be exercised without live Veo/Supabase: the per-segment prompt
construction in handlers and the poller's poll → record → stitch / fail / wait decision flow. All
external IO (Veo/Vertex clients, Supabase, the stitcher, the completion store) is faked.
"""

from dataclasses import replace

import app.core.video_profiles as vp_profiles
import app.features.videos.handlers as h
import workers.video_poller as vp
from app.features.videos import segmented_pipeline as sp


# --------------------------------------------------------------------------------------
# Prompt fan-out (handlers)
# --------------------------------------------------------------------------------------

def test_split_script_into_segments_exact_count():
    script = (
        "Erstens spare ich jeden Monat Geld. Zweitens ist es viel sicherer geworden. "
        "Drittens fühle ich mich endlich wieder wohl. Viertens würde ich es sofort wieder tun."
    )
    assert len(h._split_script_into_segments(script, 2)) == 2
    assert len(h._split_script_into_segments(script, 4)) == 4
    # single-segment tier collapses to the whole script
    assert len(h._split_script_into_segments(script, 1)) == 1


def test_build_segmented_segment_prompts_reanchors_character_consistency():
    video_prompt = {
        "character": "Laura, a 38-year-old German woman with shoulder-length brown hair",
        "scene": "a sunlit residential stairwell",
        "style": "handheld vertical UGC",
        "cinematography": "eye-level selfie framing",
        "audio": {"dialogue": "Erstens spare ich Geld. Zweitens ist es sicherer. Drittens lohnt es sich."},
    }
    beats, prompts = h._build_segmented_segment_prompts(
        seed_data={},
        video_prompt=video_prompt,
        segment_count=2,
        creation_mode="character_consistency",
        target_length_tier=16,
    )
    assert len(beats) == 2
    assert len(prompts) == 2
    # Every segment re-anchors the full character + scene (the drift fix), not "continue from prev".
    assert all("Laura" in p for p in prompts)
    assert all("stairwell" in p for p in prompts)
    # Only the final segment should differ (it carries the ending directive).
    assert prompts[0] != prompts[1]


def test_submit_segmented_character_consistency_fans_out_reference_anchored_segments(monkeypatch):
    calls = []

    def _fake_submit_video_request(**kwargs):
        calls.append(kwargs)
        index = len(calls) - 1
        return {
            "operation_id": f"op-{index}",
            "status": "submitted",
            "provider_model": kwargs.get("model") or "veo-3.1-generate-001",
            "provider_metadata": {
                "source": "actor_identity_plus_canonical_scene_anchor",
                "reference_image_count": 3,
                "reference_image_roles": [
                    "actor_identity_anchor",
                    "actor_identity_anchor",
                    "canonical_scene_anchor",
                ],
            },
        }

    monkeypatch.setattr(h, "_submit_video_request", _fake_submit_video_request)
    profile = replace(
        vp_profiles._BASE_PROFILES[16],
        route=vp_profiles.VEO_SEGMENTED_VIDEO_ROUTE,
        provider_target_seconds=16,
        veo_base_seconds=vp_profiles.SEGMENTED_SEGMENT_SECONDS,
        veo_extension_seconds=0,
        veo_extension_hops=1,
    )
    video_prompt = {
        "character": "Laura, a 38-year-old German woman with shoulder-length brown hair",
        "scene": "a sunlit residential stairwell",
        "style": "handheld vertical UGC",
        "cinematography": "eye-level selfie framing",
        "audio": {"dialogue": "Erstens spare ich Geld. Zweitens ist es sicherer. Drittens lohnt es sich."},
    }

    result = h._submit_segmented_post(
        post={"id": "post-1", "video_metadata": {}},
        batch={"id": "batch-1", "creation_mode": "character_consistency", "actor_identity_id": "actor-1"},
        submission_plan={
            "profile": profile,
            "provider": "vertex_ai",
            "aspect_ratio": "9:16",
            "provider_aspect_ratio": "9:16",
            "requested_aspect_ratio": "9:16",
            "resolution": "720p",
            "seconds": 16,
            "size": None,
        },
        video_prompt=video_prompt,
        seed_data={},
        canonical_scene_asset=None,
        scene_reference_set=None,
        veo_seed=777,
        correlation_id="corr",
        model="veo-3.1-generate-001",
    )

    assert result["operation_ids"] == ["op-0", "op-1"]
    assert result["segment_count"] == 2
    assert result["i2v_locked"] is False
    assert len(calls) == 2
    assert [c["correlation_id"] for c in calls] == ["corr_seg0", "corr_seg1"]
    for call in calls:
        assert call["provider"] == "vertex_ai"
        assert call["model"] == "veo-3.1-generate-001"
        assert call["provider_duration_seconds"] == vp_profiles.SEGMENTED_SEGMENT_SECONDS
        assert call["first_frame_image"] is None
        assert call["creation_mode"] == "character_consistency"
        assert call["actor_identity_id"] == "actor-1"

    metadata = h._build_segmented_submission_metadata(
        existing_metadata={},
        submission_plan={
            "profile": profile,
            "provider": "vertex_ai",
            "aspect_ratio": "9:16",
            "provider_aspect_ratio": "9:16",
            "resolution": "720p",
            "seconds": 16,
            "size": None,
        },
        segmented_result=result,
        creation_mode="character_consistency",
        script_contract={"target_length_tier": 16},
        quota_reservation_key="quota-1",
        quota_reserved_units=2,
        quota_consume_error=None,
        canonical_scene_asset=None,
        actor_identity_id="actor-1",
        scene_reference_set=None,
    )
    assert "i2v_lock" not in metadata
    assert [op["operation_id"] for op in metadata["veo_segment_ops"]] == ["op-0", "op-1"]
    assert all(op["status"] == sp.SEGMENT_STATUS_SUBMITTED for op in metadata["veo_segment_ops"])


# --------------------------------------------------------------------------------------
# Poller orchestration
# --------------------------------------------------------------------------------------

class _FakeTable:
    def __init__(self, sink):
        self._sink = sink
        self._update = None

    def update(self, payload):
        self._update = payload
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def execute(self):
        self._sink.append(self._update)
        return self


class _FakeSupabase:
    def __init__(self):
        self.updates = []

    @property
    def client(self):
        return self

    def table(self, _name):
        return _FakeTable(self.updates)


class _FakeVeoClient:
    def __init__(self, statuses):
        # statuses: {operation_id: (done, uri, failed)}
        self._statuses = statuses
        self.downloaded = []

    def check_operation_status(self, *, operation_id, correlation_id):
        done, uri, failed = self._statuses[operation_id]
        if failed:
            return {"status": "failed", "done": True, "error": {"message": "boom"}}
        if done:
            return {"status": "completed", "done": True, "video_data": {"video_uri": uri}}
        return {"status": "processing", "done": False}

    def download_video(self, *, video_uri, correlation_id):
        self.downloaded.append(video_uri)
        return f"bytes::{video_uri}".encode()


def _segmented_post(ops):
    return {
        "id": "post-1",
        "video_provider": "veo_3_1",
        "video_operation_id": ops[0]["operation_id"],
        "video_metadata": {
            "video_pipeline_route": vp_profiles.VEO_SEGMENTED_VIDEO_ROUTE,
            "veo_segment_count": len(ops),
            "veo_segment_ops": ops,
        },
    }


def _ops(states):
    # states: list of "submitted"/"completed"
    return [
        {"index": i, "operation_id": f"op-{i}", "status": s, "video_uri": (f"gs://seg/{i}.mp4" if s == "completed" else None)}
        for i, s in enumerate(states)
    ]


def _patch_common(monkeypatch, fake_sb):
    monkeypatch.setattr(vp, "get_supabase", lambda: fake_sb)
    monkeypatch.setattr(vp, "_veo_available", True)
    monkeypatch.setattr(vp, "_clear_transient_polling_errors", lambda m: m)


def test_handle_segmented_video_stitches_when_all_complete(monkeypatch):
    fake_sb = _FakeSupabase()
    _patch_common(monkeypatch, fake_sb)
    fake_veo = _FakeVeoClient({"op-0": (True, "gs://seg/0.mp4", False), "op-1": (True, "gs://seg/1.mp4", False)})
    monkeypatch.setattr(vp, "get_veo_client", lambda: fake_veo)

    stitched = {}
    monkeypatch.setattr(vp, "stitch_segments", lambda **kw: (b"FINAL", {"output_duration_seconds": 16.0}))

    stored = {}
    def _fake_store(**kwargs):
        stored.update(kwargs)
    monkeypatch.setattr(vp, "_store_completed_video", _fake_store)

    post = _segmented_post(_ops(["submitted", "submitted"]))
    vp._handle_segmented_video(post, "corr")

    # Both segments downloaded in index order, stitched bytes handed to the completion store.
    assert fake_veo.downloaded == ["gs://seg/0.mp4", "gs://seg/1.mp4"]
    assert stored["video_source"] == b"FINAL"
    assert stored["provider_metadata"]["segmented"] is True
    # We never persist a "stitching" status (the posts CHECK constraint forbids it); the post stays
    # processing until the completion store flips it to caption_pending.
    assert not any(u.get("video_status") == vp.VIDEO_STATUS_STITCHING for u in fake_sb.updates)


def test_handle_segmented_video_waits_when_partial(monkeypatch):
    fake_sb = _FakeSupabase()
    _patch_common(monkeypatch, fake_sb)
    fake_veo = _FakeVeoClient({"op-0": (True, "gs://seg/0.mp4", False), "op-1": (False, None, False)})
    monkeypatch.setattr(vp, "get_veo_client", lambda: fake_veo)
    monkeypatch.setattr(vp, "stitch_segments", lambda **kw: (_ for _ in ()).throw(AssertionError("must not stitch")))
    marked = {}
    monkeypatch.setattr(vp, "_mark_processing", lambda pid, cid, op: marked.update({"post": pid}))

    post = _segmented_post(_ops(["submitted", "submitted"]))
    vp._handle_segmented_video(post, "corr")

    assert marked.get("post") == "post-1"  # stayed processing, no stitch


def test_handle_segmented_video_fails_when_any_segment_fails(monkeypatch):
    fake_sb = _FakeSupabase()
    _patch_common(monkeypatch, fake_sb)
    fake_veo = _FakeVeoClient({"op-0": (True, "gs://seg/0.mp4", False), "op-1": (False, None, True)})
    monkeypatch.setattr(vp, "get_veo_client", lambda: fake_veo)
    monkeypatch.setattr(vp, "stitch_segments", lambda **kw: (_ for _ in ()).throw(AssertionError("must not stitch")))
    released = {}
    monkeypatch.setattr(vp, "release_quota", lambda **kw: released.update(kw))

    post = _segmented_post(_ops(["submitted", "submitted"]))
    post["video_metadata"]["quota_reservation_key"] = "resv-1"
    vp._handle_segmented_video(post, "corr")

    assert any(u.get("video_status") == vp.VIDEO_STATUS_FAILED for u in fake_sb.updates)
    assert released.get("reservation_key") == "resv-1"  # reserved units released on failure


# --------------------------------------------------------------------------------------
# Identity-lock (image-to-video) poller flow
# --------------------------------------------------------------------------------------

def _i2v_lock(state):
    lock = sp.build_i2v_lock(
        provider="vertex_ai", aspect_ratio="9:16", provider_aspect_ratio="9:16", resolution="720p",
        duration_seconds=8, model="veo-3.1-generate-001", output_gcs_uri=None, beats=["beat 0", "beat 1"],
    )
    lock["state"] = state
    return lock


def _i2v_post(*, lock_state, ops):
    return {
        "id": "post-1",
        "video_provider": "veo_3_1",
        "video_operation_id": "op-0",
        "video_metadata": {
            "video_pipeline_route": vp_profiles.VEO_SEGMENTED_VIDEO_ROUTE,
            "veo_segment_count": 2,
            "veo_seed": 7,
            "veo_segment_ops": ops,
            "i2v_lock": _i2v_lock(lock_state),
        },
    }


def test_handle_segmented_video_submits_i2v_when_anchor_completes(monkeypatch):
    fake_sb = _FakeSupabase()
    _patch_common(monkeypatch, fake_sb)
    fake_veo = _FakeVeoClient({"op-0": (True, "gs://seg/0.mp4", False)})  # anchor completes this poll
    monkeypatch.setattr(vp, "get_veo_client", lambda: fake_veo)
    monkeypatch.setattr(vp, "_download_segment_bytes", lambda *a, **k: b"ANCHOR_BYTES")
    monkeypatch.setattr(vp, "record_prompt_audit", lambda **kw: None)
    monkeypatch.setattr(
        vp, "stitch_segments", lambda **kw: (_ for _ in ()).throw(AssertionError("must not stitch yet"))
    )
    marked = {}
    monkeypatch.setattr(vp, "_mark_processing", lambda pid, cid, op: marked.update({"post": pid}))

    captured = {}
    def _fake_submit(*, post_id, metadata, anchor_video_bytes, correlation_id, persist_op):
        captured["anchor_bytes"] = anchor_video_bytes
        persist_op(1, "i2v-op-1", "i2v-prompt-1", {"provider_metadata": {}})  # simulate one i2v submit
        return [{"index": 1, "operation_id": "i2v-op-1"}]
    monkeypatch.setattr(vp, "submit_locked_segments", _fake_submit)

    ops = [
        {"index": 0, "operation_id": "op-0", "status": "submitted", "video_uri": None, "kind": sp.SEGMENT_KIND_ANCHOR},
        {"index": 1, "operation_id": None, "status": "pending", "video_uri": None, "kind": sp.SEGMENT_KIND_I2V},
    ]
    vp._handle_segmented_video(_i2v_post(lock_state=sp.I2V_STATE_PENDING, ops=ops), "corr")

    assert captured.get("anchor_bytes") == b"ANCHOR_BYTES"  # anchor frame source downloaded
    final_meta = fake_sb.updates[-1]["video_metadata"]
    assert final_meta["i2v_lock"]["state"] == sp.I2V_STATE_SUBMITTED  # fan-out marked done
    assert any(o["index"] == 1 and o["operation_id"] == "i2v-op-1" for o in final_meta["veo_segment_ops"])
    assert marked.get("post") == "post-1"  # still processing, not stitched


def test_handle_segmented_video_stitches_after_i2v_segments_complete(monkeypatch):
    fake_sb = _FakeSupabase()
    _patch_common(monkeypatch, fake_sb)
    # Anchor already done; the submitted i2v segment completes this poll → ready to stitch.
    fake_veo = _FakeVeoClient({"op-0": (True, "gs://seg/0.mp4", False), "i2v-op-1": (True, "gs://seg/1.mp4", False)})
    monkeypatch.setattr(vp, "get_veo_client", lambda: fake_veo)
    monkeypatch.setattr(
        vp, "submit_locked_segments", lambda **kw: (_ for _ in ()).throw(AssertionError("already submitted"))
    )
    monkeypatch.setattr(vp, "stitch_segments", lambda **kw: (b"FINAL", {"output_duration_seconds": 16.0}))
    stored = {}
    monkeypatch.setattr(vp, "_store_completed_video", lambda **kw: stored.update(kw))

    ops = [
        {"index": 0, "operation_id": "op-0", "status": "completed", "video_uri": "gs://seg/0.mp4", "kind": sp.SEGMENT_KIND_ANCHOR},
        {"index": 1, "operation_id": "i2v-op-1", "status": "submitted", "video_uri": None, "kind": sp.SEGMENT_KIND_I2V},
    ]
    vp._handle_segmented_video(_i2v_post(lock_state=sp.I2V_STATE_SUBMITTED, ops=ops), "corr")

    assert fake_veo.downloaded == ["gs://seg/0.mp4", "gs://seg/1.mp4"]  # both segments stitched in order
    assert stored.get("video_source") == b"FINAL"
