# AYRA Actor Identity Character Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace new `character_consistency` batches with a Magnific-backed ActorIdentity workflow that trains one active LoRA, generates gated scene reference images per post, and blocks video submission until the approved scene reference contract is satisfied.

**Architecture:** Keep the existing `character_consistency` mode name and preserve legacy `CharacterSnapshot` batches. Add ActorIdentity as the new durable identity source for future batches, use a narrow Magnific adapter for LoRA training and Mystic still generation, and add local gates before batch creation, scene reference generation, and video submission. The MVP uses manual/pending identity gates first with a clear fail-closed contract; an automated face-similarity dependency remains behind one later spike, not a hidden requirement.

**Tech Stack:** FastAPI, Jinja2, HTMX, Pydantic, Supabase SQL migrations, Cloudflare R2, httpx, pytest, Magnific Mystic API. No new runtime dependency in the MVP.

**Scope Budget:** `{files: 14-18, LOC/file: <=350 target and <=600 hard, deps: 0 for MVP; optional later face gate spike may add exactly 1 dependency for face similarity after proof}`

---

## Context-Zero

**Verified local environment on 2026-05-20:**
- Worktree: `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/.worktrees/ayra-character-consistency-plan`
- Branch: `codex/ayra-character-consistency-plan`
- Base commit: `2acdc91e95cc7b5e42f8e1fb600036dfa4968c29`
- OS: Darwin 23.6.0 arm64
- Local `python3`: 3.9.6
- README target: Python 3.11+
- Key pins from `requirements.txt`: `fastapi==0.104.1`, `pydantic==2.5.0`, `pydantic-settings==2.1.0`, `supabase==2.9.0`, `httpx==0.27.2`, `boto3==1.35.36`
- Focused baseline: `python3 -m pytest -q tests/test_characters_feature.py tests/test_character_consistency_mode.py` -> 13 passed, warnings only

**Current app shape:**
- `app/features/characters/` stores one active legacy `CharacterRecord` with exactly three image URLs.
- `app/features/batches/queries.py:create_batch(...)` snapshots that record into `batches.character_snapshot` for `creation_mode = "character_consistency"`.
- `app/features/posts/prompt_builder.py` can build a text `scene_plan`, but it is not controlled by a catalog and is not image-anchored.
- `app/features/videos/handlers.py` attaches legacy three-image snapshots for some VEO/Vertex routes and records skip metadata when the route cannot carry references.
- `supabase/migrations/20260508_character_consistency_mode.sql` owns the current `characters`, `batches.character_snapshot`, and `batches.scene_plan` schema.

**Magnific docs verified on 2026-05-20:**
- Authentication uses `x-magnific-api-key` on server-to-server calls.
- Character LoRA training is `POST /v1/ai/loras/characters` with required `name`, `quality`, `gender`, and `images`; `images` must be 8-20 public image URLs.
- LoRA status is read through `GET /v1/ai/loras`; custom LoRAs expose `training.status` and `training.defaultScale`.
- Mystic generation is `POST /v1/ai/mystic`; character LoRAs can be used through `styling.characters`.
- Mystic silently ignores LoRAs when incompatible fields are present. The adapter must reject `structure_reference`, `style_reference`, and incompatible models such as `fluid`, `flexible`, `super_real`, and `editorial_portraits` locally before submission.
- Mystic task status is `GET /v1/ai/mystic/{task-id}` and completed responses include generated image URLs.
- Webhooks can be added later, but the MVP uses polling first.

## Capability Map

1. **Actor enrollment:** Upload 8-20 public R2 images from the settings page and persist one active ActorIdentity.
2. **LoRA training:** Submit Magnific character LoRA training, store provider task/name/id/status, and poll until ready.
3. **Training readiness:** Disable new `character_consistency` batches until one active ActorIdentity has a completed LoRA.
4. **Legacy compatibility:** Existing batches with `character_snapshot` continue through the old route.
5. **Intent mapping:** Convert approved scripts into controlled `scene_key` and `wardrobe_key` values.
6. **Scene reference stills:** Generate up to three Magnific Mystic still candidates per post using `styling.characters`.
7. **Still review and gate:** Persist `IdentityGateResult`; fail closed when no manual/automated gate pass exists.
8. **Video route handoff:** Submit video only when a compatible route can consume approved scene references or mark the route visibly incompatible.
9. **Video gate:** Persist a post-video gate result and block publish/approval on failed or missing identity validation.
10. **Observability:** Log correlation ids and provider metadata without secrets or signed/private URLs.

## Boundary Map

- **R2 storage:** Source ActorTrainingSet images must be public URLs. Store R2 keys and public URLs, but log only keys/counts.
- **Supabase:** New rows for actor identities, scene reference images, and identity gate results. Existing `characters` rows remain legacy data.
- **Magnific:** `POST /v1/ai/loras/characters`, `GET /v1/ai/loras`, `POST /v1/ai/mystic`, `GET /v1/ai/mystic/{task-id}`.
- **VEO/Vertex:** Current 8s/16s/32s routing must not be changed in this plan. Any route that cannot consume approved scene references must fail visibly for ActorIdentity-backed no-drift mode instead of silently falling back.
- **Operator UI:** Settings page owns training and readiness. Batch/post surfaces own per-post scene reference review and gate state.

## Dependency Map

- MVP dependencies: 0 new packages. Use existing `httpx`, `boto3`, Pydantic, FastAPI, Jinja2, HTMX, and pytest.
- Automated face similarity: not part of the first implementation block. If implemented, run a separate spike and choose exactly one dependency for face embedding/detection. Until then, gate state is explicit manual/pending and the UI must not claim automated no-drift.

## File Map

