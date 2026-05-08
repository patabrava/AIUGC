# Reference Image Video Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate every Veo base video from the platform with the three provided subject reference images, then keep the existing Veo extension chain for longer videos.

**Architecture:** Keep the existing video vertical slice and add one small reference-image helper inside `app/features/videos/handlers.py`. Base submissions send `referenceImages` to the existing `VeoClient`; extension hops continue using the existing `video` field because Google documents `referenceImages` as mutually exclusive with `image`, `video`, and `lastFrame` in one request. This work replaces the dormant first-frame-anchor experiment for base clips with the official subject-reference-image path.

**Tech Stack:** FastAPI route handlers, existing Veo REST adapter, Pydantic settings, pytest, Python stdlib `base64`/`mimetypes`/`pathlib`.

**Locality Envelope:** `{files: 5, LOC/file: app/features/videos/handlers.py <= 2000 existing exception with <= 90 added LOC, app/adapters/veo_client.py <= 650, app/core/config.py <= 320, tests/test_video_duration_routing.py <= 900 existing test file, docs/superpowers/plans/2026-05-08-reference-image-video-generation.md no runtime impact, deps: 0}`

---

## Documentation Facts To Preserve

- Vertex reference-image docs: `referenceImages` accepts up to three `referenceType: "asset"` images, each with `image.bytesBase64Encoded` and `image.mimeType`.
- `VideoGenerationModelInstance` docs: if `referenceImages` is present, `image`, `video`, and `lastFrame` are not supported in that same request.
- Veo extension docs: extension requests use `video.uri`, require MP4 input, and add 7 seconds at 720p.
- Design implication: attach the three subject images only to base generation requests. Do not attach them to extension requests.

## File Structure

- Modify `app/core/config.py`
  - Replace the singular `veo_use_reference_image` experiment flag with a plural subject-reference flag and configurable asset path list.
  - No new dependency.

- Modify `app/features/videos/handlers.py`
  - Add `_load_global_veo_reference_assets(...)` next to the existing image helper area.
  - Update `_submit_video_request(...)` to pass `reference_images` for `provider == "veo_3_1"` only.
  - Remove auto first-frame attachment from the base Veo path.
  - Add reference-image metadata to submission metadata from `submission_result["provider_metadata"]`.

- Modify `app/adapters/veo_client.py`
  - Send `referenceImages` in the REST payload instead of logging that it is unsupported.
  - Redact base64 content for both `image.inlineData` and `referenceImages[].image.bytesBase64Encoded`.
  - Reject illegal combinations locally: `first_frame_image` plus `reference_images`.

- Modify `tests/test_video_duration_routing.py`
  - Replace first-frame-anchor tests with reference-image tests.
  - Add a guard that extension submissions still receive no `reference_images`.

- Optional manual asset copy outside code edits
  - Copy the three user-provided PNGs to `static/images/video-references/` if we decide they should be committed. If not committing images, configure their paths through `VEO_REFERENCE_IMAGE_PATHS`.

---

### Task 1: Add Configuration For Three Subject Reference Images

**Files:**
- Modify: `app/core/config.py`
- Test: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Write the failing config test**

Append this test near the existing video-reference tests in `tests/test_video_duration_routing.py`:

```python
def test_reference_image_paths_parse_comma_separated_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "veo_use_reference_images", True, raising=False)
    monkeypatch.setattr(
        settings,
        "veo_reference_image_paths",
        "static/images/video-references/front.png, static/images/video-references/profile.png,static/images/video-references/full-body.png",
        raising=False,
    )

    paths = video_handlers._configured_veo_reference_image_paths(settings)

    assert paths == [
        "static/images/video-references/front.png",
        "static/images/video-references/profile.png",
        "static/images/video-references/full-body.png",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_video_duration_routing.py::test_reference_image_paths_parse_comma_separated_settings -v
```

Expected: FAIL with `AttributeError: module 'app.features.videos.handlers' has no attribute '_configured_veo_reference_image_paths'`.

- [ ] **Step 3: Add settings fields**

In `app/core/config.py`, replace the existing `veo_use_reference_image` field with these fields:

