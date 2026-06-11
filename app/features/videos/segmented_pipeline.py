"""
Pure orchestration logic for the segmented (stitch) video route.

This module holds the side-effect-free decisions for the drift-free route so they can be unit-tested
without Veo, Supabase, or the poller's IO. The submission handler and the video poller call these at
single branch points; all network/DB work stays in those callers.

Metadata contract (stored in ``posts.video_metadata``) for a segmented post:

    {
      "video_pipeline_route": "veo_segmented",
      "veo_segment_count": <int N>,
      "veo_seed": <int>,
      "veo_segment_ops": [
        {"index": 0, "operation_id": "...", "status": "submitted|processing|completed|failed", "video_uri": null},
        ... one entry per segment ...
      ]
    }

Identity-lock variant (character consistency): segment 0 is a text+reference anchor; segments 1..N-1
are image-to-video locked to a frame from segment 0 so the actor cannot drift. Such posts also carry an
``i2v_lock`` plan (see ``build_i2v_lock``) and pre-seeded ``pending`` op rows (see
``build_segment_ops_with_anchor``); the poller submits the i2v segments once the anchor completes. Posts
without ``i2v_lock`` follow the legacy all-at-once fan-out unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.video_profiles import (
    VEO_SEGMENTED_VIDEO_ROUTE,
    DurationProfile,
    SEGMENTED_SEGMENT_SECONDS,
    segment_count_for_tier,
)
from app.features.posts.prompt_builder import build_segment_prompts

SEGMENT_STATUS_SUBMITTED = "submitted"
SEGMENT_STATUS_PROCESSING = "processing"
SEGMENT_STATUS_COMPLETED = "completed"
SEGMENT_STATUS_FAILED = "failed"
# A pre-seeded segment whose image-to-video op has not been submitted yet (waits on the anchor).
SEGMENT_STATUS_PENDING = "pending"

# Per-op "kind" on the segmented-route identity-lock variant (see ``build_segment_ops_with_anchor``).
SEGMENT_KIND_ANCHOR = "anchor"  # segment 0: text + reference images, establishes the actor
SEGMENT_KIND_I2V = "i2v"        # segments 1..N-1: image-to-video locked to the anchor frame

# ``i2v_lock.state`` lifecycle for the shared-frame identity lock.
I2V_STATE_PENDING = "pending"        # anchor in flight; i2v segments not submitted
I2V_STATE_SUBMITTING = "submitting"  # anchor done; mid-fan-out (resumable)
I2V_STATE_SUBMITTED = "submitted"    # all i2v segments submitted


@dataclass(frozen=True)
class SegmentSubmission:
    """One independent 8s reference-anchored generation to submit."""

    index: int
    prompt: str
    duration_seconds: int
    seed: Optional[int]


def plan_segment_submissions(
    *,
    profile: DurationProfile,
    segments: List[str],
    seed: Optional[int],
    prompts: Optional[List[str]] = None,
    character: Optional[str] = None,
    action: Optional[str] = None,
    style: Optional[str] = None,
    scene: Optional[str] = None,
    cinematography: Optional[str] = None,
    audio_block: Optional[str] = None,
    negative_constraints: Optional[str] = None,
    legacy_32_visuals: bool = False,
) -> List[SegmentSubmission]:
    """Build the ordered list of independent segment submissions for a segmented-route post.

    Each submission is a standalone 8s generation carrying the FULL character/scene context so the
    actor reference bundle re-anchors every segment. The same ``seed`` is threaded through all of
    them to minimize cross-segment variance.

    The per-segment prompt may be supplied via ``prompts`` (one entry per beat, already built with
    the mode-appropriate builder — e.g. the reference-image scene builder for character consistency).
    When ``prompts`` is omitted the generic ``build_segment_prompts`` builder is used.

    Raises:
        ValueError: if the route is not segmented or the segment/prompt count does not match.
    """
    if profile.route != VEO_SEGMENTED_VIDEO_ROUTE:
        raise ValueError(f"plan_segment_submissions requires the segmented route, got {profile.route}")
    expected = segment_count_for_tier(profile.target_length_tier)
    if len(segments) != expected:
        raise ValueError(
            f"segmented tier {profile.target_length_tier} expects {expected} segments, got {len(segments)}"
        )

    if prompts is not None:
        if len(prompts) != expected:
            raise ValueError(
                f"segmented tier {profile.target_length_tier} expects {expected} prompts, got {len(prompts)}"
            )
    else:
        prompts = build_segment_prompts(
            segments,
            character=character,
            action=action,
            style=style,
            scene=scene,
            cinematography=cinematography,
            audio_block=audio_block,
            negative_constraints=negative_constraints,
            legacy_32_visuals=legacy_32_visuals,
        )
    return [
        SegmentSubmission(
            index=index,
            prompt=prompt,
            duration_seconds=SEGMENTED_SEGMENT_SECONDS,
            seed=seed,
        )
        for index, prompt in enumerate(prompts)
    ]


def build_initial_segment_ops(operation_ids: List[str]) -> List[Dict[str, Any]]:
    """Create the ``veo_segment_ops`` array from the operation ids returned at submission time."""
    return [
        {
            "index": index,
            "operation_id": operation_id,
            "status": SEGMENT_STATUS_SUBMITTED,
            "video_uri": None,
        }
        for index, operation_id in enumerate(operation_ids)
    ]


def build_segment_ops_with_anchor(anchor_operation_id: str, segment_count: int) -> List[Dict[str, Any]]:
    """``veo_segment_ops`` for the identity-lock variant: segment 0 is the submitted anchor; segments
    1..N-1 are pre-seeded ``pending`` image-to-video placeholders (filled once the anchor completes).

    Pre-seeding all N rows keeps ``ordered_completed_segment_uris`` index-correct and makes
    ``all_segments_completed`` block the stitch until every i2v segment is submitted and completed.
    """
    ops: List[Dict[str, Any]] = [
        {
            "index": 0,
            "operation_id": anchor_operation_id,
            "status": SEGMENT_STATUS_SUBMITTED,
            "video_uri": None,
            "kind": SEGMENT_KIND_ANCHOR,
        }
    ]
    for index in range(1, segment_count):
        ops.append(
            {
                "index": index,
                "operation_id": None,
                "status": SEGMENT_STATUS_PENDING,
                "video_uri": None,
                "kind": SEGMENT_KIND_I2V,
            }
        )
    return ops


def build_i2v_lock(
    *,
    provider: str,
    aspect_ratio: str,
    provider_aspect_ratio: Optional[str],
    resolution: str,
    duration_seconds: int,
    model: Optional[str],
    output_gcs_uri: Optional[str],
    beats: List[str],
    anchor_segment_index: int = 0,
) -> Dict[str, Any]:
    """The pending plan the poller needs to submit segments 1..N-1 as image-to-video once the anchor
    completes. ``beats`` is the FULL per-segment dialogue list (index i → ``beats[i]``)."""
    return {
        "state": I2V_STATE_PENDING,
        "anchor_segment_index": anchor_segment_index,
        "provider": provider,
        "aspect_ratio": aspect_ratio,
        "provider_aspect_ratio": provider_aspect_ratio,
        "resolution": resolution,
        "duration_seconds": duration_seconds,
        "model": model,
        "output_gcs_uri": output_gcs_uri,
        "beats": list(beats),
    }


def _anchor_op(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    lock = (metadata or {}).get("i2v_lock") or {}
    anchor_index = int(lock.get("anchor_segment_index", 0))
    for op in (metadata or {}).get("veo_segment_ops") or []:
        if int(op.get("index", -1)) == anchor_index:
            return op
    return None


def i2v_plan_ready(metadata: Optional[Dict[str, Any]]) -> bool:
    """True when the anchor segment has completed and the i2v fan-out still owes submissions.

    Accepts ``submitting`` (not just ``pending``) so a crash mid-fan-out resumes on the next poll.
    """
    lock = (metadata or {}).get("i2v_lock")
    if not lock or lock.get("state") not in (I2V_STATE_PENDING, I2V_STATE_SUBMITTING):
        return False
    anchor = _anchor_op(metadata)
    return bool(anchor and anchor.get("status") == SEGMENT_STATUS_COMPLETED and anchor.get("video_uri"))


def pending_i2v_indexes(metadata: Optional[Dict[str, Any]]) -> List[int]:
    """Indexes of i2v segments not yet submitted (no operation id), ordered."""
    return [
        int(op.get("index"))
        for op in (metadata or {}).get("veo_segment_ops") or []
        if op.get("kind") == SEGMENT_KIND_I2V and not op.get("operation_id")
    ]


def _with_i2v_state(metadata: Dict[str, Any], state: str) -> Dict[str, Any]:
    new_metadata = dict(metadata)
    lock = dict(new_metadata.get("i2v_lock") or {})
    lock["state"] = state
    new_metadata["i2v_lock"] = lock
    return new_metadata


def mark_i2v_submitting(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return _with_i2v_state(metadata, I2V_STATE_SUBMITTING)


def mark_i2v_submitted(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return _with_i2v_state(metadata, I2V_STATE_SUBMITTED)


def record_i2v_submitted_op(
    metadata: Dict[str, Any], *, index: int, operation_id: str
) -> List[Dict[str, Any]]:
    """Return a new ``veo_segment_ops`` with the pending row at ``index`` filled with its op id (pure)."""
    updated: List[Dict[str, Any]] = []
    for op in metadata.get("veo_segment_ops") or []:
        if int(op.get("index", -1)) == index:
            new_op = dict(op)
            new_op["operation_id"] = operation_id
            new_op["status"] = SEGMENT_STATUS_SUBMITTED
            updated.append(new_op)
        else:
            updated.append(dict(op))
    return updated


def is_segmented_route(metadata: Optional[Dict[str, Any]]) -> bool:
    return bool(metadata) and metadata.get("video_pipeline_route") == VEO_SEGMENTED_VIDEO_ROUTE


def record_segment_result(
    metadata: Dict[str, Any],
    *,
    operation_id: str,
    status: str,
    video_uri: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a new ``veo_segment_ops`` list with the matching op updated (pure)."""
    updated: List[Dict[str, Any]] = []
    for op in metadata.get("veo_segment_ops") or []:
        if op.get("operation_id") == operation_id:
            new_op = dict(op)
            new_op["status"] = status
            if video_uri is not None:
                new_op["video_uri"] = video_uri
            updated.append(new_op)
        else:
            updated.append(dict(op))
    return updated