| File | Action | Responsibility | Budget |
| --- | --- | --- | --- |
| `supabase/migrations/20260520_actor_identity_lora.sql` | Create | ActorIdentity, SceneReferenceImage, gate metadata schema | <=220 LOC |
| `app/core/config.py` | Modify | Magnific env, timeouts, poll interval, gate mode flags | <=40 LOC added |
| `app/adapters/magnific_client.py` | Create | Magnific HTTP adapter and compatibility guard | <=350 LOC |
| `app/features/characters/schemas.py` | Modify | ActorIdentity, ActorTrainingSet, SceneReferenceImage, IdentityGateResult contracts | <=300 LOC total |
| `app/features/characters/queries.py` | Modify | Active identity persistence, training status, scene reference rows | <=350 LOC total |
| `app/features/characters/actor_identity.py` | Create | Pure readiness/status/gate helpers | <=300 LOC |
| `app/features/characters/scene_reference.py` | Create | SceneCatalog, WardrobeSet, ScriptIntentMap, prompt assembly | <=350 LOC |
| `app/features/characters/handlers.py` | Modify | Settings upload, train, poll, scene review endpoints | <=450 LOC total |
| `templates/settings/character.html` | Modify | ActorTrainingSet upload and training progress display | <=260 LOC total |
| `templates/batches/detail/_post_card.html` | Modify | Scene reference status/review affordance | <=80 LOC added |
| `app/features/batches/schemas.py` | Modify | Expose actor identity metadata in batch responses | <=40 LOC added |
| `app/features/batches/queries.py` | Modify | TrainingReadinessGate and ActorIdentity snapshot metadata | <=80 LOC added |
| `app/features/videos/handlers.py` | Modify | Require approved scene reference and thread scene reference metadata | <=180 LOC added |
| `workers/video_poller.py` | Modify | Record video IdentityGateResult after completion | <=90 LOC added |
| `tests/test_actor_identity_training.py` | Create | Training/readiness contracts | <=300 LOC |
| `tests/test_magnific_actor_identity.py` | Create | Magnific adapter payload/status tests | <=300 LOC |
| `tests/test_actor_identity_scene_reference.py` | Create | Scene intent, still generation, review/gate tests | <=300 LOC |
| `tests/test_character_consistency_mode.py` | Modify | Legacy compatibility and route guard regressions | <=120 LOC added |
| `tests/live/test_magnific_actor_identity_smoke.py` | Create optional | Paid-provider smoke behind explicit env flags | <=220 LOC |

---

### Task 1: Add ActorIdentity Persistence Contracts

**Files:**
- Create: `supabase/migrations/20260520_actor_identity_lora.sql`
- Modify: `app/features/characters/schemas.py`
- Modify: `app/features/characters/queries.py`
- Create: `tests/test_actor_identity_training.py`

- [ ] **Step 1: Write failing schema tests**

Add the first tests in `tests/test_actor_identity_training.py`.

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.features.characters.schemas import ActorTrainingSet


def _urls(count: int) -> list[str]:
    return [f"https://cdn.example.com/actor/{idx}.png" for idx in range(count)]


def test_actor_training_set_accepts_8_to_20_public_urls():
    assert len(ActorTrainingSet(images=_urls(8)).images) == 8
    assert len(ActorTrainingSet(images=_urls(20)).images) == 20


@pytest.mark.parametrize("count", [0, 3, 7, 21])
def test_actor_training_set_rejects_invalid_image_count(count):
    with pytest.raises(ValidationError):
        ActorTrainingSet(images=_urls(count))


def test_actor_training_set_rejects_non_public_urls():
    with pytest.raises(ValidationError):
        ActorTrainingSet(images=["/local/file.png"] * 8)
```

Run: `python3 -m pytest -q tests/test_actor_identity_training.py`

Expected: FAIL because `ActorTrainingSet` does not exist.

- [ ] **Step 2: Add Pydantic contracts**

In `app/features/characters/schemas.py`, keep `CharacterRecord` and `CharacterSnapshot` unchanged for legacy batches, then add the new contracts.

```python
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class ActorTrainingSet(BaseModel):
    images: list[str] = Field(min_length=8, max_length=20)
    consent_source: str = Field(default="", max_length=500)

    @field_validator("images")
    @classmethod
    def validate_public_urls(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) != len(value):
            raise ValueError("Training image URLs cannot be blank")
        if any(not item.startswith(("https://", "http://")) for item in cleaned):
            raise ValueError("Training images must be public URLs")
        return cleaned


class ActorIdentityRecord(BaseModel):
    id: str
    name: str
    is_active: bool
    provider: Literal["magnific"]
    provider_lora_id: Optional[str] = None
    provider_lora_name: Optional[str] = None
    provider_training_task_id: Optional[str] = None
    training_status: str
    training_phase: str
    training_progress_percent: int
    training_error: Optional[str] = None
    training_images: list[str]
    consent_source: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    training_started_at: Optional[datetime] = None
    training_completed_at: Optional[datetime] = None


class IdentityGateResult(BaseModel):
    status: Literal["pending", "passed", "failed", "manual_required"]
    reason: str
    score: Optional[float] = None
    gate_type: Literal["manual", "automated", "unavailable"] = "manual"
    checked_at: Optional[datetime] = None
    details: dict[str, Any] = Field(default_factory=dict)


class SceneReferenceImageRecord(BaseModel):
    id: str
    actor_identity_id: str
    post_id: str
    scene_key: str
    wardrobe_key: str
    provider: Literal["magnific"]
    provider_task_id: Optional[str] = None
    image_url: Optional[str] = None
    prompt: str
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    identity_gate_result: Optional[IdentityGateResult] = None
    status: str
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 3: Add migration**

Create `supabase/migrations/20260520_actor_identity_lora.sql`.

```sql
-- Migration: add Magnific-backed ActorIdentity and scene reference image state.
-- Date: 2026-05-20

CREATE TABLE IF NOT EXISTS public.actor_identities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  provider TEXT NOT NULL DEFAULT 'magnific',
  provider_lora_id TEXT,
  provider_lora_name TEXT,
  provider_training_task_id TEXT,
  training_status TEXT NOT NULL DEFAULT 'not_started',
  training_phase TEXT NOT NULL DEFAULT 'not_started',
  training_progress_percent INTEGER NOT NULL DEFAULT 0 CHECK (training_progress_percent >= 0 AND training_progress_percent <= 100),
  training_error TEXT,
  training_images JSONB NOT NULL DEFAULT '[]'::jsonb,
  consent_source TEXT,
  training_started_at TIMESTAMPTZ,
  training_completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS actor_identities_one_active
  ON public.actor_identities (is_active)
  WHERE is_active IS TRUE;

ALTER TABLE public.actor_identities ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.scene_reference_images (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_identity_id UUID NOT NULL REFERENCES public.actor_identities(id) ON DELETE RESTRICT,
  post_id UUID NOT NULL REFERENCES public.posts(id) ON DELETE CASCADE,
  scene_key TEXT NOT NULL,
  wardrobe_key TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'magnific',
  provider_task_id TEXT,
  image_url TEXT,
  prompt TEXT NOT NULL,
  provider_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  identity_gate_result JSONB,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scene_reference_images_post_status_idx
  ON public.scene_reference_images (post_id, status);

ALTER TABLE public.scene_reference_images ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS actor_identity_id UUID REFERENCES public.actor_identities(id),
  ADD COLUMN IF NOT EXISTS actor_identity_snapshot JSONB;

ALTER TABLE public.posts
  ADD COLUMN IF NOT EXISTS scene_reference_image_id UUID REFERENCES public.scene_reference_images(id),
  ADD COLUMN IF NOT EXISTS identity_gate_result JSONB;
```

