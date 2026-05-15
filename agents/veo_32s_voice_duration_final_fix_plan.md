# Veo 32s Voice And Duration Final Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution or superpowers:subagent-driven-development only if the user explicitly requests delegated implementation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the 32-second UI video-generation path so a reviewed 32s script produces one coherent 32s-ish Veo chain without underfilled segments, repeated speech, voice overlap, or unintended music/background voices.

**Architecture:** Keep the fix inside the existing video/post vertical slice. Treat video submission as the final contract gate: scripts and prompts may be generated upstream, but no paid Veo call is allowed unless the actual submitted segment chain, prompt text, Vertex payload, and audit rows satisfy the contract. Preserve the current WIP sentence-boundary/model-routing fixes; add missing budget validation, prompt de-duplication, and Vertex parity tests before another live UI run.

**Tech Stack:** Python 3.11 target, current local `.venv` Python 3.9.6 observed in tests, FastAPI, Supabase, Vertex AI Veo, pytest, ffprobe/ffmpeg for post-run media inspection.

**Locality Budget:** `{files: 6 modified + 1 plan file, LOC/file: <=120 changed LOC per production file, <=160 changed LOC per test file, deps: 0}`

---

## Context-Zero

- OS/runtime: macOS local checkout at `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC`; shell `zsh`.
- Current branch: `main`.
- Current worktree: dirty. Existing WIP touches `app/features/videos/handlers.py`, `workers/video_poller.py`, `tests/test_video_duration_routing.py`, and `tests/test_video_poller_extension_chain.py`. Do not revert it; it contains the sentence-boundary split and Vertex model-routing work.
- Local verification run on 2026-05-15:

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py tests/test_video_poller_extension_chain.py
```

Expected current baseline: `110 passed`, with Python 3.9 / Pydantic warnings only.

## Root Cause Map

### Failure Boundary 1: Underfilled 32s Chains

The latest 32s chain asks Veo for `8 + 7 + 7 + 7`, which yields about 29 seconds of provider video. The observed scripts often contain only 16-22 seconds of spoken material, and some extension hops receive 7-12 words. A 7-second hop with 7-9 spoken words forces Veo to fill time with silence, room tone, repeated mouth movement, or drift.

### Failure Boundary 2: Base Prompt Contradiction

`_build_veo_extended_base_prompt(...)` currently reads a saved `ending_directive` from `video_prompt_json`; for extended chains this can put a final-stop ending into a non-final base segment while the audio block says to continue without trailing silence.

### Failure Boundary 3: Prompt Duplication

`build_video_prompt_from_seed(...)` still puts the spoken line in `Action:` via `She says: ...` and also in `Dialogue:`. If a fallback prompt path is used, `prompt_text.py` can append `audio.dialogue` again. Veo 3/3.1 prompt rewriting cannot be disabled, so duplicated speech is high-risk.

### Failure Boundary 4: Vertex Parity

The non-Vertex Veo path forwards `negativePrompt` and `seed` on extension. The Vertex extension payload currently does not. Current Google docs list `negativePrompt` and `seed` as optional extension parameters, and Google’s best practices recommend the same seed for visual, stylistic, and voice consistency across scenes.

## Files

| File | Action | Responsibility |
| --- | --- | --- |
| `app/features/videos/handlers.py` | Modify | Final 32s submission gate, segment budget validation, extended-base prompt cleanup, seed policy |
| `app/features/posts/prompt_builder.py` | Modify | Remove spoken-line duplication from Action and expose a prompt contract that keeps dialogue in one place |
| `app/features/posts/prompt_text.py` | Modify | Prevent fallback prompt assembly from appending dialogue/audio duplicates when a provider prompt already exists |
| `app/adapters/vertex_ai_client.py` | Modify | Forward `negativePrompt` and `seed` through Vertex text-video and extension payloads when supplied |
| `workers/video_poller.py` | Modify | Pass Vertex extension `negative_prompt` and `veo_seed`; keep requested model propagation |
| `tests/test_video_duration_routing.py` | Modify | Lock per-hop budgets and extended-base prompt contract |
| `tests/test_veo_prompt_contract.py` | Modify | Lock single-dialogue prompt assembly and fallback de-duplication |
| `tests/test_video_poller_extension_chain.py` | Modify | Lock Vertex extension payload parity |
| `tests/test_vertex_ai_client.py` | Modify | Lock Vertex payload includes optional `negativePrompt` and `seed` |

## Capability Map

- A 32s chain is valid only when every submitted segment has enough spoken words for its assigned Veo window.
- A script that is too short must fail before provider submission with structured diagnostics, not burn credits.
- Base and continuation prompts must contain the spoken line exactly once.
- Extended base prompts must not include final-stop ending language unless the base is also the final segment.
- Vertex base and extension payloads must preserve `negativePrompt` and `seed` when available.
- The final acceptance test is a UI-created 32-second script and a UI-submitted 32-second video, followed by Supabase audit and media inspection.

## Task 1: Lock Current WIP Baseline

**Files:**
- Read: `app/features/videos/handlers.py`
- Read: `workers/video_poller.py`
- Read: `tests/test_video_duration_routing.py`
- Read: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Capture dirty diff before edits**

```bash
git status --short
git diff --stat -- app/features/videos/handlers.py workers/video_poller.py tests/test_video_duration_routing.py tests/test_video_poller_extension_chain.py
```

Expected: only the known WIP files and untracked handoff/plan artifacts are dirty. If unrelated tracked files are dirty, do not edit them.

- [ ] **Step 2: Re-run the current focused baseline**

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py tests/test_video_poller_extension_chain.py
```