```python
    veo_use_reference_images: bool = Field(
        default=False,
        validation_alias=AliasChoices("VEO_USE_REFERENCE_IMAGES"),
        description="Attach global Veo subject reference images to base video submissions",
    )
    veo_reference_image_paths: str = Field(
        default=(
            "static/images/video-references/front.png,"
            "static/images/video-references/profile.png,"
            "static/images/video-references/full-body.png"
        ),
        validation_alias=AliasChoices("VEO_REFERENCE_IMAGE_PATHS"),
        description="Comma-separated local paths for up to three Veo subject reference images",
    )
```

- [ ] **Step 4: Add the path parser**

In `app/features/videos/handlers.py`, add this helper below the `_GLOBAL_VEO_ANCHOR_ENABLED` constant:

```python
def _configured_veo_reference_image_paths(settings: Any) -> list[str]:
    raw_paths = str(getattr(settings, "veo_reference_image_paths", "") or "")
    return [path.strip() for path in raw_paths.split(",") if path.strip()]
```

- [ ] **Step 5: Run the focused test**

Run:

```bash
python3 -m pytest tests/test_video_duration_routing.py::test_reference_image_paths_parse_comma_separated_settings -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/core/config.py app/features/videos/handlers.py tests/test_video_duration_routing.py
git commit -m "feat: configure veo reference image paths"
```

---

### Task 2: Load And Validate Up To Three Reference Images

**Files:**
- Modify: `app/features/videos/handlers.py`
- Test: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Write failing loader tests**

Add these tests after the config parser test:

```python
def test_load_global_veo_reference_assets_reads_three_pngs(monkeypatch, tmp_path):
    paths = []
    for name, content in [
        ("front.png", b"front-image"),
        ("profile.png", b"profile-image"),
        ("full-body.png", b"full-body-image"),
    ]:
        image_path = tmp_path / name
        image_path.write_bytes(content)
        paths.append(str(image_path))

    settings = type(
        "S",
        (),
        {
            "veo_use_reference_images": True,
            "veo_reference_image_paths": ",".join(paths),
        },
    )()
    monkeypatch.setattr(video_handlers, "get_settings", lambda: settings)

    bundle = video_handlers._load_global_veo_reference_assets(correlation_id="corr-ref", strict=True)

    assert [item["mime_type"] for item in bundle["reference_images"]] == ["image/png", "image/png", "image/png"]
    assert [base64.b64decode(item["data_base64"]) for item in bundle["reference_images"]] == [
        b"front-image",
        b"profile-image",
        b"full-body-image",
    ]
    assert bundle["metadata"]["reference_images_enabled"] is True
    assert bundle["metadata"]["reference_image_count"] == 3


def test_load_global_veo_reference_assets_rejects_more_than_three(monkeypatch, tmp_path):
    paths = []
    for index in range(4):
        image_path = tmp_path / f"ref-{index}.png"
        image_path.write_bytes(b"image")
        paths.append(str(image_path))

    settings = type(
        "S",
        (),
        {
            "veo_use_reference_images": True,
            "veo_reference_image_paths": ",".join(paths),
        },
    )()
    monkeypatch.setattr(video_handlers, "get_settings", lambda: settings)

    with pytest.raises(ValidationError) as exc:
        video_handlers._load_global_veo_reference_assets(correlation_id="corr-ref", strict=True)

    assert "at most three" in exc.value.message
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest \
  tests/test_video_duration_routing.py::test_load_global_veo_reference_assets_reads_three_pngs \
  tests/test_video_duration_routing.py::test_load_global_veo_reference_assets_rejects_more_than_three \
  -v
```

Expected: FAIL with missing `_load_global_veo_reference_assets`.

- [ ] **Step 3: Implement the loader**

In `app/features/videos/handlers.py`, add this helper below `_configured_veo_reference_image_paths(...)`:

