# Native-Voice Acoustic Seams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove duplicated breath/noise restarts from semantic Veo UGC jump cuts while preserving native voice, exact dialogue, lip-sync, hard visual cuts, and the existing four approved takes.

**Architecture:** Add one focused acoustic-seam module that converts FFprobe/FFmpeg frame metadata plus Deepgram word guards into an immutable per-take/per-seam plan. Extend the existing stitcher to hard-concatenate plan-adjusted video windows while sequentially equal-power-crossfading the aligned audio windows. The pilot runner persists the plan and hard-gate report, then produces a separately named acoustic-preview upload without changing Veo generation or the production-default route.

**Tech Stack:** Python 3.9 standard library, FFmpeg/FFprobe (`astats`, `aspectralstats`, `ebur128`, `acrossfade`), existing Deepgram and Gemini adapters, pytest.

---

### Task 1: Deterministic Acoustic Telemetry

**Files:**
- Create: `app/features/shot_production/audio_seams.py`
- Create: `tests/test_shot_production_audio_seams.py`

- [ ] **Step 1: Write failing tests for FFprobe frame parsing and cache identity**

Add tests that build representative FFprobe JSON with `pts_time` and the exact tags produced by the installed filter chain. Assert finite typed values, ordered frames, rejection of missing/NaN fields, and a cache key that changes with media SHA, FFmpeg version, or analyzer version.

```python
def test_parse_frame_metrics_reads_installed_ffprobe_tags():
    payload = {"frames": [{"pts_time": "0.160000", "tags": {
        "lavfi.astats.1.RMS_level": "-45.1",
        "lavfi.astats.1.Peak_level": "-32.0",
        "lavfi.astats.1.Zero_crossings_rate": "0.116",
        "lavfi.aspectralstats.1.centroid": "3760.0",
        "lavfi.aspectralstats.1.flatness": "0.61",
    }}]}
    assert parse_frame_metrics(payload) == (
        AudioFrameMetrics(0.16, -45.1, -32.0, 0.116, 3760.0, 0.61),
    )

def test_analysis_cache_key_covers_all_behavior_inputs():
    one = acoustic_analysis_cache_key("abc", "ffmpeg-8", "acoustic-v1")
    assert one != acoustic_analysis_cache_key("abd", "ffmpeg-8", "acoustic-v1")
    assert one != acoustic_analysis_cache_key("abc", "ffmpeg-9", "acoustic-v1")
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
APP_ENV_FILE=/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.env \
/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.venv/bin/python -m pytest \
  tests/test_shot_production_audio_seams.py -q
```

Expected: collection fails because `audio_seams` does not exist.

- [ ] **Step 3: Implement the telemetry boundary**

Create immutable types and these public functions:

```python
ACOUSTIC_ANALYZER_VERSION = "native-acoustic-seams-v1"

@dataclass(frozen=True)
class AudioFrameMetrics:
    timestamp_seconds: float
    rms_dbfs: float
    peak_dbfs: float
    zero_crossing_rate: float
    spectral_centroid_hz: float
    spectral_flatness: float

def acoustic_analysis_cache_key(media_sha256: str, ffmpeg_version: str, analyzer_version: str) -> str:
    material = f"{media_sha256}\n{ffmpeg_version}\n{analyzer_version}".encode("utf-8")
    return sha256(material).hexdigest()

def analyze_audio_frames(media_path: Path) -> Tuple[AudioFrameMetrics, ...]:
    filter_graph = (
        f"amovie='{_escape_lavfi_path(media_path)}',"
        "aformat=sample_rates=16000:channel_layouts=mono,"
        "aspectralstats=win_size=512:overlap=0.5,"
        "astats=metadata=1:reset=1"
    )
    command = ["ffprobe", "-v", "error", "-f", "lavfi", "-i", filter_graph,
               "-show_frames", "-show_entries", "frame=pts_time:frame_tags", "-of", "json"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise ValidationError("Acoustic frame analysis failed.", {"stderr": result.stderr[-400:]})
    return parse_frame_metrics(json.loads(result.stdout))
```