- [ ] **Step 4: Add query helpers**

In `app/features/characters/queries.py`, add functions that stay adjacent to legacy character queries.

```python
def get_active_actor_identity() -> Optional[ActorIdentityRecord]:
    response = get_supabase().client.table("actor_identities").select("*").eq("is_active", True).maybe_single().execute()
    row = getattr(response, "data", None)
    return ActorIdentityRecord.model_validate(row) if row else None


def upsert_active_actor_identity(
    *,
    name: str,
    training_images: list[str],
    consent_source: str,
    correlation_id: str,
) -> ActorIdentityRecord:
    existing = get_active_actor_identity()
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "name": name.strip() or "Default Actor",
        "provider": "magnific",
        "training_images": training_images,
        "consent_source": consent_source,
        "training_status": "not_started",
        "training_phase": "not_started",
        "training_progress_percent": 0,
        "training_error": None,
        "is_active": True,
        "updated_at": now,
    }
    client = get_supabase().client
    if existing is None:
        payload["id"] = str(uuid4())
        payload["created_at"] = now
        client.table("actor_identities").insert(payload).execute()
        logger.info("actor_identity_created", correlation_id=correlation_id, actor_identity_id=payload["id"])
        return ActorIdentityRecord.model_validate(payload)
    client.table("actor_identities").update(payload).eq("id", existing.id).execute()
    logger.info("actor_identity_replaced", correlation_id=correlation_id, actor_identity_id=existing.id)
    return ActorIdentityRecord.model_validate({**payload, "id": existing.id, "created_at": existing.created_at})
```

- [ ] **Step 5: Run the focused tests**

Run: `python3 -m pytest -q tests/test_actor_identity_training.py tests/test_characters_feature.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/20260520_actor_identity_lora.sql app/features/characters/schemas.py app/features/characters/queries.py tests/test_actor_identity_training.py
git commit -m "feat: add actor identity persistence contracts"
```

### Task 2: Build The Magnific Adapter With Local Compatibility Guards

**Files:**
- Create: `app/adapters/magnific_client.py`
- Modify: `app/core/config.py`
- Create: `tests/test_magnific_actor_identity.py`

- [ ] **Step 1: Write failing adapter tests**

Create `tests/test_magnific_actor_identity.py`.

```python
from __future__ import annotations

import pytest

from app.adapters.magnific_client import (
    MagnificClient,
    MagnificCompatibilityError,
    build_mystic_character_payload,
    normalize_lora_training_status,
)


def test_build_magnific_training_payload_uses_required_fields():
    client = MagnificClient(api_key="test-key")
    payload = client.build_character_training_payload(
        name="ayra_actor",
        quality="high",
        gender="female",
        images=[f"https://cdn.example.com/{idx}.png" for idx in range(8)],
        description="Primary AYRA actor",
        webhook_url=None,
    )
    assert payload["name"] == "ayra_actor"
    assert payload["quality"] == "high"
    assert payload["gender"] == "female"
    assert len(payload["images"]) == 8
    assert "webhook_url" not in payload


def test_mystic_payload_uses_styling_characters():
    payload = build_mystic_character_payload(
        prompt="Portrait of the actor in a bright bathroom",
        lora_id="110",
        strength=100,
        aspect_ratio="social_story_9_16",
        resolution="2k",
    )
    assert payload["styling"]["characters"] == [{"id": "110", "strength": 100}]
    assert "structure_reference" not in payload
    assert "style_reference" not in payload
    assert "model" not in payload


@pytest.mark.parametrize("field", ["structure_reference", "style_reference"])
def test_mystic_payload_rejects_lora_incompatible_reference_fields(field):
    with pytest.raises(MagnificCompatibilityError):
        build_mystic_character_payload(
            prompt="Actor in car",
            lora_id="110",
            strength=100,
            extra_options={field: "base64"},
        )


@pytest.mark.parametrize("model", ["fluid", "flexible", "super_real", "editorial_portraits"])
def test_mystic_payload_rejects_lora_incompatible_models(model):
    with pytest.raises(MagnificCompatibilityError):
        build_mystic_character_payload(
            prompt="Actor in car",
            lora_id="110",
            strength=100,
            extra_options={"model": model},
        )


def test_normalize_training_status_maps_completed_to_ready():
    status = normalize_lora_training_status({"training": {"status": "completed"}, "id": 110, "name": "ayra_actor"})
    assert status.phase == "ready"
    assert status.progress_percent == 100
    assert status.provider_lora_id == "110"
```

Run: `python3 -m pytest -q tests/test_magnific_actor_identity.py`

Expected: FAIL because the adapter does not exist.

- [ ] **Step 2: Add config fields**

In `app/core/config.py`, add these fields near provider settings.

```python
magnific_api_key: str = Field("", validation_alias=AliasChoices("MAGNIFIC_API_KEY"), description="Magnific API key")
magnific_base_url: str = Field("https://api.magnific.com", validation_alias=AliasChoices("MAGNIFIC_BASE_URL"))
magnific_timeout_seconds: int = Field(60, ge=5, le=300, validation_alias=AliasChoices("MAGNIFIC_TIMEOUT_SECONDS"))
magnific_poll_seconds: int = Field(10, ge=2, le=60, validation_alias=AliasChoices("MAGNIFIC_POLL_SECONDS"))
magnific_webhook_secret: str = Field("", validation_alias=AliasChoices("MAGNIFIC_WEBHOOK_SECRET"))
actor_identity_gate_mode: Literal["manual", "disabled"] = Field("manual", validation_alias=AliasChoices("ACTOR_IDENTITY_GATE_MODE"))
```

