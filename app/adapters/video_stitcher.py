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
import math
import os
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.features.shot_production.audio_seams import (
    MAX_EXACT_DELIVERY_RETIME_RATIO,
)
from app.features.shot_production.duration import (
    EXACT_SHORT_FORM_DURATION_SECONDS,
    SEMANTIC_END_PAN_TAIL_EXCLUSION_SECONDS,
)

logger = get_logger(__name__)

_FFMPEG_TIMEOUT_SECONDS = 300
_FFPROBE_TIMEOUT_SECONDS = 30
_DEFAULT_FPS = 24.0
_I2V_HEAD_TRIM_SECONDS = 0.0
_NON_FINAL_TAIL_TRIM_SECONDS = 0.0
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


def _probe_av_stream_durations(video_path: str) -> Tuple[float, float]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,duration:format=duration",
        "-of",
        "json",
        video_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise ValueError(f"ffprobe stream duration failed: {result.stderr[-200:]}")
    payload = json.loads(result.stdout)
    fallback = float((payload.get("format") or {})["duration"])
    durations: Dict[str, float] = {}
    for stream in payload.get("streams") or []:
        stream_type = str(stream.get("codec_type") or "")
        try:
            duration = float(stream.get("duration"))
        except (TypeError, ValueError):
            duration = fallback
        if stream_type in {"video", "audio"} and duration > 0:
            durations.setdefault(stream_type, duration)
    return durations.get("video", fallback), durations.get("audio", fallback)


def _even_dimension(value: float) -> int:
    rounded = max(2, int(round(value)))
    return rounded if rounded % 2 == 0 else rounded + 1