Only the five required tags enter persisted telemetry. Reject empty output, non-monotonic timestamps, missing tags, non-finite values, or unsupported FFmpeg filters with `ValidationError`.

- [ ] **Step 4: Run tests and verify GREEN**

Expected: telemetry tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/features/shot_production/audio_seams.py tests/test_shot_production_audio_seams.py
git commit -m "feat: analyze semantic take audio seams"
```

### Task 2: Transcript-Guarded Seam Planning

**Files:**
- Modify: `app/features/shot_production/audio_seams.py`
- Modify: `tests/test_shot_production_audio_seams.py`

- [ ] **Step 1: Write failing tests for natural-pause selection and safety**

Use synthetic 16 ms frame series for a clean single pause and a `pause -> 140 ms broadband island -> pause` pattern. Assert 60 ms word guards, 40-70 ms overlap, 100-320 ms output gap, no speech overlap, median speech-gain targeting capped at plus/minus 2 dB, and duration-preserving visual-window math.

```python
def test_planner_rejects_pause_breath_pause_and_selects_one_pause():
    evidence = fixture_take_evidence_with_breath_island(0.140)
    plan = plan_acoustic_seams(evidence)
    assert plan.seams[0].retained_island_duration_seconds == 0.0
    assert 0.100 <= plan.seams[0].final_word_gap_seconds <= 0.320
    assert 0.040 <= plan.seams[0].overlap_seconds <= 0.070

def test_video_windows_cancel_audio_overlap_duration():
    plan = plan_acoustic_seams(fixture_clean_takes())
    video_duration = sum(t.video_end_seconds - t.video_start_seconds for t in plan.takes)
    audio_duration = sum(t.audio_end_seconds - t.audio_start_seconds for t in plan.takes)
    audio_duration -= sum(s.overlap_seconds for s in plan.seams)
    assert video_duration == pytest.approx(audio_duration, abs=1 / 24)
```

- [ ] **Step 2: Run focused tests and verify RED**

Expected: missing planning types/functions.

- [ ] **Step 3: Implement immutable plan types and candidate scoring**

Add:

```python
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
    overlap_seconds: float
    visual_cut_position_seconds: float
    final_word_gap_seconds: float
    short_window_energy_delta_db: float
    retained_island_duration_seconds: float
    rejected_candidates: Tuple[Dict[str, object], ...]

@dataclass(frozen=True)
class AcousticSeamPlan:
    analyzer_version: str
    takes: Tuple[PlannedTakeWindow, ...]
    seams: Tuple[PlannedSeam, ...]
    active_speech_rms_range_db: float
    final_duration_seconds: float

def plan_acoustic_seams(takes: Sequence[TakeAudioEvidence], *, fps: float = 24.0,
                        min_duration_seconds: float = 14.5,
                        max_duration_seconds: float = 16.5) -> AcousticSeamPlan:
    ordered = _validate_take_evidence(takes)
    gains, rms_range = _plan_speech_gains(ordered)
    seams = tuple(
        _select_seam(index, ordered[index], ordered[index + 1])
        for index in range(len(ordered) - 1)
    )
    planned_takes = _derive_video_windows(ordered, seams, gains)
    planned_takes = _extend_final_outro(
        planned_takes,
        ordered,
        seams,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
    )
    final_duration = sum(
        take.audio_end_seconds - take.audio_start_seconds for take in planned_takes
    ) - sum(seam.overlap_seconds for seam in seams)
    if not min_duration_seconds <= final_duration <= max_duration_seconds:
        raise ValidationError("Acoustic plan cannot satisfy the duration envelope.")
    return AcousticSeamPlan(
        analyzer_version=ACOUSTIC_ANALYZER_VERSION,
        takes=planned_takes,
        seams=seams,
        active_speech_rms_range_db=rms_range,
        final_duration_seconds=final_duration,
    )