def any_segment_failed(metadata: Optional[Dict[str, Any]]) -> bool:
    if not metadata:
        return False
    return any(
        (op.get("status") == SEGMENT_STATUS_FAILED)
        for op in (metadata.get("veo_segment_ops") or [])
    )


def all_segments_completed(metadata: Optional[Dict[str, Any]]) -> bool:
    """True only when every expected segment op has completed with a usable video_uri."""
    if not is_segmented_route(metadata):
        return False
    ops = metadata.get("veo_segment_ops") or []
    expected = int(metadata.get("veo_segment_count") or 0)
    if expected <= 0 or len(ops) != expected:
        return False
    return all(
        op.get("status") == SEGMENT_STATUS_COMPLETED and op.get("video_uri")
        for op in ops
    )


def segment_stitch_ready(metadata: Optional[Dict[str, Any]]) -> bool:
    """The poller stitches exactly when all segments are complete and none failed."""
    return all_segments_completed(metadata) and not any_segment_failed(metadata)


def ordered_completed_segment_uris(metadata: Dict[str, Any]) -> List[str]:
    """Return segment video_uris ordered by segment index. Caller guarantees readiness."""
    ops = sorted(
        (metadata.get("veo_segment_ops") or []),
        key=lambda op: int(op.get("index", 0)),
    )
    return [str(op["video_uri"]) for op in ops]