```python
def _load_global_veo_reference_assets(
    *,
    correlation_id: str,
    strict: bool,
) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    if not bool(getattr(settings, "veo_use_reference_images", False)):
        return None

    configured_paths = _configured_veo_reference_image_paths(settings)
    if not configured_paths:
        if strict:
            raise ValidationError(
                "Veo reference images are enabled but no image paths are configured.",
                {"reference_image_paths": configured_paths},
            )
        logger.warning("veo_reference_images_missing_paths", correlation_id=correlation_id)
        return None

    if len(configured_paths) > 3:
        raise ValidationError(
            "Veo reference image generation supports at most three subject images.",
            {"reference_image_count": len(configured_paths), "reference_image_paths": configured_paths},
        )

    root_dir = Path(__file__).resolve().parents[3]
    reference_images: list[Dict[str, str]] = []
    metadata_items: list[Dict[str, Any]] = []

    for configured_path in configured_paths:
        image_path = Path(configured_path)
        if not image_path.is_absolute():
            image_path = root_dir / configured_path

        if not image_path.exists():
            if strict:
                raise ValidationError(
                    "Configured Veo reference image is missing.",
                    {"reference_image_path": configured_path},
                )
            logger.warning(
                "veo_reference_image_missing_text_only_fallback",
                correlation_id=correlation_id,
                reference_image_path=configured_path,
            )
            return None

        try:
            image_bytes = image_path.read_bytes()
        except OSError as exc:
            if strict:
                raise ValidationError(
                    "Configured Veo reference image could not be read.",
                    {"reference_image_path": configured_path, "error": str(exc)},
                ) from exc
            logger.warning(
                "veo_reference_image_unreadable_text_only_fallback",
                correlation_id=correlation_id,
                reference_image_path=configured_path,
                error=str(exc),
            )
            return None

        if not image_bytes:
            if strict:
                raise ValidationError(
                    "Configured Veo reference image is empty.",
                    {"reference_image_path": configured_path},
                )
            logger.warning(
                "veo_reference_image_empty_text_only_fallback",
                correlation_id=correlation_id,
                reference_image_path=configured_path,
            )
            return None

        mime_type = mimetypes.guess_type(image_path.name)[0] or ""
        if mime_type not in {"image/png", "image/jpeg"}:
            raise ValidationError(
                "Configured Veo reference image must be PNG or JPEG.",
                {"reference_image_path": configured_path, "mime_type": mime_type},
            )

        reference_images.append(
            {
                "mime_type": mime_type,
                "data_base64": base64.b64encode(image_bytes).decode("ascii"),
            }
        )
        metadata_items.append(
            {
                "path": configured_path,
                "mime_type": mime_type,
                "size_bytes": len(image_bytes),
            }
        )

    return {
        "reference_images": reference_images,
        "metadata": {
            "reference_images_enabled": True,
            "reference_image_count": len(reference_images),
            "reference_image_assets": metadata_items,
        },
    }
```

- [ ] **Step 4: Run the loader tests**

Run:

```bash
python3 -m pytest \
  tests/test_video_duration_routing.py::test_load_global_veo_reference_assets_reads_three_pngs \
  tests/test_video_duration_routing.py::test_load_global_veo_reference_assets_rejects_more_than_three \
  -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/features/videos/handlers.py tests/test_video_duration_routing.py
git commit -m "feat: load veo subject reference images"
```

---

### Task 3: Send `referenceImages` In The Veo REST Payload

**Files:**
- Modify: `app/adapters/veo_client.py`
- Test: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Write failing adapter tests**

Add these tests to `tests/test_video_duration_routing.py`:

```python
def test_veo_client_payload_includes_asset_reference_images(monkeypatch):
    from app.adapters.veo_client import VeoClient

    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"name":"operations/reference-test"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"name": "operations/reference-test"}

    class FakeHttpClient:
        def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"gemini_api_key": "test-key"})())
    VeoClient._instance = None
    client = VeoClient()
    client._http_client = FakeHttpClient()

    result = client.submit_video_generation(
        prompt="Prompt",
        negative_prompt=None,
        correlation_id="corr",
        aspect_ratio="9:16",
        resolution="720p",
        duration_seconds=8,
        reference_images=[
            {"mime_type": "image/png", "data_base64": "Zmlyc3Q="},
            {"mime_type": "image/png", "data_base64": "c2Vjb25k"},
        ],
        model="veo-3.1-generate-001",
    )

    assert result["operation_id"] == "operations/reference-test"
    assert captured["json"]["instances"][0]["referenceImages"] == [
        {
            "image": {
                "bytesBase64Encoded": "Zmlyc3Q=",
                "mimeType": "image/png",
            },
            "referenceType": "asset",
        },
        {
            "image": {
                "bytesBase64Encoded": "c2Vjb25k",
                "mimeType": "image/png",
            },
            "referenceType": "asset",
        },
    ]


def test_veo_client_rejects_first_frame_and_reference_images_together(monkeypatch):
    from app.adapters.veo_client import VeoClient

    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"gemini_api_key": "test-key"})())
    VeoClient._instance = None
    client = VeoClient()

    with pytest.raises(ValueError) as exc:
        client.submit_video_generation(
            prompt="Prompt",
            negative_prompt=None,
            correlation_id="corr",
            aspect_ratio="9:16",
            resolution="720p",
            duration_seconds=8,
            first_frame_image={"mime_type": "image/png", "data_base64": "aW1hZ2U="},
            reference_images=[{"mime_type": "image/png", "data_base64": "cmVm"}],
            model="veo-3.1-generate-001",
        )

    assert "referenceImages cannot be combined" in str(exc.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest \
  tests/test_video_duration_routing.py::test_veo_client_payload_includes_asset_reference_images \
  tests/test_video_duration_routing.py::test_veo_client_rejects_first_frame_and_reference_images_together \
  -v
```

Expected: first test FAILS because payload has no `referenceImages`; second test FAILS because no local rejection exists.

- [ ] **Step 3: Implement payload support**

In `app/adapters/veo_client.py`, replace the current `if reference_images:` warning block in `submit_video_generation(...)` with:

```python
            if first_frame_image and reference_images:
                raise ValueError("referenceImages cannot be combined with image/video/lastFrame inputs")

            if reference_images:
                instance["referenceImages"] = [
                    {
                        "image": {
                            "bytesBase64Encoded": item["data_base64"],
                            "mimeType": item["mime_type"],
                        },
                        "referenceType": "asset",
                    }
                    for item in reference_images
                ]
```

Make sure this block runs after `instance` is created and before `payload` is logged and submitted.

- [ ] **Step 4: Redact reference image payloads in logs**

In `_payload_for_logging(...)`, after the existing `inlineData` redaction block, add:

```python
            for reference_image in instance.get("referenceImages", []) or []:
                if not isinstance(reference_image, dict):
                    continue
                reference_payload = reference_image.get("image") or {}
                if not isinstance(reference_payload, dict):
                    continue
                if "bytesBase64Encoded" in reference_payload:
                    raw_value = str(reference_payload["bytesBase64Encoded"])
                    reference_payload["bytesBase64Encoded"] = f"<redacted_base64:{len(raw_value)}_chars>"
```

- [ ] **Step 5: Run adapter tests**

Run:

```bash
python3 -m pytest \
  tests/test_video_duration_routing.py::test_veo_client_payload_includes_asset_reference_images \
  tests/test_video_duration_routing.py::test_veo_client_rejects_first_frame_and_reference_images_together \
  -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/adapters/veo_client.py tests/test_video_duration_routing.py
git commit -m "feat: send veo asset reference images"
```

---

### Task 4: Route Base Generation Through Reference Images, Leave Extensions Text+Video

**Files:**
- Modify: `app/features/videos/handlers.py`
- Test: `tests/test_video_duration_routing.py`

- [ ] **Step 1: Write failing routing tests**

Add these tests to `tests/test_video_duration_routing.py`:

```python
def test_submit_video_request_attaches_reference_images_to_veo_base(monkeypatch, tmp_path):
    captured = {}
    image_path = tmp_path / "front.png"
    image_path.write_bytes(b"front-image")

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "operations/ref-base",
                "status": "submitted",
                "provider_model": kwargs.get("model"),
            }

    settings = type(
        "S",
        (),
        {
            "veo_use_reference_images": True,
            "veo_reference_image_paths": str(image_path),
        },
    )()
    monkeypatch.setattr(video_handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())

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
        correlation_id="corr-ref-base",
        provider_duration_seconds=8,
    )

    assert captured["first_frame_image"] is None
    assert len(captured["reference_images"]) == 1
    assert base64.b64decode(captured["reference_images"][0]["data_base64"]) == b"front-image"
    assert result["provider_metadata"]["reference_image_count"] == 1
    assert result["provider_metadata"]["reference_images_enabled"] is True


def test_submit_video_request_skips_reference_images_when_disabled(monkeypatch, tmp_path):
    captured = {}
    image_path = tmp_path / "front.png"
    image_path.write_bytes(b"front-image")

    class FakeVeoClient:
        def submit_video_generation(self, **kwargs):
            captured.update(kwargs)
            return {"operation_id": "operations/text-base", "status": "submitted"}

    settings = type(
        "S",
        (),
        {
            "veo_use_reference_images": False,
            "veo_reference_image_paths": str(image_path),
        },
    )()
    monkeypatch.setattr(video_handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(video_handlers, "get_veo_client", lambda: FakeVeoClient())

    video_handlers._submit_video_request(
        provider="veo_3_1",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr-text-base",
        provider_duration_seconds=8,
    )

    assert captured["reference_images"] is None
    assert captured["first_frame_image"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest \
  tests/test_video_duration_routing.py::test_submit_video_request_attaches_reference_images_to_veo_base \
  tests/test_video_duration_routing.py::test_submit_video_request_skips_reference_images_when_disabled \
  -v
```

Expected: FAIL because `_submit_video_request(...)` still uses the old first-frame path and never passes `reference_images`.

- [ ] **Step 3: Update `_submit_video_request(...)` for `provider == "veo_3_1"`**

Inside `_submit_video_request(...)`, replace the old `use_reference_image` / `resolved_first_frame_image` block with:

```python
        reference_bundle = _load_global_veo_reference_assets(correlation_id=correlation_id, strict=False)
        reference_images = reference_bundle["reference_images"] if reference_bundle else None
```

Then update the `veo_client.submit_video_generation(...)` call to:

```python
            result = veo_client.submit_video_generation(
                prompt=prompt_text,
                negative_prompt=negative_prompt,
                correlation_id=correlation_id,
                aspect_ratio=provider_aspect,
                resolution=resolution,
                duration_seconds=veo_duration_seconds,
                first_frame_image=None,
                reference_images=reference_images,
                seed=seed,
                model=model_name,
            )
```

After `requested_size` and `provider_requested_size` are computed, build provider metadata like this:

```python
        provider_metadata = dict(result)
        if reference_bundle:
            provider_metadata.update(reference_bundle["metadata"])
```

Then return `provider_metadata` instead of `result`:

```python
            "provider_metadata": provider_metadata,
```

- [ ] **Step 4: Remove batch-level first-frame anchor use**

In `generate_all_videos(...)`, remove the `anchor_image_bundle = ...` line and remove this argument expression:

```python
                    first_frame_image=(
                        anchor_image_bundle["first_frame_image"]
                        if submission_plan["provider"] == VEO_PROVIDER and anchor_image_bundle
                        else None
                    ),
```

Replace it with:

```python
                    first_frame_image=None,
```

Also remove the metadata update block:

```python
                if submission_plan["provider"] == VEO_PROVIDER and anchor_image_bundle:
                    submission_metadata.update(anchor_image_bundle["metadata"])
```

- [ ] **Step 5: Run routing tests**

Run:

```bash
python3 -m pytest \
  tests/test_video_duration_routing.py::test_submit_video_request_attaches_reference_images_to_veo_base \
  tests/test_video_duration_routing.py::test_submit_video_request_skips_reference_images_when_disabled \
  -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/features/videos/handlers.py tests/test_video_duration_routing.py
git commit -m "feat: route veo base clips through reference images"
```

---

### Task 5: Preserve Existing Extension Chain Contract

**Files:**
- Modify: `tests/test_video_duration_routing.py`
- No runtime code should be required unless this test reveals a regression.

- [ ] **Step 1: Add extension contract test**

Add this test to `tests/test_video_duration_routing.py`:

```python
def test_veo_extension_request_uses_video_without_reference_images(monkeypatch):
    from app.adapters.veo_client import VeoClient

    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"name":"operations/extension-test"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"name": "operations/extension-test"}

    class FakeHttpClient:
        def post(self, url, headers, json):
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"gemini_api_key": "test-key"})())
    VeoClient._instance = None
    client = VeoClient()
    client._http_client = FakeHttpClient()

    result = client.submit_video_extension(
        prompt="Continue the same presenter.",
        video_uri="gs://bucket/base.mp4",
        correlation_id="corr-extension",
        aspect_ratio="9:16",
        resolution="720p",
        duration_seconds=7,
        negative_prompt=None,
    )

    assert result["operation_id"] == "operations/extension-test"
    instance = captured["json"]["instances"][0]
    assert instance["video"]["uri"] == "gs://bucket/base.mp4"
    assert "referenceImages" not in instance
    assert "image" not in instance
    assert "lastFrame" not in instance
```

- [ ] **Step 2: Run extension contract test**

Run:

```bash
python3 -m pytest tests/test_video_duration_routing.py::test_veo_extension_request_uses_video_without_reference_images -v
```

Expected: PASS. If it fails, change only `app/adapters/veo_client.py::submit_video_extension(...)` so its instance contains `prompt` and `video` only.

- [ ] **Step 3: Commit**

```bash
git add tests/test_video_duration_routing.py
git commit -m "test: lock veo extension input contract"
```

---

### Task 6: Replace Or Remove Old First-Frame Tests

**Files:**
- Modify: `tests/test_video_duration_routing.py`
- Modify: `app/features/videos/handlers.py` only if unused first-frame helper code is removed.

- [ ] **Step 1: Remove obsolete tests**

Delete these old first-frame-anchor tests from `tests/test_video_duration_routing.py` because the experiment now uses `referenceImages`, not `image.inlineData`:

```python
test_resolve_global_veo_anchor_image_reads_repo_asset
test_submit_video_request_passes_anchor_image_to_veo_client
test_submit_video_request_auto_attaches_anchor_image_to_veo_client
test_submit_video_request_skips_anchor_image_when_toggle_disabled
test_submit_video_request_falls_back_when_anchor_unreadable
```

- [ ] **Step 2: Remove unused first-frame mirroring only if no references remain**

Run:

```bash
rg -n "_resolve_global_veo_anchor_image|_load_global_veo_anchor_asset|_GLOBAL_VEO_ANCHOR" app tests
```

If only dead references remain, delete these from `app/features/videos/handlers.py`:

```python
_GLOBAL_VEO_ANCHOR_RELATIVE_PATH = "static/images/sarah.jpg"
_GLOBAL_VEO_ANCHOR_PATH = Path(__file__).resolve().parents[3] / _GLOBAL_VEO_ANCHOR_RELATIVE_PATH
_GLOBAL_VEO_ANCHOR_OBJECT_KEY = "Lippe Lift Studio/images/anchors/sarah.jpg"
_GLOBAL_VEO_ANCHOR_ENABLED = False
```

Also delete the full definitions of:

```python
def _load_global_veo_anchor_asset(...):
    ...

def _resolve_global_veo_anchor_image(...):
    ...
```

Keep the `Path`, `base64`, and `mimetypes` imports because `_load_global_veo_reference_assets(...)` uses them.

- [ ] **Step 3: Run the full focused video routing suite**

Run:

```bash
python3 -m pytest tests/test_video_duration_routing.py -v
```

Expected: PASS. Existing unrelated failures should be documented with the exact failing test names before continuing.

- [ ] **Step 4: Commit**

```bash
git add app/features/videos/handlers.py tests/test_video_duration_routing.py
git commit -m "refactor: remove obsolete first frame anchor path"
```

---

### Task 7: Add The Three Reference Images

**Files:**
- Create: `static/images/video-references/front.png`
- Create: `static/images/video-references/profile.png`
- Create: `static/images/video-references/full-body.png`

- [ ] **Step 1: Create the asset directory**

Run:

```bash
mkdir -p static/images/video-references
```

Expected: directory exists.

- [ ] **Step 2: Copy the provided reference images into stable repo paths**

Run:

```bash
cp "/Users/camiloecheverri/Downloads/ChatGPT Image 8. Mai 2026, 01_03_46.png" static/images/video-references/front.png
cp "/Users/camiloecheverri/Downloads/b2ff4e9e-0602-4968-a530-2781a5edb1b1.png" static/images/video-references/profile.png
cp "/Users/camiloecheverri/Downloads/ChatGPT Image 8. Mai 2026, 01_10_14.png" static/images/video-references/full-body.png
```

Expected: all three files exist.

- [ ] **Step 3: Verify MIME and dimensions**

Run:

```bash
file static/images/video-references/front.png
file static/images/video-references/profile.png
file static/images/video-references/full-body.png
```

Expected:

```text
static/images/video-references/front.png: PNG image data, 1024 x 1536
static/images/video-references/profile.png: PNG image data, 1254 x 1254
static/images/video-references/full-body.png: PNG image data, 1023 x 1537
```

- [ ] **Step 4: Run loader test against real defaults**

Add this test:

```python
def test_default_reference_image_assets_exist_and_load(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "veo_use_reference_images", True, raising=False)

    bundle = video_handlers._load_global_veo_reference_assets(correlation_id="corr-default-ref", strict=True)

    assert bundle is not None
    assert bundle["metadata"]["reference_image_count"] == 3
    assert [item["mime_type"] for item in bundle["reference_images"]] == ["image/png", "image/png", "image/png"]
```

Run:

```bash
python3 -m pytest tests/test_video_duration_routing.py::test_default_reference_image_assets_exist_and_load -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add static/images/video-references tests/test_video_duration_routing.py
git commit -m "feat: add default veo subject reference images"
```

---

### Task 8: Focused Regression And Optional Live Dry-Run

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused unit regressions**

Run:

```bash
python3 -m pytest tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py -v
```

Expected: PASS.

- [ ] **Step 2: Run a payload-only smoke check without submitting paid video**

Use the adapter fake tests as the payload acceptance criterion. Run:

```bash
python3 -m pytest \
  tests/test_video_duration_routing.py::test_veo_client_payload_includes_asset_reference_images \
  tests/test_video_duration_routing.py::test_veo_extension_request_uses_video_without_reference_images \
  -v
```

Expected: PASS.

- [ ] **Step 3: Manual live verification gate**

Only run a real paid Veo submission if the operator explicitly approves cost. Use one 8-second post first. Required environment:

```bash
export VEO_USE_REFERENCE_IMAGES=true
export VEO_REFERENCE_IMAGE_PATHS="static/images/video-references/front.png,static/images/video-references/profile.png,static/images/video-references/full-body.png"
```

Expected provider request shape from logs:

```text
instances[0].prompt = "...approved prompt..."
instances[0].referenceImages[0].referenceType = "asset"
instances[0].referenceImages[1].referenceType = "asset"
instances[0].referenceImages[2].referenceType = "asset"
instances[0].image is absent
instances[0].video is absent
instances[0].lastFrame is absent
parameters.aspectRatio = "9:16"
parameters.durationSeconds = 8
```

For a 16s/32s chain, expected extension request shape:

```text
instances[0].prompt = "...continuation prompt..."
instances[0].video.uri = "gs://...previous-hop.mp4"
instances[0].referenceImages is absent
instances[0].image is absent
instances[0].lastFrame is absent
parameters.durationSeconds = 7
```

- [ ] **Step 4: Commit final verification notes if docs changed**

If a short project note is added after live verification, commit it:

```bash
git add docs
git commit -m "docs: record reference image video verification"
```

---

## Self-Review

- Spec coverage: The plan covers creating the worktree, using three subject reference images for base generation, preserving extensions through the `video` field, and avoiding illegal `referenceImages` combinations with `image`/`lastFrame`/`video`.
- Placeholder scan: No `TBD`, empty placeholders, or deferred implementation steps remain. Each code-editing step includes concrete code or exact deletion targets.
- Type consistency: The plan consistently uses `reference_images` internally, `referenceImages` for the REST payload, and settings names `veo_use_reference_images` / `veo_reference_image_paths`.
- Dependency budget: No new dependencies.
- File budget: Five touched runtime/test/docs areas, with images counted as required assets rather than code modules.