```

Implement the private helpers shown above in the same module. `_validate_take_evidence()` sorts and validates consecutive indexes and finite word/provider bounds. `_plan_speech_gains()` targets median active-speech RMS and rejects a post-clamp range over 1.5 dB. `_select_seam()` performs candidate enumeration and ranking. `_derive_video_windows()` applies the approved overlap/cut-position equations. `_extend_final_outro()` may move only the final audio/video end inside the final provider take.

Candidate enumeration uses only post-word and pre-word regions outside 60 ms guards. Classify a retained island only when an above-low-energy frame group longer than 80 ms is bounded by low-energy groups and excluded from all word intervals. Rank valid candidates by: no island, no speech overlap, energy delta, distance from a 160 ms word gap, then earliest deterministic timestamp.

Select overlap in 10 ms increments from 40-70 ms. Set `visual_cut_position_seconds` within the overlap and no farther than 60 ms from its selected acoustic valley. Compute video windows using the approved specification equations. Extend only the last native outro when needed to reach 14.5 seconds; otherwise raise `ValidationError`.

- [ ] **Step 4: Run tests and verify GREEN**

Expected: planner tests pass with no new dependency.

- [ ] **Step 5: Commit**

```bash
git add app/features/shot_production/audio_seams.py tests/test_shot_production_audio_seams.py
git commit -m "feat: plan breath safe semantic seams"
```

### Task 3: Separate Hard-Cut Video and Equal-Power Audio Composition

**Files:**
- Modify: `app/adapters/video_stitcher.py`
- Modify: `tests/test_video_stitcher.py`

- [ ] **Step 1: Write failing real-FFmpeg integration tests**

Extend the synthetic clip helper to accept frequency and sample rate. Pass a serialized acoustic plan and assert hard video cuts, sequential audio overlap, final duration within one frame, metadata for every source window/seam, and unchanged legacy behavior when no plan is supplied.

```python
final_bytes, meta = stitch_segments(
    segment_videos=[bytes_a, bytes_b],
    post_id="post_test",
    correlation_id="corr_test",
    acoustic_plan={
        "takes": [
            {"audio_start_seconds": 0.0, "audio_end_seconds": 2.0,
             "video_start_seconds": 0.0, "video_end_seconds": 1.97, "gain_db": 0.0},
            {"audio_start_seconds": 0.0, "audio_end_seconds": 2.0,
             "video_start_seconds": 0.03, "video_end_seconds": 2.0, "gain_db": -0.5},
        ],
        "seams": [{"overlap_seconds": 0.06, "visual_cut_position_seconds": 0.03}],
    },
)
assert meta["stitch_cut_softening_applied"] is True
assert meta["stitch_audio_overlap_s"] == [0.06]
assert abs(meta["stitch_audio_video_duration_delta_s"]) <= 1 / 24
```

- [ ] **Step 2: Run stitcher test and verify RED**

Expected: `stitch_segments()` rejects `acoustic_plan`.

- [ ] **Step 3: Implement plan validation and two filter chains**

Add optional `acoustic_plan: Optional[Dict[str, Any]] = None`. Validate take/seam counts, finite ordered windows, gains inside plus/minus 2 dB, overlaps inside 40-70 ms, and picture positions inside their overlaps.

For planned composition, generate video labels independently:

```python
filter_parts.append(
    f"[{i}:v]trim=start={v_start:.3f}:end={v_end:.3f},setpts=PTS-STARTPTS,"
    f"{reframe},setsar=1,fps={fps:.5f},format=yuv420p[v{i}]"
)
filter_parts.append(
    f"[{i}:a]atrim=start={a_start:.3f}:end={a_end:.3f},asetpts=PTS-STARTPTS,"
    f"volume={gain_db:.3f}dB,aresample=48000:async=1[a{i}]"
)
```

Hard-concatenate only video with `concat=n=N:v=1:a=0`. Fold audio labels sequentially with:

```python
filter_parts.append(
    f"[{audio_left}][a{i}]acrossfade=d={overlap:.3f}:o=1:c1=qsin:c2=qsin[ax{i}]"
)
```

Map the final video and audio labels, preserve H.264/AAC output, probe audio/video stream durations, and fail if their delta exceeds one output frame. Persist audio/video source windows, overlap durations, cut positions, gains, and duration delta in stitch metadata. Preserve the existing concat path exactly when `acoustic_plan` is absent.

- [ ] **Step 4: Run stitcher tests and verify GREEN**

Run `tests/test_video_stitcher.py -q`. Expected: legacy and acoustic tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/adapters/video_stitcher.py tests/test_video_stitcher.py
git commit -m "feat: crossfade native audio at semantic cuts"
```

