"""Unit tests for the segmented identity-lock image-to-video fan-out (segments 1..N-1).

All external IO is faked: the anchor-frame extractor and the Vertex client are monkeypatched, so these
exercise the submission/idempotency/prompt logic without ffmpeg, Veo, or Supabase.
"""

import pytest

import app.core.video_profiles as vp
from app.features.posts.prompt_builder import build_character_consistency_mid_continuation_prompt
from app.features.videos import segmented_i2v as i2v
from app.features.videos import segmented_pipeline as sp


class _FakeVertex:
    def __init__(self):
        self.image_calls = []

    def submit_image_video(self, *, prompt, image_bytes, mime_type, correlation_id, aspect_ratio,
                           duration_seconds, output_gcs_uri=None, model=None, use_fast_model=False):
        self.image_calls.append(
            {
                "prompt": prompt,
                "image_bytes": image_bytes,
                "mime_type": mime_type,
                "aspect_ratio": aspect_ratio,
                "duration_seconds": duration_seconds,
                "model": model,
            }
        )
        op = f"i2v-op-{len(self.image_calls)}"
        return {"operation_id": op, "status": "submitted", "provider_model": model, "provider_metadata": {}}

    def submit_text_video(self, **_kwargs):  # pragma: no cover - must never be reached on the i2v path
        raise AssertionError("i2v submission must not call submit_text_video (no reference images)")


def _meta(*, segment_count=2, provider="vertex_ai", anchor_completed=True, filled_indexes=()):
    ops = [
        {
            "index": 0,
            "operation_id": "anchor-op",
            "status": sp.SEGMENT_STATUS_COMPLETED if anchor_completed else sp.SEGMENT_STATUS_SUBMITTED,
            "video_uri": "gs://seg/0.mp4" if anchor_completed else None,
            "kind": sp.SEGMENT_KIND_ANCHOR,
        }
    ]
    for index in range(1, segment_count):
        if index in filled_indexes:
            ops.append({"index": index, "operation_id": f"i2v-{index}", "status": sp.SEGMENT_STATUS_SUBMITTED,
                        "video_uri": None, "kind": sp.SEGMENT_KIND_I2V})
        else:
            ops.append({"index": index, "operation_id": None, "status": sp.SEGMENT_STATUS_PENDING,
                        "video_uri": None, "kind": sp.SEGMENT_KIND_I2V})
    return {
        "video_pipeline_route": vp.VEO_SEGMENTED_VIDEO_ROUTE,
        "veo_segment_count": segment_count,
        "veo_seed": 123,
        "veo_segment_ops": ops,
        "i2v_lock": sp.build_i2v_lock(
            provider=provider,
            aspect_ratio="9:16",
            provider_aspect_ratio="9:16",
            resolution="720p",
            duration_seconds=8,
            model="veo-3.1-generate-001",
            output_gcs_uri=None,
            beats=[f"beat {i}" for i in range(segment_count)],
        ),
    }


@pytest.fixture
def fake_vertex(monkeypatch):
    client = _FakeVertex()
    monkeypatch.setattr(i2v, "get_vertex_ai_client", lambda: client)
    # Skip real ffmpeg; return DISTINCT bytes per requested fraction so we can assert that each
    # segment locks to a different seg-0 frame (zero-drift jump-cuts, not one identical reset frame).
    monkeypatch.setattr(
        i2v,
        "extract_anchor_frame",
        lambda *, video_bytes, post_id, correlation_id, at_fraction: (
            f"FRAME@{at_fraction}".encode(),
            "image/jpeg",
        ),
    )
    return client


def _recording_persist(meta):
    recorded = []

    def persist_op(index, operation_id, prompt, result):
        recorded.append({"index": index, "operation_id": operation_id, "prompt": prompt})
        meta["veo_segment_ops"] = sp.record_i2v_submitted_op(meta, index=index, operation_id=operation_id)

    return recorded, persist_op


def test_submits_one_i2v_per_pending_segment_with_distinct_seg0_frames(fake_vertex):
    meta = _meta(segment_count=4)  # tier 32 → segments 1,2,3 are i2v
    recorded, persist_op = _recording_persist(meta)

    submitted = i2v.submit_locked_segments(
        post_id="post-1", metadata=meta, anchor_video_bytes=b"ANCHOR_VIDEO",
        correlation_id="corr", persist_op=persist_op,
    )

    assert [s["index"] for s in submitted] == [1, 2, 3]
    assert fake_vertex.image_calls  # submit_image_video used (submit_text_video would raise)
    frames = [c["image_bytes"] for c in fake_vertex.image_calls]
    # Each i2v segment locks to a DISTINCT frame of seg 0 → no identical-reset glitch at the cuts.
    assert len(set(frames)) == len(frames)
    fractions = [float(f.decode().split("@")[1]) for f in frames]
    # The first pending segment must not anchor near the end of seg 0. A near-end anchor made the
    # stitch look like failed seamless continuity: same shot, but a fresh generated body state.
    assert 0.35 <= fractions[0] <= 0.6
    assert fractions[0] != max(fractions)
    assert all(c["duration_seconds"] == 8 for c in fake_vertex.image_calls)
    assert [r["index"] for r in recorded] == [1, 2, 3]


def test_prompt_per_segment_and_only_last_carries_ending(fake_vertex):
    meta = _meta(segment_count=4)
    _recorded, persist_op = _recording_persist(meta)
    i2v.submit_locked_segments(
        post_id="post-1", metadata=meta, anchor_video_bytes=b"V", correlation_id="c", persist_op=persist_op,
    )
    prompts = [c["prompt"] for c in fake_vertex.image_calls]  # for indexes 1,2,3
    # Middle segments are continuation prompts (no final ending); the last carries the ending.
    assert prompts[0] == build_character_consistency_mid_continuation_prompt("beat 1", include_final_ending=False)
    assert prompts[1] == build_character_consistency_mid_continuation_prompt("beat 2", include_final_ending=False)
    assert prompts[2] == build_character_consistency_mid_continuation_prompt("beat 3", include_final_ending=True)
    assert prompts[0] != prompts[2]


def test_idempotent_resume_submits_only_unfilled_indexes(fake_vertex):
    # Segment 1 already submitted (crash before 2,3) → resume submits only 2 and 3.
    meta = _meta(segment_count=4, filled_indexes=(1,))
    recorded, persist_op = _recording_persist(meta)
    submitted = i2v.submit_locked_segments(
        post_id="post-1", metadata=meta, anchor_video_bytes=b"V", correlation_id="c", persist_op=persist_op,
    )
    assert [s["index"] for s in submitted] == [2, 3]
    assert len(fake_vertex.image_calls) == 2
    assert [r["index"] for r in recorded] == [2, 3]


def test_no_pending_indexes_is_a_noop(fake_vertex):
    meta = _meta(segment_count=2, filled_indexes=(1,))  # everything already submitted
    _recorded, persist_op = _recording_persist(meta)
    submitted = i2v.submit_locked_segments(
        post_id="post-1", metadata=meta, anchor_video_bytes=b"V", correlation_id="c", persist_op=persist_op,
    )
    assert submitted == []
    assert fake_vertex.image_calls == []


def test_unsupported_provider_raises_without_submitting(fake_vertex):
    meta = _meta(segment_count=2, provider="veo_3_1")
    _recorded, persist_op = _recording_persist(meta)
    with pytest.raises(ValueError):
        i2v.submit_locked_segments(
            post_id="post-1", metadata=meta, anchor_video_bytes=b"V", correlation_id="c", persist_op=persist_op,
        )
    assert fake_vertex.image_calls == []  # guarded before any provider call
