# VEO Drift Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Veo continuation drift on the active efficient Veo long-route flow by making segment packing duration-aware and slimming extension prompts to stable anchors plus hop-specific deltas.

**Architecture:** Keep the existing vertical slice around VEO submission intact. Add a small duration estimator and a greedy segment packer in the video handler, keep segment metadata persisted on submission, and teach the extension prompt builder to reuse a stable prompt core instead of repeating the full prompt baggage on every hop. The implementation target is the active efficient long-route flow: `16s = 8+7` and `32s = 8+7+7+7`.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, existing prompt builder utilities, existing VEO worker flow

**Locality Budget:** `{files: 6 modified + 1 doc, LOC/file: <= 180 touched lines, deps: 0 new dependencies}`

**Spec Inputs:** `simulation-promptFlow.md`, `prompt_consistency.md`, `docs/character_consistency.md`, repo rules in `AGENTS.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `app/features/videos/handlers.py` | Add dialogue-duration estimation, duration-aware segment packing, and persisted prompt-core metadata |
| `app/features/posts/prompt_builder.py` | Add a minimal extension prompt composition path that keeps stable identity anchors and only changes hop-specific dialogue/ending/audio wording |
| `workers/video_poller.py` | Reuse persisted segments and prompt core for extension hops; stop rebuilding from the full script shape |
| `tests/test_video_duration_routing.py` | Lock route policy and test duration-aware segmentation behavior |
| `tests/test_veo_prompt_contract.py` | Test stable-core/minimal-delta prompt composition |
| `tests/test_video_poller_extension_chain.py` | Test extension-hop prompt assembly against persisted metadata |
| `simulation-promptFlow.md` | Refresh the “recommended” section to match the implemented contract |

## Constraints

- Build against the active efficient long-route contract: `16s = 8+7` and `32s = 8+7+7+7`.
- The current `AGENTS.md` repo note still says `32s` should remain on the legacy `4+7+7+7+7` chain. Treat that as stale documentation to be reconciled during implementation, not as the target behavior for this plan.
- Do not add dependencies. Use a small local estimator based on words/sentence cadence rather than a speech library.
- Preserve sentence boundaries. Duration-aware packing may rebalance sentence groups, but must not cut inside a sentence.
- Persist the exact packed segments to `video_metadata.veo_segments` and use them verbatim in the poller.

---

### Task 1: Lock The Efficient Route Policy And Prompt Baseline In Tests

**Files:**
- Modify: `tests/test_video_duration_routing.py`
- Modify: `tests/test_veo_prompt_contract.py`
- Modify: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Write failing tests for the active route contract**

In `tests/test_video_duration_routing.py`, add assertions that the active long-route profile is `16s = 8+7` and `32s = 8+7+7+7`:

```python
def test_efficient_long_route_applies_to_16s_and_32s(monkeypatch):
    monkeypatch.setattr(get_settings(), "veo_enable_efficient_long_route", True)
    profile_16 = get_duration_profile(16)
    profile_32 = get_duration_profile(32)
    assert profile_16.veo_base_seconds == 8
    assert profile_16.veo_extension_hops == 1
    assert profile_32.veo_base_seconds == 8
    assert profile_32.veo_extension_hops == 3
```

- [ ] **Step 2: Write failing tests for slim extension prompts**

In `tests/test_veo_prompt_contract.py`, add a prompt-shape contract test:

```python
def test_build_veo_prompt_segment_minimal_extension_reuses_stable_core():
    prompt = build_veo_prompt_segment(
        "Zweiter Satz.",
        include_ending=False,
        character="Stable character",
        style="Stable style",
        scene="Stable scene",
        cinematography="Stable cinematography",
        audio_block="Continuous speech, no trailing room tone.",
    )
    assert "Stable character" in prompt
    assert "Stable style" in prompt
    assert "Stable scene" in prompt
    assert "Stable cinematography" in prompt
    assert "Zweiter Satz." in prompt
    assert "Continue directly into the next segment with no concluding pause or scene-ending hold." in prompt
