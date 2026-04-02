# External Speech Lip Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Veo speech with Deepgram TTS plus VEED lip sync for speech-bearing clips, while keeping silent clips on the current fast path and preserving the existing caption worker.

**Architecture:** Keep Veo generation and polling as the existing visual-generation spine. Add two thin adapters for Deepgram TTS and VEED lip sync, then extend `workers/video_poller.py` so completed scripted posts enter `voiceover_*` and `lipsync_*` states before `caption_pending`. Update batch and handler gates so only truly final assets reach captioning, QA, and publish progression.

**Tech Stack:** Python 3.11, FastAPI, Pydantic Settings, `httpx`, Supabase, Cloudflare R2, pytest

**Spec:** `docs/superpowers/specs/2026-04-02-external-speech-lipsync-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `app/core/config.py` | Modify | Add minimal settings for Deepgram TTS, VEED, and feature flagging |
| `app/core/video_profiles.py` | Modify | Add `voiceover_*` / `lipsync_*` statuses and pollable helper functions |
| `app/adapters/deepgram_tts_client.py` | Create | Deepgram TTS submission and error normalization |
| `app/adapters/veed_lipsync_client.py` | Create | VEED lip sync submit/status boundary |
| `workers/video_poller.py` | Modify | Route completed Veo posts into TTS/lip-sync stages before captioning |
| `app/features/batches/state_machine.py` | Modify | Keep batches from advancing while speech stages are in progress |
| `app/features/batches/handlers.py` | Modify | Treat voiceover/lipsync states as active polling states in batch detail |
| `app/features/videos/handlers.py` | Modify | Prevent resubmitting posts already in external-audio states |
| `tests/test_caption_status_constants.py` | Modify | Assert new status constants and helpers |
| `tests/test_deepgram_tts_client.py` | Create | Unit coverage for TTS payloads and error handling |
| `tests/test_veed_lipsync_client.py` | Create | Unit coverage for VEED payloads and status mapping |
| `tests/test_video_poller_external_audio.py` | Create | Poller routing, retries, and success/failure coverage |
| `tests/test_batches_status_progress.py` | Modify | Batch progression stays blocked until caption-complete |
| `tests/test_video_poller_caption_handoff.py` | Modify | Speech clips no longer go directly to `caption_pending` |

**Scope note:** The spec estimated five files plus tests. The implementation must also touch `app/core/config.py`, `app/features/batches/state_machine.py`, `app/features/batches/handlers.py`, and `app/features/videos/handlers.py` to make the new states safe. Keep those edits minimal and localized.

---

### Task 1: Add Config and Status Contracts

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/core/video_profiles.py`
- Test: `tests/test_caption_status_constants.py`

- [ ] **Step 1: Write the failing status/config tests**

Append these tests to `tests/test_caption_status_constants.py`:

```python
from app.core.video_profiles import (
    VIDEO_STATUS_VOICEOVER_PENDING,
    VIDEO_STATUS_VOICEOVER_PROCESSING,
    VIDEO_STATUS_VOICEOVER_FAILED,
    VIDEO_STATUS_LIPSYNC_PENDING,
    VIDEO_STATUS_LIPSYNC_PROCESSING,
    VIDEO_STATUS_LIPSYNC_FAILED,
    get_external_audio_pollable_statuses,
)


def test_external_audio_status_constants_exist():
    assert VIDEO_STATUS_VOICEOVER_PENDING == "voiceover_pending"
    assert VIDEO_STATUS_VOICEOVER_PROCESSING == "voiceover_processing"
    assert VIDEO_STATUS_VOICEOVER_FAILED == "voiceover_failed"
    assert VIDEO_STATUS_LIPSYNC_PENDING == "lipsync_pending"
    assert VIDEO_STATUS_LIPSYNC_PROCESSING == "lipsync_processing"
    assert VIDEO_STATUS_LIPSYNC_FAILED == "lipsync_failed"


def test_external_audio_statuses_not_in_video_pollable():
    pollable = get_pollable_video_statuses()
    assert "voiceover_pending" not in pollable
    assert "lipsync_pending" not in pollable


def test_get_external_audio_pollable_statuses():
    statuses = get_external_audio_pollable_statuses()
    assert statuses == ("voiceover_pending", "lipsync_pending")
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
pytest tests/test_caption_status_constants.py -v
```

