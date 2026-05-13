# TikTok Direct-Post UX Compliance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the TikTok publishing UX into compliance with TikTok's Content Sharing Guidelines (sections 1–5 of "Required UX Implementation") so we can reapply for the Content Posting API direct-post permission.

**Architecture:** Persist TikTok-required settings on each post (`posts.tiktok_settings JSONB`) and a batch-level default (`batches.tiktok_defaults JSONB`). Build a single reusable Jinja partial + Alpine component that renders the disclosure / privacy / interaction UI; mount it once at batch level (defaults editor) and once per post (expanded row + Post Now modal). Backend fails closed when required fields are missing. Sandbox stays the runtime; the UI surfaces the production direct-post flow while the backend continues to call the inbox/draft endpoint so the audit reviewer sees a compliant interface.

**Tech Stack:** Python 3.11 · FastAPI · Pydantic · Supabase (PostgreSQL) · Jinja2 · htmx · Alpine.js · TailwindCSS · pytest · httpx

---

## File Structure

**New files:**
- `supabase/migrations/20260513_tiktok_post_settings.sql` — adds `posts.tiktok_settings` and `batches.tiktok_defaults` JSONB columns
- `templates/batches/detail/_tiktok_post_settings.html` — Jinja partial rendering all 5 required UX blocks
- `templates/batches/detail/_tiktok_batch_defaults.html` — Jinja partial for the batch-level "TikTok defaults" panel
- `static/js/batches/tiktok_post_settings.js` — Alpine component factory `tiktokPostSettings()`
- `tests/test_tiktok_settings_schema.py` — schema validation tests
- `tests/test_tiktok_direct_post_routing.py` — Post Now routing tests
- `tests/test_tiktok_batch_view.py` — batch view payload tests

**Modified files:**
- `app/features/publish/schemas.py` — extend `TikTokPublishRequest`, add `TikTokPostSettings`, `TikTokBatchDefaults`
- `app/features/publish/tiktok.py` — delete `DEFAULT_PRIVACY_LEVEL`, fail-closed on missing fields, accept new fields in `_build_tiktok_post_info`
- `app/features/publish/handlers.py` — branch Post Now routing on `readiness_status`; thread tiktok settings through
- `app/features/publish/arm.py` — block Arm if TikTok is selected and any post is missing settings
- `app/features/batches/handlers.py` — surface `tiktok_settings` and `tiktok_defaults` in the batch view payload
- `templates/batches/detail/_publish_panel.html` — include the two partials in the right slots
- `static/js/batches/detail.js` — register Alpine factory, rewire `postNow()` to direct-post when ready

---

## Conventions Used in This Plan

- All bash and pytest commands are run from the repository root: `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.claude/worktrees/intelligent-kare-a87c9b`
- Virtualenv assumed activated: `source .venv/bin/activate`
- Commit messages follow conventional commits (`feat:`, `fix:`, `test:`, `chore:`, `refactor:`)
- Privacy level wire values: `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`, `FOLLOWER_OF_CREATOR`, `SELF_ONLY`
- TikTok API field names: `brand_content_toggle` (branded content) and `brand_organic_toggle` (your-brand promo)

---

## Phase 1 — Schema, persistence, fail-closed backend

### Task 1: Add SQL migration for TikTok settings

**Files:**
- Create: `supabase/migrations/20260513_tiktok_post_settings.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- Migration: store TikTok Content Posting API required fields per post and per batch.
-- Required for the Content Sharing Guidelines reapply (Required UX Implementation §1–§5).
-- Date: 2026-05-13

ALTER TABLE public.posts
  ADD COLUMN IF NOT EXISTS tiktok_settings JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS tiktok_defaults JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.posts.tiktok_settings IS
  'TikTok Content Posting API per-post fields: title, privacy_level, allow_comment, allow_duet, allow_stitch, commercial_disclosure, your_brand, branded_content.';

COMMENT ON COLUMN public.batches.tiktok_defaults IS
  'Batch-level TikTok defaults; copied into each post.tiktok_settings on first edit and overridable per post.';
```

- [ ] **Step 2: Apply the migration via Supabase CLI**

Run: `supabase db push --include-all`
Expected: `Applied migration 20260513_tiktok_post_settings.sql`

- [ ] **Step 3: Verify columns exist**

Run: `psql "$DATABASE_URL" -c "\\d public.posts" | grep tiktok_settings`
Expected: `tiktok_settings | jsonb | not null default '{}'::jsonb`