- [ ] **Step 3: Implement adapter**

Create `app/adapters/magnific_client.py` with a thin httpx client and pure payload helpers.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

INCOMPATIBLE_MYSTIC_MODELS = {"fluid", "flexible", "super_real", "editorial_portraits"}


class MagnificCompatibilityError(ValueError):
    pass


@dataclass(frozen=True)
class MagnificTrainingStatus:
    raw_status: str
    phase: str
    progress_percent: int
    provider_lora_id: Optional[str]
    provider_lora_name: Optional[str]
    default_scale: Optional[float]


def normalize_lora_training_status(row: dict[str, Any]) -> MagnificTrainingStatus:
    training = row.get("training") if isinstance(row.get("training"), dict) else {}
    raw = str(training.get("status") or row.get("status") or "unknown").lower()
    phase_map = {
        "created": ("queued", 10),
        "queued": ("queued", 10),
        "in_progress": ("training", 50),
        "processing": ("training", 50),
        "training": ("training", 50),
        "completed": ("ready", 100),
        "failed": ("failed", 0),
        "error": ("failed", 0),
    }
    phase, percent = phase_map.get(raw, ("training", 35))
    return MagnificTrainingStatus(
        raw_status=raw,
        phase=phase,
        progress_percent=percent,
        provider_lora_id=str(row.get("id")) if row.get("id") is not None else None,
        provider_lora_name=str(row.get("name")) if row.get("name") else None,
        default_scale=training.get("defaultScale"),
    )


