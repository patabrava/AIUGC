# Duration-Driven Veo UGC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalize the semantic Veo pilot from exactly four short takes to the minimum number of at-most-eight-second takes required by the script, then prove the two-shot 16-second result against the X reference.

**Architecture:** A duration-aware semantic partitioner produces an arbitrary ordered beat sequence. The shot deck, prompt compiler, runner, voice/visual/acoustic gates and comparison reporter consume that sequence without fixed cardinality. Existing manifests remain array-driven and the paid request hash remains the immutability boundary.

**Tech Stack:** Python 3.9, dataclasses, Pillow, FFmpeg/FFprobe, Deepgram, Gemini 2.5 Flash, Vertex Veo 3.1, pytest.

---

### Task 1: Minimum-shot semantic planning

**Files:**
- Modify: `app/features/shot_production/planner.py`
- Modify: `tests/test_shot_production_planner.py`

- [ ] **Step 1: Write failing planner tests**

Add tests asserting that the approved 30-word German script produces two ordered 8-second beats, each preserves complete words, and a punctuation-rich approximately 50-second script produces seven beats with every provider duration at most eight seconds.

- [ ] **Step 2: Verify RED**

Run `pytest -q tests/test_shot_production_planner.py -k 'minimum_shots or fifty_second'` and confirm the existing three-to-four-beat planner fails both expectations.

- [ ] **Step 3: Implement the minimum-shot search**

Replace fixed `MIN_BEAT_COUNT`/`MAX_BEAT_COUNT` iteration with a smallest-feasible-count search. Candidate boundaries retain costs `0.0` for sentence punctuation, `1.0` for commas, `2.0` for coordinating conjunctions and `4.0` for neutral word boundaries. Reject partitions whose estimated beat speech exceeds `7.5` seconds or whose provider bucket exceeds eight seconds.

- [ ] **Step 4: Verify GREEN and compatibility**

Run `pytest -q tests/test_shot_production_planner.py tests/test_shot_production_composer.py` and require zero failures.

- [ ] **Step 5: Commit**

Commit planner and tests as `feat: plan minimum Veo shot counts`.

### Task 2: Cardinality-independent shot and request compilation

**Files:**
- Modify: `app/features/shot_production/shot_deck.py`
- Modify: `app/features/shot_production/prompts.py`
- Modify: `tests/test_shot_production_shot_deck.py`
- Modify: `tests/test_shot_production_prompts.py`

- [ ] **Step 1: Write failing tests**

Assert `derive_shot_deck(..., shot_count=2)` returns original and center variants, `shot_count=7` returns seven indexed variants using the deterministic framing cycle, and prompt compilation requires exact equality between beat and shot counts rather than four deck entries.

- [ ] **Step 2: Verify RED**

Run the two test modules and confirm `shot_count` is unsupported and the four-variant guard rejects valid two- and seven-shot plans.

- [ ] **Step 3: Implement variable deck sizing**

Add a positive `shot_count` argument. Precompute original, center, left and right image bytes, then emit `shot_count` indexed `ShotVariant` values by cycling that profile. Require `len(beats) == len(shot_deck)` in request compilation.

- [ ] **Step 4: Verify GREEN**

Run both test modules and require zero failures.

- [ ] **Step 5: Commit**

Commit as `feat: compile variable Veo shot decks`.

### Task 3: Generalize manifest and continuity gates

**Files:**
- Modify: `app/features/shot_production/runner.py`
- Modify: `app/features/shot_production/voice_qa.py`
- Modify: `app/features/shot_production/visual_qa.py`
- Modify: `app/features/shot_production/acoustic_qa.py`
- Modify: `scripts/run_semantic_ugc_pilot.py`
- Modify: `tests/test_shot_production_runner.py`
- Modify: `tests/test_shot_production_voice_qa.py`
- Modify: `tests/test_shot_production_visual_qa.py`
- Modify: `tests/test_shot_production_acoustic_qa.py`

- [ ] **Step 1: Write failing cardinality tests**

Test two- and seven-take initialization, dynamic request-contract validation, two-or-more full-clip voice QA with range-checked outlier indexes, arbitrary visual contact-sheet take counts, and `N-1` acoustic seam clips.

- [ ] **Step 2: Verify RED**

Run the four focused modules and confirm failures cite the current fixed-four guards.

- [ ] **Step 3: Implement dynamic contracts**

Persist `planning_profile="minimum-eight-second-shots-v1"` and `requested_duration_seconds` in new manifests. Validate the stored duration array against the stored takes and supported provider buckets instead of `[4,6,6,4]`. Make voice QA prompt/cardinality and outlier range dynamic. Make acoustic QA accept exactly the supplied adjacent seam count and require at least one seam.

- [ ] **Step 4: Verify GREEN and old-manifest compatibility**

Run the semantic runner, voice, visual, acoustic, stitcher and composer tests. Existing four-take fixtures must still resume and pass.

- [ ] **Step 5: Commit**

Commit as `feat: run duration driven semantic pilots`.

### Task 4: Reference comparison report

**Files:**
- Create: `app/features/shot_production/reference_comparison.py`
- Create: `tests/test_shot_production_reference_comparison.py`
- Create: `scripts/compare_semantic_ugc_reference.py`

- [ ] **Step 1: Write failing metric tests**

Use synthetic cut timestamps to prove shot-duration derivation, cuts-per-second calculation and closeness scoring against a reference. Reject missing media and non-increasing cut timestamps.

- [ ] **Step 2: Verify RED**

Run the new test and confirm the comparison module does not exist.

- [ ] **Step 3: Implement deterministic reporting**

Probe duration with FFprobe and detect scene cuts with FFmpeg `select=gt(scene\,0.12),showinfo`. Produce JSON containing duration, cuts, shot durations, cut density, seconds per cut, reference distance, control distance and `closer_to_reference_than_control`.

- [ ] **Step 4: Verify GREEN**

Run the new tests, then run the CLI against the cached X reference and current four-take control.

- [ ] **Step 5: Commit**

Commit as `feat: compare UGC editorial cut density`.

### Task 5: Paid two-shot proof and delivery

**Runtime artifacts:**
- Create: `output/semantic-ugc-pilot/2026-07-12-ayra-minimum-shots-16s/manifest.json`
- Create: the run's raw takes, QA evidence, stitched and captioned MP4s
- Create: the run's X comparison JSON

- [ ] **Step 1: Run the scoped suite**

Run all shot-production planner, prompt, deck, runner, continuity, stitcher and comparison tests with the main checkout `.env` and virtualenv.

- [ ] **Step 2: Initialize and inspect the paid plan**

Initialize the new manifest from the approved master and audited revised script. Confirm exactly two pending takes, both use `veo-3.1-generate-001`, duration eight seconds, the expected script halves and unchanged continuity locks.

- [ ] **Step 3: Submit and finish end to end**

Run the CLI with `--confirm-paid-plan --acoustic-seams`. Poll both operations, retry only a failed take when a hard gate identifies it, then require transcript WER 0.0, voice/visual/acoustic/media QA and captions.

- [ ] **Step 4: Compare to the X reference and control**

Run the comparison CLI with the X reference, four-take control and two-take candidate. Require exactly one candidate cut and `closer_to_reference_than_control=true`. Inspect seam audio and a before/after frame grid.

- [ ] **Step 5: Upload and verify**

Upload under a distinct content-addressed minimum-shot preview name. Download the public object and require identical size and SHA-256 to the local captioned MP4.

- [ ] **Step 6: Final verification**

Run `git diff --check`, the scoped suite and the broader suite. Record unrelated baseline failures separately and leave generated media untracked.
