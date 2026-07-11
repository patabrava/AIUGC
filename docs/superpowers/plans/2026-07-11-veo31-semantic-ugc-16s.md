# Veo 3.1 Semantic UGC 16-Second Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate and verify one captioned 16-second-tier German UGC video made from independent semantic Veo 3.1 image-to-video takes anchored to an approved Gemini still.

**Architecture:** Add one focused `shot_production` module that compiles an app-generated script into semantic beats, derives non-generative crop variants from the approved still, builds auditable Veo requests, and composes completed takes through the existing Deepgram, stitcher, aligner, and caption adapters. A resumable CLI owns the live experiment and writes all paid-operation evidence to a local manifest.

**Tech Stack:** Python 3.9, Pillow, Vertex Veo 3.1 REST adapter, Gemini text adapter, Deepgram, FFmpeg/ffprobe, pytest.

---

### Task 1: Semantic Beat and Provider-Duration Compiler

**Files:**
- Create: `app/features/shot_production/__init__.py`
- Create: `app/features/shot_production/planner.py`
- Create: `tests/test_shot_production_planner.py`

- [ ] **Step 1: Write failing tests for semantic beats**

Cover a real 28-36-word German 16-second script, clause-boundary splitting, complete ordered word preservation, three-to-four beats, estimated 3-5 second pacing, and provider durations restricted to `{4, 6, 8}`.

- [ ] **Step 2: Run the planner tests and verify RED**

Run: `APP_ENV_FILE=/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.env /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.venv/bin/python -m pytest tests/test_shot_production_planner.py -q`

Expected: collection or import failure because `shot_production.planner` does not exist.

- [ ] **Step 3: Implement immutable `EditorialBeat` planning**

Implement a dataclass with `index`, `text`, `word_count`, `estimated_speech_seconds`, and `provider_duration_seconds`. Split on terminal punctuation and `:;`, then commas and coordinating boundaries only when necessary; rebalance without changing word order. Map speech estimates to 4/6/8-second provider buckets.

- [ ] **Step 4: Run the planner tests and verify GREEN**

Expected: all planner tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: plan semantic UGC takes`

### Task 2: Deterministic Approved Shot Deck and Veo Request Contract

**Files:**
- Create: `app/features/shot_production/shot_deck.py`
- Create: `app/features/shot_production/prompts.py`
- Modify: `app/adapters/vertex_ai_client.py`
- Create: `tests/test_shot_production_contract.py`
- Modify: `tests/test_vertex_ai_client.py`

- [ ] **Step 1: Write failing tests for crops and payloads**

Assert four 9:16 variants retain source dimensions, each records SHA-256 provenance, and every compiled Veo payload has one image, no references/video/lastFrame, full model, exact beat dialogue, matching non-empty negative prompt, supported duration, and deterministic seed.

- [ ] **Step 2: Run the contract tests and verify RED**

Expected: missing shot-deck/prompt functions and missing image-to-video seed transport.

- [ ] **Step 3: Implement minimal crop, prompt, and seed support**

Use Pillow LANCZOS resizing with declared center/left/right crop anchors. Add optional `seed` to `submit_image_video()` and pass it to `_build_request_payload()`. Keep the prompt first-frame-led and omit all legacy phenotype constants.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run both new contract tests and existing Vertex adapter tests.

- [ ] **Step 5: Commit**

Commit message: `feat: compile approved shot deck requests`

### Task 3: Transcript QA and Composition

**Files:**
- Create: `app/features/shot_production/composer.py`
- Create: `tests/test_shot_production_composer.py`

- [ ] **Step 1: Write failing tests for transcript evidence**

Assert normalization, normalized word-error rate, first/last expected word coverage, rejection of cross-beat leakage, trim end equal to final detected word plus 0.35 seconds, ordered merged timestamps after stitching, and caption alignment input.

- [ ] **Step 2: Run composer tests and verify RED**

Expected: missing `shot_production.composer`.

- [ ] **Step 3: Implement transcript and trim helpers**

Return structured `TakeTranscriptQA` rather than a boolean. Fail closed when expected boundary words are absent. Reuse `stitch_segments`, `align_transcript_to_script`, and `burn_captions`; do not duplicate their FFmpeg behavior.

- [ ] **Step 4: Run composer tests and verify GREEN**

Expected: all composer and existing stitch/caption tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: validate and compose semantic takes`

