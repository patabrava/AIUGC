import math
import json

import pytest

from app.core.errors import ValidationError
from app.features.shot_production.audio_seams import (
    ACOUSTIC_ANALYZER_VERSION,
    AcousticSeamPlan,
    AudioFrameMetrics,
    TakeAudioEvidence,
    acoustic_analysis_cache_key,
    analyze_audio_frames,
    parse_frame_metrics,
    plan_acoustic_seams,
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


def test_parse_frame_metrics_maps_digital_silence_dbfs_to_finite_floor():
    parsed = parse_frame_metrics(
        {
            "frames": [
                _frame(
                    **{
                        "lavfi.astats.1.RMS_level": "-inf",
                        "lavfi.astats.1.Peak_level": "-inf",
                    }
                )
            ]
        }
    )

    assert parsed[0].rms_dbfs == -120.0
    assert parsed[0].peak_dbfs == -120.0


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


def _evidence(
    index,
    *,
    duration=4.0,
    first_word=0.5,
    final_word=3.0,
    speech_rms=-20.0,
    room_rms=-60.0,
    breath_start=None,
    breath_end=None,
):
    frames = []
    timestamp = 0.0
    while timestamp <= duration:
        rms = room_rms
        centroid = 2200.0
        flatness = 0.25
        zcr = 0.04
        if first_word <= timestamp <= final_word:
            rms = speech_rms
            centroid = 1200.0
            flatness = 0.12
            zcr = 0.08
        if breath_start is not None and breath_start <= timestamp <= breath_end:
            rms = -42.0
            centroid = 3600.0
            flatness = 0.62
            zcr = 0.12
        frames.append(AudioFrameMetrics(timestamp, rms, rms + 8.0, zcr, centroid, flatness))
        timestamp = round(timestamp + 0.016, 6)
    return TakeAudioEvidence(
        take_index=index,
        provider_duration_seconds=duration,
        first_word_start_seconds=first_word,
        final_word_end_seconds=final_word,
        frames=tuple(frames),
    )


def test_planner_resolves_equally_safe_candidates_deterministically():
    takes = (
        _evidence(0, final_word=2.8),
        _evidence(1, first_word=0.10),
    )

    plan = plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=10.0)

    assert plan.seams[0].next_audio_start_seconds == 0.0
    assert plan.seams[0].overlap_seconds == 0.04


def test_planner_defers_bounded_room_tone_delta_to_perceptual_seam_qa():
    takes = (
        _evidence(0, final_word=3.0, room_rms=-60.0),
        _evidence(1, first_word=0.5, room_rms=-51.0),
    )

    plan = plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=10.0)

    assert plan.seams[0].energy_fallback is True
    assert 6.0 < plan.seams[0].short_window_energy_delta_db <= 12.0


def test_planner_accepts_one_frame_of_delivery_duration_quantization():
    previous = _evidence(
        0,
        duration=8.0,
        first_word=0.24,
        final_word=6.66,
        room_rms=-60.0,
    )
    takes = (
        previous,
        _evidence(
            1,
            duration=8.0,
            first_word=0.48,
            final_word=6.18,
            room_rms=-51.0,
        ),
    )

    plan = plan_acoustic_seams(
        takes,
        fps=24.0,
        min_duration_seconds=14.5,
        max_duration_seconds=16.5,
    )

    assert 14.5 - (1 / 24) <= plan.final_duration_seconds < 14.5


def test_planner_removes_pause_breath_pause_from_next_take_head():
    takes = (
        _evidence(0, final_word=3.0),
        _evidence(1, first_word=0.5, breath_start=0.18, breath_end=0.34),
    )

    plan = plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=10.0)

    assert isinstance(plan, AcousticSeamPlan)
    assert plan.takes[1].audio_start_seconds > 0.34
    assert plan.seams[0].retained_island_duration_seconds == 0.0
    assert 0.100 <= plan.seams[0].final_word_gap_seconds <= 0.320
    assert 0.040 <= plan.seams[0].overlap_seconds <= 0.070


def test_planner_limits_unavoidable_boundary_breath_tail_to_crossfade_window():
    takes = (
        _evidence(0, final_word=3.0, room_rms=-42.0),
        _evidence(
            1,
            first_word=0.56,
            room_rms=-42.0,
            breath_start=0.36,
            breath_end=0.46,
        ),
    )

    plan = plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=10.0)

    assert 0.46 - plan.takes[1].audio_start_seconds <= 0.032
    assert plan.seams[0].retained_island_duration_seconds == 0.0