Expected: `110 passed`. If this fails, stop and diagnose before changing code.

## Task 2: Add Per-Hop Spoken Budget Gate

**Files:**
- Modify: `app/features/videos/handlers.py`
- Modify: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Add failing tests for the exact underfilled live scripts**

Add tests near the existing 32s segment-packing tests:

```python
def test_build_veo_extended_base_prompt_rejects_underfilled_32s_hop():
    seed_data = {
        "script": (
            "Alle reden über Sport und Muskeln, wenn es um Energie im Rollstuhl geht. "
            "Aber niemand spricht darüber, wie wichtig die richtige Sitzposition wirklich ist. "
            "Ich dachte früher, das sei nur Komfort. "
            "Dabei entlastet eine optimale Haltung unglaublich und spart dir Kraft."
        ),
        "estimated_duration_s": 16,
    }

    with pytest.raises(ValidationError) as exc_info:
        video_handlers._build_veo_extended_base_prompt(
            seed_data,
            planned_extension_hops=3,
            target_length_tier=32,
        )

    details = exc_info.value.details
    assert details["target_length_tier"] == 32
    assert details["segment_index"] == 2
    assert details["word_count"] == 7
    assert details["minimum_words"] >= 12
```

- [ ] **Step 2: Add the local validation helper**

Place this near `_segment_time_budget_seconds(...)`:

```python
def _minimum_words_for_veo_window(*, budget_seconds: int, is_final_segment: bool) -> int:
    if budget_seconds >= 8:
        return 16
    return 12 if is_final_segment else 14


def _validate_veo_segment_spoken_budget(
    *,
    segments: list[str],
    profile: Any,
    target_length_tier: Optional[int],
) -> None:
    if profile.route != VEO_EXTENDED_VIDEO_ROUTE:
        return

    for index, segment in enumerate(segments):
        budget_seconds = _segment_time_budget_seconds(profile=profile, segment_index=index)
        word_count = len(str(segment).split())
        is_final = index == len(segments) - 1
        minimum_words = _minimum_words_for_veo_window(
            budget_seconds=budget_seconds,
            is_final_segment=is_final,
        )
        if word_count < minimum_words:
            raise ValidationError(
                "Veo extended segment is too short for its assigned duration window.",
                details={
                    "target_length_tier": target_length_tier,
                    "segment_index": index,
                    "budget_seconds": budget_seconds,
                    "word_count": word_count,
                    "minimum_words": minimum_words,
                    "segment_preview": str(segment)[:180],
                },
            )
```

- [ ] **Step 3: Call the validator after final packing**

In `_build_veo_extended_base_prompt(...)`, after `segments = _pack_veo_segments_for_profile(...)` and after `profile` is resolved:

```python
if profile is not None and profile.route == VEO_EXTENDED_VIDEO_ROUTE:
    _validate_veo_segment_spoken_budget(
        segments=segments,
        profile=profile,
        target_length_tier=target_length_tier,
    )
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py::test_build_veo_extended_base_prompt_rejects_underfilled_32s_hop tests/test_video_duration_routing.py
```