Expected: `ImportError` or assertion failure because the new status constants and helper do not exist yet.

- [ ] **Step 3: Add the settings and status helpers**

Update `app/core/config.py` by adding these fields near the existing provider settings:

```python
    deepgram_tts_model: str = Field(
        "aura-2-thalia-en",
        description="Deepgram TTS model identifier used for speech-bearing clips",
    )
    deepgram_tts_voice: str = Field(
        "viktoria",
        description="Deepgram German TTS voice used for synthesized speech",
    )
    veed_api_key: str = Field("", description="VEED / fal.ai API key for lip sync")
    external_audio_enable_speech_pipeline: bool = Field(
        default=False,
        description="Route scripted Veo completions through Deepgram TTS and VEED lip sync",
    )
```

Update `app/core/video_profiles.py` by adding:

```python
VIDEO_STATUS_VOICEOVER_PENDING = "voiceover_pending"
VIDEO_STATUS_VOICEOVER_PROCESSING = "voiceover_processing"
VIDEO_STATUS_VOICEOVER_FAILED = "voiceover_failed"
VIDEO_STATUS_LIPSYNC_PENDING = "lipsync_pending"
VIDEO_STATUS_LIPSYNC_PROCESSING = "lipsync_processing"
VIDEO_STATUS_LIPSYNC_FAILED = "lipsync_failed"


def get_external_audio_pollable_statuses() -> tuple[str, ...]:
    return (
        VIDEO_STATUS_VOICEOVER_PENDING,
        VIDEO_STATUS_LIPSYNC_PENDING,
    )
```

- [ ] **Step 4: Run the test again and verify it passes**

Run:

```bash
pytest tests/test_caption_status_constants.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py app/core/video_profiles.py tests/test_caption_status_constants.py
git commit -m "feat(video): add external audio status contracts"
```

---

### Task 2: Add the Deepgram TTS Adapter

**Files:**
- Create: `app/adapters/deepgram_tts_client.py`
- Test: `tests/test_deepgram_tts_client.py`

- [ ] **Step 1: Write the failing adapter tests**

Create `tests/test_deepgram_tts_client.py` with:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

import app.adapters.deepgram_tts_client as tts_module


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    monkeypatch.setattr(
        tts_module,
        "get_settings",
        lambda: SimpleNamespace(
            deepgram_api_key="test-key",
            deepgram_tts_model="aura-2-thalia-en",
            deepgram_tts_voice="viktoria",
        ),
    )


def test_synthesize_posts_expected_payload():
    tts_module.DeepgramTTSClient._instance = None
    client = tts_module.DeepgramTTSClient()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.content = b"mp3-bytes"
    response.headers = {"content-type": "audio/mpeg"}
    client._client = MagicMock()
    client._client.post.return_value = response

    result = client.synthesize(
        text="Guten Tag aus Berlin.",
        correlation_id="corr-1",
    )

    call = client._client.post.call_args
    assert call.kwargs["headers"]["Authorization"] == "Token test-key"
    assert call.kwargs["json"]["text"] == "Guten Tag aus Berlin."
    assert result.audio_bytes == b"mp3-bytes"
    assert result.content_type == "audio/mpeg"
    assert result.model == "aura-2-thalia-en"
    assert result.voice == "viktoria"


