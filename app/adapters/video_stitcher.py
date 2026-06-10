"""
Video stitcher adapter — concatenate independent Veo segments into one clip.

The segmented route generates N standalone 8s reference-anchored segments (each re-attaching the
actor reference bundle, so identity never drifts across hops). This adapter joins them into the
final video with a single ffmpeg concat-filter pass that re-encodes and normalizes every segment to
a common resolution / SAR / frame rate, so minor inter-segment encoder differences cannot corrupt
the concatenation. Joins are hard cuts — the native grammar of talking-head UGC and masked by the
burned-in captions added downstream.

System ffmpeg/ffprobe are assumed in PATH (already required by the trim/crop/caption paths).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any, Dict, List, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

_FFMPEG_TIMEOUT_SECONDS = 300
_FFPROBE_TIMEOUT_SECONDS = 30
_DEFAULT_FPS = 24.0


def _probe_video_geometry(video_path: str) -> Tuple[int, int, float]:
    """Return (width, height, fps) for the first video stream."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate",
        "-of",
        "json",
        video_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise ValueError(f"ffprobe geometry failed: {result.stderr[-200:]}")
    stream = (json.loads(result.stdout).get("streams") or [{}])[0]
    width = int(stream["width"])
    height = int(stream["height"])
    fps = _parse_frame_rate(stream.get("r_frame_rate"))
    return width, height, fps


def _parse_frame_rate(raw: Any) -> float:
    text = str(raw or "").strip()
    if "/" in text:
        num, _, den = text.partition("/")
        try:
            numerator = float(num)
            denominator = float(den)
            if denominator > 0 and numerator > 0:
                return numerator / denominator
        except ValueError:
            return _DEFAULT_FPS
    try:
        value = float(text)
        return value if value > 0 else _DEFAULT_FPS
    except ValueError:
        return _DEFAULT_FPS


def _probe_duration(video_path: str) -> float:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise ValueError(f"ffprobe duration failed: {result.stderr[-200:]}")
    return float(result.stdout.strip())


def stitch_segments(
    *,
    segment_videos: List[bytes],
    post_id: str,
    correlation_id: str,
) -> Tuple[bytes, Dict[str, Any]]:
    """Concatenate ordered segment videos into one mp4.

    Args:
        segment_videos: Ordered raw mp4 bytes, one per segment. Must be non-empty.
        post_id: Owning post id for logging.
        correlation_id: Correlation id for structured logging.

    Returns:
        (final_video_bytes, stitch_metadata).

    Raises:
        ValueError: empty input or ffmpeg/ffprobe failure.
    """
    if not segment_videos:
        raise ValueError("stitch_segments requires at least one segment")

    # A single segment needs no concatenation — return it untouched.
    if len(segment_videos) == 1:
        logger.info(
            "stitch_single_segment_passthrough",
            post_id=post_id,
            correlation_id=correlation_id,
        )
        return segment_videos[0], {"stitch_segment_count": 1, "stitch_applied": False}

    with tempfile.TemporaryDirectory(prefix="video_stitch_") as temp_dir:
        input_paths: List[str] = []
        for index, video_bytes in enumerate(segment_videos):
            if not video_bytes:
                raise ValueError(f"Segment {index} for post {post_id} is empty")
            input_path = os.path.join(temp_dir, f"segment_{index}.mp4")
            with open(input_path, "wb") as file_obj:
                file_obj.write(video_bytes)
            input_paths.append(input_path)

        # Normalize every segment to the first segment's geometry before concatenating so small
        # encoder differences between independent generations cannot break the concat filter.
        width, height, fps = _probe_video_geometry(input_paths[0])
        segment_durations = [_probe_duration(path) for path in input_paths]

        command: List[str] = ["ffmpeg", "-y"]
        for path in input_paths:
            command += ["-i", path]

        filter_parts: List[str] = []
        concat_inputs: List[str] = []
        for index in range(len(input_paths)):
            filter_parts.append(
                f"[{index}:v]scale={width}:{height},setsar=1,fps={fps:.5f},format=yuv420p[v{index}]"
            )
            filter_parts.append(f"[{index}:a]aresample=async=1[a{index}]")
            concat_inputs.append(f"[v{index}][a{index}]")
        filter_parts.append(
            "".join(concat_inputs) + f"concat=n={len(input_paths)}:v=1:a=1[vout][aout]"
        )
        filter_complex = ";".join(filter_parts)

        output_path = os.path.join(temp_dir, "stitched.mp4")
        command += [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            output_path,
        ]

        result = subprocess.run(
            command, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SECONDS
        )
        if result.returncode != 0:
            # stderr can be long; keep the tail and never leak full file paths to callers.
            raise ValueError(f"ffmpeg concat failed: {result.stderr[-400:]}")

        final_duration = _probe_duration(output_path)
        with open(output_path, "rb") as file_obj:
            final_bytes = file_obj.read()

    stitch_metadata = {
        "stitch_applied": True,
        "stitch_segment_count": len(segment_videos),
        "stitch_segment_durations_s": [round(value, 3) for value in segment_durations],
        "stitch_final_duration_s": round(final_duration, 3),
        "stitch_width": width,
        "stitch_height": height,
        "stitch_fps": round(fps, 3),
    }
    logger.info(
        "stitch_segments_completed",
        post_id=post_id,
        correlation_id=correlation_id,
        **stitch_metadata,
    )
    return final_bytes, stitch_metadata