### Task 4: Persisted Acoustic QA and Pilot Integration

**Files:**
- Create: `app/features/shot_production/acoustic_qa.py`
- Modify: `app/features/shot_production/runner.py`
- Modify: `scripts/run_semantic_ugc_pilot.py`
- Create: `tests/test_shot_production_acoustic_qa.py`
- Modify: `tests/test_shot_production_runner.py`

- [ ] **Step 1: Write failing QA and orchestration tests**

Test strict Gemini JSON parsing for `no_breath_restart`, `no_duplicated_breath`, `no_click`, `no_room_tone_reset`, `cadence_continuous`, `speaker_continuous`, `evidence_sufficient`, `confidence`, and reason arrays. Test that `compose_and_caption(..., acoustic_seams=True)` analyzes checksum-verified raw takes, persists the plan before stitching, passes it to the stitcher, archives invalid cached delivery, requires acoustic QA before returning/uploading, and never calls Vertex.

```python
assert compose_calls[0]["acoustic_plan"] == payload["acoustic_seam_plan"]
assert payload["acoustic_seam_qa"]["passed"] is True
assert vertex_calls == []

def test_acoustic_qa_fails_on_breath_restart_even_with_high_confidence():
    report = evaluate_acoustic_seam_continuity(clips, llm_client=fake_gemini({
        "no_breath_restart": False, "no_duplicated_breath": True,
        "no_click": True, "no_room_tone_reset": True,
        "cadence_continuous": True, "speaker_continuous": True,
        "evidence_sufficient": True, "confidence": 0.99,
        "blocking_reasons": ["breath restarts at seam 1"], "observed_differences": []}))
    assert report.passed is False
```

- [ ] **Step 2: Run focused tests and verify RED**

Expected: acoustic QA module and orchestration flag are absent.

- [ ] **Step 3: Implement qualitative gate and runner path**

Model `acoustic_qa.py` after `voice_qa.py`, with rubric version `acoustic-seams-v1`, default model `gemini-2.5-flash`, exact schema validation, confidence threshold 0.85, and all boolean fields blocking.

Add `acoustic_seams: bool = False` to `compose_and_caption()`. When true:

1. analyze each raw take or reuse hash/version-matched persisted analysis;
2. build and atomically persist `acoustic_seam_plan`;
3. pass `asdict(plan)` to `stitch_segments()`;
4. retain exact final Deepgram transcript and 100-320 ms word-gap gates;
5. extract 1.5-second seam excerpts centered on persisted picture cuts;
6. run and persist deterministic `acoustic_seam_qa` plus the qualitative report;
7. require both reports before caption success and upload.

Add CLI flags:

```python
parser.add_argument("--acoustic-seams", action="store_true")
parser.add_argument("--acoustic-model", default="gemini-2.5-flash")
```

Pass them into composition. A valid cached legacy caption must not short-circuit an acoustic run because it lacks the acoustic plan/report.