def test_planner_does_not_treat_word_adjacent_fricative_as_isolated_breath():
    previous = _evidence(0, final_word=3.0)
    frames = tuple(
        AudioFrameMetrics(
            frame.timestamp_seconds,
            -42.0,
            -34.0,
            0.12,
            3600.0,
            0.62,
        )
        if 3.0 <= frame.timestamp_seconds <= 3.05
        else frame
        for frame in previous.frames
    )
    previous = TakeAudioEvidence(
        take_index=previous.take_index,
        provider_duration_seconds=previous.provider_duration_seconds,
        first_word_start_seconds=previous.first_word_start_seconds,
        final_word_end_seconds=previous.final_word_end_seconds,
        frames=frames,
    )

    plan = plan_acoustic_seams(
        (previous, _evidence(1)),
        min_duration_seconds=0.0,
        max_duration_seconds=10.0,
    )

    assert plan.seams[0].retained_island_duration_seconds == 0.0


def test_planner_prefers_energy_safe_candidate_then_target_cadence():
    previous = _evidence(0, final_word=3.0, room_rms=-42.0)
    next_take = _evidence(1, first_word=0.56, room_rms=-60.0)
    frames = tuple(
        AudioFrameMetrics(
            frame.timestamp_seconds,
            -42.0,
            -34.0,
            0.04,
            1200.0,
            0.12,
        )
        if 0.20 <= frame.timestamp_seconds < 0.42
        else frame
        for frame in next_take.frames
    )
    next_take = TakeAudioEvidence(
        take_index=next_take.take_index,
        provider_duration_seconds=next_take.provider_duration_seconds,
        first_word_start_seconds=next_take.first_word_start_seconds,
        final_word_end_seconds=next_take.final_word_end_seconds,
        frames=frames,
    )

    plan = plan_acoustic_seams(
        (previous, next_take),
        min_duration_seconds=0.0,
        max_duration_seconds=10.0,
    )

    assert plan.takes[1].audio_start_seconds < 0.42
    assert 0.10 <= plan.seams[0].final_word_gap_seconds <= 0.32
    assert plan.seams[0].short_window_energy_delta_db <= 6.0


def test_planner_keeps_word_guards_and_never_crossfades_speech():
    takes = (_evidence(0), _evidence(1))

    plan = plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=10.0)

    seam = plan.seams[0]
    untouched_tail = (
        plan.takes[0].audio_end_seconds
        - takes[0].final_word_end_seconds
        - seam.overlap_seconds
    )
    untouched_head = (
        takes[1].first_word_start_seconds
        - plan.takes[1].audio_start_seconds
        - seam.overlap_seconds
    )
    assert untouched_tail >= 0.100 - 1e-9
    assert untouched_head >= 0.060 - 1e-9
    assert plan.seams[0].speech_overlap is False


def test_planner_video_windows_match_crossfaded_audio_duration():
    takes = (_evidence(0), _evidence(1), _evidence(2), _evidence(3))

    plan = plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=20.0)
    video_duration = sum(
        take.video_end_seconds - take.video_start_seconds for take in plan.takes
    )
    audio_duration = sum(
        take.audio_end_seconds - take.audio_start_seconds for take in plan.takes
    ) - sum(seam.overlap_seconds for seam in plan.seams)

    assert video_duration == pytest.approx(audio_duration, abs=1 / 24)
    assert plan.final_duration_seconds == pytest.approx(audio_duration)


def test_planner_matches_active_speech_gain_within_clamp():
    takes = (
        _evidence(0, speech_rms=-18.0),
        _evidence(1, speech_rms=-19.0),
        _evidence(2, speech_rms=-20.0),
        _evidence(3, speech_rms=-19.5),
    )

    plan = plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=20.0)
    gains = [take.gain_db for take in plan.takes]

    assert all(-2.0 <= gain <= 2.0 for gain in gains)
    assert plan.active_speech_rms_range_db <= 1.5


def test_planner_reports_boundary_energy_after_gain_matching():
    takes = (
        _evidence(0, speech_rms=-18.0, room_rms=-52.0),
        _evidence(1, speech_rms=-20.0, room_rms=-60.0),
    )

    plan = plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=10.0)

    assert plan.seams[0].short_window_energy_delta_db <= 6.0