def _coerce_seconds(value: Any) -> Optional[float]:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _trim_window(
    index: int,
    count: int,
    duration: float,
    *,
    trim_windows: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[float, float, str]:
    if trim_windows and index < len(trim_windows) and isinstance(trim_windows[index], dict):
        window = trim_windows[index]
        start = _coerce_seconds(window.get("start_seconds"))
        end = _coerce_seconds(window.get("end_seconds"))
        if end is not None:
            resolved_start = min(start or 0.0, max(duration - _MIN_TRIMMED_SEGMENT_SECONDS, 0.0))
            resolved_end = min(duration, max(resolved_start + _MIN_TRIMMED_SEGMENT_SECONDS, end))
            return resolved_start, resolved_end, str(window.get("source") or "provided")

    head = _I2V_HEAD_TRIM_SECONDS if index > 0 else 0.0
    tail = _NON_FINAL_TAIL_TRIM_SECONDS if index < count - 1 else 0.0
    start = min(head, max(duration - _MIN_TRIMMED_SEGMENT_SECONDS, 0.0))
    end = max(start + _MIN_TRIMMED_SEGMENT_SECONDS, duration - tail)
    return start, min(duration, end), "default"


def _reframe_filter(index: int, width: int, height: int) -> Tuple[str, str]:
    name, zoom, x_anchor, y_anchor = _REFRAME_PROFILES[0]
    scaled_width = _even_dimension(width * zoom)
    scaled_height = _even_dimension(height * zoom)
    crop_x = max(0, int(round((scaled_width - width) * x_anchor)))
    crop_y = max(0, int(round((scaled_height - height) * y_anchor)))
    return name, f"scale={scaled_width}:{scaled_height},crop={width}:{height}:{crop_x}:{crop_y}"


def _finite_plan_seconds(value: Any, *, field: str) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Acoustic plan {field} must be finite") from exc
    if not math.isfinite(seconds):
        raise ValueError(f"Acoustic plan {field} must be finite")
    return seconds


def _validate_acoustic_plan(
    acoustic_plan: Dict[str, Any],
    *,
    count: int,
    durations: List[float],
) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
    if not isinstance(acoustic_plan, dict):
        raise ValueError("Acoustic plan must be a mapping")
    raw_takes = acoustic_plan.get("takes")
    raw_seams = acoustic_plan.get("seams")
    if not isinstance(raw_takes, (list, tuple)) or len(raw_takes) != count:
        raise ValueError("Acoustic plan take count must match segment count")
    if not isinstance(raw_seams, (list, tuple)) or len(raw_seams) != count - 1:
        raise ValueError("Acoustic plan seam count must match segment count")
    takes: List[Dict[str, float]] = []
    for index, raw_take in enumerate(raw_takes):
        if not isinstance(raw_take, dict):
            raise ValueError("Acoustic plan takes must be mappings")
        take = {
            field: _finite_plan_seconds(raw_take.get(field), field=field)
            for field in (
                "audio_start_seconds",
                "audio_end_seconds",
                "video_start_seconds",
                "video_end_seconds",
                "gain_db",
            )
        }
        if not (
            0 <= take["audio_start_seconds"] < take["audio_end_seconds"] <= durations[index] + 0.05
            and 0 <= take["video_start_seconds"] < take["video_end_seconds"] <= durations[index] + 0.05
        ):
            raise ValueError(f"Acoustic plan take {index} windows are outside the source duration")
        if not -2.0 <= take["gain_db"] <= 2.0:
            raise ValueError(f"Acoustic plan take {index} gain exceeds the allowed clamp")
        takes.append(take)
    seams: List[Dict[str, float]] = []
    for index, raw_seam in enumerate(raw_seams):
        if not isinstance(raw_seam, dict):
            raise ValueError("Acoustic plan seams must be mappings")
        overlap = _finite_plan_seconds(raw_seam.get("overlap_seconds"), field="overlap")
        visual_position = _finite_plan_seconds(
            raw_seam.get("visual_cut_position_seconds"), field="visual cut position"
        )
        if not 0.04 - 1e-9 <= overlap <= 0.07 + 1e-9:
            raise ValueError(f"Acoustic plan seam {index} overlap is outside 40-70 ms")
        if not 0 <= visual_position <= overlap:
            raise ValueError(f"Acoustic plan seam {index} visual cut must sit inside overlap")
        seams.append(
            {
                "overlap_seconds": overlap,
                "visual_cut_position_seconds": visual_position,
            }
        )
    return takes, seams


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
    trim_windows: Optional[List[Dict[str, Any]]] = None,
    acoustic_plan: Optional[Dict[str, Any]] = None,
    target_duration_seconds: Optional[float] = None,
) -> Tuple[bytes, Dict[str, Any]]:
    """Concatenate ordered segment videos into one mp4.

    Args:
        segment_videos: Ordered raw mp4 bytes, one per segment. Must be non-empty.
        post_id: Owning post id for logging.
        correlation_id: Correlation id for structured logging.
        trim_windows: Optional per-segment start/end seconds. When present, each segment is
            trimmed to its spoken window before concatenation.
        acoustic_plan: Optional validated native-audio plan with independent audio/video windows.
        target_duration_seconds: Optional exact delivery target. Transcript-bearing content is
            never shortened by more than one source frame. Cadence-safe source windows are used
            first, then a bounded whole-output A/V retime may fill the remaining duration. At most
            one frame/sample interval may be resolved as encoder rounding.

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
        planned_takes: Optional[List[Dict[str, float]]] = None
        planned_seams: Optional[List[Dict[str, float]]] = None
        if acoustic_plan is not None:
            planned_takes, planned_seams = _validate_acoustic_plan(
                acoustic_plan,
                count=len(input_paths),
                durations=segment_durations,
            )
        plan_target = acoustic_plan.get("target_duration_seconds") if acoustic_plan else None
        if target_duration_seconds is None and plan_target is not None:
            target_duration_seconds = _finite_plan_seconds(
                plan_target,
                field="target duration",
            )
        elif target_duration_seconds is not None:
            target_duration_seconds = _finite_plan_seconds(
                target_duration_seconds,
                field="target duration",
            )
            if plan_target is not None and abs(
                target_duration_seconds
                - _finite_plan_seconds(plan_target, field="target duration")
            ) > 1e-6:
                raise ValueError("Acoustic plan target duration does not match stitch target")
        if target_duration_seconds is not None and target_duration_seconds <= 0:
            raise ValueError("Stitch target duration must be positive")
        delivery_retime_ratio = 1.0
        if acoustic_plan is not None and acoustic_plan.get("delivery_retime_ratio") is not None:
            delivery_retime_ratio = _finite_plan_seconds(
                acoustic_plan.get("delivery_retime_ratio"),
                field="delivery retime ratio",
            )
        if not 1.0 <= delivery_retime_ratio <= MAX_EXACT_DELIVERY_RETIME_RATIO + 1e-9:
            raise ValueError("Acoustic plan delivery retime ratio exceeds the bounded allowance")
        if delivery_retime_ratio > 1.0 + 1e-9 and (
            target_duration_seconds is None or planned_seams is None
        ):
            raise ValueError("Bounded delivery retime requires a targeted acoustic plan")

        command: List[str] = ["ffmpeg", "-y"]
        for path in input_paths:
            command += ["-i", path]

        filter_parts: List[str] = []
        concat_inputs: List[str] = []
        head_trims: List[float] = []
        tail_trims: List[float] = []
        audio_window_durations: List[float] = []
        trim_sources: List[str] = []
        reframe_names: List[str] = []
        for index in range(len(input_paths)):
            if planned_takes is not None:
                plan_take = planned_takes[index]
                audio_start = plan_take["audio_start_seconds"]
                audio_end = plan_take["audio_end_seconds"]
                video_start = plan_take["video_start_seconds"]
                video_end = plan_take["video_end_seconds"]
                gain_db = plan_take["gain_db"]
                trim_source = "acoustic_seam_plan"
            else:
                start, end, trim_source = _trim_window(
                    index,
                    len(input_paths),
                    segment_durations[index],
                    trim_windows=trim_windows,
                )
                audio_start = video_start = start
                audio_end = video_end = end
                gain_db = 0.0
            head_trims.append(round(audio_start, 3))
            tail_trims.append(round(max(segment_durations[index] - audio_end, 0.0), 3))
            audio_window_durations.append(audio_end - audio_start)
            trim_sources.append(trim_source)
            reframe_name, reframe = _reframe_filter(index, width, height)
            reframe_names.append(reframe_name)
            filter_parts.append(
                f"[{index}:v]trim=start={video_start:.3f}:end={video_end:.3f},setpts=PTS-STARTPTS,"
                f"{reframe},setsar=1,fps={fps:.5f},format=yuv420p[v{index}]"
            )
            filter_parts.append(
                f"[{index}:a]atrim=start={audio_start:.3f}:end={audio_end:.3f},asetpts=PTS-STARTPTS,"
                f"volume={gain_db:.3f}dB,aresample=48000:async=1[a{index}]"
            )
            concat_inputs.append(f"[v{index}][a{index}]")
        content_duration = sum(audio_window_durations)
        base_video_label = "vout"
        if planned_seams is None:
            concat_video_label = "vbase" if target_duration_seconds is not None else "vout"
            concat_audio_label = "abase" if target_duration_seconds is not None else "aout"
            filter_parts.append(
                "".join(concat_inputs)
                + f"concat=n={len(input_paths)}:v=1:a=1[{concat_video_label}][{concat_audio_label}]"
            )
            base_video_label = concat_video_label
            final_audio_label = concat_audio_label
        else:
            video_inputs = "".join(f"[v{index}]" for index in range(len(input_paths)))
            planned_audio_duration = sum(
                take["audio_end_seconds"] - take["audio_start_seconds"]
                for take in planned_takes
            ) - sum(seam["overlap_seconds"] for seam in planned_seams)
            content_duration = planned_audio_duration
            filter_parts.append(f"{video_inputs}concat=n={len(input_paths)}:v=1:a=0[vcat]")
            base_video_label = "vbase" if target_duration_seconds is not None else "vout"
            filter_parts.append(
                f"[vcat]trim=duration={planned_audio_duration:.6f},setpts=PTS-STARTPTS"
                f"[{base_video_label}]"
            )
            final_audio_label = "a0"
            for index in range(1, len(input_paths)):
                output_label = f"ax{index}"
                overlap = planned_seams[index - 1]["overlap_seconds"]
                filter_parts.append(
                    f"[{final_audio_label}][a{index}]acrossfade=d={overlap:.3f}:o=1:"
                    f"c1=qsin:c2=qsin[{output_label}]"
                )
                final_audio_label = output_label
        native_shortfall_seconds = 0.0
        delivery_padding_seconds = 0.0
        delivery_audio_tempo = 1.0
        delivery_mode = "native"
        end_pan_protection_applied = False
        end_pan_retime_ratio = 1.0
        final_video_label = "vout"
        if target_duration_seconds is not None:
            frame_duration_seconds = 1.0 / fps
            if content_duration > target_duration_seconds + frame_duration_seconds + 1e-6:
                raise ValueError(
                    "Stitch target would truncate transcript-bearing content by more than one frame"
                )
            native_shortfall_seconds = max(
                0.0,
                target_duration_seconds - content_duration,
            )
            declared_content_duration = None
            if acoustic_plan is not None and acoustic_plan.get("content_duration_seconds") is not None:
                declared_content_duration = _finite_plan_seconds(
                    acoustic_plan.get("content_duration_seconds"),
                    field="content duration",
                )
                if abs(declared_content_duration - content_duration) > 1e-4:
                    raise ValueError(
                        "Acoustic plan content duration does not match its native source windows"
                    )
            declared_padding = None
            if acoustic_plan is not None and acoustic_plan.get("delivery_padding_seconds") is not None:
                declared_padding = _finite_plan_seconds(
                    acoustic_plan.get("delivery_padding_seconds"),
                    field="delivery padding",
                )
                if declared_padding < 0.0:
                    raise ValueError("Acoustic plan delivery padding cannot be negative")
            if delivery_retime_ratio > 1.0 + 1e-9:
                expected_retime_ratio = target_duration_seconds / content_duration
                if abs(delivery_retime_ratio - expected_retime_ratio) > 1e-4:
                    raise ValueError(
                        "Acoustic plan delivery retime ratio does not match its native source windows"
                    )
                if declared_padding is not None and declared_padding > 1e-9:
                    raise ValueError(
                        "Bounded A/V retime cannot be combined with synthetic delivery padding"
                    )
                delivery_audio_tempo = 1.0 / delivery_retime_ratio
                delivery_mode = "bounded_av_retime"
                filter_parts.append(
                    f"[{base_video_label}]setpts={delivery_retime_ratio:.9f}*PTS,"
                    f"fps={fps:.5f},trim=duration={target_duration_seconds:.6f},"
                    "setpts=PTS-STARTPTS[vout]"
                )
                filter_parts.append(
                    f"[{final_audio_label}]atempo={delivery_audio_tempo:.9f},"
                    f"atrim=duration={target_duration_seconds:.6f},"
                    "asetpts=PTS-STARTPTS[adelivery]"
                )
            else:
                delivery_padding_seconds = native_shortfall_seconds
                if (
                    delivery_padding_seconds > frame_duration_seconds + 1e-6
                    or (
                        declared_padding is not None
                        and declared_padding > frame_duration_seconds + 1e-6
                    )
                ):
                    raise ValueError(
                        "Exact delivery would require more than one frame of synthetic padding"
                    )
                if (
                    declared_padding is not None
                    and abs(declared_padding - delivery_padding_seconds) > 1e-4
                ):
                    raise ValueError(
                        "Acoustic plan delivery padding does not match its native source windows"
                    )
            if delivery_mode == "native" and delivery_padding_seconds > 1e-9:
                delivery_mode = "encoder_rounding"
                filter_parts.append(
                    f"[{base_video_label}]tpad=stop_mode=clone:"
                    f"stop_duration={frame_duration_seconds:.6f},"
                    f"trim=duration={target_duration_seconds:.6f},"
                    "setpts=PTS-STARTPTS[vout]"
                )
                filter_parts.append(
                    f"[{final_audio_label}]apad=pad_dur={frame_duration_seconds:.6f},"
                    f"atrim=duration={target_duration_seconds:.6f},"
                    "asetpts=PTS-STARTPTS[adelivery]"
                )
            elif delivery_mode == "native":
                delivery_mode = "native_trim"
                filter_parts.append(
                    f"[{base_video_label}]trim=duration={target_duration_seconds:.6f},"
                    "setpts=PTS-STARTPTS[vout]"
                )
                filter_parts.append(
                    f"[{final_audio_label}]atrim=duration={target_duration_seconds:.6f},"
                    "asetpts=PTS-STARTPTS[adelivery]"
                )
            final_audio_label = "adelivery"

        if (
            acoustic_plan is not None
            and target_duration_seconds is not None
            and math.isclose(
                target_duration_seconds,
                EXACT_SHORT_FORM_DURATION_SECONDS,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
        ):
            protected_content_duration = (
                target_duration_seconds - SEMANTIC_END_PAN_TAIL_EXCLUSION_SECONDS
            )
            if protected_content_duration <= 0:
                raise ValueError("End-pan protection requires a positive retained duration")
            end_pan_retime_ratio = target_duration_seconds / protected_content_duration
            end_pan_audio_tempo = 1.0 / end_pan_retime_ratio
            filter_parts.append(
                f"[vout]trim=duration={protected_content_duration:.6f},"
                "setpts=PTS-STARTPTS,"
                f"setpts={end_pan_retime_ratio:.9f}*PTS,fps={fps:.5f},"
                f"trim=duration={target_duration_seconds:.6f},"
                "setpts=PTS-STARTPTS[vprotected]"
            )
            filter_parts.append(
                f"[{final_audio_label}]atrim=duration={protected_content_duration:.6f},"
                "asetpts=PTS-STARTPTS,"
                f"atempo={end_pan_audio_tempo:.9f},"
                f"atrim=duration={target_duration_seconds:.6f},"
                "asetpts=PTS-STARTPTS[aprotected]"
            )
            final_video_label = "vprotected"
            final_audio_label = "aprotected"
            end_pan_protection_applied = True
        filter_complex = ";".join(filter_parts)

        output_path = os.path.join(temp_dir, "stitched.mp4")
        command += [
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{final_video_label}]",
            "-map",
            f"[{final_audio_label}]",
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
        video_duration, audio_duration = _probe_av_stream_durations(output_path)
        duration_delta = abs(video_duration - audio_duration)
        if (
            target_duration_seconds is not None
            and abs(final_duration - target_duration_seconds) > 1.0 / fps + 1e-6
        ):
            raise ValueError(
                "Exact stitch duration exceeded one frame of the delivery target"
            )
        if planned_seams is not None and duration_delta > 1.0 / fps + 1e-6:
            raise ValueError(
                f"Acoustic stitch audio/video duration drift exceeded one frame: {duration_delta:.6f}s"
            )
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
        "stitch_cut_softening_applied": planned_seams is not None,
        "stitch_head_trim_s": head_trims,
        "stitch_tail_trim_s": tail_trims,
        "stitch_trim_window_source": trim_sources,
        "stitch_reframe_profile": reframe_names,
        "stitch_audio_overlap_s": [
            round(seam["overlap_seconds"], 3) for seam in (planned_seams or [])
        ],
        "stitch_visual_cut_position_s": [
            round(seam["visual_cut_position_seconds"], 3) for seam in (planned_seams or [])
        ],
        "stitch_gain_db": [
            round(take["gain_db"], 3) for take in (planned_takes or [])
        ],
        "stitch_audio_duration_s": round(audio_duration, 3),
        "stitch_video_duration_s": round(video_duration, 3),
        "stitch_audio_video_duration_delta_s": round(duration_delta, 6),
        "stitch_content_duration_s": round(content_duration, 6),
        "stitch_delivery_target_s": (
            round(target_duration_seconds, 6)
            if target_duration_seconds is not None
            else None
        ),
        "stitch_delivery_padding_s": round(delivery_padding_seconds, 6),
        "stitch_delivery_native_shortfall_s": round(native_shortfall_seconds, 6),
        "stitch_delivery_retime_ratio": round(delivery_retime_ratio, 9),
        "stitch_delivery_audio_tempo": round(delivery_audio_tempo, 9),
        "stitch_delivery_mode": delivery_mode,
        "stitch_end_pan_protection_applied": end_pan_protection_applied,
        "stitch_end_pan_tail_exclusion_s": (
            SEMANTIC_END_PAN_TAIL_EXCLUSION_SECONDS
            if end_pan_protection_applied
            else 0.0
        ),
        "stitch_end_pan_retime_ratio": round(end_pan_retime_ratio, 9),
    }
    logger.info(
        "stitch_segments_completed",
        post_id=post_id,
        correlation_id=correlation_id,
        **stitch_metadata,
    )
    return final_bytes, stitch_metadata