### Task 4: Structured Vision Identity Gate

**Files:**
- Modify: `app/adapters/llm_client.py`
- Modify: `app/adapters/vertex_gemini_client.py`
- Create: `app/features/shot_production/visual_qa.py`
- Create: `tests/test_shot_production_visual_qa.py`
- Modify: `tests/test_vertex_ai_client.py`

- [ ] **Step 1: Write failing multimodal-text and rubric tests**

Assert ordered image parts work for Gemini text generation, the evaluator receives the approved master before the contact sheet, valid structured JSON becomes a typed report, any false identity/hair/wardrobe/room condition blocks the take set, and malformed model output fails closed.

- [ ] **Step 2: Run visual QA tests and verify RED**

Expected: text adapters do not yet expose image inputs and the visual QA module is missing.

- [ ] **Step 3: Add minimal multimodal text transport and evaluator**

Thread validated `input_images` through the existing Gemini text methods and reuse the already centralized payload builder. Implement a strict schema with component booleans, confidence, blocking reasons, and observed differences. Do not add a second provider client.

- [ ] **Step 4: Run focused tests and verify GREEN**

Expected: visual QA and existing Gemini adapter tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: gate semantic takes with visual QA`

### Task 5: Resumable End-to-End Pilot Runner

**Files:**
- Create: `app/features/shot_production/runner.py`
- Create: `scripts/run_semantic_ugc_pilot.py`
- Create: `tests/test_shot_production_runner.py`

- [ ] **Step 1: Write failing orchestration tests**

Use fake app script, Vertex, Deepgram, and media adapters. Assert the runner persists accepted operation IDs immediately, resumes without duplicate submissions, downloads every raw clip, uses transcript windows, captions only after stitch, and records exact prompts, negative prompts, hashes, model, durations, transcripts, and final artifact paths.

- [ ] **Step 2: Run runner tests and verify RED**

Expected: missing runner module.

- [ ] **Step 3: Implement the minimal resumable state machine**

States are `planned`, `submitted`, `completed`, `transcribed`, `stitched`, `captioned`, and `qa_complete`. Persist JSON atomically after each paid or irreversible boundary. Provide a polling timeout and terminal provider-error handling.

- [ ] **Step 4: Run runner tests and verify GREEN**

Expected: runner tests pass with exactly one submission per planned take across a simulated restart.

- [ ] **Step 5: Commit**

Commit message: `feat: add resumable semantic UGC pilot`

### Task 6: Generate and Validate the Live Artifact

**Files:**
- Create at runtime: `output/semantic-ugc-pilot/<run-id>/manifest.json`
- Create at runtime: `output/semantic-ugc-pilot/<run-id>/raw/take-*.mp4`
- Create at runtime: `output/semantic-ugc-pilot/<run-id>/qa/contact-sheet.png`
- Create at runtime: `output/semantic-ugc-pilot/<run-id>/stitched.mp4`
- Create at runtime: `output/semantic-ugc-pilot/<run-id>/final-captioned.mp4`

- [ ] **Step 1: Copy the approved Candidate 2 into the pilot input directory**

Verify its SHA-256 equals `10e493306de65ae7530860f365e148d3b8272ea53a35229505eb2dd783653bda`.

- [ ] **Step 2: Generate the live 16-second script through the app**

Call `generate_dialog_scripts()` with `get_duration_profile(16)` and retain the complete returned script bundle in the manifest. Select the problem-agitate-solution script only after it passes the existing duration validator.

- [ ] **Step 3: Run the pilot CLI**

Submit all independent takes to `veo-3.1-generate-001`, poll to completion, download raw clips, run take-level Deepgram QA, trim, stitch, transcribe the final video, align captions, and burn captions.

- [ ] **Step 4: Inspect and repair by failed take**

Probe every raw and final file; inspect the contact sheet and final video. If identity, transcript, or seam QA fails, resubmit only the failed take with its approved shot frame and update the attempt history.

- [ ] **Step 5: Verify the final artifact**

Run focused and regression tests, `ffprobe`, transcript coverage checks, manifest consistency checks, and visual inspection. The final output must be a playable 9:16 captioned MP4 with native German audio and intentional semantic jump cuts.

- [ ] **Step 6: Commit source and test changes**

Do not commit generated videos or credentials. Commit message: `test: prove semantic Veo UGC pilot`