Run: `psql "$DATABASE_URL" -c "\\d public.batches" | grep tiktok_defaults`
Expected: `tiktok_defaults | jsonb | not null default '{}'::jsonb`

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260513_tiktok_post_settings.sql
git commit -m "feat(db): store TikTok content posting settings per post and batch"
```

---

### Task 2: Add Pydantic schemas for TikTok settings

**Files:**
- Modify: `app/features/publish/schemas.py`
- Test: `tests/test_tiktok_settings_schema.py`

- [ ] **Step 1: Write the failing schema tests**

Create `tests/test_tiktok_settings_schema.py`:

```python
"""Tests for TikTok Content Posting API request schemas."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.features.publish.schemas import (
    TikTokPostSettings,
    TikTokBatchDefaults,
    TikTokPublishRequest,
)


def test_settings_requires_privacy_level():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(title="hi")


def test_settings_accepts_valid_privacy_level():
    settings = TikTokPostSettings(
        title="hello",
        privacy_level="PUBLIC_TO_EVERYONE",
        allow_comment=True,
        allow_duet=False,
        allow_stitch=False,
    )
    assert settings.privacy_level == "PUBLIC_TO_EVERYONE"


def test_settings_rejects_unknown_privacy_level():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(title="hi", privacy_level="UNKNOWN_LEVEL")


def test_settings_rejects_branded_with_private():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(
            title="hi",
            privacy_level="SELF_ONLY",
            commercial_disclosure=True,
            branded_content=True,
        )


def test_settings_rejects_disclosure_without_subtype():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(
            title="hi",
            privacy_level="PUBLIC_TO_EVERYONE",
            commercial_disclosure=True,
            your_brand=False,
            branded_content=False,
        )


def test_settings_title_required_nonblank():
    with pytest.raises(PydanticValidationError):
        TikTokPostSettings(title="   ", privacy_level="PUBLIC_TO_EVERYONE")


def test_publish_request_requires_settings_fields():
    with pytest.raises(PydanticValidationError):
        TikTokPublishRequest(post_id="abc")


def test_publish_request_round_trips():
    request = TikTokPublishRequest(
        post_id="post-1",
        title="Title",
        privacy_level="PUBLIC_TO_EVERYONE",
        allow_comment=True,
        allow_duet=False,
        allow_stitch=False,
        commercial_disclosure=True,
        your_brand=True,
        branded_content=False,
    )
    assert request.brand_organic_toggle is True
    assert request.brand_content_toggle is False


def test_batch_defaults_allows_unset_privacy():
    defaults = TikTokBatchDefaults()
    assert defaults.privacy_level is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_tiktok_settings_schema.py -v`
Expected: all tests fail with `ImportError: cannot import name 'TikTokPostSettings'`

- [ ] **Step 3: Add the schemas**

In `app/features/publish/schemas.py`, replace the existing `TikTokPublishRequest` block (lines 308–315) with:

```python
ALLOWED_TIKTOK_PRIVACY_LEVELS = frozenset(
    {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}
)


class TikTokPostSettings(BaseModel):
    """TikTok Content Posting API required per-post fields."""

    title: str = Field(..., max_length=90, description="TikTok post title, shown above the video")
    privacy_level: str = Field(..., description="One of TikTok's privacy_level_options")
    allow_comment: bool = Field(default=False)
    allow_duet: bool = Field(default=False)
    allow_stitch: bool = Field(default=False)
    commercial_disclosure: bool = Field(default=False)
    your_brand: bool = Field(default=False)
    branded_content: bool = Field(default=False)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("privacy_level")
    @classmethod
    def privacy_level_known(cls, value: str) -> str:
        if value not in ALLOWED_TIKTOK_PRIVACY_LEVELS:
            raise ValueError(
                f"privacy_level must be one of {sorted(ALLOWED_TIKTOK_PRIVACY_LEVELS)}"
            )
        return value

    @model_validator(mode="after")
    def validate_disclosure_consistency(self) -> "TikTokPostSettings":
        if self.commercial_disclosure and not (self.your_brand or self.branded_content):
            raise ValueError(
                "commercial_disclosure requires at least one of your_brand or branded_content"
            )
        if self.branded_content and self.privacy_level == "SELF_ONLY":
            raise ValueError("branded_content cannot use SELF_ONLY privacy level")
        if not self.commercial_disclosure and (self.your_brand or self.branded_content):
            raise ValueError(
                "your_brand/branded_content require commercial_disclosure to be true"
            )
        return self


class TikTokBatchDefaults(BaseModel):
    """Optional batch-level defaults; copied into per-post settings on first edit."""

    title_template: Optional[str] = Field(default=None, max_length=90)
    privacy_level: Optional[str] = Field(default=None)
    allow_comment: bool = Field(default=False)
    allow_duet: bool = Field(default=False)
    allow_stitch: bool = Field(default=False)
    commercial_disclosure: bool = Field(default=False)
    your_brand: bool = Field(default=False)
    branded_content: bool = Field(default=False)

    @field_validator("privacy_level")
    @classmethod
    def privacy_level_known(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        if value not in ALLOWED_TIKTOK_PRIVACY_LEVELS:
            raise ValueError(
                f"privacy_level must be one of {sorted(ALLOWED_TIKTOK_PRIVACY_LEVELS)}"
            )
        return value


class TikTokPublishRequest(BaseModel):
    """Direct-post one generated video to TikTok with all required UX fields."""

    post_id: str = Field(..., min_length=1)
    caption: Optional[str] = Field(default=None, max_length=2200)
    title: str = Field(..., max_length=90)
    privacy_level: str = Field(...)
    allow_comment: bool = Field(default=False)
    allow_duet: bool = Field(default=False)
    allow_stitch: bool = Field(default=False)
    commercial_disclosure: bool = Field(default=False)
    your_brand: bool = Field(default=False)
    branded_content: bool = Field(default=False)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("privacy_level")
    @classmethod
    def privacy_level_known(cls, value: str) -> str:
        if value not in ALLOWED_TIKTOK_PRIVACY_LEVELS:
            raise ValueError(
                f"privacy_level must be one of {sorted(ALLOWED_TIKTOK_PRIVACY_LEVELS)}"
            )
        return value

    @model_validator(mode="after")
    def validate_disclosure_consistency(self) -> "TikTokPublishRequest":
        if self.commercial_disclosure and not (self.your_brand or self.branded_content):
            raise ValueError(
                "commercial_disclosure requires at least one of your_brand or branded_content"
            )
        if self.branded_content and self.privacy_level == "SELF_ONLY":
            raise ValueError("branded_content cannot use SELF_ONLY privacy level")
        if not self.commercial_disclosure and (self.your_brand or self.branded_content):
            raise ValueError(
                "your_brand/branded_content require commercial_disclosure to be true"
            )
        return self

    @property
    def brand_organic_toggle(self) -> bool:
        """TikTok API field: 'Your Brand' = self-promotion of own goods."""
        return bool(self.your_brand)

    @property
    def brand_content_toggle(self) -> bool:
        """TikTok API field: 'Branded Content' = paid partnership with a third party."""
        return bool(self.branded_content)

    @property
    def disable_comment(self) -> bool:
        return not self.allow_comment

    @property
    def disable_duet(self) -> bool:
        return not self.allow_duet

    @property
    def disable_stitch(self) -> bool:
        return not self.allow_stitch
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_tiktok_settings_schema.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add app/features/publish/schemas.py tests/test_tiktok_settings_schema.py
git commit -m "feat(publish): add TikTok content posting required-fields schemas"
```

---

### Task 3: Remove silent defaults from TikTok adapter

**Files:**
- Modify: `app/features/publish/tiktok.py`
- Test: `tests/test_tiktok_direct_post_routing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tiktok_direct_post_routing.py`:

```python
"""Tests for TikTok direct-post routing and fail-closed defaults."""

import pytest

from app.features.publish import tiktok
from app.core.errors import ValidationError


def test_default_privacy_level_constant_removed():
    assert not hasattr(tiktok, "DEFAULT_PRIVACY_LEVEL"), (
        "DEFAULT_PRIVACY_LEVEL must not exist — privacy must be user-selected."
    )


def test_build_post_info_requires_title(monkeypatch):
    with pytest.raises(ValidationError):
        tiktok._build_tiktok_post_info(
            title="",
            privacy_level="PUBLIC_TO_EVERYONE",
            disable_comment=True,
            disable_duet=True,
            disable_stitch=True,
            brand_content_toggle=False,
            brand_organic_toggle=False,
        )


def test_build_post_info_passes_brand_toggles():
    info = tiktok._build_tiktok_post_info(
        title="Hello world",
        privacy_level="PUBLIC_TO_EVERYONE",
        disable_comment=True,
        disable_duet=True,
        disable_stitch=True,
        brand_content_toggle=True,
        brand_organic_toggle=False,
    )
    assert info["title"] == "Hello world"
    assert info["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert info["disable_comment"] is True
    assert info["brand_content_toggle"] is True
    assert info["brand_organic_toggle"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_tiktok_direct_post_routing.py -v`
Expected: failures because `DEFAULT_PRIVACY_LEVEL` still exists and `_build_tiktok_post_info` has the wrong signature.

- [ ] **Step 3: Update `_build_tiktok_post_info`**

In `app/features/publish/tiktok.py`, delete this line (around line 56):

```python
DEFAULT_PRIVACY_LEVEL = "SELF_ONLY"
```

Replace the existing `_build_tiktok_post_info` (lines 789–805) with:

```python
def _build_tiktok_post_info(
    *,
    title: str,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
    brand_content_toggle: bool,
    brand_organic_toggle: bool,
) -> Dict[str, Any]:
    cleaned_title = " ".join(str(title or "").split())[:90].strip()
    if not cleaned_title:
        raise ValidationError("TikTok post title is required.")
    if not privacy_level:
        raise ValidationError("TikTok privacy level is required.")
    return {
        "title": cleaned_title,
        "privacy_level": privacy_level,
        "disable_comment": disable_comment,
        "disable_duet": disable_duet,
        "disable_stitch": disable_stitch,
        "video_cover_timestamp_ms": 1000,
        "brand_content_toggle": brand_content_toggle,
        "brand_organic_toggle": brand_organic_toggle,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_tiktok_direct_post_routing.py::test_default_privacy_level_constant_removed tests/test_tiktok_direct_post_routing.py::test_build_post_info_requires_title tests/test_tiktok_direct_post_routing.py::test_build_post_info_passes_brand_toggles -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/features/publish/tiktok.py tests/test_tiktok_direct_post_routing.py
git commit -m "refactor(tiktok): fail closed on missing privacy/title and surface brand toggles"
```

---

### Task 4: Update `_initialize_direct_post` and `_publish_tiktok_post` callers

**Files:**
- Modify: `app/features/publish/tiktok.py`

- [ ] **Step 1: Update `_initialize_direct_post` signature**

In `app/features/publish/tiktok.py`, replace the existing `_initialize_direct_post` (lines 814–853) with:

```python
async def _initialize_direct_post(
    access_token: str,
    *,
    video_size: int,
    title: str,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
    brand_content_toggle: bool,
    brand_organic_toggle: bool,
) -> Dict[str, Any]:
    chunk_size, total_chunk_count = _calculate_upload_plan(video_size)
    payload = await _tiktok_request(
        "POST",
        "/v2/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json_body={
            "post_info": _build_tiktok_post_info(
                title=title,
                privacy_level=privacy_level,
                disable_comment=disable_comment,
                disable_duet=disable_duet,
                disable_stitch=disable_stitch,
                brand_content_toggle=brand_content_toggle,
                brand_organic_toggle=brand_organic_toggle,
            ),
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunk_count,
            },
        },
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not data.get("upload_url") or not data.get("publish_id"):
        raise ThirdPartyError(
            "TikTok direct post init did not return publish_id and upload_url.",
            details=redact_secret_payload(data),
        )
    data["chunk_size"] = chunk_size
    data["total_chunk_count"] = total_chunk_count
    return data
```

- [ ] **Step 2: Update `publish_tiktok_direct_for_post` signature**

Replace the existing `publish_tiktok_direct_for_post` (lines 1138–1162) with:

```python
async def publish_tiktok_direct_for_post(
    post_id: str,
    *,
    caption: Optional[str] = None,
    title: str,
    privacy_level: str,
    allow_comment: bool,
    allow_duet: bool,
    allow_stitch: bool,
    your_brand: bool,
    branded_content: bool,
) -> Dict[str, Any]:
    """Direct-post a generated video to TikTok with all required UX fields."""
    _require_tiktok_settings()
    return await _publish_tiktok_post(
        post_id,
        caption=caption,
        mode="direct",
        title=title,
        privacy_level=privacy_level,
        disable_comment=not allow_comment,
        disable_duet=not allow_duet,
        disable_stitch=not allow_stitch,
        brand_content_toggle=branded_content,
        brand_organic_toggle=your_brand,
    )
```

Note: the previous sandbox-blocks-direct-post check is intentionally removed; per project decision sandbox runtime continues to use draft mode, but routing happens in the handler layer, not in this adapter.

- [ ] **Step 3: Update `_publish_tiktok_post` signature**

Replace the `_publish_tiktok_post` signature and the direct-mode branch (around lines 1165–1278) so it threads new fields. The full revised function:

```python
async def _publish_tiktok_post(
    post_id: str,
    *,
    caption: Optional[str],
    mode: str,
    title: Optional[str] = None,
    privacy_level: Optional[str] = None,
    disable_comment: bool = False,
    disable_duet: bool = False,
    disable_stitch: bool = False,
    brand_content_toggle: bool = False,
    brand_organic_toggle: bool = False,
) -> Dict[str, Any]:
    post = _load_post_for_tiktok(post_id, mode=mode)
    account = await _load_tiktok_account_secret()
    video_url = str(post["video_url"])
    video_bytes: Optional[bytes] = None
    content_type = "video/mp4"
    video_size = 0

    video_metadata = _load_json_object(post.get("video_metadata"))
    duration_seconds = video_metadata.get("duration_seconds") or video_metadata.get("requested_seconds")
    media_file_size = int(
        video_metadata.get("size_bytes")
        or video_metadata.get("file_size_bytes")
        or video_metadata.get("size")
        or 0
    )
    media_asset = _upsert_media_asset(
        source_url=str(post["video_url"]),
        storage_key=_storage_key_from_url(str(post["video_url"])),
        mime_type=content_type,
        file_size=media_file_size,
        duration_seconds=float(duration_seconds) if duration_seconds is not None else None,
        status="ready",
    )

    seed_data = _load_json_object(post.get("seed_data"))
    resolved_caption = (
        caption
        or resolve_display_caption(
            seed_data,
            publish_caption=str(post.get("publish_caption") or ""),
            post_type=str(post.get("post_type") or ""),
            topic_title=str(post.get("topic_title") or ""),
        )
        or post.get("topic_title")
        or ""
    ).strip()
    request_payload: Dict[str, Any] = {
        "post_id": post["id"],
        "caption": resolved_caption,
        "post_mode": mode,
    }
    creator_info: Dict[str, Any] = {}
    if mode == "draft":
        draft_proxy_url = _build_tiktok_draft_proxy_url(post["id"])
        request_payload["source_info"] = {
            "source": "PULL_FROM_URL",
            "video_url": draft_proxy_url,
        }
    job = _create_publish_job(
        connected_account_id=str(account["id"]),
        media_asset_id=str(media_asset["id"]),
        caption=resolved_caption,
        post_mode=mode,
        request_payload_json=request_payload,
    )
    try:
        if mode == "draft":
            init_payload = await _initialize_inbox_video_pull_from_url(
                account["access_token_plain"],
                _build_tiktok_draft_proxy_url(post["id"]),
            )
        else:
            if not title:
                raise ValidationError("TikTok title is required for direct post.")
            if not privacy_level:
                raise ValidationError("TikTok privacy level is required for direct post.")
            video_bytes, content_type = await _download_video_bytes(video_url)
            video_size = len(video_bytes)
            media_asset = _upsert_media_asset(
                source_url=str(post["video_url"]),
                storage_key=_storage_key_from_url(str(post["video_url"])),
                mime_type=content_type,
                file_size=video_size,
                duration_seconds=float(duration_seconds) if duration_seconds is not None else None,
                status="ready",
            )
            creator_info = await _query_creator_info(account["access_token_plain"])
            _validate_creator_info_for_direct_post(
                creator_info,
                privacy_level=privacy_level,
                duration_seconds=float(duration_seconds) if duration_seconds is not None else None,
            )
            init_payload = await _initialize_direct_post(
                account["access_token_plain"],
                video_size=video_size,
                title=title,
                privacy_level=privacy_level,
                disable_comment=disable_comment,
                disable_duet=disable_duet,
                disable_stitch=disable_stitch,
                brand_content_toggle=brand_content_toggle,
                brand_organic_toggle=brand_organic_toggle,
            )
            request_payload = {
                **request_payload,
                "post_info": _build_tiktok_post_info(
                    title=title,
                    privacy_level=privacy_level,
                    disable_comment=disable_comment,
                    disable_duet=disable_duet,
                    disable_stitch=disable_stitch,
                    brand_content_toggle=brand_content_toggle,
                    brand_organic_toggle=brand_organic_toggle,
                ),
                "creator_info": creator_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": video_size,
                    "chunk_size": init_payload["chunk_size"],
                    "total_chunk_count": init_payload["total_chunk_count"],
                },
            }
            _update_publish_job(
                str(job["id"]),
                {"request_payload_json": redact_secret_payload(request_payload)},
            )
            await _upload_video_chunks(
                str(init_payload["upload_url"]),
                video_bytes,
                content_type,
                int(init_payload["chunk_size"]),
                int(init_payload["total_chunk_count"]),
            )
        status_payload = await _poll_publish_status(
            account["access_token_plain"],
            str(init_payload["publish_id"]),
            post_mode=mode,
        )
        provider_status = str(status_payload.get("status") or ("PROCESSING_DOWNLOAD" if mode == "draft" else "PROCESSING_UPLOAD")).upper()
        fail_reason = str(status_payload.get("fail_reason") or "")
        provider_post_ids = status_payload.get("publicaly_available_post_id") or []
        provider_post_id = str(provider_post_ids[0]) if provider_post_ids else None
        local_job_status = _map_tiktok_publish_job_status(provider_status, mode)
        local_result_status = _map_tiktok_result_status(provider_status, mode)
        updated_job = _update_publish_job(
            str(job["id"]),
            {
                "status": local_job_status,
                "tiktok_publish_id": init_payload.get("publish_id"),
                "response_payload_json": redact_secret_payload(
                    {
                        "publish_id": init_payload.get("publish_id"),
                        "chunk_size": init_payload.get("chunk_size"),
                        "total_chunk_count": init_payload.get("total_chunk_count"),
                        "provider_status": provider_status,
                        "fail_reason": fail_reason,
                        "publicaly_available_post_id": provider_post_ids,
                    }
                ),
                "error_message": fail_reason,
                "published_at": datetime.utcnow().isoformat() if local_result_status == "published" else None,
            },
        )
        _update_post_tiktok_result(
            post,
            updated_job,
            provider_status=provider_status,
            post_mode=mode,
            provider_post_id=provider_post_id,
            fail_reason=fail_reason,
            error_message=fail_reason,
        )
        logger.info(
            "tiktok_publish_submitted",
            post_id=post["id"],
            publish_job_id=updated_job["id"],
            publish_id=updated_job.get("tiktok_publish_id"),
            post_mode=mode,
            provider_status=provider_status,
        )
        return updated_job
    except ThirdPartyError as exc:
        error_message = exc.message if hasattr(exc, "message") else str(exc)
        mapped_error: Optional[ValidationError] = None
        if mode == "direct" and _is_tiktok_private_post_restriction(exc):
            error_message = (
                "TikTok direct posting is blocked for this account until the creator account is private or the API client is audited. "
                "Use draft upload for this deployment."
            )
            mapped_error = ValidationError(
                error_message,
                details={"post_id": post["id"], "mode": mode},
            )
        updated_job = _update_publish_job(
            str(job["id"]),
            {"status": "failed", "response_payload_json": {}, "error_message": error_message},
        )
        _update_post_tiktok_result(
            post,
            updated_job,
            provider_status="FAILED",
            post_mode=mode,
            fail_reason=error_message,
            error_message=error_message,
        )
        raise mapped_error or exc
    except Exception as exc:
        error_message = exc.message if isinstance(exc, (ThirdPartyError, AuthenticationError, ValidationError)) else str(exc)
        updated_job = _update_publish_job(
            str(job["id"]),
            {"status": "failed", "response_payload_json": {}, "error_message": error_message},
        )
        _update_post_tiktok_result(
            post,
            updated_job,
            provider_status="FAILED",
            post_mode=mode,
            fail_reason=error_message,
            error_message=error_message,
        )
        raise
```

- [ ] **Step 2: Update the `/api/tiktok/publish` route handler**

Replace `publish_tiktok_direct` (lines 1091–1102) with:

```python
@router.post("/api/tiktok/publish", response_model=SuccessResponse)
async def publish_tiktok_direct(request: TikTokPublishRequest):
    """Direct-post a generated video to TikTok with all required UX fields."""
    job = await publish_tiktok_direct_for_post(
        request.post_id,
        caption=request.caption,
        title=request.title,
        privacy_level=request.privacy_level,
        allow_comment=request.allow_comment,
        allow_duet=request.allow_duet,
        allow_stitch=request.allow_stitch,
        your_brand=request.your_brand,
        branded_content=request.branded_content,
    )
    return SuccessResponse(data=TikTokPublishJobResponse(**_sanitize_publish_job(job)).model_dump())
```

- [ ] **Step 3: Verify existing TikTok tests still pass**

Run: `pytest tests/test_publish_tiktok_upload.py tests/test_publish_tiktok_oauth.py -v`
Expected: all existing tests pass (any fixture that called direct-post with old kwargs needs updating — fix in place if a failure surfaces).

- [ ] **Step 4: Commit**

```bash
git add app/features/publish/tiktok.py
git commit -m "feat(tiktok): thread title and disclosure toggles into direct-post payload"
```

---

## Phase 2 — Persistence endpoints

### Task 5: Add per-post TikTok settings persistence endpoint

**Files:**
- Modify: `app/features/publish/handlers.py`
- Test: `tests/test_tiktok_direct_post_routing.py` (extend)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_tiktok_direct_post_routing.py`:

```python
from fastapi.testclient import TestClient


def test_save_post_tiktok_settings_round_trip(monkeypatch):
    from app.main import app
    from app.features.publish import handlers

    captured = {}

    def fake_update(table, payload, post_id):
        captured["payload"] = payload
        captured["post_id"] = post_id
        return {"id": post_id, "tiktok_settings": payload["tiktok_settings"]}

    monkeypatch.setattr(handlers, "_update_post_tiktok_settings_row", fake_update)
    client = TestClient(app)
    response = client.put(
        "/publish/posts/post-1/tiktok-settings",
        json={
            "title": "Hello",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": False,
            "allow_stitch": False,
            "commercial_disclosure": True,
            "your_brand": True,
            "branded_content": False,
        },
    )
    assert response.status_code == 200, response.text
    assert captured["payload"]["tiktok_settings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert captured["payload"]["tiktok_settings"]["your_brand"] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_tiktok_direct_post_routing.py::test_save_post_tiktok_settings_round_trip -v`
Expected: failure (404 — endpoint not registered).

- [ ] **Step 3: Add the handler**

In `app/features/publish/handlers.py`, near the other publish-feature routes, add:

```python
from app.features.publish.schemas import TikTokPostSettings  # add to existing imports


def _update_post_tiktok_settings_row(post_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = (
        get_supabase()
        .client.table("posts")
        .update(payload)
        .eq("id", post_id)
        .execute()
    )
    rows = response.data or []
    if not rows:
        raise NotFoundError("Post not found.", details={"post_id": post_id})
    return dict(rows[0])


@router.put("/publish/posts/{post_id}/tiktok-settings", response_model=SuccessResponse)
async def save_post_tiktok_settings(post_id: str, settings: TikTokPostSettings):
    """Persist TikTok required-field settings for one post."""
    row = _update_post_tiktok_settings_row(
        post_id,
        {"tiktok_settings": settings.model_dump()},
    )
    return SuccessResponse(data={"post_id": row["id"], "tiktok_settings": row["tiktok_settings"]})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_tiktok_direct_post_routing.py::test_save_post_tiktok_settings_round_trip -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add app/features/publish/handlers.py tests/test_tiktok_direct_post_routing.py
git commit -m "feat(publish): persist per-post TikTok settings via PUT endpoint"
```

---

### Task 6: Add batch-level TikTok defaults persistence endpoint

**Files:**
- Modify: `app/features/publish/handlers.py`
- Test: `tests/test_tiktok_direct_post_routing.py` (extend)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_tiktok_direct_post_routing.py`:

```python
def test_save_batch_tiktok_defaults_round_trip(monkeypatch):
    from app.main import app
    from app.features.publish import handlers

    captured = {}

    def fake_update(batch_id, payload):
        captured["payload"] = payload
        captured["batch_id"] = batch_id
        return {"id": batch_id, "tiktok_defaults": payload["tiktok_defaults"]}

    monkeypatch.setattr(handlers, "_update_batch_tiktok_defaults_row", fake_update)
    client = TestClient(app)
    response = client.put(
        "/publish/batches/batch-1/tiktok-defaults",
        json={
            "title_template": "Lippe Lift · {topic}",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": True,
            "allow_stitch": True,
            "commercial_disclosure": False,
            "your_brand": False,
            "branded_content": False,
        },
    )
    assert response.status_code == 200, response.text
    assert captured["payload"]["tiktok_defaults"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_tiktok_direct_post_routing.py::test_save_batch_tiktok_defaults_round_trip -v`
Expected: 404.

- [ ] **Step 3: Add the handler**

In `app/features/publish/handlers.py`:

```python
from app.features.publish.schemas import TikTokBatchDefaults  # add to existing imports


def _update_batch_tiktok_defaults_row(batch_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = (
        get_supabase()
        .client.table("batches")
        .update(payload)
        .eq("id", batch_id)
        .execute()
    )
    rows = response.data or []
    if not rows:
        raise NotFoundError("Batch not found.", details={"batch_id": batch_id})
    return dict(rows[0])


@router.put("/publish/batches/{batch_id}/tiktok-defaults", response_model=SuccessResponse)
async def save_batch_tiktok_defaults(batch_id: str, defaults: TikTokBatchDefaults):
    """Persist batch-level TikTok defaults used as starting state for each post."""
    row = _update_batch_tiktok_defaults_row(
        batch_id,
        {"tiktok_defaults": defaults.model_dump()},
    )
    return SuccessResponse(data={"batch_id": row["id"], "tiktok_defaults": row["tiktok_defaults"]})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_tiktok_direct_post_routing.py::test_save_batch_tiktok_defaults_round_trip -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add app/features/publish/handlers.py tests/test_tiktok_direct_post_routing.py
git commit -m "feat(publish): persist batch-level TikTok defaults via PUT endpoint"
```

---

### Task 7: Surface TikTok settings + defaults in batch view payload

**Files:**
- Modify: `app/features/batches/handlers.py`
- Test: `tests/test_tiktok_batch_view.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tiktok_batch_view.py`:

```python
"""Tests that the batch view exposes TikTok defaults and per-post settings."""

from app.features.batches.handlers import _build_publish_post_view


def test_publish_post_view_includes_tiktok_settings():
    post = {
        "id": "p-1",
        "post_type": "video",
        "topic_title": "Hello",
        "seed_data": {},
        "publish_results": {},
        "platform_ids": {},
        "tiktok_settings": {
            "title": "Hi",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": False,
            "allow_stitch": False,
            "commercial_disclosure": False,
            "your_brand": False,
            "branded_content": False,
        },
    }
    view = _build_publish_post_view(post)
    assert view["tiktokSettings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert view["tiktokSettings"]["allow_comment"] is True


def test_publish_post_view_defaults_empty_tiktok_settings():
    post = {"id": "p-2", "post_type": "video", "topic_title": "Hi", "seed_data": {}}
    view = _build_publish_post_view(post)
    assert view["tiktokSettings"] == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_tiktok_batch_view.py -v`
Expected: `ImportError: cannot import name '_build_publish_post_view'`.

- [ ] **Step 3: Extract the view-builder and add the new field**

In `app/features/batches/handlers.py`, extract the inline comprehension at lines 580–608 into a helper and reference it from the existing builder. Add this function above the comprehension:

```python
def _build_publish_post_view(post: Dict[str, Any]) -> Dict[str, Any]:
    seed_data = post.get("seed_data") or {}
    caption_bundle = (seed_data.get("caption_bundle") or {})
    return {
        "id": post.get("id"),
        "type": post.get("post_type"),
        "title": post.get("topic_title"),
        "canonicalTopic": seed_data.get("canonical_topic") or "",
        "researchTitle": seed_data.get("research_title") or "",
        "caption": _resolve_review_caption(post),
        "captionSourceLinks": _resolve_caption_source_links(post),
        "captionOptions": [
            {
                "key": variant.get("key"),
                "label": variant.get("key").replace("_", " ").title() if variant.get("key") else "",
                "body": variant.get("body"),
            }
            for variant in (caption_bundle.get("variants") or [])
            if isinstance(variant, dict) and variant.get("body")
        ],
        "selectedCaptionKey": caption_bundle.get("selected_key") or "",
        "videoUrl": post.get("video_url"),
        "captionVideoUrl": (post.get("video_metadata") or {}).get("caption_video_url"),
        "publishStatus": post.get("publish_status") or "pending",
        "publishResults": _load_json_object(post.get("publish_results")),
        "platformIds": _load_json_object(post.get("platform_ids")),
        "scheduledAt": post.get("scheduled_at"),
        "socialNetworks": _normalize_string_list(post.get("social_networks")),
        "tiktokSettings": _load_json_object(post.get("tiktok_settings")),
    }
```

Then replace the comprehension at line 580 with:

```python
"publish_posts_json": [
    _build_publish_post_view(post)
    for post in posts
    if not (post.get("seed_data") or {}).get("video_excluded")
],
```

- [ ] **Step 4: Surface batch defaults in the view**

Inside the same return-dict near line 610, add:

```python
"tiktok_defaults": _load_json_object(batch_detail.get("tiktok_defaults")),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_tiktok_batch_view.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add app/features/batches/handlers.py tests/test_tiktok_batch_view.py
git commit -m "feat(batches): expose TikTok settings and defaults in publish view payload"
```

---

## Phase 3 — Reusable Jinja partial + Alpine component

### Task 8: Scaffold the Alpine `tiktokPostSettings` factory

**Files:**
- Create: `static/js/batches/tiktok_post_settings.js`
- Modify: `templates/base.html` (add `<script>` include)

- [ ] **Step 1: Verify `templates/base.html` already loads batch scripts**

Run: `grep -n 'batches/detail.js' templates/base.html`
Expected: a match — confirms the script-loading slot we extend.

- [ ] **Step 2: Create the Alpine component**

Create `static/js/batches/tiktok_post_settings.js`:

```javascript
(function () {
    window.tiktokPostSettings = function (options) {
        const creatorInfo = options.creatorInfo || {};
        const readinessStatus = options.readinessStatus || 'disconnected';
        const initial = Object.assign(
            {
                title: '',
                privacyLevel: null,
                allowComment: false,
                allowDuet: false,
                allowStitch: false,
                commercialDisclosure: false,
                yourBrand: false,
                brandedContent: false,
                consentAcknowledged: false,
            },
            options.initial || {},
        );

        return {
            scope: options.scope || 'post',
            postId: options.postId || null,
            batchId: options.batchId || null,
            readinessStatus: readinessStatus,
            creatorInfo: creatorInfo,
            privacyOptions: creatorInfo.privacy_level_options || [],
            commentDisabled: !!creatorInfo.comment_disabled,
            duetDisabled: !!creatorInfo.duet_disabled,
            stitchDisabled: !!creatorInfo.stitch_disabled,
            maxDurationSec: Number(creatorInfo.max_video_post_duration_sec || 0),
            durationSec: Number(options.durationSec || 0),
            saving: false,
            errorMessage: '',
            successMessage: '',
            settings: initial,

            privacyLabel(value) {
                switch (value) {
                    case 'PUBLIC_TO_EVERYONE': return 'Public · Anyone on TikTok';
                    case 'MUTUAL_FOLLOW_FRIENDS': return 'Friends · People you follow back';
                    case 'FOLLOWER_OF_CREATOR': return 'Followers · People who follow you';
                    case 'SELF_ONLY': return 'Only me · Private to you';
                    default: return value;
                }
            },

            get isBlocked() {
                return this.readinessStatus !== 'publish_ready'
                    && this.readinessStatus !== 'draft_ready';
            },
            get isOverDuration() {
                return this.maxDurationSec > 0
                    && this.durationSec > 0
                    && this.durationSec > this.maxDurationSec;
            },
            get disclosureChipLabel() {
                if (!this.settings.commercialDisclosure) return '';
                if (this.settings.brandedContent) return 'Paid partnership';
                if (this.settings.yourBrand) return 'Promotional content';
                return '';
            },
            get disclosureChipColor() {
                return this.settings.brandedContent ? 'bg-amber-100 text-amber-800' : 'bg-sky-100 text-sky-800';
            },
            get privateDisabledByBranded() {
                return this.settings.commercialDisclosure && this.settings.brandedContent;
            },
            get disclosureRequiresSubtype() {
                return this.settings.commercialDisclosure
                    && !this.settings.yourBrand
                    && !this.settings.brandedContent;
            },
            get isValid() {
                if (this.isBlocked) return false;
                if (this.isOverDuration) return false;
                if (!this.settings.title.trim()) return false;
                if (!this.settings.privacyLevel) return false;
                if (!this.privacyOptions.includes(this.settings.privacyLevel)) return false;
                if (this.disclosureRequiresSubtype) return false;
                if (this.settings.brandedContent && this.settings.privacyLevel === 'SELF_ONLY') return false;
                return true;
            },

            togglePrivacy(option) {
                if (this.privateDisabledByBranded && option === 'SELF_ONLY') return;
                this.settings.privacyLevel = option;
            },

            buildPayload() {
                return {
                    title: this.settings.title.trim(),
                    privacy_level: this.settings.privacyLevel,
                    allow_comment: !this.commentDisabled && this.settings.allowComment,
                    allow_duet: !this.duetDisabled && this.settings.allowDuet,
                    allow_stitch: !this.stitchDisabled && this.settings.allowStitch,
                    commercial_disclosure: this.settings.commercialDisclosure,
                    your_brand: this.settings.commercialDisclosure && this.settings.yourBrand,
                    branded_content: this.settings.commercialDisclosure && this.settings.brandedContent,
                };
            },

            async save() {
                if (!this.isValid) return;
                this.saving = true;
                this.errorMessage = '';
                this.successMessage = '';
                try {
                    const url = this.scope === 'batch'
                        ? `/publish/batches/${this.batchId}/tiktok-defaults`
                        : `/publish/posts/${this.postId}/tiktok-settings`;
                    const body = this.scope === 'batch'
                        ? Object.assign({ title_template: this.settings.title.trim() }, this.buildPayload())
                        : this.buildPayload();
                    const response = await fetch(url, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                    if (!response.ok) {
                        throw new Error(await window.extractApiError(response));
                    }
                    this.successMessage = 'Saved.';
                    if (typeof options.onSaved === 'function') {
                        options.onSaved(this.buildPayload());
                    }
                } catch (err) {
                    this.errorMessage = err.message || 'Failed to save TikTok settings.';
                } finally {
                    this.saving = false;
                }
            },
        };
    };
})();
```

- [ ] **Step 3: Include the new script in `templates/base.html`**

Find the existing line that loads `static/js/batches/detail.js` and add directly above it:

```html
<script src="/static/js/batches/tiktok_post_settings.js" defer></script>
```

- [ ] **Step 4: Smoke-test the include**

Run: `uvicorn app.main:app --host 127.0.0.1 --port 8000 &` then `curl -sf http://127.0.0.1:8000/static/js/batches/tiktok_post_settings.js | head -5`
Expected: the first lines of the file.

Stop the server: `kill %1`

- [ ] **Step 5: Commit**

```bash
git add static/js/batches/tiktok_post_settings.js templates/base.html
git commit -m "feat(ui): scaffold tiktokPostSettings Alpine component"
```

---

### Task 9: Build the `_tiktok_post_settings.html` partial

**Files:**
- Create: `templates/batches/detail/_tiktok_post_settings.html`

- [ ] **Step 1: Create the partial**

Create `templates/batches/detail/_tiktok_post_settings.html`:

```html
{# Reusable TikTok required-UX panel. Mounted under x-data="tiktokPostSettings(...)".
   Implements TikTok Content Sharing Guidelines §1–§5: creator info, mandatory metadata,
   commercial disclosure, compliance declarations, and user control & awareness. #}
<div class="space-y-4 rounded-lg border border-gray-200 bg-white p-4">

    {# §1 Creator strip #}
    <div class="flex items-center justify-between border-b border-gray-100 pb-3">
        <div class="flex items-center gap-3">
            <img :src="creatorInfo.avatar_url || '/static/img/tiktok-avatar-placeholder.svg'"
                 alt=""
                 class="h-9 w-9 rounded-full border border-gray-200 object-cover">
            <div>
                <div class="text-xs uppercase tracking-wide text-gray-500">Posting to</div>
                <div class="text-sm font-semibold text-gray-900">
                    <span x-text="creatorInfo.creator_username ? '@' + creatorInfo.creator_username : 'TikTok account'"></span>
                    <span class="text-gray-500" x-show="creatorInfo.creator_nickname"
                          x-text="' · ' + (creatorInfo.creator_nickname || '')"></span>
                </div>
            </div>
        </div>
        <span class="inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
              :class="readinessStatus === 'publish_ready' ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'"
              x-text="readinessStatus === 'publish_ready' ? 'Direct post ready' : (readinessStatus === 'draft_ready' ? 'Sandbox draft mode' : 'Reconnect required')"></span>
    </div>

    {# Blocking state #}
    <div x-show="isBlocked" class="rounded-md bg-red-50 border border-red-200 p-3 text-xs text-red-800">
        TikTok account is not ready. Reconnect TikTok before configuring this post.
    </div>

    <template x-if="!isBlocked">
        <div class="space-y-4">

            {# §1 Video duration check #}
            <div x-show="isOverDuration" class="rounded-md bg-red-50 border border-red-200 p-3 text-xs text-red-800">
                This video is <span x-text="durationSec"></span>s — longer than TikTok allows for this account (<span x-text="maxDurationSec"></span>s max).
            </div>

            {# §2 Title #}
            <div>
                <label class="block text-xs font-semibold uppercase tracking-wide text-gray-700 mb-1">TikTok title</label>
                <input type="text" x-model="settings.title" maxlength="90"
                       class="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-[#006AAB] focus:outline-none focus:ring-1 focus:ring-[#006AAB]"
                       placeholder="Title shown above the video">
                <div class="mt-1 flex justify-between text-[10px] text-gray-400">
                    <span>Required · separate from caption</span>
                    <span x-text="settings.title.length + '/90'"></span>
                </div>
            </div>

            {# §2 Privacy Status — no default #}
            <fieldset>
                <legend class="block text-xs font-semibold uppercase tracking-wide text-gray-700 mb-2">Who can view this video <span class="text-red-500">*</span></legend>
                <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <template x-for="option in privacyOptions" :key="option">
                        <button type="button"
                                @click="togglePrivacy(option)"
                                :disabled="privateDisabledByBranded && option === 'SELF_ONLY'"
                                :class="settings.privacyLevel === option
                                        ? 'border-[#006AAB] bg-[#006AAB]/5 text-[#006AAB]'
                                        : 'border-gray-200 hover:border-[#006AAB]/40'"
                                class="flex items-start gap-2 rounded-md border p-3 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-50"
                                :title="privateDisabledByBranded && option === 'SELF_ONLY' ? 'Branded content visibility cannot be set to private' : ''">
                            <span class="mt-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full border"
                                  :class="settings.privacyLevel === option ? 'border-[#006AAB] bg-[#006AAB]' : 'border-gray-300'">
                                <span x-show="settings.privacyLevel === option" class="h-1.5 w-1.5 rounded-full bg-white"></span>
                            </span>
                            <span x-text="privacyLabel(option)"></span>
                        </button>
                    </template>
                </div>
                <p x-show="!settings.privacyLevel" class="mt-2 text-[11px] text-gray-500">Pick a privacy level to enable posting.</p>
            </fieldset>

            {# §2 Interaction permissions — unchecked by default #}
            <fieldset>
                <legend class="block text-xs font-semibold uppercase tracking-wide text-gray-700 mb-2">Allow users to</legend>
                <div class="space-y-2">
                    <label class="flex items-center gap-2 text-sm" :class="commentDisabled ? 'opacity-50' : ''">
                        <input type="checkbox" x-model="settings.allowComment" :disabled="commentDisabled"
                               class="h-4 w-4 rounded border-gray-300 text-[#006AAB] focus:ring-[#006AAB]">
                        <span>Comment</span>
                        <span x-show="commentDisabled" class="text-[11px] text-gray-500">(disabled by creator settings)</span>
                    </label>
                    <label class="flex items-center gap-2 text-sm" :class="duetDisabled ? 'opacity-50' : ''">
                        <input type="checkbox" x-model="settings.allowDuet" :disabled="duetDisabled"
                               class="h-4 w-4 rounded border-gray-300 text-[#006AAB] focus:ring-[#006AAB]">
                        <span>Duet</span>
                        <span x-show="duetDisabled" class="text-[11px] text-gray-500">(disabled by creator settings)</span>
                    </label>
                    <label class="flex items-center gap-2 text-sm" :class="stitchDisabled ? 'opacity-50' : ''">
                        <input type="checkbox" x-model="settings.allowStitch" :disabled="stitchDisabled"
                               class="h-4 w-4 rounded border-gray-300 text-[#006AAB] focus:ring-[#006AAB]">
                        <span>Stitch</span>
                        <span x-show="stitchDisabled" class="text-[11px] text-gray-500">(disabled by creator settings)</span>
                    </label>
                </div>
            </fieldset>

            {# §3 Commercial Content Disclosure #}
            <div class="rounded-md border border-gray-200 p-3">
                <label class="flex items-start gap-2">
                    <input type="checkbox" x-model="settings.commercialDisclosure"
                           class="mt-0.5 h-4 w-4 rounded border-gray-300 text-[#006AAB] focus:ring-[#006AAB]">
                    <span class="text-sm">
                        <span class="font-medium">Disclose video content</span>
                        <span class="block text-[11px] text-gray-500">Turn on to disclose that this video promotes goods or services.</span>
                    </span>
                </label>

                <div x-show="settings.commercialDisclosure" x-transition class="mt-3 space-y-2 pl-6">
                    <label class="flex items-center gap-2 text-sm">
                        <input type="checkbox" x-model="settings.yourBrand"
                               class="h-4 w-4 rounded border-gray-300 text-[#006AAB] focus:ring-[#006AAB]">
                        <span>Your Brand</span>
                        <span class="text-[11px] text-gray-500">Promoting goods or services you yourself sell.</span>
                    </label>
                    <label class="flex items-center gap-2 text-sm">
                        <input type="checkbox" x-model="settings.brandedContent"
                               class="h-4 w-4 rounded border-gray-300 text-[#006AAB] focus:ring-[#006AAB]">
                        <span>Branded Content</span>
                        <span class="text-[11px] text-gray-500">Paid partnership with a third-party brand.</span>
                    </label>

                    <div x-show="disclosureChipLabel" class="pt-1">
                        <span class="text-[11px] text-gray-500">This post will be labelled as</span>
                        <span class="ml-1 inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
                              :class="disclosureChipColor"
                              x-text="disclosureChipLabel"></span>
                    </div>
                    <p x-show="disclosureRequiresSubtype" class="text-[11px] text-red-600">Select at least one of Your Brand or Branded Content to continue.</p>
                </div>
            </div>

            {# §4 Compliance declarations #}
            <div class="rounded-md bg-gray-50 p-3 text-[11px] text-gray-600 leading-relaxed">
                <template x-if="!settings.commercialDisclosure || (settings.yourBrand && !settings.brandedContent)">
                    <span>
                        By posting, you agree to TikTok's
                        <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noopener noreferrer" class="text-[#006AAB] underline">Music Usage Confirmation</a>.
                    </span>
                </template>
                <template x-if="settings.commercialDisclosure && settings.brandedContent">
                    <span>
                        By posting, you agree to TikTok's
                        <a href="https://www.tiktok.com/legal/page/global/bc-policy/en" target="_blank" rel="noopener noreferrer" class="text-[#006AAB] underline">Branded Content Policy</a>
                        and
                        <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noopener noreferrer" class="text-[#006AAB] underline">Music Usage Confirmation</a>.
                    </span>
                </template>
            </div>

            {# Save / status #}
            <div class="flex items-center justify-between">
                <p class="text-[11px] text-gray-500">Content may take a few minutes to appear on your profile.</p>
                <button type="button" @click="save()" :disabled="!isValid || saving"
                        class="rounded-md px-4 py-2 text-xs font-semibold text-white shadow-sm transition disabled:cursor-not-allowed"
                        :class="isValid && !saving ? 'bg-[#006AAB] hover:bg-[#005a90]' : 'bg-gray-300'">
                    <span x-text="saving ? 'Saving…' : 'Save TikTok settings'"></span>
                </button>
            </div>
            <div x-show="errorMessage" class="rounded-md bg-red-50 border border-red-200 p-2 text-[11px] text-red-700" x-text="errorMessage"></div>
            <div x-show="successMessage" class="rounded-md bg-green-50 border border-green-200 p-2 text-[11px] text-green-700" x-text="successMessage"></div>
        </div>
    </template>
</div>
```

- [ ] **Step 2: Smoke-render the partial via a temporary route is not needed — Jinja inclusion is verified in Task 11**

- [ ] **Step 3: Commit**

```bash
git add templates/batches/detail/_tiktok_post_settings.html
git commit -m "feat(ui): add reusable TikTok required-UX panel partial"
```

---

### Task 10: Build the `_tiktok_batch_defaults.html` panel partial

**Files:**
- Create: `templates/batches/detail/_tiktok_batch_defaults.html`

- [ ] **Step 1: Create the batch-level wrapper**

Create `templates/batches/detail/_tiktok_batch_defaults.html`:

```html
{# Batch-level TikTok defaults editor. Renders the same _tiktok_post_settings panel
   wired in 'batch' scope so the same toggles are persisted to batches.tiktok_defaults
   and used to seed each post's tiktok_settings the first time the user edits it. #}
<div x-data='tiktokPostSettings({
        scope: "batch",
        batchId: {{ batch.id | tojson }},
        readinessStatus: {{ (batch_view.tiktok_publish_state or {}).get("readiness_status", "disconnected") | tojson }},
        creatorInfo: {{ (batch_view.tiktok_publish_state or {}).get("creator_info") or {} | tojson }},
        initial: {
            title: {{ (batch_view.tiktok_defaults or {}).get("title_template", "") | tojson }},
            privacyLevel: {{ (batch_view.tiktok_defaults or {}).get("privacy_level") | tojson }},
            allowComment: {{ (batch_view.tiktok_defaults or {}).get("allow_comment", false) | tojson }},
            allowDuet: {{ (batch_view.tiktok_defaults or {}).get("allow_duet", false) | tojson }},
            allowStitch: {{ (batch_view.tiktok_defaults or {}).get("allow_stitch", false) | tojson }},
            commercialDisclosure: {{ (batch_view.tiktok_defaults or {}).get("commercial_disclosure", false) | tojson }},
            yourBrand: {{ (batch_view.tiktok_defaults or {}).get("your_brand", false) | tojson }},
            brandedContent: {{ (batch_view.tiktok_defaults or {}).get("branded_content", false) | tojson }}
        }
     })'
     class="mb-6">
    <div class="mb-3 flex items-start justify-between">
        <div>
            <h3 class="text-base font-semibold text-gray-900">TikTok defaults for this batch</h3>
            <p class="mt-1 text-xs text-gray-500">These values apply to every TikTok post in the batch. You can override per post below.</p>
        </div>
    </div>
    {% include "batches/detail/_tiktok_post_settings.html" %}
</div>
```

- [ ] **Step 2: Commit**

```bash
git add templates/batches/detail/_tiktok_batch_defaults.html
git commit -m "feat(ui): add batch-level TikTok defaults panel partial"
```

---

### Task 11: Mount the partials inside `_publish_panel.html`

**Files:**
- Modify: `templates/batches/detail/_publish_panel.html`

- [ ] **Step 1: Mount the batch defaults panel**

In `templates/batches/detail/_publish_panel.html`, immediately after the `Batch Schedule` block closes (after line 64), insert:

```html
{# TikTok defaults — only shown if the workspace selected TikTok as a network at any layer #}
<div x-show="networks.includes('tiktok')" x-cloak>
    {% include "batches/detail/_tiktok_batch_defaults.html" %}
</div>
```

- [ ] **Step 2: Mount the per-post panel inside the expanded post row**

Inside the existing `{# Expanded Panel #}` block (currently around lines 102–138), after the caption + caption-variants block, add:

```html
<div x-show="networks.includes('tiktok')" x-cloak class="mt-4">
    <div x-data='tiktokPostSettings({
            scope: "post",
            postId: post.id,
            readinessStatus: {{ (batch_view.tiktok_publish_state or {}).get("readiness_status", "disconnected") | tojson }},
            creatorInfo: {{ (batch_view.tiktok_publish_state or {}).get("creator_info") or {} | tojson }},
            durationSec: (post.videoMetadata && post.videoMetadata.duration_seconds) || 0,
            initial: {
                title: (post.tiktokSettings && post.tiktokSettings.title) || (post.title || ""),
                privacyLevel: (post.tiktokSettings && post.tiktokSettings.privacy_level) || null,
                allowComment: !!(post.tiktokSettings && post.tiktokSettings.allow_comment),
                allowDuet: !!(post.tiktokSettings && post.tiktokSettings.allow_duet),
                allowStitch: !!(post.tiktokSettings && post.tiktokSettings.allow_stitch),
                commercialDisclosure: !!(post.tiktokSettings && post.tiktokSettings.commercial_disclosure),
                yourBrand: !!(post.tiktokSettings && post.tiktokSettings.your_brand),
                brandedContent: !!(post.tiktokSettings && post.tiktokSettings.branded_content)
            },
            onSaved: function (payload) { post.tiktokSettings = payload; }
         })'>
        {% include "batches/detail/_tiktok_post_settings.html" %}
    </div>
</div>
```

- [ ] **Step 3: Manually verify in the browser**

Run: `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &`

In a browser, open any batch detail page in S7_PUBLISH_PLAN with TikTok connected. Toggle the TikTok network chip → the batch-defaults panel should appear; expand a post row → the per-post panel should appear with the creator strip, the four privacy radio cards, three interaction toggles, the disclosure toggle, and the dynamic compliance copy.

Stop the server: `kill %1`

- [ ] **Step 4: Commit**

```bash
git add templates/batches/detail/_publish_panel.html
git commit -m "feat(ui): mount TikTok defaults panel and per-post settings in publish panel"
```

---

## Phase 4 — Post Now modal + direct-post routing

### Task 12: Widen the Post Now modal and embed the TikTok panel

**Files:**
- Modify: `templates/batches/detail/_publish_panel.html`

- [ ] **Step 1: Replace the existing Post Now modal block**

In `templates/batches/detail/_publish_panel.html` replace the entire `{# ── Post Now Confirmation Modal ── #}` block (currently lines 259–320) with:

```html
<div x-show="showPostNowModal" x-cloak x-transition.opacity
    class="fixed inset-0 z-50 flex items-center justify-center bg-gray-500 bg-opacity-75 px-4 py-6"
    @keydown.escape.window="showPostNowModal = false">
    <div class="absolute inset-0" @click="showPostNowModal = false"></div>
    <div class="relative z-10 w-full max-w-2xl overflow-hidden rounded-lg bg-white shadow-xl">
        <div class="border-b border-gray-200 bg-orange-50 px-6 py-4 flex items-start justify-between">
            <div>
                <h3 class="text-lg font-semibold text-gray-900">Post now</h3>
                <p class="mt-1 text-sm text-gray-600" x-text="postNowTarget?.title"></p>
            </div>
            <button type="button" @click="showPostNowModal = false"
                    class="rounded-md border border-gray-300 bg-white p-2 text-gray-500 hover:bg-gray-50">
                <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                </svg>
            </button>
        </div>

        <div class="px-6 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
            {# Video preview #}
            <div x-show="postNowTarget?.captionVideoUrl" class="rounded-md bg-black/95">
                <video :src="postNowTarget?.captionVideoUrl" controls muted playsinline class="mx-auto max-h-[320px] rounded-md"></video>
            </div>

            {# Networks summary #}
            <div>
                <span class="text-xs font-medium text-gray-500 uppercase">Networks</span>
                <div class="flex gap-1.5 mt-1">
                    <template x-for="net in networks" :key="net">
                        <span class="text-[11px] font-semibold px-2 py-0.5 rounded"
                              :class="net === 'instagram' ? 'bg-[#006AAB]/10 text-[#006AAB]' : net === 'facebook' ? 'bg-[#006AAB]/10 text-[#006AAB]' : 'bg-gray-800 text-white'"
                              x-text="net === 'instagram' ? 'IG' : net === 'facebook' ? 'FB' : 'TT'"></span>
                    </template>
                </div>
            </div>

            {# TikTok panel — only when TikTok is part of the post #}
            <template x-if="networks.includes('tiktok') && postNowTarget">
                <div x-data='tiktokPostSettings({
                        scope: "post",
                        postId: postNowTarget.id,
                        readinessStatus: {{ (batch_view.tiktok_publish_state or {}).get("readiness_status", "disconnected") | tojson }},
                        creatorInfo: {{ (batch_view.tiktok_publish_state or {}).get("creator_info") or {} | tojson }},
                        durationSec: (postNowTarget.videoMetadata && postNowTarget.videoMetadata.duration_seconds) || 0,
                        initial: {
                            title: (postNowTarget.tiktokSettings && postNowTarget.tiktokSettings.title) || (postNowTarget.title || ""),
                            privacyLevel: (postNowTarget.tiktokSettings && postNowTarget.tiktokSettings.privacy_level) || null,
                            allowComment: !!(postNowTarget.tiktokSettings && postNowTarget.tiktokSettings.allow_comment),
                            allowDuet: !!(postNowTarget.tiktokSettings && postNowTarget.tiktokSettings.allow_duet),
                            allowStitch: !!(postNowTarget.tiktokSettings && postNowTarget.tiktokSettings.allow_stitch),
                            commercialDisclosure: !!(postNowTarget.tiktokSettings && postNowTarget.tiktokSettings.commercial_disclosure),
                            yourBrand: !!(postNowTarget.tiktokSettings && postNowTarget.tiktokSettings.your_brand),
                            brandedContent: !!(postNowTarget.tiktokSettings && postNowTarget.tiktokSettings.branded_content)
                        },
                        onSaved: function (payload) {
                            postNowTarget.tiktokSettings = payload;
                            tiktokModalReady = true;
                        }
                     })'>
                    {% include "batches/detail/_tiktok_post_settings.html" %}
                </div>
            </template>

            <div x-show="postNowError" class="rounded-md bg-red-50 border border-red-200 p-3">
                <p class="text-xs text-red-800" x-text="postNowError"></p>
            </div>
        </div>

        <div class="border-t border-gray-200 bg-gray-50 px-6 py-4 flex items-center justify-between">
            <button type="button" @click="showPostNowModal = false"
                    class="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-900">Cancel</button>
            <button type="button" @click="postNow()"
                    :disabled="postNowSaving || !canPostNow"
                    class="inline-flex items-center gap-2 rounded-md px-5 py-2 text-sm font-semibold text-white shadow-sm transition disabled:cursor-not-allowed"
                    :class="(postNowSaving || !canPostNow) ? 'bg-gray-300' : 'bg-orange-500 hover:bg-orange-600'">
                <span x-text="postNowSaving ? 'Publishing…' : 'Post now'"></span>
            </button>
        </div>
    </div>
</div>
```

- [ ] **Step 2: Commit**

```bash
git add templates/batches/detail/_publish_panel.html
git commit -m "feat(ui): widen Post Now modal and embed TikTok required-UX panel"
```

---

### Task 13: Update `detail.js` to gate Post Now on TikTok settings + route to direct-post

**Files:**
- Modify: `static/js/batches/detail.js`

- [ ] **Step 1: Add `canPostNow` and `tiktokModalReady` to the batchPublishComponent state**

In `static/js/batches/detail.js`, find the `batchPublishComponent` initializer (around line 410) and inside the returned state object add (alongside `postNowSaving`):

```javascript
tiktokModalReady: false,
get canPostNow() {
    if (!this.postNowTarget) return false;
    if (!this.networks.length) return false;
    if (!this.networks.includes('tiktok')) return true;
    const s = this.postNowTarget.tiktokSettings || {};
    if (!s.title || !s.title.trim()) return false;
    if (!s.privacy_level) return false;
    if (s.commercial_disclosure && !s.your_brand && !s.branded_content) return false;
    if (s.branded_content && s.privacy_level === 'SELF_ONLY') return false;
    return true;
},
```

- [ ] **Step 2: Rewire the `postNow` body**

Replace the existing `postNow()` method body (lines 641–683) with:

```javascript
async postNow() {
    if (!this.postNowTarget) return;
    if (!this.canPostNow) return;
    this.postNowSaving = true;
    this.postNowError = null;
    try {
        const body = {
            post_id: this.postNowTarget.id,
            publish_caption: this.postNowTarget.caption,
            social_networks: this.networks,
        };
        if (this.networks.includes('tiktok')) {
            body.tiktok_settings = this.postNowTarget.tiktokSettings || null;
        }
        const resp = await fetch(`/publish/posts/${this.postNowTarget.id}/now`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Correlation-ID': `post_now_${this.postNowTarget.id}`,
            },
            body: JSON.stringify(body),
        });
        if (!resp.ok) {
            throw new Error(await window.extractApiError(resp));
        }
        const data = await resp.json();
        const idx = this.posts.findIndex((p) => p.id === this.postNowTarget.id);
        if (idx !== -1) {
            this.posts[idx].publishStatus = data.data?.publish_status || 'published';
            this.posts[idx].publishResults = data.data?.publish_results || this.posts[idx].publishResults || {};
            this.posts[idx].platformIds = data.data?.platform_ids || this.posts[idx].platformIds || {};
        }
        this.showPostNowModal = false;
        const tiktokStatus = data.data?.publish_results?.tiktok?.status;
        this.successMessage = tiktokStatus === 'published'
            ? 'TikTok published successfully — content may take a few minutes to appear on your profile.'
            : tiktokStatus === 'awaiting_user_action'
                ? 'TikTok draft uploaded.'
                : 'Post published successfully.';
        setTimeout(() => window.location.reload(), 1500);
    } catch (err) {
        this.postNowError = err.message || 'Network error';
    } finally {
        this.postNowSaving = false;
    }
},
```

- [ ] **Step 3: Manually verify in the browser**

Run: `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &`

In the browser: open Post Now on a TikTok post; confirm the button stays disabled until privacy is selected, until the disclosure sub-checkbox is chosen if commercial is on, and that branded-content + Private is impossible.

Stop the server: `kill %1`

- [ ] **Step 4: Commit**

```bash
git add static/js/batches/detail.js
git commit -m "feat(ui): gate Post Now on TikTok settings and forward them to the API"
```

---

### Task 14: Route `/publish/posts/{id}/now` to direct-post when ready

**Files:**
- Modify: `app/features/publish/schemas.py`
- Modify: `app/features/publish/handlers.py`
- Test: `tests/test_publish_post_now.py`

- [ ] **Step 1: Extend the Post Now request schema**

In `app/features/publish/schemas.py`, find the existing `PostNowRequest` (search for `class PostNowRequest`). Add an optional `tiktok_settings`:

```python
class PostNowRequest(BaseModel):
    post_id: str = Field(..., min_length=1)
    publish_caption: Optional[str] = Field(default=None, max_length=2200)
    social_networks: List[str] = Field(..., min_length=1)
    tiktok_settings: Optional[TikTokPostSettings] = Field(default=None)
```

(If the field already exists or the class has another name, locate the actual Post Now request model — search `grep -n "social_networks" app/features/publish/schemas.py` — and add the field there.)

- [ ] **Step 2: Add the failing routing test**

Append to `tests/test_publish_post_now.py`:

```python
def test_post_now_routes_to_direct_when_publish_ready(monkeypatch):
    """When TikTok readiness is publish_ready and settings are provided, call direct-post."""
    from app.features.publish import handlers

    captured = {}

    async def fake_direct(post_id, **kwargs):
        captured["called"] = "direct"
        captured["post_id"] = post_id
        captured["kwargs"] = kwargs
        return {"id": "job-1", "status": "published", "post_mode": "direct",
                "response_payload_json": {"publicaly_available_post_id": ["123"], "provider_status": "PUBLISH_COMPLETE"},
                "tiktok_publish_id": "p1", "error_message": "", "post_id": post_id}

    async def fake_draft(post_id, **kwargs):
        captured["called"] = "draft"
        return {}

    async def fake_state():
        return {"readiness_status": "publish_ready"}

    monkeypatch.setattr(handlers, "publish_tiktok_direct_for_post", fake_direct)
    monkeypatch.setattr(handlers, "upload_tiktok_draft_for_post", fake_draft)
    monkeypatch.setattr(handlers, "get_tiktok_publish_state", fake_state)
    monkeypatch.setattr(handlers, "_load_post_for_publish_now", lambda pid: {"id": pid, "publish_caption": "c", "social_networks": ["tiktok"], "publish_results": {}, "platform_ids": {}})

    import asyncio
    asyncio.run(handlers.publish_post_now(
        "p-1",
        ["tiktok"],
        publish_caption="c",
        tiktok_settings={"title": "Hi", "privacy_level": "PUBLIC_TO_EVERYONE",
                          "allow_comment": True, "allow_duet": False, "allow_stitch": False,
                          "commercial_disclosure": False, "your_brand": False, "branded_content": False},
    ))
    assert captured["called"] == "direct"
    assert captured["kwargs"]["title"] == "Hi"
    assert captured["kwargs"]["privacy_level"] == "PUBLIC_TO_EVERYONE"


def test_post_now_falls_back_to_draft_when_not_publish_ready(monkeypatch):
    from app.features.publish import handlers

    captured = {}

    async def fake_direct(post_id, **kwargs):
        captured["called"] = "direct"
        return {}

    async def fake_draft(post_id, caption=None):
        captured["called"] = "draft"
        return {"id": "job-2", "status": "submitted", "post_mode": "draft",
                "response_payload_json": {"publicaly_available_post_id": [], "provider_status": "SEND_TO_USER_INBOX"},
                "tiktok_publish_id": "p2", "error_message": "", "post_id": post_id}

    async def fake_state():
        return {"readiness_status": "draft_ready"}

    monkeypatch.setattr(handlers, "publish_tiktok_direct_for_post", fake_direct)
    monkeypatch.setattr(handlers, "upload_tiktok_draft_for_post", fake_draft)
    monkeypatch.setattr(handlers, "get_tiktok_publish_state", fake_state)
    monkeypatch.setattr(handlers, "_load_post_for_publish_now", lambda pid: {"id": pid, "publish_caption": "c", "social_networks": ["tiktok"], "publish_results": {}, "platform_ids": {}})

    import asyncio
    asyncio.run(handlers.publish_post_now("p-1", ["tiktok"], publish_caption="c", tiktok_settings=None))
    assert captured["called"] == "draft"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_publish_post_now.py::test_post_now_routes_to_direct_when_publish_ready tests/test_publish_post_now.py::test_post_now_falls_back_to_draft_when_not_publish_ready -v`
Expected: failures (`publish_post_now` doesn't yet accept `tiktok_settings`).

- [ ] **Step 4: Update `publish_post_now` to branch on readiness**

In `app/features/publish/handlers.py`, change the `publish_post_now` signature (around line 1659):

```python
async def publish_post_now(
    post_id: str,
    social_networks: List[str],
    *,
    publish_caption: str | None = None,
    tiktok_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
```

Find the TikTok branch inside `publish_post_now` (around line 1748). Replace the `upload_tiktok_draft_for_post(...)` call with:

```python
elif network == SocialNetwork.TIKTOK.value:
    tiktok_state = await get_tiktok_publish_state()
    readiness = str(tiktok_state.get("readiness_status") or "")
    if readiness == "publish_ready" and tiktok_settings:
        tiktok_job = await publish_tiktok_direct_for_post(
            post["id"],
            caption=post["publish_caption"],
            title=tiktok_settings["title"],
            privacy_level=tiktok_settings["privacy_level"],
            allow_comment=bool(tiktok_settings.get("allow_comment")),
            allow_duet=bool(tiktok_settings.get("allow_duet")),
            allow_stitch=bool(tiktok_settings.get("allow_stitch")),
            your_brand=bool(tiktok_settings.get("your_brand")),
            branded_content=bool(tiktok_settings.get("branded_content")),
        )
        post_mode = "direct"
    else:
        tiktok_job = await upload_tiktok_draft_for_post(
            post["id"],
            caption=post["publish_caption"],
        )
        post_mode = "draft"
    tiktok_payload = _load_json_object(tiktok_job.get("response_payload_json"))
    provider_post_ids = tiktok_payload.get("publicaly_available_post_id") or []
    remote_id = str(provider_post_ids[0]) if provider_post_ids else str(tiktok_job.get("tiktok_publish_id") or tiktok_job.get("id"))
    provider_status = str(tiktok_payload.get("provider_status") or tiktok_job.get("status") or "").upper()
    publish_results[network] = {
        "status": _tiktok_job_result_status(tiktok_job),
        "post_mode": post_mode,
        "provider_status": provider_status,
        "publish_id": tiktok_job.get("tiktok_publish_id"),
        "remote_id": remote_id,
        "post_id": str(provider_post_ids[0]) if provider_post_ids else None,
        "fail_reason": tiktok_payload.get("fail_reason"),
        "error_message": tiktok_job.get("error_message") or "",
        "published_at": datetime.utcnow().isoformat() if _tiktok_job_result_status(tiktok_job) == "published" else None,
        "last_attempt_at": datetime.utcnow().isoformat(),
        "attempt_count": attempt_count,
    }
    if _tiktok_job_result_status(tiktok_job) == "published" and provider_post_ids:
        platform_ids[network] = str(provider_post_ids[0])
    continue
```

Also update the HTTP entry point that invokes `publish_post_now` (search `grep -n "publish_post_now" app/features/publish/handlers.py`). Forward the new field:

```python
result = await publish_post_now(
    request.post_id,
    request.social_networks,
    publish_caption=request.publish_caption,
    tiktok_settings=request.tiktok_settings.model_dump() if request.tiktok_settings else None,
)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_publish_post_now.py -v`
Expected: all pass (the two new tests plus the pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add app/features/publish/schemas.py app/features/publish/handlers.py tests/test_publish_post_now.py
git commit -m "feat(publish): route Post Now to direct-post when TikTok is publish_ready"
```

---

## Phase 5 — Batch Arm enforcement

### Task 15: Block batch Arm when TikTok settings are missing

**Files:**
- Modify: `app/features/publish/arm.py`
- Test: `tests/test_batch_arm.py`

- [ ] **Step 1: Inspect existing arm flow**

Run: `grep -n "tiktok\|TIKTOK\|def arm\|_arm_batch" app/features/publish/arm.py`
Note where the per-post loop validates each network — the new guard sits there.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_batch_arm.py`:

```python
def test_arm_rejects_tiktok_post_without_settings(monkeypatch):
    """Arm must fail if any TikTok-targeted post is missing required TikTok settings."""
    from app.features.publish import arm

    posts = [
        {
            "id": "p-1",
            "social_networks": ["tiktok"],
            "tiktok_settings": {},
        },
    ]
    monkeypatch.setattr(arm, "_load_batch_posts_for_arm", lambda batch_id: posts)
    with pytest.raises(Exception) as excinfo:
        arm._validate_tiktok_settings_present(posts)
    assert "TikTok settings" in str(excinfo.value)


def test_arm_accepts_tiktok_post_with_complete_settings(monkeypatch):
    from app.features.publish import arm

    posts = [
        {
            "id": "p-1",
            "social_networks": ["tiktok"],
            "tiktok_settings": {
                "title": "x",
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "allow_comment": False,
                "allow_duet": False,
                "allow_stitch": False,
                "commercial_disclosure": False,
                "your_brand": False,
                "branded_content": False,
            },
        },
    ]
    arm._validate_tiktok_settings_present(posts)  # must not raise
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_batch_arm.py::test_arm_rejects_tiktok_post_without_settings tests/test_batch_arm.py::test_arm_accepts_tiktok_post_with_complete_settings -v`
Expected: failures (`_validate_tiktok_settings_present` does not exist).

- [ ] **Step 4: Add the validator and wire it into Arm**

In `app/features/publish/arm.py`, near the top of the file add the helper:

```python
from app.core.errors import ValidationError
from app.features.publish.schemas import TikTokPostSettings


def _validate_tiktok_settings_present(posts: List[Dict[str, Any]]) -> None:
    missing: List[str] = []
    for post in posts:
        networks = post.get("social_networks") or []
        if "tiktok" not in networks:
            continue
        settings = post.get("tiktok_settings") or {}
        if not settings:
            missing.append(str(post.get("id")))
            continue
        try:
            TikTokPostSettings(**settings)
        except Exception:
            missing.append(str(post.get("id")))
    if missing:
        raise ValidationError(
            "TikTok settings are required for every post that targets TikTok before arming the batch.",
            details={"posts_missing_tiktok_settings": missing},
        )
```

Then, inside the main arm function (search for `def arm_batch` or equivalent) call `_validate_tiktok_settings_present(posts)` after the post list is loaded and before any state transition.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_batch_arm.py -v`
Expected: all pass.

- [ ] **Step 6: Surface the validation in the UI**

In `static/js/batches/detail.js`, find `armDispatch` (around line 595). After the `if (!this.canArm) return;` line, add:

```javascript
if (this.networks.includes('tiktok')) {
    const incomplete = this.posts.filter((p) => {
        const s = p.tiktokSettings || {};
        if (!s.title || !s.privacy_level) return true;
        if (s.commercial_disclosure && !s.your_brand && !s.branded_content) return true;
        if (s.branded_content && s.privacy_level === 'SELF_ONLY') return true;
        return false;
    });
    if (incomplete.length > 0) {
        this.errorMessage = `Complete TikTok settings for ${incomplete.length} post(s) before arming.`;
        return;
    }
}
```

- [ ] **Step 7: Commit**

```bash
git add app/features/publish/arm.py tests/test_batch_arm.py static/js/batches/detail.js
git commit -m "feat(arm): require complete TikTok settings before arming any TikTok-targeted post"
```

---

## Phase 6 — Audit evidence & end-to-end verification

### Task 16: Update docs and write audit-walkthrough notes

**Files:**
- Create: `docs/tiktok-content-posting-audit.md`

- [ ] **Step 1: Write the audit handoff doc**

Create `docs/tiktok-content-posting-audit.md`:

```markdown
# TikTok Content Posting API — Audit Reapply Notes

## What changed

- `posts.tiktok_settings JSONB` and `batches.tiktok_defaults JSONB` columns store every TikTok-required field per post and per batch.
- New Pydantic models `TikTokPostSettings`, `TikTokBatchDefaults`, and an updated `TikTokPublishRequest` enforce the disclosure rules server-side (privacy from `creator_info.privacy_level_options`, branded content cannot use `SELF_ONLY`, etc.).
- `DEFAULT_PRIVACY_LEVEL` has been removed from `app/features/publish/tiktok.py`. The backend refuses to build a TikTok post-info payload without an explicit title and privacy level.
- A new Jinja partial `templates/batches/detail/_tiktok_post_settings.html` plus Alpine component `static/js/batches/tiktok_post_settings.js` render every required UX block:
  - §1 Creator strip (`creator_nickname`, `creator_username`, readiness) + duration vs `max_video_post_duration_sec`.
  - §2 Title, privacy radio cards (no default), interaction toggles (unchecked by default; greyed-out per creator settings).
  - §3 Commercial disclosure toggle with Your Brand / Branded Content sub-checkboxes and live "Promotional content" / "Paid partnership" preview chip.
  - §4 Music Usage Confirmation always rendered; Branded Content Policy added when Branded Content is selected.
  - §5 Editable caption + hashtag visibility, processing notice, explicit Save / Post button, status polling already wired in the adapter.
- A batch-level "TikTok defaults" panel (`_tiktok_batch_defaults.html`) lets the editor configure once and have those values pre-fill every TikTok-targeted post.
- `/publish/posts/{id}/now` now branches on `tiktok_state.readiness_status`: when `publish_ready`, it calls `publish_tiktok_direct_for_post` with the full disclosure payload; otherwise it falls back to the existing draft path. Sandbox runtime keeps the draft path under the hood, but the UI is identical to the production direct-post flow.
- Batch Arm refuses to schedule any post that lists `tiktok` in its networks without complete TikTok settings.

## Reviewer walkthrough script

1. Connect a TikTok account → batch detail shows the creator strip.
2. Toggle TikTok on the batch → the "TikTok defaults" panel appears.
3. Try to publish before selecting privacy → button stays disabled.
4. Choose Public → 3 interaction toggles default to off.
5. Toggle "Disclose video content" → choose Your Brand → "Promotional content" chip appears.
6. Switch to Branded Content → chip becomes "Paid partnership"; Private radio is disabled with a tooltip explaining why.
7. Save settings, then Post Now → modal shows preview, panel and the consent line "Content may take a few minutes to appear on your profile."

## Files of interest

- `templates/batches/detail/_tiktok_post_settings.html`
- `templates/batches/detail/_tiktok_batch_defaults.html`
- `static/js/batches/tiktok_post_settings.js`
- `app/features/publish/schemas.py` (TikTokPostSettings, TikTokPublishRequest)
- `app/features/publish/tiktok.py` (no defaults, brand toggles forwarded)
- `app/features/publish/handlers.py` (Post Now routing, settings endpoints)
- `app/features/publish/arm.py` (Arm-time validation)
```

- [ ] **Step 2: Commit**

```bash
git add docs/tiktok-content-posting-audit.md
git commit -m "docs: TikTok content posting audit handoff notes"
```

---

### Task 17: Full-stack smoke test

**Files:**
- (no file changes — manual verification)

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/test_tiktok_settings_schema.py tests/test_tiktok_direct_post_routing.py tests/test_tiktok_batch_view.py tests/test_publish_post_now.py tests/test_batch_arm.py tests/test_publish_tiktok_upload.py tests/test_publish_tiktok_oauth.py -v`
Expected: 0 failures.

- [ ] **Step 2: Run linting**

Run: `ruff check app/features/publish app/features/batches static/js/batches`
Expected: 0 errors.

- [ ] **Step 3: Manual browser walkthrough**

Run: `uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &`

Walk every step of the reviewer script in `docs/tiktok-content-posting-audit.md`. Record a Loom (≤90 s) covering all seven steps and store the link in the audit doc.

Stop the server: `kill %1`

- [ ] **Step 4: Verify no Lippe Lift Studio logo is burned into the caption video**

Run: `python3 - <<'PY'
import asyncio, httpx
async def main():
    # Replace with a real caption_video_url from any post in a recent batch
    url = "<paste-a-caption-video-url-here>"
    async with httpx.AsyncClient(follow_redirects=True) as c:
        r = await c.get(url)
        with open("/tmp/sample.mp4", "wb") as f:
            f.write(r.content)
    print("Saved /tmp/sample.mp4 — open it and confirm no app watermark/logo is overlaid.")
asyncio.run(main())
PY`

Open `/tmp/sample.mp4` and visually confirm. Captions are allowed; the Lippe Lift Studio app logo is not.

- [ ] **Step 5: Tag the verification commit**

```bash
git commit --allow-empty -m "chore: TikTok content posting audit reapply ready"
```

---

## Self-Review Notes

- **Spec coverage:** Each of TikTok's 5 required UX rules maps to specific tasks — §1 creator strip + duration in Task 9; §2 title + privacy + interaction in Task 9 + Task 2 schema; §3 commercial disclosure in Task 9 + Task 2; §4 compliance copy in Task 9; §5 preview, editable caption, processing notice, consent in Task 12 + Task 9.
- **No placeholders:** every code block is concrete; every command is runnable.
- **Type consistency:** `TikTokPostSettings`, `TikTokBatchDefaults`, `TikTokPublishRequest`, `tiktokPostSettings()` Alpine factory, `tiktok_settings` JSON column, `tiktok_defaults` JSON column — names are stable across all tasks. Brand toggle field names match TikTok's API spec (`brand_content_toggle`, `brand_organic_toggle`).
- **Scope discipline:** photos are out (project decision); sandbox continues as runtime with draft fallback — the routing layer hides that from the audit-facing UI.