def test_synthesize_5xx_is_transient():
    tts_module.DeepgramTTSClient._instance = None
    client = tts_module.DeepgramTTSClient()
    response = MagicMock(spec=httpx.Response)
    response.status_code = 503
    request = MagicMock(spec=httpx.Request)
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Service unavailable", request=request, response=response
    )
    client._client = MagicMock()
    client._client.post.return_value = response

    with pytest.raises(tts_module.DeepgramTTSError) as exc:
        client.synthesize(text="Hallo", correlation_id="corr-2")

    assert exc.value.transient is True
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
pytest tests/test_deepgram_tts_client.py -v
```

Expected: FAIL because `app.adapters.deepgram_tts_client` does not exist yet.

- [ ] **Step 3: Write the minimal adapter**

Create `app/adapters/deepgram_tts_client.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DEEPGRAM_TTS_API_URL = "https://api.deepgram.com/v1/speak"


class DeepgramTTSError(Exception):
    def __init__(self, message: str, *, transient: bool = False, details: Optional[dict] = None):
        super().__init__(message)
        self.transient = transient
        self.details = details or {}


@dataclass
class SynthesizedAudio:
    audio_bytes: bytes
    content_type: str
    model: str
    voice: str


class DeepgramTTSClient:
    _instance: Optional["DeepgramTTSClient"] = None

    def __new__(cls) -> "DeepgramTTSClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        settings = get_settings()
        self._api_key = settings.deepgram_api_key
        self._model = settings.deepgram_tts_model
        self._voice = settings.deepgram_tts_voice
        self._client = httpx.Client(timeout=60.0)
        self._initialized = True

    def synthesize(self, *, text: str, correlation_id: str) -> SynthesizedAudio:
        headers = {"Authorization": f"Token {self._api_key}"}
        payload = {"text": text}
        params = {"model": self._model, "voice": self._voice}
        response = self._client.post(DEEPGRAM_TTS_API_URL, params=params, headers=headers, json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise DeepgramTTSError(
                f"Deepgram TTS API error {status}",
                transient=status >= 500,
                details={"status_code": status, "correlation_id": correlation_id},
            ) from exc
        return SynthesizedAudio(
            audio_bytes=response.content,
            content_type=response.headers.get("content-type", "audio/mpeg"),
            model=self._model,
            voice=self._voice,
        )


def get_deepgram_tts_client() -> DeepgramTTSClient:
    return DeepgramTTSClient()
```

- [ ] **Step 4: Run the adapter test and verify it passes**

Run:

```bash
pytest tests/test_deepgram_tts_client.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/adapters/deepgram_tts_client.py tests/test_deepgram_tts_client.py
git commit -m "feat(video): add deepgram tts adapter"
```

---

### Task 3: Add the VEED Lip Sync Adapter

**Files:**
- Create: `app/adapters/veed_lipsync_client.py`
- Test: `tests/test_veed_lipsync_client.py`

- [ ] **Step 1: Write the failing VEED adapter tests**

Create `tests/test_veed_lipsync_client.py` with:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

import app.adapters.veed_lipsync_client as veed_module


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    monkeypatch.setattr(
        veed_module,
        "get_settings",
        lambda: SimpleNamespace(veed_api_key="veed-key"),
    )


def test_submit_job_posts_video_and_audio_urls():
    veed_module.VeedLipSyncClient._instance = None
    client = veed_module.VeedLipSyncClient()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"request_id": "req_123"}
    client._client = MagicMock()
    client._client.post.return_value = response

    request_id = client.submit_job(
        video_url="https://cdn.example.com/video.mp4",
        audio_url="https://cdn.example.com/audio.mp3",
        correlation_id="corr-1",
    )

    call = client._client.post.call_args
    assert call.kwargs["json"]["video_url"] == "https://cdn.example.com/video.mp4"
    assert call.kwargs["json"]["audio_url"] == "https://cdn.example.com/audio.mp3"
    assert request_id == "req_123"


def test_get_result_maps_completed_response():
    veed_module.VeedLipSyncClient._instance = None
    client = veed_module.VeedLipSyncClient()
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "status": "COMPLETED",
        "video": {"url": "https://cdn.example.com/final.mp4"},
    }
    client._client = MagicMock()
    client._client.get.return_value = response

    result = client.get_result("req_123", correlation_id="corr-2")

    assert result.status == "completed"
    assert result.video_url == "https://cdn.example.com/final.mp4"


def test_submit_job_429_is_transient():
    veed_module.VeedLipSyncClient._instance = None
    client = veed_module.VeedLipSyncClient()
    response = MagicMock(spec=httpx.Response)
    response.status_code = 429
    request = MagicMock(spec=httpx.Request)
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "rate limit", request=request, response=response
    )
    client._client = MagicMock()
    client._client.post.return_value = response

    with pytest.raises(veed_module.VeedLipSyncError) as exc:
        client.submit_job(
            video_url="https://cdn.example.com/video.mp4",
            audio_url="https://cdn.example.com/audio.mp3",
            correlation_id="corr-3",
        )

    assert exc.value.transient is True
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
pytest tests/test_veed_lipsync_client.py -v
```

Expected: FAIL because the adapter file does not exist yet.

- [ ] **Step 3: Write the minimal adapter**

Create `app/adapters/veed_lipsync_client.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

VEED_SUBMIT_URL = "https://queue.fal.run/fal-ai/veed/lipsync"
VEED_STATUS_URL = "https://queue.fal.run/fal-ai/veed/lipsync/requests/{request_id}/status"


class VeedLipSyncError(Exception):
    def __init__(self, message: str, *, transient: bool = False, details: Optional[dict] = None):
        super().__init__(message)
        self.transient = transient
        self.details = details or {}


@dataclass
class VeedLipSyncResult:
    status: str
    video_url: Optional[str]
    payload: dict


class VeedLipSyncClient:
    _instance: Optional["VeedLipSyncClient"] = None

    def __new__(cls) -> "VeedLipSyncClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._api_key = get_settings().veed_api_key
        self._client = httpx.Client(timeout=60.0)
        self._initialized = True

    def submit_job(self, *, video_url: str, audio_url: str, correlation_id: str) -> str:
        response = self._client.post(
            VEED_SUBMIT_URL,
            headers={"Authorization": f"Key {self._api_key}"},
            json={"video_url": video_url, "audio_url": audio_url},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise VeedLipSyncError(
                f"VEED lip sync API error {status}",
                transient=status >= 500 or status == 429,
                details={"status_code": status, "correlation_id": correlation_id},
            ) from exc
        payload = response.json()
        return payload["request_id"]

    def get_result(self, request_id: str, *, correlation_id: str) -> VeedLipSyncResult:
        response = self._client.get(
            VEED_STATUS_URL.format(request_id=request_id),
            headers={"Authorization": f"Key {self._api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        normalized = payload.get("status", "").lower()
        return VeedLipSyncResult(
            status=normalized,
            video_url=((payload.get("video") or {}).get("url")),
            payload=payload,
        )


def get_veed_lipsync_client() -> VeedLipSyncClient:
    return VeedLipSyncClient()
```

- [ ] **Step 4: Run the VEED adapter test and verify it passes**

Run:

```bash
pytest tests/test_veed_lipsync_client.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/adapters/veed_lipsync_client.py tests/test_veed_lipsync_client.py
git commit -m "feat(video): add veed lipsync adapter"
```

---

### Task 4: Integrate External Audio Into the Video Poller

**Files:**
- Modify: `workers/video_poller.py`
- Modify: `tests/test_video_poller_caption_handoff.py`
- Create: `tests/test_video_poller_external_audio.py`

- [ ] **Step 1: Write the failing poller tests**

Create `tests/test_video_poller_external_audio.py` with:

```python
from unittest.mock import MagicMock, patch

from app.core.video_profiles import (
    VIDEO_STATUS_VOICEOVER_PENDING,
    VIDEO_STATUS_LIPSYNC_PENDING,
    VIDEO_STATUS_CAPTION_PENDING,
)


@patch("workers.video_poller.get_settings")
@patch("workers.video_poller.get_supabase")
def test_completed_scripted_post_routes_to_voiceover_pending(mock_sb_factory, mock_settings):
    from workers.video_poller import _next_completed_video_status

    mock_settings.return_value.external_audio_enable_speech_pipeline = True
    post = {"seed_data": {"script": "Hallo aus Berlin."}}

    assert _next_completed_video_status(post) == VIDEO_STATUS_VOICEOVER_PENDING


@patch("workers.video_poller.get_settings")
def test_completed_silent_post_routes_to_caption_pending(mock_settings):
    from workers.video_poller import _next_completed_video_status

    mock_settings.return_value.external_audio_enable_speech_pipeline = True
    post = {"seed_data": {}}

    assert _next_completed_video_status(post) == VIDEO_STATUS_CAPTION_PENDING


@patch("workers.video_poller.get_storage_client")
@patch("workers.video_poller.get_deepgram_tts_client")
@patch("workers.video_poller.get_supabase")
def test_process_voiceover_pending_uploads_audio_and_sets_lipsync_pending(mock_sb_factory, mock_tts, mock_storage):
    from workers.video_poller import _process_voiceover_post

    mock_tts.return_value.synthesize.return_value.audio_bytes = b"audio"
    mock_tts.return_value.synthesize.return_value.content_type = "audio/mpeg"
    mock_tts.return_value.synthesize.return_value.model = "aura-2-thalia-en"
    mock_tts.return_value.synthesize.return_value.voice = "viktoria"
    mock_storage.return_value.upload_video.return_value = {
        "url": "https://cdn.example.com/audio.mp3",
        "storage_key": "audio/test.mp3",
    }

    mock_client = MagicMock()
    mock_sb_factory.return_value.client = mock_client
    mock_table = mock_client.table.return_value
    mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock()

    post = {
        "id": "post_1",
        "seed_data": {"script": "Hallo aus Berlin."},
        "video_metadata": {},
    }

    _process_voiceover_post(post)

    payload = mock_table.update.call_args_list[-1][0][0]
    assert payload["video_status"] == VIDEO_STATUS_LIPSYNC_PENDING
    assert payload["video_metadata"]["tts_audio_url"] == "https://cdn.example.com/audio.mp3"
```

Modify `tests/test_video_poller_caption_handoff.py` by replacing the assertion:

```python
        for call in update_calls:
            data = call[0][0]
            if "video_status" in data:
                assert data["video_status"] == VIDEO_STATUS_CAPTION_PENDING
                found = True
```

with:

```python
        for call in update_calls:
            data = call[0][0]
            if "video_status" in data:
                assert data["video_status"] in {"voiceover_pending", "caption_pending"}
                found = True
```

- [ ] **Step 2: Run the poller tests and verify they fail**

Run:

```bash
pytest tests/test_video_poller_caption_handoff.py tests/test_video_poller_external_audio.py -v
```

Expected: FAIL because the new helper functions and routing do not exist yet.

- [ ] **Step 3: Add the new poller helpers and routing**

In `workers/video_poller.py`, add these imports:

```python
from app.adapters.deepgram_tts_client import DeepgramTTSError, get_deepgram_tts_client
from app.adapters.veed_lipsync_client import VeedLipSyncError, get_veed_lipsync_client
from app.core.video_profiles import (
    get_external_audio_pollable_statuses,
    VIDEO_STATUS_VOICEOVER_PENDING,
    VIDEO_STATUS_VOICEOVER_PROCESSING,
    VIDEO_STATUS_VOICEOVER_FAILED,
    VIDEO_STATUS_LIPSYNC_PENDING,
    VIDEO_STATUS_LIPSYNC_PROCESSING,
    VIDEO_STATUS_LIPSYNC_FAILED,
)
```

Add these helper functions near the existing completion logic:

```python
def _has_spoken_script(post: Dict[str, Any]) -> bool:
    seed_data = post.get("seed_data") or {}
    return bool(str(seed_data.get("script") or seed_data.get("dialog_script") or "").strip())


def _next_completed_video_status(post: Dict[str, Any]) -> str:
    settings = get_settings()
    if settings.external_audio_enable_speech_pipeline and _has_spoken_script(post):
        return VIDEO_STATUS_VOICEOVER_PENDING
    return VIDEO_STATUS_CAPTION_PENDING
```

Inside `_store_completed_video(...)`, replace the hard-coded status update:

```python
    supabase.table("posts").update({
        "video_status": VIDEO_STATUS_CAPTION_PENDING,
        "video_url": upload_result["url"],
        "video_metadata": merged_metadata,
    }).eq("id", post_id).execute()
```

with:

```python
    current_post = (
        supabase.table("posts")
        .select("id, batch_id, seed_data")
        .eq("id", post_id)
        .single()
        .execute()
        .data
    )
    next_status = _next_completed_video_status(current_post or {})
    supabase.table("posts").update({
        "video_status": next_status,
        "video_url": upload_result["url"],
        "video_metadata": {
            **merged_metadata,
            "audio_strategy": "external_voiceover" if next_status == VIDEO_STATUS_VOICEOVER_PENDING else "silent_passthrough",
        },
    }).eq("id", post_id).execute()
```

Then add `_process_voiceover_post(post)` and `_process_lipsync_post(post)` that:

- synthesize TTS from `seed_data.script` or `seed_data.dialog_script`
- upload audio bytes through `get_storage_client().upload_video(...)`
- write `tts_audio_url`, `tts_model`, `tts_voice`
- move to `lipsync_pending`
- submit VEED job and store `lipsync_job_id`
- poll VEED and, on completion, overwrite `video_url` and move to `caption_pending`

Finally, in the main poll loop, after the existing video-operation polling, query:

```python
supabase.table("posts").select("*").in_("video_status", list(get_external_audio_pollable_statuses())).limit(5).execute()
```

Dispatch `voiceover_pending` posts to `_process_voiceover_post()` and `lipsync_pending` posts to `_process_lipsync_post()`.

- [ ] **Step 4: Run the poller tests and verify they pass**

Run:

```bash
pytest tests/test_video_poller_caption_handoff.py tests/test_video_poller_external_audio.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workers/video_poller.py tests/test_video_poller_caption_handoff.py tests/test_video_poller_external_audio.py
git commit -m "feat(video): add external audio poller pipeline"
```

---

### Task 5: Block Premature Batch Progression and Submission Edge Cases

**Files:**
- Modify: `app/features/batches/state_machine.py`
- Modify: `app/features/batches/handlers.py`
- Modify: `app/features/videos/handlers.py`
- Modify: `tests/test_batches_status_progress.py`

- [ ] **Step 1: Write the failing batch and handler regression tests**

Append this test to `tests/test_batches_status_progress.py`:

```python
def test_reconcile_batch_video_pipeline_state_waits_for_external_audio():
    from app.features.batches.state_machine import reconcile_batch_video_pipeline_state

    supabase = _make_fake_supabase(
        batch_state="S5_PROMPTS_BUILT",
        posts=[
            {
                "id": "post_1",
                "video_prompt_json": {"veo_prompt": "ok"},
                "video_status": "voiceover_pending",
                "seed_data": {"script_review_status": "approved"},
            },
            {
                "id": "post_2",
                "video_prompt_json": {"veo_prompt": "ok"},
                "video_status": "caption_completed",
                "seed_data": {"script_review_status": "approved"},
            },
        ],
    )

    state = reconcile_batch_video_pipeline_state(
        batch_id="batch_1",
        correlation_id="corr-1",
        supabase_client=supabase.client,
    )

    assert state == "S5_PROMPTS_BUILT"
```

If `tests/test_video_submission_flow.py` already covers skip logic, add this assertion there; otherwise add a small focused test to `tests/test_batches_status_progress.py`:

```python
def test_batch_detail_counts_voiceover_and_lipsync_as_active_polling():
    from app.features.batches.handlers import _build_batch_detail_view

    view = _build_batch_detail_view(
        {
            "state": "S5_PROMPTS_BUILT",
            "posts": [
                {"video_status": "voiceover_pending", "video_url": "https://cdn.example.com/a.mp4", "seed_data": {"script_review_status": "approved"}},
                {"video_status": "lipsync_pending", "video_url": "https://cdn.example.com/b.mp4", "seed_data": {"script_review_status": "approved"}},
            ],
        }
    )

    assert view["active_video_poll_count"] == 2
```

- [ ] **Step 2: Run the regression tests and verify they fail**

Run:

```bash
pytest tests/test_batches_status_progress.py -v
```

Expected: FAIL because the new external-audio states are not treated as in-progress.

- [ ] **Step 3: Update the batch and handler gates**

In `app/features/batches/handlers.py`, extend `polling_video_statuses`:

```python
    polling_video_statuses = {
        "submitted",
        "processing",
        "extended_submitted",
        "extended_processing",
        "voiceover_pending",
        "voiceover_processing",
        "lipsync_pending",
        "lipsync_processing",
        "caption_pending",
        "caption_processing",
    }
```

In `app/features/videos/handlers.py`, extend the resubmission guard:

```python
            if post.get("video_status") in [
                "submitted",
                "processing",
                "completed",
                "extended_submitted",
                "extended_processing",
                "voiceover_pending",
                "voiceover_processing",
                "lipsync_pending",
                "lipsync_processing",
            ]:
```

Leave `app/features/batches/state_machine.py` strict:

```python
    videos_ready = all(post.get("video_status") == VIDEO_STATUS_CAPTION_COMPLETED for post in active_posts)
```

Do not relax this. The regression test should prove that `voiceover_pending` blocks QA promotion.

- [ ] **Step 4: Run the regression tests and verify they pass**

Run:

```bash
pytest tests/test_batches_status_progress.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/features/batches/state_machine.py app/features/batches/handlers.py app/features/videos/handlers.py tests/test_batches_status_progress.py
git commit -m "feat(video): guard batch flow during external audio stages"
```

---

### Task 6: Run the Focused End-to-End Verification Suite

**Files:**
- No new files

- [ ] **Step 1: Run the complete focused suite**

Run:

```bash
pytest \
  tests/test_caption_status_constants.py \
  tests/test_deepgram_tts_client.py \
  tests/test_veed_lipsync_client.py \
  tests/test_video_poller_caption_handoff.py \
  tests/test_video_poller_external_audio.py \
  tests/test_batches_status_progress.py -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run a broader regression pass for adjacent behavior**

Run:

```bash
pytest tests/test_caption_worker.py tests/test_video_duration_routing.py -v
```

Expected: PASS, proving the caption worker and existing duration routing still behave.

- [ ] **Step 3: Manually inspect the final behavior in code**

Confirm all three are true before merging:

```python
assert _next_completed_video_status({"seed_data": {"script": "Hallo"}}) == "voiceover_pending"
assert _next_completed_video_status({"seed_data": {}}) == "caption_pending"
assert VIDEO_STATUS_CAPTION_COMPLETED == "caption_completed"
```

- [ ] **Step 4: Final commit**

```bash
git status --short
git commit --allow-empty -m "chore(video): verify external audio pipeline integration"
```

Expected: clean working tree or only unrelated pre-existing changes remain.

---

## Self-Review

### Spec coverage

- Externalize speech with Deepgram + VEED: covered by Tasks 2-4.
- Silent clips bypass external audio: covered by Task 4 helper routing tests.
- New statuses and pollable contracts: covered by Task 1.
- Batch progression blocked until final asset reaches caption completion: covered by Task 5.
- Keep caption worker unchanged as the post-final-video stage: covered by Task 4 and Task 6 regression suite.

### Placeholder scan

No `TODO`, `TBD`, or deferred “add error handling later” steps remain. Every task includes file paths, commands, and code snippets.

### Type consistency

- Status names are consistent across all tasks: `voiceover_pending`, `voiceover_processing`, `voiceover_failed`, `lipsync_pending`, `lipsync_processing`, `lipsync_failed`.
- Adapter names are consistent across all tasks: `DeepgramTTSClient`, `VeedLipSyncClient`.
- Poller helper names are consistent across all tasks: `_next_completed_video_status`, `_process_voiceover_post`, `_process_lipsync_post`.