Expected: new rejection test passes; existing 32s valid-chain tests still pass.

## Task 3: Clear Final Ending From Extended Base Prompts

**Files:**
- Modify: `app/features/videos/handlers.py`
- Modify: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Add failing test**

```python
def test_extended_base_prompt_does_not_inherit_final_stop_ending():
    seed_data = {
        "script": (
            "Erster ausreichend langer Satz für den Start dieses Videos. "
            "Zweiter ausreichend langer Satz für die erste Erweiterung. "
            "Dritter ausreichend langer Satz für die zweite Erweiterung. "
            "Vierter ausreichend langer Satz für die finale Erweiterung."
        )
    }
    saved_prompt = {
        "audio": {"dialogue": seed_data["script"]},
        "ending_directive": "After the final spoken word, speech stops completely.",
    }

    prompt, _metadata = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        saved_prompt,
        planned_extension_hops=3,
        target_length_tier=32,
    )

    assert "After the final spoken word, speech stops completely." not in prompt
    assert "Continue directly into the next segment" in prompt
```

- [ ] **Step 2: Clear the saved ending for extended routes**

Inside the existing `if profile is not None and profile.route == VEO_EXTENDED_VIDEO_ROUTE:` block:

```python
prompt_ending = None
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py::test_extended_base_prompt_does_not_inherit_final_stop_ending tests/test_video_duration_routing.py
```

Expected: extended base prompt uses continuation ending; final extension hop still uses final ending through `build_lean_veo_continuation_prompt(... include_final_ending=True)`.

## Task 4: Remove Dialogue Duplication From Base Prompt Contract

**Files:**
- Modify: `app/features/posts/prompt_builder.py`
- Modify: `app/features/posts/prompt_text.py`
- Modify: `tests/test_veo_prompt_contract.py`

- [ ] **Step 1: Add prompt duplication tests**

```python
def test_veo_prompt_does_not_repeat_dialogue_in_action_and_dialogue():
    script = "Das ist ein eindeutiger Testsatz für die Sprachspur."
    prompt = build_video_prompt_from_seed({"script": script})
    veo_prompt = prompt["veo_prompt"]

    assert veo_prompt.count(script) == 1
    assert "She says:" not in veo_prompt
    assert "Dialogue:" in veo_prompt


def test_full_prompt_text_does_not_append_audio_dialogue_when_provider_prompt_exists():
    script = "Dieser Satz darf im Fallback nicht doppelt auftauchen."
    prompt = build_video_prompt_from_seed({"script": script})
    full_text = video_handlers._build_provider_prompt_text(prompt, "sora")[0]

    assert full_text.count(script) == 1
```

- [ ] **Step 2: Remove speech from the action block**

In `build_video_prompt_from_seed(...)`, replace the `script_line` / `action_value` block with:

```python
action_value = (
    "Seated in a wheelchair in the bedroom, she speaks directly to camera in one continuous "
    "take. She speaks at a natural conversational pace, uses small natural hand gestures and "
    "subtle upper-body nods while speaking, then holds a gentle smile and remains still briefly "
    "after the spoken line."
)
```

- [ ] **Step 3: Prevent fallback duplication**

In `app/features/posts/prompt_text.py`, update `_compose_prompt_sections(...)` so `audio.dialogue` is only appended when the prompt has no provider-assembled `veo_prompt` or `optimized_prompt`:

```python
has_provider_prompt = bool(video_prompt.get("veo_prompt") or video_prompt.get("optimized_prompt"))
...
if dialogue and not has_provider_prompt:
    sections.append(str(dialogue).strip())
```

- [ ] **Step 4: Run prompt contract tests**

```bash
.venv/bin/pytest -q tests/test_veo_prompt_contract.py
```

Expected: dialogue appears once in Veo prompt and fallback path.

## Task 5: Add Vertex Seed And Negative Prompt Parity

**Files:**
- Modify: `app/adapters/vertex_ai_client.py`
- Modify: `workers/video_poller.py`
- Modify: `app/features/videos/handlers.py`
- Modify or create: `tests/test_vertex_ai_client.py`
- Modify: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Add adapter payload tests**

