# Veo 3.1 Shot Frame Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` task by task and `superpowers:verification-before-completion` before claiming success.

**Goal:** Generate auditable 9:16 shot-frame candidates from two actor references and one canonical location, then stop for approval before Veo 3.1.

**Architecture:** Add ordered image parts to the existing Gemini adapter. Add a compact shot-frame feature that runs the supplied Raw Camera prompt writer first and Nano Banana Pro composition second. Keep Veo behind a later explicit approval call.

**Tech Stack:** Python, existing FastAPI feature conventions, Vertex Gemini REST, pytest, existing Veo 3.1 adapter.

**Scope Budget:** `{files: 6-8, LOC/file: <=350, deps: 0}`

## Task 1: Ordered Gemini Reference Images

**Files:** `app/adapters/vertex_gemini_client.py`, `app/adapters/llm_client.py`, `tests/test_vertex_gemini_client.py`, `tests/test_blog_feature.py`

- [ ] Write a failing test proving text is followed by ordered `inlineData` image parts.
- [ ] Add validated `input_images` support to Vertex and direct Gemini image calls.
- [ ] Run focused adapter tests.

## Task 2: Raw Camera Prompt Writer and Composition Contract

**Files:** `app/features/shot_frames/raw_camera_casting_system_prompt.txt`, `app/features/shot_frames/service.py`, `tests/test_shot_frames.py`

- [ ] Store the attached system prompt verbatim.
- [ ] Write failing tests for exactly two actor references, one location reference, role ordering, and no Veo dependency.
- [ ] Implement the text prompt-writer pass and Nano Banana Pro composition pass.
- [ ] Include actor identity, wardrobe, room geometry, raw-camera, AIUGC, and no-extra-person locks.

## Task 3: Live Candidate Run and Review Gate

**Files:** `scripts/generate_shot_frame_candidates.py`, `output/shot-frame-candidates/<run-id>/`

- [ ] Add a deterministic CLI that reads local reference files and writes candidates plus a JSON manifest.
- [ ] Run it against the pending Ayra post using two actor refs and the canonical scene.
- [ ] Inspect each generated raster for identity, wardrobe, scene, anatomy, and AIUGC realism.
- [ ] Show the candidates to the user and stop.

## Task 4: Veo 3.1 After Explicit Approval

**Files:** existing video submission feature plus focused regression tests

- [ ] Accept an explicitly selected candidate as the Veo start frame.
- [ ] Submit one 8-second 9:16 Veo 3.1 image-to-video request.
- [ ] Poll, download, inspect, and record prompt/input/output audit data.
- [ ] Continue only after video QA passes.

