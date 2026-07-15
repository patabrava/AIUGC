"""Deterministic acoustic evidence and seam planning for semantic Veo takes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.core.errors import ValidationError


ACOUSTIC_ANALYZER_VERSION = "native-acoustic-seams-v1"
_ANALYSIS_TIMEOUT_SECONDS = 120
_MAX_SEAM_WORD_GAP_SECONDS = 0.320
_DIGITAL_SILENCE_DBFS = -120.0
MAX_PERCEPTUAL_SEAM_ENERGY_DELTA_DB = 12.0
_PREFERRED_SEAM_ENERGY_DELTA_DB = 6.0
_FRAME_TAGS = {
    "rms_dbfs": "lavfi.astats.1.RMS_level",
    "peak_dbfs": "lavfi.astats.1.Peak_level",
    "zero_crossing_rate": "lavfi.astats.1.Zero_crossings_rate",
    "spectral_centroid_hz": "lavfi.aspectralstats.1.centroid",
    "spectral_flatness": "lavfi.aspectralstats.1.flatness",
}


@dataclass(frozen=True)
class AudioFrameMetrics:
    timestamp_seconds: float
    rms_dbfs: float
    peak_dbfs: float
    zero_crossing_rate: float
    spectral_centroid_hz: float
    spectral_flatness: float


@dataclass(frozen=True)
class TakeAudioEvidence:
    take_index: int
    provider_duration_seconds: float
    first_word_start_seconds: float
    final_word_end_seconds: float
    frames: Tuple[AudioFrameMetrics, ...]


@dataclass(frozen=True)
class PlannedTakeWindow:
    take_index: int
    audio_start_seconds: float
    audio_end_seconds: float
    video_start_seconds: float
    video_end_seconds: float
    gain_db: float


@dataclass(frozen=True)
class PlannedSeam:
    seam_index: int
    previous_audio_end_seconds: float
    next_audio_start_seconds: float
    overlap_seconds: float
    visual_cut_position_seconds: float
    final_word_gap_seconds: float
    short_window_energy_delta_db: float
    retained_island_duration_seconds: float
    speech_overlap: bool
    rejected_candidates: Tuple[Dict[str, object], ...]
    energy_fallback: bool = False


@dataclass(frozen=True)
class AcousticSeamPlan:
    analyzer_version: str
    takes: Tuple[PlannedTakeWindow, ...]
    seams: Tuple[PlannedSeam, ...]
    active_speech_rms_range_db: float
    final_duration_seconds: float


def acoustic_analysis_cache_key(
    media_sha256: str,
    ffmpeg_version: str,
    analyzer_version: str = ACOUSTIC_ANALYZER_VERSION,
) -> str:
    values = tuple(str(value or "").strip() for value in (media_sha256, ffmpeg_version, analyzer_version))
    if any(not value for value in values):
        raise ValidationError("Acoustic analysis cache identity requires non-empty values.")
    return sha256("\n".join(values).encode("utf-8")).hexdigest()


def _finite_tag(tags: Dict[str, Any], key: str) -> float:
    try:
        value = float(tags[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            "Acoustic frame evidence is missing a required finite tag.",
            {"tag": key},
        ) from exc
    if not math.isfinite(value):
        raise ValidationError(
            "Acoustic frame evidence contains a non-finite tag.",
            {"tag": key},
        )
    return value


def _dbfs_tag(tags: Dict[str, Any], key: str) -> float:
    raw_value = tags.get(key)
    if isinstance(raw_value, str) and raw_value.strip().lower() == "-inf":
        return _DIGITAL_SILENCE_DBFS
    return _finite_tag(tags, key)


def parse_frame_metrics(payload: Any) -> Tuple[AudioFrameMetrics, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("frames"), list) or not payload["frames"]:
        raise ValidationError("Acoustic frame analysis returned no frames.")
    parsed = []
    previous_timestamp: Optional[float] = None
    for index, frame in enumerate(payload["frames"]):
        if not isinstance(frame, dict) or not isinstance(frame.get("tags"), dict):
            raise ValidationError("Acoustic frame evidence must contain frame tags.", {"frame": index})
        timestamp = _finite_tag(frame, "pts_time")
        if timestamp < 0 or (previous_timestamp is not None and timestamp <= previous_timestamp):
            raise ValidationError(
                "Acoustic frame timestamps must be non-negative and strictly increasing.",
                {"frame": index},
            )
        tags = frame["tags"]
        metric = AudioFrameMetrics(
            timestamp_seconds=timestamp,
            rms_dbfs=_dbfs_tag(tags, _FRAME_TAGS["rms_dbfs"]),
            peak_dbfs=_dbfs_tag(tags, _FRAME_TAGS["peak_dbfs"]),
            zero_crossing_rate=_finite_tag(tags, _FRAME_TAGS["zero_crossing_rate"]),
            spectral_centroid_hz=_finite_tag(tags, _FRAME_TAGS["spectral_centroid_hz"]),
            spectral_flatness=_finite_tag(tags, _FRAME_TAGS["spectral_flatness"]),
        )
        if not 0 <= metric.zero_crossing_rate <= 1:
            raise ValidationError("Acoustic zero-crossing rate must be between zero and one.")
        if metric.spectral_centroid_hz < 0 or not 0 <= metric.spectral_flatness <= 1:
            raise ValidationError("Acoustic spectral evidence is outside its valid range.")
        parsed.append(metric)
        previous_timestamp = timestamp
    return tuple(parsed)


def _escape_lavfi_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def analyze_audio_frames(
    media_path: Path,
    *,
    run_fn: Optional[Callable[..., Any]] = None,
) -> Tuple[AudioFrameMetrics, ...]:
    path = Path(media_path)
    if not path.is_file():
        raise ValidationError("Acoustic analysis requires an existing media file.")
    filter_graph = (
        f"amovie='{_escape_lavfi_path(path)}',"
        "aformat=sample_rates=16000:channel_layouts=mono,"
        "aspectralstats=win_size=512:overlap=0.5,"
        "astats=metadata=1:reset=1"
    )
    command = [
        "ffprobe",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        filter_graph,
        "-show_frames",
        "-show_entries",
        "frame=pts_time:frame_tags",
        "-of",
        "json",
    ]
    runner = run_fn or subprocess.run
    result = runner(
        command,
        capture_output=True,
        text=True,
        timeout=_ANALYSIS_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise ValidationError(
            "Acoustic frame analysis failed.",
            {"stderr": str(result.stderr or "")[-400:]},
        )
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("Acoustic frame analysis returned invalid JSON.") from exc
    return parse_frame_metrics(payload)


def _validate_take_evidence(takes: Sequence[TakeAudioEvidence]) -> Tuple[TakeAudioEvidence, ...]:
    if not isinstance(takes, (list, tuple)) or len(takes) < 2:
        raise ValidationError("Acoustic seam planning requires at least two takes.")
    ordered = tuple(sorted(takes, key=lambda take: take.take_index))
    if [take.take_index for take in ordered] != list(range(len(ordered))):
        raise ValidationError("Acoustic take indexes must be consecutive from zero.")
    for take in ordered:
        values = (
            take.provider_duration_seconds,
            take.first_word_start_seconds,
            take.final_word_end_seconds,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValidationError("Acoustic take timing values must be finite.")
        if not (
            0.0 < take.first_word_start_seconds
            < take.final_word_end_seconds
            < take.provider_duration_seconds
        ):
            raise ValidationError(
                "Acoustic take word timings must fit inside provider duration.",
                {"take_index": take.take_index},
            )
        if not take.frames:
            raise ValidationError("Acoustic take evidence requires frame metrics.")
    return ordered


def _active_speech_rms(take: TakeAudioEvidence) -> float:
    values = [
        frame.rms_dbfs
        for frame in take.frames
        if take.first_word_start_seconds <= frame.timestamp_seconds <= take.final_word_end_seconds
        and frame.rms_dbfs > -45.0
    ]
    if not values:
        raise ValidationError(
            "Acoustic take has no measurable active speech.",
            {"take_index": take.take_index},
        )
    return float(median(values))


def _plan_speech_gains(
    takes: Sequence[TakeAudioEvidence],
) -> Tuple[Tuple[float, ...], float]:
    measured = tuple(_active_speech_rms(take) for take in takes)
    target = float(median(measured))
    gains = tuple(max(-2.0, min(2.0, target - value)) for value in measured)
    adjusted = tuple(value + gain for value, gain in zip(measured, gains))
    adjusted_range = max(adjusted) - min(adjusted)
    if adjusted_range > 1.5 + 1e-9:
        raise ValidationError(
            "Acoustic speech loudness cannot be matched inside the gain clamp.",
            {"active_speech_rms_range_db": round(adjusted_range, 3)},
        )
    return gains, adjusted_range


def _frames_between(
    take: TakeAudioEvidence,
    start_seconds: float,
    end_seconds: float,
) -> Tuple[AudioFrameMetrics, ...]:
    return tuple(
        frame
        for frame in take.frames
        if start_seconds <= frame.timestamp_seconds < end_seconds
    )


def _is_breath_like(frame: AudioFrameMetrics) -> bool:
    return (
        frame.rms_dbfs > -52.0
        and frame.spectral_centroid_hz >= 2600.0
        and frame.spectral_flatness >= 0.30
        and frame.zero_crossing_rate >= 0.08
    )


def _maximum_breath_island_duration(frames: Sequence[AudioFrameMetrics]) -> float:
    longest = 0.0
    current_start: Optional[float] = None
    previous_timestamp: Optional[float] = None
    bounded_on_left = False
    saw_low_energy = False
    frame_step = 0.016
    for frame in frames:
        if _is_breath_like(frame):
            if current_start is None:
                current_start = frame.timestamp_seconds
                bounded_on_left = saw_low_energy
            previous_timestamp = frame.timestamp_seconds
            continue
        if current_start is not None and previous_timestamp is not None:
            if bounded_on_left:
                longest = max(longest, previous_timestamp - current_start + frame_step)
        current_start = None
        previous_timestamp = None
        bounded_on_left = False
        saw_low_energy = True
    if current_start is not None and previous_timestamp is not None and bounded_on_left:
        longest = max(longest, previous_timestamp - current_start + frame_step)
    return max(0.0, longest)


def _boundary_rms(
    take: TakeAudioEvidence,
    boundary_seconds: float,
    *,
    before: bool,
) -> float:
    start = boundary_seconds - 0.032 if before else boundary_seconds
    end = boundary_seconds if before else boundary_seconds + 0.032
    frames = _frames_between(take, max(0.0, start), min(take.provider_duration_seconds, end))
    if not frames:
        raise ValidationError(
            "Acoustic seam boundary has insufficient frame evidence.",
            {"take_index": take.take_index, "boundary_seconds": boundary_seconds},
        )
    return float(median(frame.rms_dbfs for frame in frames))


def _boundary_starts_inside_isolated_breath(
    take: TakeAudioEvidence,
    boundary_seconds: float,
) -> bool:
    frames = _frames_between(
        take,
        max(0.0, boundary_seconds - 0.128),
        min(take.first_word_start_seconds, boundary_seconds + 0.128),
    )
    group_start: Optional[float] = None
    group_end: Optional[float] = None

    def group_crosses_boundary() -> bool:
        return bool(
            group_start is not None
            and group_end is not None
            and group_start <= boundary_seconds + 0.016
            and group_end >= boundary_seconds
            and group_end - boundary_seconds > 0.032 + 1e-9
        )

    for frame in frames:
        if _is_breath_like(frame):
            if group_start is None:
                group_start = frame.timestamp_seconds
            group_end = frame.timestamp_seconds + 0.016
            continue
        if group_crosses_boundary():
            return True
        group_start = None
        group_end = None
    return group_crosses_boundary()


def _select_seam(
    seam_index: int,
    previous: TakeAudioEvidence,
    next_take: TakeAudioEvidence,
    previous_gain_db: float,
    next_gain_db: float,
) -> PlannedSeam:
    tail_contexts = (0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.22)
    head_contexts = (0.06, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.22)
    overlaps = (0.04, 0.05, 0.06, 0.07)
    valid = []
    perceptual_fallbacks = []
    rejected: List[Dict[str, object]] = []
    for tail_context in tail_contexts:
        previous_end = min(
            previous.provider_duration_seconds,
            previous.final_word_end_seconds + tail_context,
        )
        for head_context in head_contexts:
            next_start = max(0.0, next_take.first_word_start_seconds - head_context)
            previous_margin = _frames_between(
                previous, previous.final_word_end_seconds, previous_end
            )
            next_margin = _frames_between(next_take, next_start, next_take.first_word_start_seconds)
            island_duration = max(
                _maximum_breath_island_duration(previous_margin),
                _maximum_breath_island_duration(next_margin),
            )
            for overlap in overlaps:
                word_gap = (
                    previous_end
                    - previous.final_word_end_seconds
                    + next_take.first_word_start_seconds
                    - next_start
                    - overlap
                )
                candidate = {
                    "tail_context_seconds": tail_context,
                    "head_context_seconds": head_context,
                    "overlap_seconds": overlap,
                    "word_gap_seconds": round(word_gap, 6),
                    "retained_island_duration_seconds": round(island_duration, 6),
                }
                reasons = []
                if not 0.100 - 1e-9 <= word_gap <= _MAX_SEAM_WORD_GAP_SECONDS + 1e-9:
                    reasons.append("word_gap_out_of_range")
                if previous_end - previous.final_word_end_seconds - overlap < 0.100 - 1e-9:
                    reasons.append("post_word_crossfade_guard")
                if next_take.first_word_start_seconds - next_start - overlap < 0.060 - 1e-9:
                    reasons.append("pre_word_crossfade_guard")
                if island_duration > 0.080 + 1e-9:
                    reasons.append("retained_breath_island")
                if _boundary_starts_inside_isolated_breath(next_take, next_start):
                    reasons.append("boundary_inside_breath")
                try:
                    energy_delta = abs(
                        _boundary_rms(previous, previous_end, before=True)
                        + previous_gain_db
                        - _boundary_rms(next_take, next_start, before=False)
                        - next_gain_db
                    )
                except ValidationError:
                    reasons.append("insufficient_boundary_evidence")
                    energy_delta = math.inf
                deterministic_reasons = tuple(reasons)
                if energy_delta > _PREFERRED_SEAM_ENERGY_DELTA_DB + 1e-9:
                    reasons.append("energy_delta_exceeded")
                    if (
                        not deterministic_reasons
                        and energy_delta <= MAX_PERCEPTUAL_SEAM_ENERGY_DELTA_DB + 1e-9
                    ):
                        perceptual_fallbacks.append(
                            (
                                island_duration,
                                abs(word_gap - 0.160),
                                energy_delta,
                                next_start,
                                previous_end,
                                overlap,
                                candidate,
                            )
                        )
                if reasons:
                    rejected.append({**candidate, "reasons": reasons})
                    continue
                valid.append(
                    (
                        island_duration,
                        abs(word_gap - 0.160),
                        energy_delta,
                        next_start,
                        previous_end,
                        overlap,
                        candidate,
                    )
                )
    energy_fallback = False
    if not valid and perceptual_fallbacks:
        valid = perceptual_fallbacks
        energy_fallback = True
    if not valid:
        raise ValidationError(
            "No transcript-safe acoustic seam candidate exists.",
            {"seam_index": seam_index, "rejected_candidate_count": len(rejected)},
        )
    island_duration, _, energy_delta, next_start, previous_end, overlap, _ = min(
        valid,
        key=lambda candidate: candidate[:6],
    )
    visual_position = overlap / 2.0
    word_gap = (
        previous_end
        - previous.final_word_end_seconds
        + next_take.first_word_start_seconds
        - next_start
        - overlap
    )
    return PlannedSeam(
        seam_index=seam_index,
        previous_audio_end_seconds=previous_end,
        next_audio_start_seconds=next_start,
        overlap_seconds=overlap,
        visual_cut_position_seconds=visual_position,
        final_word_gap_seconds=word_gap,
        short_window_energy_delta_db=energy_delta,
        retained_island_duration_seconds=island_duration,
        speech_overlap=False,
        rejected_candidates=tuple(rejected),
        energy_fallback=energy_fallback,
    )


def _derive_video_windows(
    takes: Sequence[TakeAudioEvidence],
    seams: Sequence[PlannedSeam],
    gains: Sequence[float],
) -> Tuple[PlannedTakeWindow, ...]:
    planned = []
    for index, take in enumerate(takes):
        audio_start = 0.0 if index == 0 else seams[index - 1].next_audio_start_seconds
        audio_end = (
            take.final_word_end_seconds + 0.08
            if index == len(takes) - 1
            else seams[index].previous_audio_end_seconds
        )
        audio_end = min(take.provider_duration_seconds, audio_end)
        video_start = audio_start
        if index > 0:
            video_start += seams[index - 1].visual_cut_position_seconds
        video_end = audio_end
        if index < len(seams):
            video_end -= seams[index].overlap_seconds - seams[index].visual_cut_position_seconds
        if video_end <= video_start:
            raise ValidationError("Acoustic seam plan produced an empty video window.")
        planned.append(
            PlannedTakeWindow(
                take_index=take.take_index,
                audio_start_seconds=audio_start,
                audio_end_seconds=audio_end,
                video_start_seconds=video_start,
                video_end_seconds=video_end,
                gain_db=float(gains[index]),
            )
        )
    return tuple(planned)


def _planned_duration(
    takes: Sequence[PlannedTakeWindow], seams: Sequence[PlannedSeam]
) -> float:
    return sum(take.audio_end_seconds - take.audio_start_seconds for take in takes) - sum(
        seam.overlap_seconds for seam in seams
    )


def _extend_delivery_windows(
    planned: Sequence[PlannedTakeWindow],
    evidence: Sequence[TakeAudioEvidence],
    seams: Sequence[PlannedSeam],
    *,
    min_duration_seconds: float,
    max_duration_seconds: float,
) -> Tuple[Tuple[PlannedTakeWindow, ...], Tuple[PlannedSeam, ...]]:
    result = list(planned)
    adjusted_seams = list(seams)
    current_duration = _planned_duration(result, seams)
    if current_duration > max_duration_seconds + 1e-9:
        raise ValidationError("Acoustic plan exceeds the duration envelope.")
    if current_duration >= min_duration_seconds - 1e-9:
        return tuple(result), tuple(adjusted_seams)
    required = min_duration_seconds - current_duration
    raw_capacities = [
        max(0.0, take.provider_duration_seconds - window.audio_end_seconds)
        for window, take in zip(result, evidence)
    ]
    capacities = [
        min(
            raw_capacity,
            max(0.0, _MAX_SEAM_WORD_GAP_SECONDS - seams[index].final_word_gap_seconds),
        )
        if index < len(seams)
        else raw_capacity
        for index, raw_capacity in enumerate(raw_capacities)
    ]
    cadence_safe_available = sum(capacities)
    if cadence_safe_available + 1e-9 < required:
        fair_share = required / len(result)
        raise ValidationError(
            "Acoustic plan cannot satisfy the duration envelope.",
            {
                "required_seconds": required,
                "total_available_seconds": sum(raw_capacities),
                "available_seconds_by_take": {
                    str(window.take_index): capacity
                    for window, capacity in zip(result, raw_capacities)
                },
                "cadence_safe_available_seconds": cadence_safe_available,
                "cadence_safe_available_seconds_by_take": {
                    str(window.take_index): capacity
                    for window, capacity in zip(result, capacities)
                },
                "under_capacity_take_indexes": [
                    window.take_index
                    for window, capacity in zip(result, capacities)
                    if capacity + 1e-9 < fair_share
                ],
            },
        )

    if capacities[-1] + 1e-9 >= required:
        extensions = [0.0] * len(result)
        extensions[-1] = required
    else:
        extensions = [0.0] * len(result)
        remaining = required
        active = {index for index, capacity in enumerate(capacities) if capacity > 1e-9}
        while remaining > 1e-9 and active:
            fair_share = remaining / len(active)
            allocated = 0.0
            for index in tuple(active):
                available = capacities[index] - extensions[index]
                addition = min(available, fair_share)
                extensions[index] += addition
                allocated += addition
                if available - addition <= 1e-9:
                    active.remove(index)
            remaining -= allocated

    for index, extension in enumerate(extensions):
        if extension <= 1e-9:
            continue
        window = result[index]
        result[index] = PlannedTakeWindow(
            take_index=window.take_index,
            audio_start_seconds=window.audio_start_seconds,
            audio_end_seconds=window.audio_end_seconds + extension,
            video_start_seconds=window.video_start_seconds,
            video_end_seconds=window.video_end_seconds + extension,
            gain_db=window.gain_db,
        )
        if index >= len(adjusted_seams):
            continue
        seam = adjusted_seams[index]
        new_end = result[index].audio_end_seconds
        retained_island = max(
            seam.retained_island_duration_seconds,
            _maximum_breath_island_duration(
                _frames_between(
                    evidence[index],
                    evidence[index].final_word_end_seconds,
                    new_end,
                )
            ),
        )
        try:
            energy_delta = abs(
                _boundary_rms(evidence[index], new_end, before=True)
                + result[index].gain_db
                - _boundary_rms(
                    evidence[index + 1],
                    seam.next_audio_start_seconds,
                    before=False,
                )
                - result[index + 1].gain_db
            )
        except ValidationError as exc:
            raise ValidationError(
                "Acoustic duration extension has insufficient boundary evidence.",
                {"seam_index": index, "extended_audio_end_seconds": new_end},
            ) from exc
        if retained_island > 0.080 + 1e-9:
            raise ValidationError(
                "Acoustic duration extension retains an unsafe breath island.",
                {
                    "seam_index": index,
                    "retained_island_duration_seconds": retained_island,
                },
            )
        if energy_delta > MAX_PERCEPTUAL_SEAM_ENERGY_DELTA_DB + 1e-9:
            raise ValidationError(
                "Acoustic duration extension exceeds the seam energy limit.",
                {"seam_index": index, "short_window_energy_delta_db": energy_delta},
            )
        adjusted_seams[index] = replace(
            seam,
            previous_audio_end_seconds=new_end,
            final_word_gap_seconds=seam.final_word_gap_seconds + extension,
            short_window_energy_delta_db=energy_delta,
            retained_island_duration_seconds=retained_island,
            energy_fallback=(
                seam.energy_fallback
                or energy_delta > _PREFERRED_SEAM_ENERGY_DELTA_DB + 1e-9
            ),
        )
    return tuple(result), tuple(adjusted_seams)


def plan_acoustic_seams(
    takes: Sequence[TakeAudioEvidence],
    *,
    fps: float = 24.0,
    min_duration_seconds: float = 14.5,
    max_duration_seconds: float = 16.5,
) -> AcousticSeamPlan:
    if not math.isfinite(fps) or fps <= 0:
        raise ValidationError("Acoustic seam planning requires a positive finite frame rate.")
    if (
        not math.isfinite(min_duration_seconds)
        or not math.isfinite(max_duration_seconds)
        or min_duration_seconds < 0
        or max_duration_seconds <= min_duration_seconds
    ):
        raise ValidationError("Acoustic duration envelope is invalid.")
    ordered = _validate_take_evidence(takes)
    gains, rms_range = _plan_speech_gains(ordered)
    seams = tuple(
        _select_seam(
            index,
            ordered[index],
            ordered[index + 1],
            gains[index],
            gains[index + 1],
        )
        for index in range(len(ordered) - 1)
    )
    planned = _derive_video_windows(ordered, seams, gains)
    planned, seams = _extend_delivery_windows(
        planned,
        ordered,
        seams,
        min_duration_seconds=max(0.0, min_duration_seconds - (1.0 / fps)),
        max_duration_seconds=max_duration_seconds,
    )
    final_duration = _planned_duration(planned, seams)
    video_duration = sum(take.video_end_seconds - take.video_start_seconds for take in planned)
    if abs(final_duration - video_duration) > 1.0 / fps + 1e-9:
        raise ValidationError("Acoustic audio/video plan exceeds one frame of duration drift.")
    return AcousticSeamPlan(
        analyzer_version=ACOUSTIC_ANALYZER_VERSION,
        takes=planned,
        seams=seams,
        active_speech_rms_range_db=rms_range,
        final_duration_seconds=final_duration,
    )


__all__ = [
    "ACOUSTIC_ANALYZER_VERSION",
    "MAX_PERCEPTUAL_SEAM_ENERGY_DELTA_DB",
    "AcousticSeamPlan",
    "AudioFrameMetrics",
    "PlannedSeam",
    "PlannedTakeWindow",
    "TakeAudioEvidence",
    "acoustic_analysis_cache_key",
    "analyze_audio_frames",
    "parse_frame_metrics",
    "plan_acoustic_seams",
]