```python
def test_vertex_extension_payload_includes_seed_and_negative_prompt():
    client = VertexAIClient()
    payload = client._build_extension_request_payload(
        prompt="Weiterer Satz.",
        video_uri="gs://bucket/input.mp4",
        video_mime_type="video/mp4",
        aspect_ratio="9:16",
        duration_seconds=7,
        output_gcs_uri="gs://bucket/output/",
        negative_prompt="music bed, background voices",
        seed=12345,
    )

    params = payload["parameters"]
    assert params["negativePrompt"] == "music bed, background voices"
    assert params["seed"] == 12345
```

- [ ] **Step 2: Extend Vertex adapter signatures and payload builders**

Add optional `negative_prompt: Optional[str] = None` and `seed: Optional[int] = None` to `submit_text_video(...)`, `submit_video_extension(...)`, `_build_request_payload(...)`, and `_build_extension_request_payload(...)`. In each payload builder:

```python
if negative_prompt:
    parameters["negativePrompt"] = negative_prompt
if seed is not None:
    parameters["seed"] = seed
```

- [ ] **Step 3: Thread values from handlers and worker**

For Vertex base submit in `_submit_video_request(...)`:

```python
result = vertex_client.submit_text_video(
    prompt=prompt_text,
    correlation_id=correlation_id,
    aspect_ratio=aspect_ratio,
    duration_seconds=vertex_duration,
    output_gcs_uri=output_gcs_uri,
    model=model,
    reference_images=reference_images,
    negative_prompt=negative_prompt,
    seed=seed,
)
```

For Vertex extension in `workers/video_poller.py`:

```python
result = vertex_client.submit_video_extension(
    prompt=prompt,
    video_uri=video_uri,
    video_mime_type=video_mime_type,
    correlation_id=f"{correlation_id}_ext_{hops_completed + 1}",
    aspect_ratio=metadata.get("provider_aspect_ratio", metadata.get("requested_aspect_ratio", "9:16")),
    duration_seconds=7,
    output_gcs_uri=output_gcs_uri,
    model=requested_model,
    negative_prompt=negative_prompt,
    seed=metadata.get("veo_seed"),
)
```

- [ ] **Step 4: Decide seed policy explicitly**

Current code only assigns `veo_seed` for non-Vertex `veo_3_1`. Change `_should_assign_veo_seed(...)` to return true for `provider in {VEO_PROVIDER, "vertex_ai"}` when the profile is an actual extended route. If legacy 32s must remain unseeded, write that as a failing/expected test and document the reason. Do not leave it as accidental drift.

- [ ] **Step 5: Run adapter and worker tests**

```bash
.venv/bin/pytest -q tests/test_video_poller_extension_chain.py tests/test_vertex_ai_client.py tests/test_video_duration_routing.py
```

Expected: Vertex extension payload now includes `negativePrompt` and `seed` when available; requested model propagation still passes.

## Task 6: Tighten 32s Upstream Script Envelope

**Files:**
- Modify: `app/core/video_profiles.py`
- Modify: focused topic/script-generation tests if they assert old 32s word ranges

- [ ] **Step 1: Raise the 32s minimums**

Change both `_BASE_PROFILES[32]` and `_EFFICIENT_LONG_ROUTE_PROFILES[32]`:

```python
prompt1_min_words=68,
prompt1_max_words=88,
prompt2_min_words=64,
prompt2_max_words=84,
```

Keep `provider_target_seconds=29` and `8 + 7 + 7 + 7` route unchanged.