def test_planner_fails_when_final_take_cannot_reach_duration_floor():
    takes = (_evidence(0, duration=2.0, final_word=1.5), _evidence(1, duration=2.0, final_word=1.5))

    with pytest.raises(ValidationError, match="duration envelope") as exc_info:
        plan_acoustic_seams(takes, min_duration_seconds=10.0, max_duration_seconds=12.0)

    assert exc_info.value.details["required_seconds"] > exc_info.value.details["total_available_seconds"]
    assert exc_info.value.details["available_seconds_by_take"] == {
        "0": pytest.approx(0.36),
        "1": pytest.approx(0.42),
    }
    assert exc_info.value.details["under_capacity_take_indexes"] == [0, 1]


def test_planner_distributes_long_form_duration_floor_across_take_windows():
    takes = tuple(
        _evidence(index, duration=8.0, first_word=0.5, final_word=7.0)
        for index in range(7)
    )
    baseline = plan_acoustic_seams(
        takes,
        min_duration_seconds=0.0,
        max_duration_seconds=50.5,
    )

    plan = plan_acoustic_seams(
        takes,
        min_duration_seconds=48.5,
        max_duration_seconds=50.5,
    )

    assert plan.final_duration_seconds == pytest.approx(48.5 - (1 / 24))
    extended_indexes = [
        index
        for index, (window, baseline_window) in enumerate(zip(plan.takes, baseline.takes))
        if window.audio_end_seconds > baseline_window.audio_end_seconds + 1e-9
    ]
    assert len(extended_indexes) >= 2
    assert all(window.audio_end_seconds <= 8.0 for window in plan.takes)
    for index, seam in enumerate(plan.seams):
        rendered_gap = (
            plan.takes[index].audio_end_seconds
            - takes[index].final_word_end_seconds
            + takes[index + 1].first_word_start_seconds
            - plan.takes[index + 1].audio_start_seconds
            - seam.overlap_seconds
        )
        assert seam.previous_audio_end_seconds == pytest.approx(
            plan.takes[index].audio_end_seconds
        )
        assert seam.final_word_gap_seconds == pytest.approx(rendered_gap)
        assert 0.10 <= seam.final_word_gap_seconds <= 0.32


def test_planner_rejects_long_form_padding_that_would_exceed_the_word_gap_ceiling():
    takes = tuple(
        _evidence(index, duration=8.0, first_word=0.5, final_word=6.8)
        for index in range(7)
    )

    with pytest.raises(ValidationError, match="duration envelope") as exc_info:
        plan_acoustic_seams(
            takes,
            min_duration_seconds=48.5,
            max_duration_seconds=50.5,
        )

    assert exc_info.value.details["required_seconds"] > exc_info.value.details[
        "cadence_safe_available_seconds"
    ]
    assert exc_info.value.details["under_capacity_take_indexes"]


def test_planner_preserves_two_take_final_outro_when_it_has_capacity():
    takes = (_evidence(0), _evidence(1))

    plan = plan_acoustic_seams(takes, min_duration_seconds=6.0, max_duration_seconds=10.0)

    assert plan.takes[0].audio_end_seconds == pytest.approx(
        plan.seams[0].previous_audio_end_seconds
    )
    assert plan.takes[1].audio_end_seconds > takes[1].final_word_end_seconds + 0.08


def test_planner_rejects_breath_crossing_truncated_head_analysis_windows():
    takes = (
        _evidence(0, final_word=3.0, room_rms=-42.0),
        _evidence(
            1,
            first_word=0.5,
            room_rms=-42.0,
            breath_start=0.18,
            breath_end=0.48,
        ),
    )

    with pytest.raises(ValidationError, match="No transcript-safe acoustic seam candidate"):
        plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=10.0)


def test_planner_rejects_isolated_breath_open_at_tail_analysis_window_end():
    takes = (
        _evidence(
            0,
            final_word=3.0,
            room_rms=-42.0,
            breath_start=3.04,
            breath_end=3.40,
        ),
        _evidence(1, room_rms=-42.0),
    )

    with pytest.raises(ValidationError, match="No transcript-safe acoustic seam candidate"):
        plan_acoustic_seams(takes, min_duration_seconds=0.0, max_duration_seconds=10.0)
