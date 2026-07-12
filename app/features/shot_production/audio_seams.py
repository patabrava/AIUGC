"""Deterministic acoustic evidence and seam planning for semantic Veo takes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict, Optional, Tuple

from app.core.errors import ValidationError


ACOUSTIC_ANALYZER_VERSION = "native-acoustic-seams-v1"
_ANALYSIS_TIMEOUT_SECONDS = 120
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
            rms_dbfs=_finite_tag(tags, _FRAME_TAGS["rms_dbfs"]),
            peak_dbfs=_finite_tag(tags, _FRAME_TAGS["peak_dbfs"]),
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


__all__ = [
    "ACOUSTIC_ANALYZER_VERSION",
    "AudioFrameMetrics",
    "acoustic_analysis_cache_key",
    "analyze_audio_frames",
    "parse_frame_metrics",
]
