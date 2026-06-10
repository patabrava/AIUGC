"""Unit tests for the segmented-route IO wiring (prompt fan-out + poller orchestration).

These cover the glue that cannot be exercised without live Veo/Supabase: the per-segment prompt
construction in handlers and the poller's poll → record → stitch / fail / wait decision flow. All
external IO (Veo/Vertex clients, Supabase, the stitcher, the completion store) is faked.
"""

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
