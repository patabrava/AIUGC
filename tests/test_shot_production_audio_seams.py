import math
import json
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.features.shot_production.audio_seams import (
    ACOUSTIC_ANALYZER_VERSION,
    AudioFrameMetrics,
    acoustic_analysis_cache_key,
    analyze_audio_frames,
    parse_frame_metrics,
)


def _frame(timestamp="0.160000", **tag_overrides):
    tags = {
        "lavfi.astats.1.RMS_level": "-45.1",
        "lavfi.astats.1.Peak_level": "-32.0",
        "lavfi.astats.1.Zero_crossings_rate": "0.116",
        "lavfi.aspectralstats.1.centroid": "3760.0",
        "lavfi.aspectralstats.1.flatness": "0.61",
    }
    tags.update(tag_overrides)
    return {"pts_time": timestamp, "tags": tags}


def test_parse_frame_metrics_reads_installed_ffprobe_tags():
    parsed = parse_frame_metrics({"frames": [_frame()]})

    assert parsed == (
        AudioFrameMetrics(
            timestamp_seconds=0.16,
            rms_dbfs=-45.1,
            peak_dbfs=-32.0,
            zero_crossing_rate=0.116,
            spectral_centroid_hz=3760.0,
            spectral_flatness=0.61,
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"frames": []},
        {"frames": [_frame(**{"lavfi.astats.1.RMS_level": "nan"})]},
        {"frames": [_frame(), _frame(timestamp="0.150000")]},
        {"frames": [{"pts_time": "0.1", "tags": {}}]},
    ],
)
def test_parse_frame_metrics_rejects_incomplete_or_invalid_evidence(payload):
    with pytest.raises(ValidationError):
        parse_frame_metrics(payload)


def test_analysis_cache_key_covers_all_behavior_inputs():
    baseline = acoustic_analysis_cache_key("abc", "ffmpeg-8", ACOUSTIC_ANALYZER_VERSION)

    assert len(baseline) == 64
    assert baseline != acoustic_analysis_cache_key(
        "abd", "ffmpeg-8", ACOUSTIC_ANALYZER_VERSION
    )
    assert baseline != acoustic_analysis_cache_key(
        "abc", "ffmpeg-9", ACOUSTIC_ANALYZER_VERSION
    )
    assert baseline != acoustic_analysis_cache_key("abc", "ffmpeg-8", "new-rubric")
    assert all(character in "0123456789abcdef" for character in baseline)


def test_audio_frame_metrics_values_are_finite():
    metric = AudioFrameMetrics(0.0, -50.0, -40.0, 0.1, 3200.0, 0.5)

    assert all(
        math.isfinite(value)
        for value in (
            metric.timestamp_seconds,
            metric.rms_dbfs,
            metric.peak_dbfs,
            metric.zero_crossing_rate,
            metric.spectral_centroid_hz,
            metric.spectral_flatness,
        )
    )


def test_analyze_audio_frames_uses_installed_ffprobe_filters(tmp_path):
    media_path = tmp_path / "take.mp4"
    media_path.write_bytes(b"video")
    calls = []

    class Result:
        returncode = 0
        stdout = json.dumps({"frames": [_frame()]})
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    frames = analyze_audio_frames(media_path, run_fn=fake_run)

    assert len(frames) == 1
    command = calls[0][0]
    filter_graph = command[command.index("-i") + 1]
    assert command[0] == "ffprobe"
    assert "aformat=sample_rates=16000:channel_layouts=mono" in filter_graph
    assert "aspectralstats=win_size=512:overlap=0.5" in filter_graph
    assert "astats=metadata=1:reset=1" in filter_graph
    assert calls[0][1]["timeout"] == 120


def test_analyze_audio_frames_rejects_failed_ffprobe(tmp_path):
    media_path = tmp_path / "take.mp4"
    media_path.write_bytes(b"video")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "filter unavailable"

    with pytest.raises(ValidationError, match="Acoustic frame analysis failed"):
        analyze_audio_frames(media_path, run_fn=lambda *_args, **_kwargs: Result())
