"""
Phase-2 image-to-video submission for the segmented identity-lock route.

When a character-consistency segmented post finishes its anchor segment (segment 0, generated from the
actor reference bundle), the actor must be re-used verbatim for the remaining segments instead of being
re-rolled. This module submits segments 1..N-1 as image-to-video, each locked to a *distinct* frame of
segment 0, so the face/wardrobe cannot drift across the stitched clip. Every segment anchors to seg 0
(one hop → zero compounding drift); the first i2v segment anchors near seg 0's end so that cut is
near-seamless, while the rest use spread-out frames so each cut reads as a natural UGC jump-cut (same
person/outfit/scene, different pose) instead of snapping back to one identical reset frame.

IO lives here (the file-size budget keeps it out of handlers.py / video_poller.py); persistence is
injected via ``persist_op`` so the poller owns the DB writes and this stays unit-testable. Exceptions
propagate so the caller can leave the lock resumable: each accepted submission is persisted before the
next is attempted, so a retry submits only the not-yet-submitted indexes.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from app.adapters.vertex_ai_client import get_vertex_ai_client
from app.adapters.video_stitcher import extract_anchor_frame
from app.core.logging import get_logger
from app.core.video_profiles import SEGMENTED_SEGMENT_SECONDS
from app.features.posts.prompt_builder import build_character_consistency_mid_continuation_prompt
from app.features.videos.segmented_pipeline import pending_i2v_indexes

logger = get_logger(__name__)

# persist_op(index, operation_id, prompt, provider_result) -> None
PersistOp = Callable[[int, str, str, Dict[str, Any]], None]

_SUPPORTED_I2V_PROVIDER = "vertex_ai"

# Where in seg 0 each i2v segment is anchored, so cuts read as natural jump-cuts rather than an
# identical reset. The first pending segment locks near seg 0's end (the seg0->seg1 cut is then
# near-seamless); the rest spread across the clip so every segment starts from a different pose.
_SEAMLESS_FRACTION = 0.9
_SPREAD_LOW = 0.2
_SPREAD_HIGH = 0.7


def _anchor_fractions(count: int) -> List[float]:
    """Distinct seg-0 frame fractions for ``count`` pending i2v segments, in pending order.

    Index 0 (the first pending segment) -> ~0.9 so the cut into it is near-seamless; the remaining
    ``count - 1`` fractions are evenly spread across [0.2, 0.7]. All values are distinct, so each
    i2v segment locks to a different frame of seg 0 (still one hop from seg 0 -> zero compounding
    drift). ``count`` is small (N <= 8), so per-fraction frame extraction is cheap.
    """
    if count <= 0:
        return []
    if count == 1:
        return [_SEAMLESS_FRACTION]
    rest = count - 1
    if rest == 1:
        spread = [(_SPREAD_LOW + _SPREAD_HIGH) / 2.0]
    else:
        step = (_SPREAD_HIGH - _SPREAD_LOW) / (rest - 1)
        spread = [_SPREAD_LOW + step * i for i in range(rest)]
    return [_SEAMLESS_FRACTION, *spread]


def submit_locked_segments(
    *,
    post_id: str,
    metadata: Dict[str, Any],
    anchor_video_bytes: bytes,
    correlation_id: str,
    persist_op: PersistOp,
) -> List[Dict[str, Any]]:
    """Submit every not-yet-submitted i2v segment, locked to the anchor segment's frame.

    Idempotent across calls: only ``pending_i2v_indexes`` (rows with no operation id) are submitted, so
    a resumed run never re-submits an already-accepted segment. ``persist_op`` is invoked immediately
    after each accepted submission so a crash mid-fan-out leaves a resumable trail.

    Returns the list of ``{"index", "operation_id"}`` submitted this call.

    Raises:
        ValueError: unsupported provider or anchor-frame extraction failure (caller handles).
    """
    lock = metadata.get("i2v_lock") or {}
    provider = str(lock.get("provider") or "")
    if provider != _SUPPORTED_I2V_PROVIDER:
        # Defensive: duration-routed CC always resolves to vertex_ai. Fail loudly rather than
        # silently submitting a reference-less text-to-video (which would re-introduce drift).
        raise ValueError(
            f"segmented i2v lock supports provider '{_SUPPORTED_I2V_PROVIDER}', got '{provider}' "
            f"(post {post_id})"
        )

    indexes = pending_i2v_indexes(metadata)
    if not indexes:
        return []

    beats: List[str] = list(lock.get("beats") or [])
    segment_count = int(metadata.get("veo_segment_count") or len(beats))
    last_index = segment_count - 1

    # A distinct seg-0 frame per pending segment (first -> near seg-0 end, rest spread out), so every
    # segment locks to the same actor but starts from a different pose -> natural jump-cuts, no reset.
    fractions = _anchor_fractions(len(indexes))

    client = get_vertex_ai_client()
    aspect_ratio = str(lock.get("aspect_ratio") or "9:16")
    duration_seconds = int(lock.get("duration_seconds") or SEGMENTED_SEGMENT_SECONDS)
    model = lock.get("model")
    output_gcs_uri = lock.get("output_gcs_uri")

    logger.info(
        "segmented_video_i2v_submit",
        post_id=post_id,
        correlation_id=correlation_id,
        pending_segment_indexes=indexes,
        provider=provider,
        duration_seconds=duration_seconds,
    )

    submitted: List[Dict[str, Any]] = []
    for position, index in enumerate(indexes):
        # This segment's own seg-0 frame (one extract per segment — cheap for N <= 8).
        frame_bytes, mime_type = extract_anchor_frame(
            video_bytes=anchor_video_bytes,
            post_id=post_id,
            correlation_id=f"{correlation_id}_seg{index}",
            at_fraction=fractions[position],
        )
        beat = beats[index] if 0 <= index < len(beats) else ""
        # No character/scene text and no "submitted reference images" wording — identity and
        # environment come from the supplied first frame; the prompt only carries the spoken beat.
        prompt = build_character_consistency_mid_continuation_prompt(
            beat,
            include_final_ending=(index == last_index),
        )
        result = client.submit_image_video(
            prompt=prompt,
            image_bytes=frame_bytes,
            mime_type=mime_type,
            correlation_id=f"{correlation_id}_seg{index}",
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            output_gcs_uri=output_gcs_uri,
            model=model,
        )
        operation_id = result["operation_id"]
        # Persist before the next submit so a crash leaves only the not-yet-recorded tail to resume.
        persist_op(index, operation_id, prompt, result)
        submitted.append({"index": index, "operation_id": operation_id})

    return submitted