- [ ] **Step 2: Run duration/profile tests**

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py tests/test_topic_prompt_templates.py tests/test_topics_gemini_flow.py
```

Expected: 32s script-generation constraints now ask for enough spoken material before video generation.

## Task 7: Add Preflight Simulation Before Paid Submit

**Files:**
- Modify: `app/features/videos/handlers.py`
- Test: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Add a debug metadata contract**

After budget validation passes, add per-segment budget diagnostics to `segment_metadata`:

```python
"veo_segment_spoken_budgets": [
    {
        "segment_index": index,
        "budget_seconds": _segment_time_budget_seconds(profile=profile, segment_index=index),
        "word_count": len(segment.split()),
    }
    for index, segment in enumerate(segments)
],
```

- [ ] **Step 2: Log preflight result before provider submit**

When building `prepared_submissions`, log:

```python
logger.info(
    "veo_extended_preflight_passed",
    post_id=post_id,
    batch_id=batch_id,
    target_length_tier=profile.target_length_tier if profile else None,
    segments=segment_metadata.get("veo_segment_spoken_budgets") if segment_metadata else None,
)
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py
```

Expected: metadata exposes segment word counts and budget seconds for audit before live generation.

## Task 8: Verification Before Live Credits

**Files:**
- No production edits

- [ ] **Step 1: Run focused regression suite**

```bash
.venv/bin/pytest -q \
  tests/test_video_duration_routing.py \
  tests/test_veo_prompt_contract.py \
  tests/test_video_poller_extension_chain.py \
  tests/test_vertex_ai_client.py
```

Expected: all pass.

- [ ] **Step 2: Run broader video/topic guard suite**

```bash
.venv/bin/pytest -q \
  tests/test_video_quota_guard.py \
  tests/test_batches_manual_mode.py \
  tests/test_topics_gemini_flow.py \
  tests/test_topic_prompt_templates.py
```

Expected: all pass, or any failure is unrelated and documented before live testing.

- [ ] **Step 3: Inspect exact prompt once locally**

Use a local script or `python - <<'PY'` one-off to call `_build_veo_extended_base_prompt(...)` on a target 32s script and print:

```text
segment_index | budget_seconds | word_count | segment_text
```

Pass condition: every segment passes the minimum budget and the prompt contains the spoken line once.

## Task 9: Final UI Acceptance Run

**Files:**
- No code edits unless live verification exposes a defect

- [ ] **Step 1: Start the app and verify readiness**

Use the repo’s normal local server path. Confirm `/livez` and UI load before touching paid generation.

- [ ] **Step 2: From the UI, create a fresh 32s batch/script**

Use the actual UI flow, not a direct database insert. The reviewed/approved script must be visibly a 32-second script and should pass the per-segment preflight.

- [ ] **Step 3: Submit one 32s video from the UI**

Submit through the UI with provider `vertex_ai` / selected Veo model. Do not submit a batch of multiple videos until one passes.

- [ ] **Step 4: Verify Supabase truth**

Check `posts.video_metadata` and `video_prompt_audit` for the submitted post:

- `target_length_tier = 32`
- `veo_segments_total = 4`
- `veo_segment_spoken_budgets` all meet minimums
- base + 3 extension audit rows exist
- all rows use the requested model
- extension rows preserve `negative_prompt`
- seed is present when seed policy says it should be
- submitted prompt text contains each spoken segment once

- [ ] **Step 5: Verify final media**

Download the final captioned MP4 and run:

```bash
ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 <video.mp4>
ffmpeg -hide_banner -nostats -i <video.mp4> -af silencedetect=noise=-35dB:d=0.35 -f null -
```

Acceptance target:

- final duration is around the provider route length for 32s, about 28-30 seconds;
- no internal seam silence longer than about 1.0 second;
- no repeated spoken line or overlapping voice;
- no background music or background voices;
- captions/transcript match the submitted script;
- character and voice remain acceptably consistent for review.

- [ ] **Step 6: Human evaluation checkpoint**

Save the final MP4 URL, prompt audit IDs, post ID, batch ID, and media-analysis output in a new `agents/veo_32s_final_live_verification.md`. Stop there so the video can be evaluated together before generating more.

## Official Reference Checks

- Google’s current Veo extension docs list 7-second extension output and optional `negativePrompt` / `seed` parameters for REST extension requests.
- Google’s prompt guide recommends separate audio sentences and describes dialogue as explicit spoken words.
- Google’s best practices recommend copying the unchanged character description and using the same seed for consistent visual, stylistic, and voice output across scenes.
- Google’s prompt rewriter page says Veo 3 and 3.1 do not allow disabling the rewriter, which makes prompt duplication unsafe.

## Self-Review

- Spec coverage: script length mismatch, voice overlap, background music, previous commit drift, and final UI 32s generation are all covered.
- Placeholder review: no unresolved placeholder language remains.
- Locality envelope: changes stay in 6 production/test areas plus this plan, with zero new dependencies.
- Cost control: paid provider generation is last, after local tests and preflight segment/prompt inspection.