```

In `tests/test_video_poller_extension_chain.py`, add a worker-level contract test:

```python
def test_extension_prompt_uses_persisted_segments_and_prompt_core():
    post = {
        "seed_data": {"script": "S1. S2. S3."},
        "video_metadata": {
            "veo_segments": ["S1.", "S2.", "S3."],
            "veo_prompt_core": {
                "character": "Stable character",
                "style": "Stable style",
                "scene": "Stable scene",
                "cinematography": "Stable cinematography",
            },
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 0,
        },
    }
    result = _build_veo_extension_prompt(post, segment_index=1)
    assert "S2." in result["prompt_text"]
    assert "Stable character" in result["prompt_text"]
```

- [ ] **Step 3: Run the targeted tests to verify they fail**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_video_duration_routing.py \
  tests/test_veo_prompt_contract.py \
  tests/test_video_poller_extension_chain.py
```

Expected: FAIL on the efficient-route and prompt-core assertions.

- [ ] **Step 4: Commit the red tests**

```bash
git add tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py tests/test_video_poller_extension_chain.py
git commit -m "test: lock efficient veo route and drift-control prompt contracts"
```

---

### Task 2: Add Duration-Aware Segment Packing In The Submission Path

**Files:**
- Modify: `app/features/videos/handlers.py`
- Modify: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Write failing duration-packing tests**

In `tests/test_video_duration_routing.py`, add focused tests around estimated spoken-time packing:

```python
def test_duration_aware_packing_balances_dense_first_sentence_for_16s():
    seed_data = {
        "script": (
            "Das erste Segment ist deutlich laenger und dichter als die anderen Saetze. "
            "Kurzer Satz. Noch ein kurzer Satz."
        ),
    }
    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        seed_data,
        planned_extension_hops=1,
        target_length_tier=16,
    )
    assert len(seg_meta["veo_segments"]) == 2
    assert seg_meta["veo_segments"][0] != (
        "Das erste Segment ist deutlich laenger und dichter als die anderen Saetze."
    )


def test_duration_aware_packing_preserves_sentence_boundaries():
    segments = video_handlers._pack_veo_segments_for_profile(
        ["Erster Satz.", "Zweiter Satz.", "Dritter Satz."],
        planned_extension_hops=1,
        target_length_tier=16,
    )
    assert all(segment.endswith((".", "!", "?")) for segment in segments)
```

- [ ] **Step 2: Implement a local spoken-duration estimator**

In `app/features/videos/handlers.py`, add small helpers above `_pack_veo_segments_for_profile(...)`:

```python
def _estimate_segment_seconds(segment: str) -> float:
    words = len(segment.split())
    punctuation_pauses = segment.count(",") * 0.12 + segment.count(":") * 0.18
    sentence_stop = 0.35 if segment.endswith((".", "!", "?")) else 0.0
    return round((words / 2.35) + punctuation_pauses + sentence_stop, 2)


def _target_segment_seconds(profile: DurationProfile, required_segments: int) -> list[float]:
    targets = [float(profile.veo_base_seconds)]
    targets.extend([float(profile.veo_extension_seconds)] * max(required_segments - 1, 0))
    return targets
```

- [ ] **Step 3: Replace merge-left packing with greedy time-aware packing**

In `app/features/videos/handlers.py`, replace the body of `_pack_veo_segments_for_profile(...)` with a greedy packer:

```python
    targets = _target_segment_seconds(profile, required_segments)
    packed_segments: list[str] = []
    current_sentences: list[str] = []
    current_seconds = 0.0

    remaining = list(segments)
    for target_index, target_seconds in enumerate(targets):
        while remaining:
            sentence = remaining[0]
            sentence_seconds = _estimate_segment_seconds(sentence)
            remaining_slots = len(targets) - len(packed_segments) - 1
            must_leave = len(remaining) - 1 >= remaining_slots
            next_total = current_seconds + sentence_seconds

            if current_sentences and next_total > target_seconds and must_leave:
                break

            current_sentences.append(remaining.pop(0))
            current_seconds = next_total

        packed_segments.append(" ".join(current_sentences).strip())
        current_sentences = []
        current_seconds = 0.0

    if remaining:
        packed_segments[-1] = f"{packed_segments[-1]} {' '.join(remaining)}".strip()
```

Implementation notes:
- Run this packer for the active efficient long-route profiles, including `32s = 8+7+7+7`.
- Keep the existing under-segmentation validation.
- Do not silently cap or pad hops.

- [ ] **Step 4: Persist duration-debug metadata**

