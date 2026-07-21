# Raw Camera Background Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate three isolated Raw Camera background treatments and present them beside the current canonical scene assets without changing production state.

**Architecture:** Add a small scene-comparison service that builds the environment-only Raw Camera brief, invokes the existing text and image adapters, and renders deterministic comparison artifacts. Add a standalone script that reads current Supabase assets, downloads controls, generates treatments, and writes a manifest, side-by-side PNGs, and an HTML index under `output/background-reference-comparison/`.

**Tech Stack:** Python 3, existing Gemini/Vertex adapter, Supabase client, httpx, Pillow, pytest.

---

### Task 1: Environment-only Raw Camera generation service

**Files:**
- Create: `app/features/scenes/background_comparison.py`
- Create: `tests/test_background_comparison.py`

- [ ] **Step 1: Write failing prompt and provider-call tests**

Add tests using a fake LLM client. Assert that `build_raw_camera_background_brief("home_living_room_advice_a")` includes the scene identity, environment anchors, lighting, exact actor-free constraints, and excludes instructions to show a subject or hands. Assert that `generate_raw_camera_background(...)` sends the checked-in Raw Camera system prompt to `generate_gemini_text`, then sends the returned finished prompt to `generate_gemini_image` with model `gemini-3.1-flash-image`, `9:16`, `2K`, and no input images.

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m pytest tests/test_background_comparison.py -q`

Expected: collection fails because `app.features.scenes.background_comparison` does not exist.

- [ ] **Step 3: Implement the minimal generation service**

Create a `RawCameraBackgroundResult` dataclass and these functions:

```python
def build_raw_camera_background_brief(scene_key: str) -> str: ...

def generate_raw_camera_background(
    *,
    scene_key: str,
    llm_client: Optional[Any] = None,
    image_model: str = "gemini-3.1-flash-image",
) -> RawCameraBackgroundResult: ...
```

Load the existing long prompt through `load_raw_camera_system_prompt()`. Use the same truncation guard as the shot-frame workflow. Request one `9:16`, `2K` image with temperature `0.7` and no reference images.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m pytest tests/test_background_comparison.py -q`

Expected: all prompt and provider-call tests pass.

### Task 2: Deterministic comparison renderers

**Files:**
- Modify: `app/features/scenes/background_comparison.py`
- Modify: `tests/test_background_comparison.py`

- [ ] **Step 1: Write failing image and HTML renderer tests**

Create two in-memory portrait images with different dimensions. Assert that `compose_side_by_side(...)` returns a valid PNG with two equal portrait cells and labels. Assert that `render_comparison_index(...)` includes all supplied scenes, the fixed left/right labels, relative image paths, and review criteria.

- [ ] **Step 2: Run the tests and verify RED**

Run: `python3 -m pytest tests/test_background_comparison.py -q`

Expected: tests fail because the renderer functions do not exist.

- [ ] **Step 3: Implement minimal renderers**

Implement:

```python
def compose_side_by_side(
    *, control_bytes: bytes, treatment_bytes: bytes, scene_name: str
) -> bytes: ...

def render_comparison_index(rows: list[dict[str, str]]) -> str: ...
```

Use Pillow with fixed 768x1365 image cells, white letterboxing, a compact label band, and PNG output. Generate a self-contained responsive HTML document that uses relative image paths.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m pytest tests/test_background_comparison.py -q`

Expected: all tests pass.

### Task 3: Standalone live comparison script

**Files:**
- Create: `scripts/compare_raw_camera_backgrounds.py`
- Modify: `tests/test_background_comparison.py`

- [ ] **Step 1: Write failing script-contract test**

Assert that the script defaults to exactly `home_living_room_advice_a`, `bathroom_accessibility_a`, and `car_transfer_residential_a`, requires a generated control asset, never calls a canonical-scene create/update function, and writes its result beneath a caller-provided output directory.

- [ ] **Step 2: Run the test and verify RED**

Run: `python3 -m pytest tests/test_background_comparison.py -q`

Expected: test fails because the script module does not exist.

- [ ] **Step 3: Implement the script**

The CLI accepts optional scene keys plus `--output-root`. It creates a UTC timestamped run directory, downloads each current asset with `httpx`, calls `generate_raw_camera_background`, writes `current.<ext>`, `raw-camera.<ext>`, `side-by-side.png`, per-scene prompt text, `manifest.json`, and `index.html`. It records the control asset id/URL and treatment hashes/model without writing to Supabase or R2. Per-scene exceptions are recorded; any failure produces exit code 1 after the remaining scenes are attempted.

- [ ] **Step 4: Run the tests and verify GREEN**

Run: `python3 -m pytest tests/test_background_comparison.py tests/test_shot_frames.py tests/test_actor_identity_scene_reference.py -q`

Expected: all tests pass.

### Task 4: Live generation and visual verification

**Files:**
- Generate: `output/background-reference-comparison/<timestamp>/...`
- Generate: `.superpowers/brainstorm/<session>/content/background-comparison.html`

- [ ] **Step 1: Commit the implementation branch**

Run:

```bash
git add app/features/scenes/background_comparison.py scripts/compare_raw_camera_backgrounds.py tests/test_background_comparison.py docs/superpowers/plans/2026-07-21-raw-camera-background-comparison.md
git commit -m "feat: compare raw camera background references"
```

- [ ] **Step 2: Integrate the branch into the main workspace**

Merge the isolated branch after fresh tests pass. Preserve unrelated user files and untracked outputs.

- [ ] **Step 3: Generate the three live treatments**

Run from the main workspace where `.env` is available:

```bash
python3 scripts/compare_raw_camera_backgrounds.py
```

Expected: exit 0 with a printed absolute path to the timestamped `index.html`, three successful scenes, and no Supabase mutation.

- [ ] **Step 4: Inspect raster outputs**

Open all three side-by-side PNGs and verify both columns render, labels are correct, images are portrait and undistorted, and the treatments contain no people/body parts.

- [ ] **Step 5: Publish the comparison through the visual companion**

Start the companion server for the project, copy the six source images or three side-by-side PNGs into its content directory, and write a new comparison HTML screen. Open the local URL and verify all assets load.

- [ ] **Step 6: Run final verification**

Run:

```bash
python3 -m pytest tests/test_background_comparison.py tests/test_shot_frames.py tests/test_actor_identity_scene_reference.py -q
git diff --check
```

Expected: all tests pass and `git diff --check` exits 0.