def build_mystic_character_payload(
    *,
    prompt: str,
    lora_id: str,
    strength: int,
    aspect_ratio: str = "social_story_9_16",
    resolution: str = "2k",
    webhook_url: Optional[str] = None,
    extra_options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    options = dict(extra_options or {})
    if options.get("structure_reference") or options.get("style_reference"):
        raise MagnificCompatibilityError("Mystic LoRA payload cannot include structure_reference or style_reference")
    model = str(options.get("model") or "").strip()
    if model in INCOMPATIBLE_MYSTIC_MODELS:
        raise MagnificCompatibilityError(f"Mystic model {model} silently ignores LoRAs")
    payload = {
        "prompt": prompt,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "styling": {"characters": [{"id": str(lora_id), "strength": int(strength)}]},
    }
    if webhook_url:
        payload["webhook_url"] = webhook_url
    payload.update(options)
    return payload
```

Then add `MagnificClient.submit_character_training(...)`, `list_loras(...)`, `create_mystic_scene_reference(...)`, and `get_mystic_task(...)` methods using the `x-magnific-api-key` header.

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest -q tests/test_magnific_actor_identity.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py app/adapters/magnific_client.py tests/test_magnific_actor_identity.py
git commit -m "feat: add Magnific actor identity adapter"
```

### Task 3: Add Actor Settings Training Flow

**Files:**
- Modify: `app/features/characters/handlers.py`
- Modify: `app/features/characters/queries.py`
- Modify: `app/features/characters/actor_identity.py`
- Modify: `templates/settings/character.html`
- Modify: `tests/test_actor_identity_training.py`

- [ ] **Step 1: Add training flow tests**

Extend `tests/test_actor_identity_training.py`.

```python
def test_ready_actor_identity_requires_completed_training():
    from app.features.characters.actor_identity import actor_identity_is_ready
    from app.features.characters.schemas import ActorIdentityRecord

    base = {
        "id": "actor-1",
        "name": "AYRA",
        "is_active": True,
        "provider": "magnific",
        "provider_lora_id": "110",
        "provider_lora_name": "ayra",
        "provider_training_task_id": "train-1",
        "training_status": "completed",
        "training_phase": "ready",
        "training_progress_percent": 100,
        "training_error": None,
        "training_images": [f"https://cdn.example.com/{idx}.png" for idx in range(8)],
        "created_at": "2026-05-20T00:00:00Z",
        "updated_at": "2026-05-20T00:00:00Z",
    }
    assert actor_identity_is_ready(ActorIdentityRecord.model_validate(base)) is True
    base["provider_lora_id"] = None
    assert actor_identity_is_ready(ActorIdentityRecord.model_validate(base)) is False
```

Add a handler test that posts 8 images and proves training is submitted with public R2 URLs, not raw bytes.

Run: `python3 -m pytest -q tests/test_actor_identity_training.py`

Expected: FAIL until handlers and helper exist.

- [ ] **Step 2: Add pure readiness helpers**

Create `app/features/characters/actor_identity.py`.

```python
from __future__ import annotations

from app.features.characters.schemas import ActorIdentityRecord, IdentityGateResult


def actor_identity_is_ready(identity: ActorIdentityRecord | None) -> bool:
    if identity is None:
        return False
    return (
        identity.is_active is True
        and identity.training_phase == "ready"
        and identity.training_progress_percent == 100
        and bool(identity.provider_lora_id or identity.provider_lora_name)
    )


def pending_manual_gate(reason: str) -> IdentityGateResult:
    return IdentityGateResult(status="manual_required", reason=reason, gate_type="manual", details={})


def passed_manual_gate(reason: str = "Operator approved identity match") -> IdentityGateResult:
    return IdentityGateResult(status="passed", reason=reason, gate_type="manual", details={})
```

- [ ] **Step 3: Add settings endpoints**

In `app/features/characters/handlers.py`, keep the old `/settings/character` three-image upload endpoint for legacy snapshots, and add ActorIdentity endpoints:

- `POST /settings/character/actor` accepts `name`, `gender`, `quality`, `consent_source`, and `training_images: list[UploadFile]`.
- Upload each image through `StorageClient.upload_image(...)`.
- Persist the active ActorIdentity with the public URLs.
- Submit Magnific training only after persistence succeeds.
- Update provider task/status on the row.
- Redirect back to `/settings/character`.
- `POST /settings/character/actor/poll` polls `GET /v1/ai/loras`, normalizes status, updates the active identity, and returns the settings page or a small status payload.

Use this submission shape:

```python
training = ActorTrainingSet(images=uploaded_urls, consent_source=consent_source)
identity = character_queries.upsert_active_actor_identity(
    name=name,
    training_images=training.images,
    consent_source=training.consent_source,
    correlation_id=correlation_id,
)
task = get_magnific_client().submit_character_training(
    name=_provider_safe_name(identity.name, identity.id),
    quality=quality,
    gender=gender,
    images=training.images,
    description=f"ActorIdentity {identity.id}",
    webhook_url=None,
    correlation_id=correlation_id,
)
character_queries.mark_actor_training_submitted(
    actor_identity_id=identity.id,
    provider_training_task_id=task["task_id"],
    provider_lora_name=_provider_safe_name(identity.name, identity.id),
    raw_status=task.get("status", "IN_PROGRESS"),
    correlation_id=correlation_id,
)
```

- [ ] **Step 4: Update settings UI**

Modify `templates/settings/character.html` so the top section is ActorIdentity training and the old three-image section is labeled legacy.

Required visible states:
- Active ActorIdentity name.
- Training image count.
- Training phase label.
- Progress percentage.
- Ready/blocked state for `character_consistency`.
- Confirmed replacement copy for retraining/replacing the active identity.
- Legacy three-image upload still available and explicitly labeled for existing snapshot flow.

Use standard form controls, no new JS dependency. Polling can be HTMX-based:

```html
<section
  class="mt-6 rounded-lg border border-gray-200 p-4"
  hx-post="/settings/character/actor/poll"
  hx-trigger="load delay:2s, every 10s"
  hx-swap="outerHTML"
>
  <!-- training status block rendered from actor_identity -->
</section>
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest -q tests/test_actor_identity_training.py tests/test_characters_feature.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/features/characters/handlers.py app/features/characters/queries.py app/features/characters/actor_identity.py templates/settings/character.html tests/test_actor_identity_training.py
git commit -m "feat: add actor identity training settings flow"
```

### Task 4: Gate New Character-Consistency Batches On Ready ActorIdentity

**Files:**
- Modify: `app/features/batches/queries.py`
- Modify: `app/features/batches/schemas.py`
- Modify: `tests/test_character_consistency_mode.py`

- [ ] **Step 1: Write failing readiness tests**

Extend `tests/test_character_consistency_mode.py`.

```python
def test_character_consistency_requires_ready_actor_identity_for_new_batches(monkeypatch):
    from app.core.errors import ValidationError
    from app.features.batches import queries as batch_queries

    monkeypatch.setattr(batch_queries, "get_active_actor_identity", lambda: None)
    monkeypatch.setattr(batch_queries, "get_active_character", lambda: None)

    with pytest.raises(ValidationError) as exc:
        batch_queries.create_batch(
            brand="Test",
            post_type_counts={"value": 1, "lifestyle": 0, "product": 0},
            creation_mode="character_consistency",
        )
    assert "actoridentity training" in exc.value.message.lower()


def test_existing_legacy_character_snapshot_batches_remain_valid():
    from app.features.characters.actor_identity import resolve_character_consistency_source

    source = resolve_character_consistency_source(
        batch={
            "id": "batch-legacy",
            "creation_mode": "character_consistency",
            "character_snapshot": {"character_id": "char-1", "front_image_url": "https://cdn/front.png"},
        }
    )
    assert source["source"] == "legacy_character_snapshot"
```

Run: `python3 -m pytest -q tests/test_character_consistency_mode.py`

Expected: FAIL until new readiness/source helpers are added.

- [ ] **Step 2: Implement source resolution**

Add this helper in `app/features/characters/actor_identity.py`.

```python
def resolve_character_consistency_source(*, batch: dict, active_identity: ActorIdentityRecord | None = None) -> dict:
    if batch.get("actor_identity_id") or batch.get("actor_identity_snapshot"):
        return {"source": "actor_identity", "actor_identity_id": batch.get("actor_identity_id")}
    if batch.get("character_snapshot"):
        return {"source": "legacy_character_snapshot", "character_snapshot": batch.get("character_snapshot")}
    if actor_identity_is_ready(active_identity):
        return {"source": "actor_identity", "actor_identity_id": active_identity.id}
    return {"source": "blocked", "reason": "ActorIdentity training is not complete"}
```

- [ ] **Step 3: Update batch creation**

In `app/features/batches/queries.py`, change only the new-batch branch. Do not break batches that already have `character_snapshot`.

```python
if creation_mode == "character_consistency":
    actor_identity = get_active_actor_identity()
    if not actor_identity_is_ready(actor_identity):
        raise ValidationError(
            "Cannot create a Character Consistency batch: ActorIdentity training is not complete. "
            "Upload 8-20 training images and wait for training at /settings/character.",
            {"creation_mode": "character_consistency"},
        )
    batch_data["actor_identity_id"] = actor_identity.id
    batch_data["actor_identity_snapshot"] = {
        "actor_identity_id": actor_identity.id,
        "name": actor_identity.name,
        "provider": actor_identity.provider,
        "provider_lora_id": actor_identity.provider_lora_id,
        "provider_lora_name": actor_identity.provider_lora_name,
        "training_completed_at": actor_identity.training_completed_at.isoformat() if actor_identity.training_completed_at else None,
    }
    batch_data["character_snapshot"] = None
    batch_data["scene_plan"] = None
```

Update `BatchResponse` and `BatchDetailResponse` with:

```python
actor_identity_id: Optional[str] = None
actor_identity_snapshot: Optional[Dict[str, Any]] = None
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest -q tests/test_character_consistency_mode.py tests/test_batches_manual_mode.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/features/batches/queries.py app/features/batches/schemas.py app/features/characters/actor_identity.py tests/test_character_consistency_mode.py
git commit -m "feat: gate character consistency on trained actor identity"
```

### Task 5: Add Controlled Scene/Wardrobe Intent Mapping And Still Generation

**Files:**
- Create: `app/features/characters/scene_reference.py`
- Modify: `app/features/characters/queries.py`
- Modify: `app/features/characters/handlers.py`
- Modify: `templates/batches/detail/_post_card.html`
- Create: `tests/test_actor_identity_scene_reference.py`

- [ ] **Step 1: Write failing intent and generation tests**

Create `tests/test_actor_identity_scene_reference.py`.

```python
from __future__ import annotations

from app.features.characters.scene_reference import (
    SCENE_CATALOG,
    WARDROBE_SET,
    build_scene_reference_prompt,
    map_script_to_scene_intent,
)


def test_script_intent_maps_only_to_catalog_values():
    result = map_script_to_scene_intent(
        script="Im Badezimmer zeigt sie, wie kleine Anpassungen am Morgen Sicherheit geben.",
        post_type="value",
        target_length_tier=8,
        seed_data={},
    )
    assert result.scene_key in SCENE_CATALOG
    assert result.wardrobe_key in WARDROBE_SET
    assert result.reason_code == "bathroom_terms"


def test_ambiguous_script_uses_conservative_default():
    result = map_script_to_scene_intent(
        script="Ein kurzer Tipp fuer heute.",
        post_type="value",
        target_length_tier=8,
        seed_data={},
    )
    assert result.scene_key == "neutral_home"
    assert result.wardrobe_key == "everyday_sweater"


def test_scene_reference_prompt_does_not_include_freeform_script_text():
    prompt = build_scene_reference_prompt(
        actor_name="AYRA",
        scene_key="bathroom_adaptation",
        wardrobe_key="everyday_sweater",
        post_type="value",
    )
    assert "Badezimmer" not in prompt
    assert "bright accessible bathroom" in prompt
```

Run: `python3 -m pytest -q tests/test_actor_identity_scene_reference.py`

Expected: FAIL until the module exists.

- [ ] **Step 2: Implement deterministic catalog and mapper**

Create `app/features/characters/scene_reference.py`.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCENE_CATALOG = {
    "bathroom_adaptation": "bright accessible bathroom with matte white tile, grab rail, folded towel, and soft daylight",
    "car_transfer": "parked compact car beside a calm residential street, open passenger door, soft overcast daylight",
    "neutral_home": "quiet modern living room with warm neutral wall, small side table, one green plant, and soft window light",
    "home_product_demo": "tidy product-friendly home interior with neutral wall, clear table surface, and bright natural light",
    "office_explainer": "compact home office with pale wall, laptop closed on desk, neat papers, and soft side light",
}

WARDROBE_SET = {
    "everyday_sweater": "cream crewneck sweater, no logos, no jewelry, natural makeup",
    "casual_blazer": "soft beige blazer over white top, no logos, no jewelry",
    "home_cardigan": "light grey cardigan over plain white top, no logos, no jewelry",
}


@dataclass(frozen=True)
class ScriptIntent:
    scene_key: str
    wardrobe_key: str
    reason_code: str


def map_script_to_scene_intent(*, script: str, post_type: str, target_length_tier: int, seed_data: dict[str, Any]) -> ScriptIntent:
    text = f"{script} {seed_data.get('topic_title', '')} {seed_data.get('topic', '')}".lower()
    if any(token in text for token in ("bad", "dusche", "toilette", "badezimmer", "bathroom", "sicherheit")):
        return ScriptIntent("bathroom_adaptation", "everyday_sweater", "bathroom_terms")
    if any(token in text for token in ("auto", "car", "mobilitaet", "mobility", "transfer", "reise")):
        return ScriptIntent("car_transfer", "casual_blazer", "mobility_terms")
    if post_type == "product":
        return ScriptIntent("home_product_demo", "home_cardigan", "product_default")
    if any(token in text for token in ("tipp", "erklaert", "advice", "explainer")):
        return ScriptIntent("office_explainer", "casual_blazer", "explainer_terms")
    return ScriptIntent("neutral_home", "everyday_sweater", "default")


def build_scene_reference_prompt(*, actor_name: str, scene_key: str, wardrobe_key: str, post_type: str) -> str:
    scene = SCENE_CATALOG[scene_key]
    wardrobe = WARDROBE_SET[wardrobe_key]
    return (
        f"Photorealistic vertical UGC still of {actor_name}, one recognizable adult person, "
        f"wearing {wardrobe}, seated naturally in a wheelchair, in {scene}. "
        f"Medium close-up, direct-to-camera friendly expression, natural skin texture, no text, no logo."
    )
```

- [ ] **Step 3: Add scene reference persistence and generation endpoint**

In `app/features/characters/queries.py`, add:
- `create_scene_reference_candidate(...)`
- `mark_scene_reference_generated(...)`
- `record_scene_reference_gate(...)`
- `get_approved_scene_reference_for_post(...)`

In `app/features/characters/handlers.py`, add:
- `POST /settings/character/posts/{post_id}/scene-reference/generate`
- `POST /settings/character/scene-reference/{reference_id}/approve`
- `POST /settings/character/scene-reference/{reference_id}/reject`

The generation endpoint must:
1. Load post and batch.
2. Resolve ActorIdentity source.
3. Run `map_script_to_scene_intent(...)`.
4. Build a Mystic prompt.
5. Submit up to three Magnific Mystic tasks with `styling.characters`.
6. Persist task ids and provider metadata.
7. Stop with `manual_required` gate state until operator approves a generated candidate.

- [ ] **Step 4: Update post card UI**

Modify `templates/batches/detail/_post_card.html` to show:
- selected `scene_key` / `wardrobe_key` when present,
- SceneReferenceImage status,
- generated still preview when present,
- approve/reject/regenerate buttons before video generation.

Do not add freeform scene prompt inputs in the MVP.

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest -q tests/test_actor_identity_scene_reference.py tests/test_character_consistency_mode.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/features/characters/scene_reference.py app/features/characters/queries.py app/features/characters/handlers.py templates/batches/detail/_post_card.html tests/test_actor_identity_scene_reference.py
git commit -m "feat: add actor scene reference workflow"
```

### Task 6: Require Approved Scene References Before ActorIdentity Video Submit

**Files:**
- Modify: `app/features/videos/handlers.py`
- Modify: `app/features/characters/actor_identity.py`
- Modify: `tests/test_character_consistency_mode.py`
- Modify: `tests/test_video_duration_routing.py`
- Modify: `tests/test_veo_prompt_contract.py`

- [ ] **Step 1: Write failing route guard tests**

Add tests that prove ActorIdentity-backed batches cannot submit without an approved scene reference.

```python
def test_actor_identity_batch_blocks_video_without_approved_scene_reference(monkeypatch):
    from app.core.errors import FlowForgeException
    from app.features.characters.actor_identity import ensure_video_scene_reference_ready

    batch = {"id": "batch-1", "creation_mode": "character_consistency", "actor_identity_id": "actor-1"}
    post = {"id": "post-1", "batch_id": "batch-1", "scene_reference_image_id": None}
    with pytest.raises(FlowForgeException) as exc:
        ensure_video_scene_reference_ready(batch=batch, post=post, scene_reference=None, route="short")
    assert "approved SceneReferenceImage" in exc.value.message
```

Add one regression that proves a legacy snapshot batch still reaches the old `_load_character_snapshot_assets(...)` path.

Run: `python3 -m pytest -q tests/test_character_consistency_mode.py`

Expected: FAIL until the video gate exists.

- [ ] **Step 2: Implement video reference readiness helper**

In `app/features/characters/actor_identity.py`, add:

```python
def ensure_video_scene_reference_ready(*, batch: dict, post: dict, scene_reference: dict | None, route: str | None) -> dict:
    if batch.get("character_snapshot") and not batch.get("actor_identity_id"):
        return {"source": "legacy_character_snapshot", "compatible": True}
    if str(batch.get("creation_mode") or "") != "character_consistency":
        return {"source": "not_character_consistency", "compatible": True}
    if not batch.get("actor_identity_id"):
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="Character Consistency batch is missing ActorIdentity metadata.",
            details={"batch_id": batch.get("id")},
            status_code=422,
        )
    if not scene_reference or scene_reference.get("status") != "approved":
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="ActorIdentity video generation requires an approved SceneReferenceImage before submit.",
            details={"post_id": post.get("id"), "batch_id": batch.get("id")},
            status_code=422,
        )
    gate = scene_reference.get("identity_gate_result") or {}
    if gate.get("status") != "passed":
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="SceneReferenceImage identity gate has not passed.",
            details={"post_id": post.get("id"), "gate": gate},
            status_code=422,
        )
    return {"source": "actor_identity_scene_reference", "compatible": True, "scene_reference": scene_reference}
```

- [ ] **Step 3: Thread approved stills through video submission**

In `app/features/videos/handlers.py`:
- Before `_submit_video_request(...)`, load `get_approved_scene_reference_for_post(post_id)`.
- Call `ensure_video_scene_reference_ready(...)`.
- Pass approved scene reference URLs as the reference image bundle where the current route supports reference images.
- If the current route cannot consume approved scene references, return a structured 422 for ActorIdentity-backed no-drift mode.
- Keep legacy `character_snapshot` behavior unchanged.

Metadata must include:

```python
submission_metadata["actor_identity_source"] = "actor_identity_scene_reference"
submission_metadata["actor_identity_id"] = batch.get("actor_identity_id")
submission_metadata["scene_reference_image_id"] = scene_reference["id"]
submission_metadata["scene_key"] = scene_reference["scene_key"]
submission_metadata["wardrobe_key"] = scene_reference["wardrobe_key"]
submission_metadata["still_identity_gate_result"] = scene_reference.get("identity_gate_result")
```

- [ ] **Step 4: Preserve duration routing**

Run the current route tests before and after edits. If they conflict with repo-specific 32s route instructions, stop and create `agents/testscripts/failure_report.md` instead of changing the route as part of this ActorIdentity plan.

Run:

```bash
python3 -m pytest -q tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py tests/test_character_consistency_mode.py
```

Expected: PASS with no route behavior changes.

- [ ] **Step 5: Commit**

```bash
git add app/features/videos/handlers.py app/features/characters/actor_identity.py tests/test_character_consistency_mode.py tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py
git commit -m "feat: require approved actor scene references for video"
```

### Task 7: Record Post-Video Identity Gate Results

**Files:**
- Modify: `workers/video_poller.py`
- Modify: `app/features/characters/queries.py`
- Modify: `app/features/qa/handlers.py`
- Modify: `tests/test_actor_identity_scene_reference.py`

- [ ] **Step 1: Write failing video gate tests**

Add a test that a completed ActorIdentity video without a passed video gate is not marked publish-ready.

```python
def test_actor_identity_video_gate_defaults_to_manual_required():
    from app.features.characters.actor_identity import build_video_identity_gate_result

    result = build_video_identity_gate_result(video_url="https://cdn.example.com/video.mp4", automated_available=False)
    assert result.status == "manual_required"
    assert result.gate_type == "manual"
    assert "manual review" in result.reason.lower()
```

Run: `python3 -m pytest -q tests/test_actor_identity_scene_reference.py`

Expected: FAIL until the helper exists.

- [ ] **Step 2: Add gate result helper**

In `app/features/characters/actor_identity.py`:

```python
def build_video_identity_gate_result(*, video_url: str | None, automated_available: bool) -> IdentityGateResult:
    if not video_url:
        return IdentityGateResult(status="failed", reason="Video URL missing; cannot verify identity", gate_type="unavailable")
    if not automated_available:
        return IdentityGateResult(
            status="manual_required",
            reason="Video identity requires manual review because automated face gate is not configured",
            gate_type="manual",
        )
    return IdentityGateResult(status="pending", reason="Automated video identity gate queued", gate_type="automated")
```

- [ ] **Step 3: Update poller completion path**

In `workers/video_poller.py`, when a video completes for a post with `video_metadata.actor_identity_source == "actor_identity_scene_reference"`:
- Store `identity_gate_result` on `posts`.
- Store the same result inside `video_metadata.video_identity_gate_result`.
- Do not mark the post publish-approved if the gate is not `passed`.

This first block should set `manual_required`; the operator review endpoint can mark it `passed`.

- [ ] **Step 4: Block QA/publish pass when gate fails**

In `app/features/qa/handlers.py`, when approving an ActorIdentity-backed post/video, require `posts.identity_gate_result.status == "passed"` or raise a validation error with the visible gate reason.

- [ ] **Step 5: Run tests**

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_reference.py tests/test_video_poller_batch_transition.py tests/test_publish_caption_guard.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add workers/video_poller.py app/features/characters/queries.py app/features/characters/actor_identity.py app/features/qa/handlers.py tests/test_actor_identity_scene_reference.py
git commit -m "feat: record actor identity video gates"
```

### Task 8: Add Optional Paid Magnific Smoke Test And Final Regression Suite

**Files:**
- Create: `tests/live/test_magnific_actor_identity_smoke.py`
- Modify: `.env.example` if it is tracked in this branch
- Modify: `README.md` or `docs/character_consistency.md`

- [ ] **Step 1: Add gated live smoke**

Create `tests/live/test_magnific_actor_identity_smoke.py`.

```python
from __future__ import annotations

import os

import pytest

from app.adapters.magnific_client import get_magnific_client


pytestmark = pytest.mark.skipif(
    os.getenv("AIUGC_LIVE_MAGNIFIC_SMOKE") != "1",
    reason="Paid Magnific smoke requires AIUGC_LIVE_MAGNIFIC_SMOKE=1",
)


def test_live_magnific_lists_loras():
    assert os.getenv("MAGNIFIC_API_KEY"), "MAGNIFIC_API_KEY is required"
    response = get_magnific_client().list_loras(correlation_id="live-magnific-list-loras")
    assert "data" in response
```

Do not submit paid training or Mystic generation unless separate explicit flags are present:
- `AIUGC_LIVE_MAGNIFIC_TRAIN=1`
- `AIUGC_LIVE_MAGNIFIC_MYSTIC=1`

- [ ] **Step 2: Document run commands**

Update docs with these commands:

```bash
python3 -m pytest -q tests/test_actor_identity_training.py tests/test_magnific_actor_identity.py tests/test_actor_identity_scene_reference.py
python3 -m pytest -q tests/test_characters_feature.py tests/test_character_consistency_mode.py tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py
AIUGC_LIVE_MAGNIFIC_SMOKE=1 python3 -m pytest -q tests/live/test_magnific_actor_identity_smoke.py
```

- [ ] **Step 3: Run final local regression**

Run:

```bash
python3 -m pytest -q \
  tests/test_actor_identity_training.py \
  tests/test_magnific_actor_identity.py \
  tests/test_actor_identity_scene_reference.py \
  tests/test_characters_feature.py \
  tests/test_character_consistency_mode.py \
  tests/test_video_duration_routing.py \
  tests/test_veo_prompt_contract.py
```

Expected: PASS.

- [ ] **Step 4: Manual browser testscript**

Start the app:

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Manual observations:
- `/settings/character` shows ActorIdentity upload first and legacy three-image upload separately.
- Seven-image ActorTrainingSet is rejected before provider submission.
- Eight-image ActorTrainingSet uploads images and shows training in progress.
- A ready mocked ActorIdentity unlocks `character_consistency` batch creation.
- A new ActorIdentity-backed post cannot submit video until one scene reference is approved.
- Legacy CharacterSnapshot batch still uses the old snapshot metadata path.

- [ ] **Step 5: Commit**

```bash
git add tests/live/test_magnific_actor_identity_smoke.py README.md docs/character_consistency.md .env.example
git commit -m "docs: add actor identity verification runbook"
```

---

## Pass/Fail Criteria

**Pass:**
- `ActorTrainingSet` requires 8-20 public image URLs.
- Settings page uploads training images to R2 and submits a Magnific character LoRA training task.
- Training progress stores both raw provider status and normalized phase/percentage.
- New `character_consistency` batches are blocked until an active ActorIdentity is ready.
- Existing `character_snapshot` batches continue through the legacy route.
- Scene and wardrobe choices come only from `SceneCatalog` and `WardrobeSet`.
- Mystic generation payload uses `styling.characters` with the persisted LoRA id.
- LoRA-incompatible Mystic options are rejected locally before API submission.
- Scene stills are persisted with prompt, provider task id, image URL, metadata, and gate result.
- Video submission blocks ActorIdentity-backed no-drift mode without an approved scene still.
- Video identity gate result is visible and blocks publish/approval unless passed.
- No route silently claims no-drift when it did not consume an approved scene reference.

**Fail:**
- The app lets operators train from only three images.
- The app creates a new ActorIdentity-backed batch before training completion.
- Freeform script text becomes a raw provider scene or wardrobe prompt.
- Magnific can silently ignore a LoRA because the adapter allowed incompatible fields.
- A post proceeds to video without a passed still gate and review checkpoint.
- A video proceeds to publish with failed, pending, or missing video identity gate state.
- 8s/16s/32s routing behavior changes as collateral damage.

## Self-Review Checklist

- Spec coverage: ActorIdentity, ActiveActorIdentity, ActorTrainingSet, TrainingReadinessGate, TrainingProgressPolling, TrainingProgressDisplay, AutoEnableOnTrainingComplete, ActorSettingsSurface, ActorReplacementAction, SceneCatalog, WardrobeSet, ScriptIntentMap, SceneReferenceImage, SceneReviewCheckpoint, IdentityGate, IdentityGateResult, and LegacyBatchCompatibility are all mapped to tasks.
- Red-flag scan: clean; every task names concrete files, checks, commands, and expected behavior.
- Type consistency: `ActorTrainingSet`, `ActorIdentityRecord`, `SceneReferenceImageRecord`, `IdentityGateResult`, `ScriptIntent`, `actor_identity_is_ready`, `resolve_character_consistency_source`, and `ensure_video_scene_reference_ready` are defined before use.
- Locality envelope: feature code remains in the existing character/batch/video slices, adapter code is isolated at the Magnific boundary, and the MVP adds zero dependencies.

## Sources

- Local spec: `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/docs/superpowers/specs/2026-05-20-actor-identity-lora-character-consistency-design.md`
- Local context glossary: `/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/CONTEXT.md`
- Magnific Character LoRA docs: https://docs.magnific.com/api-reference/mystic/post-loras-characters
- Magnific Mystic generation docs: https://docs.magnific.com/api-reference/mystic/post-mystic
- Magnific LoRA listing docs: https://docs.magnific.com/api-reference/mystic/get-loras
- Magnific Mystic task status docs: https://docs.magnific.com/api-reference/mystic/get-mystic-task
- Magnific authentication docs: https://docs.magnific.com/authentication
- Magnific webhooks docs: https://docs.magnific.com/webhooks
- Magnific rate limiting docs: https://docs.magnific.com/ratelimits