In `_build_veo_extended_base_prompt(...)`, add compact observability fields:

```python
    segment_metadata["veo_segment_seconds_estimate"] = [
        _estimate_segment_seconds(segment) for segment in segments
    ]
```

This gives the simulation doc and debugging flow evidence for why segments were grouped.

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/pytest -q tests/test_video_duration_routing.py
```

Expected: PASS, including the new balancing assertions.

- [ ] **Step 6: Commit**

```bash
git add app/features/videos/handlers.py tests/test_video_duration_routing.py
git commit -m "feat: pack veo dialogue segments by estimated spoken duration"
```

---

### Task 3: Split Stable Prompt Core From Hop-Specific Delta

**Files:**
- Modify: `app/features/posts/prompt_builder.py`
- Modify: `app/features/videos/handlers.py`
- Modify: `tests/test_veo_prompt_contract.py`

- [ ] **Step 1: Write failing prompt-core persistence tests**

In `tests/test_video_duration_routing.py`, add:

```python
def test_extended_base_prompt_persists_stable_prompt_core():
    _prompt, seg_meta = video_handlers._build_veo_extended_base_prompt(
        {"script": "Erster Satz. Zweiter Satz. Dritter Satz."},
        planned_extension_hops=1,
        target_length_tier=16,
    )
    assert seg_meta["veo_prompt_core"]["character"]
    assert seg_meta["veo_prompt_core"]["scene"]
    assert seg_meta["veo_prompt_core"]["cinematography"]
```

- [ ] **Step 2: Add a minimal-core composition path**

In `app/features/posts/prompt_builder.py`, expand `build_veo_prompt_segment(...)` so it can build from a stable prompt core and a small delta without pulling in unrelated full-script text:

```python
def build_veo_prompt_segment(
    dialogue: str,
    *,
    include_quotes: bool = False,
    include_ending: bool = False,
    prompt_core: Optional[dict[str, str]] = None,
    ...
) -> str:
    core = prompt_core or {}
    return build_optimized_prompt(
        prompt_dialogue,
        character=core.get("character") or character,
        style=core.get("style") or style,
        scene=core.get("scene") or scene,
        cinematography=core.get("cinematography") or cinematography,
        action=action,
        ending=ending,
        audio_block=audio_block,
        ...
    )
```

Do not add new sections or new prompt language. Reuse the existing canonical defaults and only make prompt assembly explicit.

- [ ] **Step 3: Persist the stable core on base prompt build**

In `_build_veo_extended_base_prompt(...)`, save only the stable fields needed by later hops:

```python
    segment_metadata["veo_prompt_core"] = {
        "character": DEFAULT_CHARACTER,
        "style": DEFAULT_STYLE,
        "scene": DEFAULT_SCENE_BODY,
        "cinematography": DEFAULT_CINEMATOGRAPHY,
    }
```

If the code already derives canonical long-form values through helper functions, call those helpers instead of duplicating constants.

- [ ] **Step 4: Run prompt-contract tests**

Run:

```bash
.venv/bin/pytest -q tests/test_veo_prompt_contract.py tests/test_video_duration_routing.py
```

Expected: PASS, and the extension prompt path no longer depends on the full-script `action` block or final audio wording from the stored prompt.

- [ ] **Step 5: Commit**

```bash
git add app/features/posts/prompt_builder.py app/features/videos/handlers.py tests/test_veo_prompt_contract.py tests/test_video_duration_routing.py
git commit -m "feat: persist stable veo prompt core for continuation hops"
```

---

### Task 4: Rewire The Poller To Use Persisted Segments And Prompt Core Only

**Files:**
- Modify: `workers/video_poller.py`
- Modify: `tests/test_video_poller_extension_chain.py`

- [ ] **Step 1: Write failing worker tests for core-only reuse**

In `tests/test_video_poller_extension_chain.py`, add:

```python
def test_build_veo_extension_prompt_does_not_fallback_to_full_script_rebuild():
    post = {
        "seed_data": {"script": "S1. S2. S3."},
        "video_prompt_json": {
            "action": "Old full-script action that should not be reused.",
            "audio_block": "Final-hop room tone that should not leak into continuation hops.",
        },
        "video_metadata": {
            "veo_segments": ["S1.", "S2.", "S3."],
            "veo_prompt_core": {
                "character": "Stable character",
                "style": "Stable style",
                "scene": "Stable scene",
                "cinematography": "Stable cinematography",
            },
            "veo_extension_hops_target": 2,
            "veo_extension_hops_completed": 0,
        },
    }
    result = _build_veo_extension_prompt(post, segment_index=1)
    assert "Old full-script action" not in result["prompt_text"]
    assert "Final-hop room tone" not in result["prompt_text"]
    assert "Continue directly into the next segment with no concluding pause or scene-ending hold." in result["prompt_text"]
