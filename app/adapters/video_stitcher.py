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
_I2V_HEAD_TRIM_SECONDS = 0.18
_NON_FINAL_TAIL_TRIM_SECONDS = 0.35
_MIN_TRIMMED_SEGMENT_SECONDS = 1.0
_REFRAME_PROFILES: List[Tuple[str, float, float, float]] = [
    ("full", 1.0, 0.5, 0.5),
    ("punch_in_center", 1.14, 0.5, 0.5),
    ("punch_in_left", 1.09, 0.36, 0.5),
    ("punch_in_right", 1.09, 0.64, 0.5),
    ("tight_center", 1.17, 0.5, 0.43),
]


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


def _even_dimension(value: float) -> int:
    rounded = max(2, int(round(value)))
    return rounded if rounded % 2 == 0 else rounded + 1


def _trim_window(index: int, count: int, duration: float) -> Tuple[float, float]:
    head = _I2V_HEAD_TRIM_SECONDS if index > 0 else 0.0
    tail = _NON_FINAL_TAIL_TRIM_SECONDS if index < count - 1 else 0.0
    start = min(head, max(duration - _MIN_TRIMMED_SEGMENT_SECONDS, 0.0))
    end = max(start + _MIN_TRIMMED_SEGMENT_SECONDS, duration - tail)
    return start, min(duration, end)


def _reframe_filter(index: int, width: int, height: int) -> Tuple[str, str]:
    name, zoom, x_anchor, y_anchor = _REFRAME_PROFILES[index % len(_REFRAME_PROFILES)]
    scaled_width = _even_dimension(width * zoom)
    scaled_height = _even_dimension(height * zoom)
    crop_x = max(0, int(round((scaled_width - width) * x_anchor)))
    crop_y = max(0, int(round((scaled_height - height) * y_anchor)))
    return name, f"scale={scaled_width}:{scaled_height},crop={width}:{height}:{crop_x}:{crop_y}"


def extract_anchor_frame(
    *, video_bytes: bytes, post_id: str, correlation_id: str, at_fraction: float = 0.5
) -> Tuple[bytes, str]:
    """Extract one frame from a segment video as JPEG bytes, at the given fraction of its duration.

    Used by the identity-lock route: the returned frame anchors the actor for the image-to-video
    segments. ``at_fraction`` (clamped to [0.05, 0.95]) picks where to seek — the default mid-point
    avoids fade-in/black (first frame) and end-of-clip settle/blink (last frame); the i2v fan-out
    passes a distinct fraction per segment so each cut lands on a different pose (natural jump-cut).

    Returns:
        (jpeg_bytes, "image/jpeg").

    Raises:
        ValueError: empty input, ffmpeg failure, or empty output.
    """
    if not video_bytes:
        raise ValueError(f"extract_anchor_frame got empty video for post {post_id}")

    fraction = min(0.95, max(0.05, at_fraction))

    with tempfile.TemporaryDirectory(prefix="anchor_frame_") as temp_dir:
        input_path = os.path.join(temp_dir, "anchor_source.mp4")
        with open(input_path, "wb") as file_obj:
            file_obj.write(video_bytes)

        try:
            seek_seconds = max(0.0, _probe_duration(input_path) * fraction)
        except ValueError:
            seek_seconds = 0.0  # unprobeable container → fall back to the first frame

        output_path = os.path.join(temp_dir, "anchor_frame.jpg")
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{seek_seconds:.3f}",
            "-i",
            input_path,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            output_path,
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SECONDS
        )
        if result.returncode != 0:
            raise ValueError(f"ffmpeg anchor-frame extract failed: {result.stderr[-400:]}")
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise ValueError(f"ffmpeg produced no anchor frame for post {post_id}")
        with open(output_path, "rb") as file_obj:
            frame_bytes = file_obj.read()

    logger.info(
        "anchor_frame_extracted",
        post_id=post_id,
        correlation_id=correlation_id,
        at_fraction=round(fraction, 3),
        seek_seconds=round(seek_seconds, 3),
        frame_bytes=len(frame_bytes),
    )
    return frame_bytes, "image/jpeg"


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
        head_trims: List[float] = []
        tail_trims: List[float] = []
        reframe_names: List[str] = []
        for index in range(len(input_paths)):
            start, end = _trim_window(index, len(input_paths), segment_durations[index])
            head_trims.append(round(start, 3))
            tail_trims.append(round(max(segment_durations[index] - end, 0.0), 3))
            reframe_name, reframe = _reframe_filter(index, width, height)
            reframe_names.append(reframe_name)
            filter_parts.append(
                f"[{index}:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
                f"{reframe},setsar=1,fps={fps:.5f},format=yuv420p[v{index}]"
            )
            filter_parts.append(
                f"[{index}:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=async=1[a{index}]"
            )
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
        "stitch_cut_softening_applied": True,
        "stitch_head_trim_s": head_trims,
        "stitch_tail_trim_s": tail_trims,
        "stitch_reframe_profile": reframe_names,
    }
    logger.info(
        "stitch_segments_completed",
        post_id=post_id,
        correlation_id=correlation_id,
        **stitch_metadata,
    )
    return final_bytes, stitch_metadata
