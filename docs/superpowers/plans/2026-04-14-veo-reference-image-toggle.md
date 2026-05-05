# VEO Reference Image Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single boolean config switch that lets the UI route VEO submissions either with the `sarah.jpg` first-frame image or as text-only video, without changing the rest of the video pipeline.

**Architecture:** Keep the decision at the video submission boundary, not inside the prompt builder or the Vertex adapter. `app/core/config.py` owns the flag, `app/features/videos/handlers.py` reads it and decides whether to attach the anchor image, and the existing adapter continues to submit the image payload when given one. This keeps the change small, explicit, and easy to reason about.

**Tech Stack:** Python 3.9/3.11-compatible FastAPI app, Pydantic Settings, `httpx`, pytest.

---

### Task 1: Add a boolean config flag for VEO image mode

**Files:**
- Modify: `app/core/config.py:1-240`
- Test: `tests/test_vertex_ai_config.py`

- [ ] **Step 1: Write the failing test**

```python
from app.core.config import get_settings


def test_veo_reference_image_toggle_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("VEO_USE_REFERENCE_IMAGE", raising=False)
    settings = get_settings()
    assert settings.veo_use_reference_image is True


def test_veo_reference_image_toggle_accepts_false(monkeypatch):
    monkeypatch.setenv("VEO_USE_REFERENCE_IMAGE", "false")
    settings = get_settings()
    assert settings.veo_use_reference_image is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q tests/test_vertex_ai_config.py -k veo_reference_image_toggle -v`
Expected: FAIL because `Settings` does not yet define `veo_use_reference_image`.

- [ ] **Step 3: Write minimal implementation**

```python
    veo_use_reference_image: bool = Field(
        default=True,
        validation_alias=AliasChoices("VEO_USE_REFERENCE_IMAGE"),
        description="Enable first-frame reference-image mode for VEO submits",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q tests/test_vertex_ai_config.py -k veo_reference_image_toggle -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py tests/test_vertex_ai_config.py
git commit -m "feat: add VEO reference image toggle"
```

### Task 2: Gate the VEO submit path on the config flag

**Files:**
- Modify: `app/features/videos/handlers.py:1-260,1650-1805`
- Test: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Write the failing test**

```python
def test_submit_video_request_uses_anchor_image_when_flag_enabled(monkeypatch, tmp_path):
    captured = {}

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {"operation_id": "operations/test", "status": "submitted"}

    image_path = tmp_path / "sarah.jpg"
    image_path.write_bytes(b"fake-jpeg")

    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())
    monkeypatch.setattr(video_handlers, "_GLOBAL_VEO_ANCHOR_PATH", image_path)
    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"veo_use_reference_image": True, "vertex_ai_output_gcs_uri": ""})())

    result = video_handlers._submit_video_request(
        provider="veo_3_1",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr-enabled",
        provider_duration_seconds=8,
    )

    assert captured["first_frame_image"] is not None
    assert result["operation_id"] == "operations/test"


def test_submit_video_request_skips_anchor_image_when_flag_disabled(monkeypatch, tmp_path):
    captured = {}

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {"operation_id": "operations/test", "status": "submitted"}

    image_path = tmp_path / "sarah.jpg"
    image_path.write_bytes(b"fake-jpeg")

    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())
    monkeypatch.setattr(video_handlers, "_GLOBAL_VEO_ANCHOR_PATH", image_path)
    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"veo_use_reference_image": False, "vertex_ai_output_gcs_uri": ""})())

    result = video_handlers._submit_video_request(
        provider="veo_3_1",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr-disabled",
        provider_duration_seconds=8,
    )

    assert captured["first_frame_image"] is None
    assert result["operation_id"] == "operations/test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q tests/test_video_duration_routing.py -k "uses_anchor_image_when_flag_enabled or skips_anchor_image_when_flag_disabled" -v`
Expected: FAIL because `_submit_video_request(...)` does not yet read the new config flag.

- [ ] **Step 3: Write minimal implementation**

```python
    settings = get_settings()
    if getattr(settings, "veo_use_reference_image", False):
        anchor_asset = _load_global_veo_anchor_asset(correlation_id=correlation_id, strict=False)
        if anchor_asset is not None:
            resolved_first_frame_image = anchor_asset["first_frame_image"]
```

Apply the same boolean gate wherever the VEO submit path currently auto-attaches the anchor image.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q tests/test_video_duration_routing.py -k "uses_anchor_image_when_flag_enabled or skips_anchor_image_when_flag_disabled" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/features/videos/handlers.py tests/test_video_duration_routing.py
git commit -m "feat: gate VEO anchor image with config flag"
```

### Task 3: Keep the adapter payload tests stable

**Files:**
- Modify: `tests/test_veo_client_payload.py`

- [ ] **Step 1: Write the failing test**

```python
def test_veo_submission_includes_first_frame_inline_image(monkeypatch):
    # already covers the payload shape; keep it asserting the inline image path
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q tests/test_veo_client_payload.py -v`
Expected: PASS only after the adapter assertions still match the inline image payload shape.

- [ ] **Step 3: Write minimal implementation**

Keep the existing payload assertions aligned to `image.inlineData` for VEO and the `bytesBase64Encoded` image body for Vertex image mode. Do not broaden the adapter surface for this feature.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q tests/test_veo_client_payload.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_veo_client_payload.py
git commit -m "test: keep VEO image payload assertions"
```

### Task 4: Verify the full feature and document the config switch

**Files:**
- Modify: `README.md` or the repo config docs if a VEO config section exists

- [ ] **Step 1: Write the documentation note**

```markdown
Set `VEO_USE_REFERENCE_IMAGE=true` to attach `static/images/sarah.jpg` as the first-frame image for VEO submissions. Set it to `false` to force text-only VEO submissions.
```

- [ ] **Step 2: Run the focused verification suite**

Run: `python3 -m pytest -q tests/test_vertex_ai_config.py tests/test_video_duration_routing.py tests/test_veo_client_payload.py`
Expected: all tests pass.

- [ ] **Step 3: Run the live VEO3 Lite sanity check**

Run the existing live request path with `model="veo-3.1-lite-generate-001"` and confirm the payload logs include `image` when the flag is enabled, then confirm the same request skips the image when the flag is disabled.

- [ ] **Step 4: Commit**

```bash
git add README.md app/core/config.py app/features/videos/handlers.py tests/test_vertex_ai_config.py tests/test_video_duration_routing.py tests/test_veo_client_payload.py
git commit -m "feat: add VEO image mode toggle"
```

## Self-Review

**Spec coverage**
- Config flag: Task 1.
- Submit-path routing: Task 2.
- Payload regression stability: Task 3.
- Documentation and end-to-end verification: Task 4.

**Placeholder scan**
- No TBD/TODO placeholders remain.
- Every code-changing step includes concrete code or exact assertions.

**Type consistency**
- The plan uses one config key, `veo_use_reference_image`, consistently across all tasks.
- The submission code keeps the same `_submit_video_request(...)` entry point and only changes the image attachment decision.

---