```

- [ ] **Step 2: Update `_build_veo_extension_prompt(...)`**

In `workers/video_poller.py`, remove the fallback path that re-splits raw script when persisted segments exist, and prefer the persisted prompt core:

```python
    prompt_core = metadata.get("veo_prompt_core") or {}
    ...
    prompt_text = build_veo_prompt_segment(
        segment_text,
        include_quotes=False,
        include_ending=is_final,
        prompt_core=prompt_core,
        negative_constraints=None,
        legacy_32_visuals=legacy_32_visuals,
    )
```

Behavior notes:
- Persisted `video_metadata.veo_segments` remains the source of truth.
- Raw-script fallback stays only for legacy/recovery cases where metadata is absent.
- Continuation hops use continuation audio wording; final hop uses final-ending wording.

- [ ] **Step 3: Run worker tests**

Run:

```bash
.venv/bin/pytest -q tests/test_video_poller_extension_chain.py
```

Expected: PASS, including the new “no full-script rebuild” assertion.

- [ ] **Step 4: Commit**

```bash
git add workers/video_poller.py tests/test_video_poller_extension_chain.py
git commit -m "feat: build veo extension hops from persisted prompt core"
```

---

### Task 5: Refresh The Simulation Document And Run The Full Drift-Control Regression Set

**Files:**
- Modify: `simulation-promptFlow.md`

- [ ] **Step 1: Update the simulation narrative**

In `simulation-promptFlow.md`, revise the “Recommended” section so it matches the implemented contract:

```md
- Active `32s` route is efficient `8+7+7+7`.
- Efficient duration-aware packing applies to the long-route profiles in production.
- Extension hops reuse `video_metadata.veo_segments` and `video_metadata.veo_prompt_core`.
- Continuation hops keep stable character/scene anchors and change only dialogue + ending/audio delta.
```

- [ ] **Step 2: Run the targeted regression suite**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_video_duration_routing.py \
  tests/test_veo_prompt_contract.py \
  tests/test_video_poller_extension_chain.py
```

Expected: PASS.

- [ ] **Step 3: Run one integration regression**

Run:

```bash
.venv/bin/pytest -q tests/test_video_submission_flow.py
```

Expected: PASS, verifying the submission path still builds and stores prompt payloads correctly.

- [ ] **Step 4: Commit**

```bash
git add simulation-promptFlow.md tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py tests/test_video_poller_extension_chain.py workers/video_poller.py app/features/videos/handlers.py app/features/posts/prompt_builder.py
git commit -m "docs: align veo simulation with duration-aware drift controls"
```

---

## Self-Review

### Spec Coverage

- Duration-aware packing: covered in Task 2.
- Stable core plus slim hop deltas: covered in Tasks 3 and 4.
- Documentation-led implementation: covered by Constraints and Task 5.
- Preserve repo route policy for `32s`: covered in Task 1 and the Constraints section.

### Placeholder Scan

- No `TODO` or deferred implementation markers remain.
- Every task includes exact files, commands, and expected outcomes.

### Type Consistency

- `veo_prompt_core` is named consistently across Tasks 3 and 4.
- `veo_segment_seconds_estimate` is named once in Task 2 and referenced only as metadata.
- `build_veo_prompt_segment(..., prompt_core=...)` is the only new prompt-builder surface proposed here.

## Notes

- This plan assumes the efficient long-route contract is the real target behavior and treats the contrary `AGENTS.md` note as stale repo documentation that should be corrected during implementation.
- If you want, the next implementation pass should also update the stale repo rule and any adjacent docs/tests that still describe legacy `32s` as the default target.

Plan complete and saved to `docs/superpowers/plans/2026-04-03-veo-drift-reduction.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