When an acoustic plan exists, `upload_final()` uses the distinct filename `semantic-ugc-<run-id>-acoustic-preview-captioned.mp4`, preserving the prior public object.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run acoustic, runner, stitcher, composer, voice-QA, and upload tests. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/features/shot_production/acoustic_qa.py app/features/shot_production/runner.py \
  scripts/run_semantic_ugc_pilot.py tests/test_shot_production_acoustic_qa.py \
  tests/test_shot_production_runner.py
git commit -m "feat: gate acoustic continuity before delivery"
```

### Task 5: Recompose and Prove the Existing Four-Take Preview

**Files:**
- Runtime update: `output/semantic-ugc-pilot/2026-07-11-ayra-semantic-16s-v2/manifest.json`
- Runtime create: versioned history and revised `stitched.mp4`
- Runtime create: revised `final-captioned.mp4`
- Runtime create: seam excerpts and QA evidence under the existing run directory

- [ ] **Step 1: Run focused and scoped regression tests**

```bash
APP_ENV_FILE=/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.env \
/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.venv/bin/python -m pytest -q \
  tests/test_shot_production_audio_seams.py \
  tests/test_shot_production_acoustic_qa.py \
  tests/test_video_stitcher.py \
  tests/test_shot_production_composer.py \
  tests/test_shot_production_runner.py \
  tests/test_shot_production_voice_qa.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Recompose without approving paid work**

```bash
APP_ENV_FILE=/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.env \
/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.venv/bin/python \
  scripts/run_semantic_ugc_pilot.py \
  --manifest output/semantic-ugc-pilot/2026-07-11-ayra-semantic-16s-v2/manifest.json \
  --approved-frame output/semantic-ugc-pilot/input/approved-master.png \
  --approved-sha 10e493306de65ae7530860f365e148d3b8272ea53a35229505eb2dd783653bda \
  --script-input output/semantic-ugc-pilot/input/generated-script.json \
  --resume --recompose --recompose-reason "approved native voice acoustic seam preview" \
  --acoustic-seams
```

Expected: no pending Veo operations, no Vertex submission, and a new local captioned artifact.

- [ ] **Step 3: Verify deterministic artifact contracts**

Probe H.264/AAC, 720x1280, 14.5-16.5 seconds, A/V duration delta within one 24 fps frame, exact final WER 0.0, three seam gaps inside 100-320 ms, no retained island over 80 ms, active-speech RMS range at most 1.5 dB, and passed acoustic/voice/visual/media QA.

- [ ] **Step 4: Inspect the actual output**

Create seam waveform/spectrogram and frame grids from the revised artifact. Compare the three revised seams against the archived prior delivery. Confirm hard picture cuts, intact actor/captions, no clipped phoneme, no duplicated inhale, and no long amplitude hole.

- [ ] **Step 5: Upload the distinct preview and verify readback**

Reuse the valid acoustic composition without another invalidation:

```bash
APP_ENV_FILE=/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.env \
/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.venv/bin/python \
  scripts/run_semantic_ugc_pilot.py \
  --manifest output/semantic-ugc-pilot/2026-07-11-ayra-semantic-16s-v2/manifest.json \
  --approved-frame output/semantic-ugc-pilot/input/approved-master.png \
  --approved-sha 10e493306de65ae7530860f365e148d3b8272ea53a35229505eb2dd783653bda \
  --script-input output/semantic-ugc-pilot/input/generated-script.json \
  --resume --acoustic-seams --upload
```

Confirm the new acoustic-preview storage key, public GET byte count, SHA-256 metadata, and local/remote byte equality. The previous public object must remain unchanged.

- [ ] **Step 6: Run final verification and commit source changes**

Run `git diff --check`, the scoped suite, and the repository's available broader tests. Record unrelated baseline failures separately. Do not commit generated media or credentials.

```bash
git status --short
git log --oneline -6
```

Expected: only intentionally ignored runtime output remains untracked; source commits are present and the revised public preview is ready for user review.
