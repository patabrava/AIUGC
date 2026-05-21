# Actor Identity Three Reference Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change ActorIdentity-backed Character Consistency so each approved script produces exactly three Mystic-generated reference images, all three must pass manual review, and the existing video submission rules receive the approved three-image set.

**Architecture:** Keep the current ActorIdentity, Magnific, Character Consistency, and VEO/Vertex routing surfaces. Add a light set contract on top of existing `scene_reference_images` rows using `provider_metadata.reference_set_id`, `provider_metadata.angle_key`, and `provider_metadata.reference_set_status`, avoiding a new table and avoiding route rewrites. Replace single-reference lookup/loading with set-aware helpers that require exactly three approved images and then hand those images into the already-implemented Character Consistency submission path.

**Tech Stack:** FastAPI, Jinja2, HTMX, Supabase/PostgREST, Pydantic, httpx, pytest, existing Magnific Mystic adapter, existing VEO/Vertex submission clients.

**Scope Budget:** `{files: 8-10, LOC/file: <=350 target and <=600 hard, deps: 0}`

---

## Current Reality

The app already has:

- Actor LoRA training and settings surfaces in `app/features/characters/handlers.py` and `templates/settings/actor.html`.
- Magnific Mystic scene reference generation in `app/adapters/magnific_client.py`.
- `scene_reference_images` rows with `provider_metadata`, `status`, and `identity_gate_result`.
- A batch creation gate requiring a ready ActorIdentity for new `character_consistency` batches.
- Video submission guards that currently call `get_approved_scene_reference_for_post(post_id)` and pass one approved image into `_load_scene_reference_asset(...)`.
- Existing Character Consistency duration/provider rules:
  - VEO route rejects ActorIdentity scene references on a 4s base request.
  - Vertex route only accepts ActorIdentity scene references when the base request is 8 seconds.
  - Legacy snapshot behavior stays unchanged.

This plan must not change duration routing. It only changes the reference payload from one approved still to exactly three approved stills where the current route can consume references.

## File Map

| File | Action | Responsibility | Budget |
| --- | --- | --- | --- |
| `app/features/characters/schemas.py` | Modify | Add angle and set contracts for scene reference rows | <=130 LOC added |
| `app/features/characters/scene_reference.py` | Modify | Define three required angles and angle-specific Mystic prompt text | <=180 LOC added |
| `app/features/characters/queries.py` | Modify | Create/list/regenerate/approve three-reference sets on existing table | <=260 LOC added |
| `app/features/characters/actor_identity.py` | Modify | Require approved three-reference set before video submit | <=120 LOC added |
| `app/features/characters/handlers.py` | Modify | Generate three-image set, regenerate one, regenerate all, approve one | <=260 LOC added |
| `app/features/videos/handlers.py` | Modify | Load three approved references and pass all images to current submit path | <=180 LOC added |
| `templates/batches/detail/_post_card.html` | Modify | Show the three angles, per-image approval/regeneration, regenerate set | <=180 LOC added |
| `tests/test_actor_identity_scene_reference.py` | Modify | Unit coverage for set contract, angles, approval rules | <=220 LOC added |
| `tests/test_character_consistency_mode.py` | Modify | Video guard and reference-image count regressions | <=180 LOC added |
| `docs/character_consistency.md` | Modify | Document the three-reference contract and routing boundary | <=80 LOC added |

## Task 1: Add Three-Reference Set Contracts

**Files:**
- Modify: `app/features/characters/schemas.py`
- Modify: `app/features/characters/scene_reference.py`
- Test: `tests/test_actor_identity_scene_reference.py`

- [ ] **Step 1: Write failing tests for fixed angles and set readiness**

Add these tests to `tests/test_actor_identity_scene_reference.py`.

```python
def test_required_scene_reference_angles_are_stable():
    from app.features.characters.scene_reference import REQUIRED_SCENE_REFERENCE_ANGLES

    assert [angle.key for angle in REQUIRED_SCENE_REFERENCE_ANGLES] == [
        "front_mid",
        "left_three_quarter",
        "right_profile",
    ]
    assert len({angle.seed_offset for angle in REQUIRED_SCENE_REFERENCE_ANGLES}) == 3


def test_scene_reference_set_summary_requires_three_approved_images():
    from app.features.characters.schemas import SceneReferenceSetSummary

    summary = SceneReferenceSetSummary.from_rows(
        post_id="post-1",
        reference_set_id="set-1",
        rows=[
            {
                "id": "ref-1",
                "status": "approved",
                "image_url": "https://cdn.example.com/front.png",
                "provider_metadata": {"angle_key": "front_mid"},
                "identity_gate_result": {"status": "passed", "reason": "ok", "gate_type": "manual"},
            },
            {
                "id": "ref-2",
                "status": "approved",
                "image_url": "https://cdn.example.com/left.png",
                "provider_metadata": {"angle_key": "left_three_quarter"},
                "identity_gate_result": {"status": "passed", "reason": "ok", "gate_type": "manual"},
            },
        ],
    )

    assert summary.is_ready is False
    assert summary.missing_angle_keys == ["right_profile"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_reference.py::test_required_scene_reference_angles_are_stable tests/test_actor_identity_scene_reference.py::test_scene_reference_set_summary_requires_three_approved_images
```

Expected: FAIL because `REQUIRED_SCENE_REFERENCE_ANGLES` and `SceneReferenceSetSummary` do not exist.

- [ ] **Step 3: Add the angle contract**

In `app/features/characters/scene_reference.py`, add this code after `ScriptIntent`.

```python
@dataclass(frozen=True)
class SceneReferenceAngle:
    key: str
    label: str
    instruction: str
    seed_offset: int


REQUIRED_SCENE_REFERENCE_ANGLES = (
    SceneReferenceAngle(
        key="front_mid",
        label="Front",
        instruction="front-facing medium close-up, shoulders square to camera, direct eye contact",
        seed_offset=101,
    ),
    SceneReferenceAngle(
        key="left_three_quarter",
        label="Left three-quarter",
        instruction="left three-quarter angle, body turned slightly away, face still clearly recognizable",
        seed_offset=202,
    ),
    SceneReferenceAngle(
        key="right_profile",
        label="Right profile",
        instruction="right-side profile angle, same person and same scene, face contour clearly visible",
        seed_offset=303,
    ),
)


def get_scene_reference_angle(angle_key: str) -> SceneReferenceAngle:
    for angle in REQUIRED_SCENE_REFERENCE_ANGLES:
        if angle.key == angle_key:
            return angle
    raise KeyError(f"Unknown scene reference angle: {angle_key}")


def build_scene_reference_prompt_for_angle(
    *,
    actor_name: str,
    scene_key: str,
    wardrobe_key: str,
    post_type: str,
    angle_key: str,
    provider_lora_name: str | None = None,
) -> str:
    base_prompt = build_scene_reference_prompt(
        actor_name=actor_name,
        scene_key=scene_key,
        wardrobe_key=wardrobe_key,
        post_type=post_type,
        provider_lora_name=provider_lora_name,
    )
    angle = get_scene_reference_angle(angle_key)
    return (
        f"{base_prompt} Keep the exact same background and wardrobe. "
        f"Camera angle requirement: {angle.instruction}."
    )
```

- [ ] **Step 4: Add the set summary contract**

In `app/features/characters/schemas.py`, add this code after `SceneReferenceImageRecord`.

```python
REQUIRED_SCENE_REFERENCE_ANGLE_KEYS = ("front_mid", "left_three_quarter", "right_profile")


class SceneReferenceSetSummary(BaseModel):
    post_id: str
    reference_set_id: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    approved_rows: list[dict[str, Any]] = Field(default_factory=list)
    missing_angle_keys: list[str] = Field(default_factory=list)
    is_ready: bool = False

    @classmethod
    def from_rows(
        cls,
        *,
        post_id: str,
        reference_set_id: str,
        rows: list[dict[str, Any]],
    ) -> "SceneReferenceSetSummary":
        approved_by_angle: dict[str, dict[str, Any]] = {}
        for row in rows:
            metadata = row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}
            angle_key = str(metadata.get("angle_key") or "")
            gate = row.get("identity_gate_result") if isinstance(row.get("identity_gate_result"), dict) else {}
            if row.get("status") == "approved" and row.get("image_url") and gate.get("status") == "passed":
                approved_by_angle[angle_key] = row

        approved_rows = [
            approved_by_angle[key]
            for key in REQUIRED_SCENE_REFERENCE_ANGLE_KEYS
            if key in approved_by_angle
        ]
        missing = [key for key in REQUIRED_SCENE_REFERENCE_ANGLE_KEYS if key not in approved_by_angle]
        return cls(
            post_id=post_id,
            reference_set_id=reference_set_id,
            rows=rows,
            approved_rows=approved_rows,
            missing_angle_keys=missing,
            is_ready=len(missing) == 0 and len(approved_rows) == len(REQUIRED_SCENE_REFERENCE_ANGLE_KEYS),
        )
```

- [ ] **Step 5: Run tests to verify pass**

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_reference.py::test_required_scene_reference_angles_are_stable tests/test_actor_identity_scene_reference.py::test_scene_reference_set_summary_requires_three_approved_images
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/features/characters/schemas.py app/features/characters/scene_reference.py tests/test_actor_identity_scene_reference.py
git commit -m "feat: define actor scene reference set contract"
```

## Task 2: Add Set-Aware Query Helpers

**Files:**
- Modify: `app/features/characters/queries.py`
- Test: `tests/test_actor_identity_scene_reference.py`

- [ ] **Step 1: Write failing tests for approved set lookup**

Add these tests to `tests/test_actor_identity_scene_reference.py`.

```python
def test_select_latest_reference_set_id_uses_newest_complete_set():
    from app.features.characters.queries import select_latest_reference_set_id

    rows = [
        {"created_at": "2026-05-21T10:00:00Z", "provider_metadata": {"reference_set_id": "old", "angle_key": "front_mid"}},
        {"created_at": "2026-05-21T10:00:01Z", "provider_metadata": {"reference_set_id": "old", "angle_key": "left_three_quarter"}},
        {"created_at": "2026-05-21T10:00:02Z", "provider_metadata": {"reference_set_id": "new", "angle_key": "front_mid"}},
        {"created_at": "2026-05-21T10:00:03Z", "provider_metadata": {"reference_set_id": "new", "angle_key": "left_three_quarter"}},
        {"created_at": "2026-05-21T10:00:04Z", "provider_metadata": {"reference_set_id": "new", "angle_key": "right_profile"}},
    ]

    assert select_latest_reference_set_id(rows) == "new"


def test_filter_reference_rows_for_set_keeps_requested_set_only():
    from app.features.characters.queries import filter_reference_rows_for_set

    rows = [
        {"id": "1", "provider_metadata": {"reference_set_id": "set-a"}},
        {"id": "2", "provider_metadata": {"reference_set_id": "set-b"}},
    ]

    assert [row["id"] for row in filter_reference_rows_for_set(rows, "set-b")] == ["2"]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_reference.py::test_select_latest_reference_set_id_uses_newest_complete_set tests/test_actor_identity_scene_reference.py::test_filter_reference_rows_for_set_keeps_requested_set_only
```

Expected: FAIL because helper functions do not exist.

- [ ] **Step 3: Add pure row helpers**

In `app/features/characters/queries.py`, add these helpers near the existing scene reference functions.

```python
def _reference_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("provider_metadata")
    return metadata if isinstance(metadata, dict) else {}


def select_latest_reference_set_id(rows: list[dict[str, Any]]) -> Optional[str]:
    latest_by_set: dict[str, str] = {}
    for row in rows:
        metadata = _reference_metadata(row)
        reference_set_id = str(metadata.get("reference_set_id") or "")
        angle_key = str(metadata.get("angle_key") or "")
        if not reference_set_id or not angle_key:
            continue
        latest_by_set[reference_set_id] = max(
            latest_by_set.get(reference_set_id, ""),
            str(row.get("created_at") or ""),
        )
    if not latest_by_set:
        return None
    return max(latest_by_set.items(), key=lambda item: item[1])[0]


def filter_reference_rows_for_set(rows: list[dict[str, Any]], reference_set_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(_reference_metadata(row).get("reference_set_id") or "") == reference_set_id
    ]
```

- [ ] **Step 4: Add set-aware query functions**

In `app/features/characters/queries.py`, replace the single-row `get_approved_scene_reference_for_post(...)` body with a compatibility wrapper and add the new set functions below it.

```python
def list_scene_references_for_post(post_id: str) -> list[dict[str, Any]]:
    response = (
        get_supabase()
        .client.table("scene_reference_images")
        .select("*")
        .eq("post_id", post_id)
        .order("created_at", desc=False)
        .execute()
    )
    return getattr(response, "data", None) or []


def get_latest_scene_reference_set_for_post(post_id: str) -> Optional[SceneReferenceSetSummary]:
    rows = list_scene_references_for_post(post_id)
    reference_set_id = select_latest_reference_set_id(rows)
    if not reference_set_id:
        return None
    return SceneReferenceSetSummary.from_rows(
        post_id=post_id,
        reference_set_id=reference_set_id,
        rows=filter_reference_rows_for_set(rows, reference_set_id),
    )


def get_approved_scene_reference_set_for_post(post_id: str) -> Optional[SceneReferenceSetSummary]:
    summary = get_latest_scene_reference_set_for_post(post_id)
    if summary is None or not summary.is_ready:
        return None
    return summary


def get_approved_scene_reference_for_post(post_id: str) -> Optional[dict[str, Any]]:
    summary = get_approved_scene_reference_set_for_post(post_id)
    if summary is None:
        return None
    return summary.approved_rows[0]
```

Also add `SceneReferenceSetSummary` to the imports from `app.features.characters.schemas`.

- [ ] **Step 5: Add reference-set metadata to candidate creation**

Change `create_scene_reference_candidate(...)` signature in `app/features/characters/queries.py` to accept set fields.

```python
def create_scene_reference_candidate(
    *,
    actor_identity_id: str,
    post_id: str,
    scene_key: str,
    wardrobe_key: str,
    provider_task_id: Optional[str],
    image_url: Optional[str],
    prompt: str,
    provider_metadata: dict[str, Any],
    correlation_id: str,
    reference_set_id: Optional[str] = None,
    angle_key: Optional[str] = None,
) -> SceneReferenceImageRecord:
    now = datetime.now(timezone.utc).isoformat()
    metadata = dict(provider_metadata)
    if reference_set_id:
        metadata["reference_set_id"] = reference_set_id
    if angle_key:
        metadata["angle_key"] = angle_key
```

Then use `metadata` in the payload:

```python
        "provider_metadata": metadata,
```

Leave existing callers valid because both new parameters default to `None`.

- [ ] **Step 6: Run tests**

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_reference.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/features/characters/queries.py tests/test_actor_identity_scene_reference.py
git commit -m "feat: add actor scene reference set queries"
```

## Task 3: Generate Three Angle-Specific Mystic References

**Files:**
- Modify: `app/features/characters/handlers.py`
- Modify: `app/features/characters/scene_reference.py`
- Test: `tests/test_actor_identity_scene_reference.py`

- [ ] **Step 1: Write failing prompt test for angle-specific same-background behavior**

Add this test to `tests/test_actor_identity_scene_reference.py`.

```python
def test_angle_specific_prompts_keep_same_background_and_distinct_angles():
    from app.features.characters.scene_reference import (
        REQUIRED_SCENE_REFERENCE_ANGLES,
        build_scene_reference_prompt_for_angle,
    )

    prompts = [
        build_scene_reference_prompt_for_angle(
            actor_name="AYRA",
            scene_key="bathroom_adaptation",
            wardrobe_key="everyday_sweater",
            post_type="value",
            angle_key=angle.key,
            provider_lora_name="ayra_actor",
        )
        for angle in REQUIRED_SCENE_REFERENCE_ANGLES
    ]

    assert all("same background" in prompt.lower() for prompt in prompts)
    assert any("front-facing" in prompt for prompt in prompts)
    assert any("left three-quarter" in prompt for prompt in prompts)
    assert any("right-side profile" in prompt for prompt in prompts)
    assert all("@ayra_actor::100" in prompt for prompt in prompts)
```

- [ ] **Step 2: Run prompt test**

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_reference.py::test_angle_specific_prompts_keep_same_background_and_distinct_angles
```

Expected: PASS if Task 1 was completed correctly.

- [ ] **Step 3: Update generation endpoint to create a set**

In `app/features/characters/handlers.py`, update imports from `scene_reference`:

```python
from app.features.characters.scene_reference import (
    REQUIRED_SCENE_REFERENCE_ANGLES,
    build_scene_reference_prompt_for_angle,
    map_script_to_scene_intent,
)
```

In `generate_scene_reference(...)`, replace the single prompt and `range(3)` loop with:

```python
    reference_set_id = str(uuid4())
    references = []
    for angle in REQUIRED_SCENE_REFERENCE_ANGLES:
        prompt = build_scene_reference_prompt_for_angle(
            actor_name=actor_identity.name,
            scene_key=intent.scene_key,
            wardrobe_key=intent.wardrobe_key,
            post_type=str(post.get("post_type") or ""),
            angle_key=angle.key,
            provider_lora_name=actor_identity.provider_lora_name,
        )
        task = client.create_mystic_scene_reference(
            prompt=prompt,
            lora_id=str(actor_identity.provider_lora_id),
            strength=100,
            correlation_id=correlation_id,
            extra_options={"seed": angle.seed_offset},
        )
        references.append(
            character_queries.create_scene_reference_candidate(
                actor_identity_id=actor_identity.id,
                post_id=post_id,
                scene_key=intent.scene_key,
                wardrobe_key=intent.wardrobe_key,
                provider_task_id=str(task.get("task_id") or ""),
                image_url=_extract_mystic_image_url(task),
                prompt=prompt,
                provider_metadata={
                    "task": task,
                    "reason_code": intent.reason_code,
                    "angle_key": angle.key,
                    "angle_label": angle.label,
                    "reference_set_id": reference_set_id,
                    "reference_set_status": "pending_review",
                },
                reference_set_id=reference_set_id,
                angle_key=angle.key,
                correlation_id=correlation_id,
            )
        )
```

Remove the old `candidate_idx` metadata from this endpoint.

- [ ] **Step 4: Add regeneration endpoint for one angle**

In `app/features/characters/handlers.py`, add this endpoint after `reject_scene_reference(...)`.

```python
@router.post("/character/scene-reference/{reference_id}/regenerate")
def regenerate_scene_reference(reference_id: str):
    correlation_id = str(uuid4())
    reference = character_queries.get_scene_reference_by_id(reference_id)
    if not reference:
        raise HTTPException(status_code=404, detail="Scene reference not found")

    metadata = reference.get("provider_metadata") if isinstance(reference.get("provider_metadata"), dict) else {}
    angle_key = str(metadata.get("angle_key") or "")
    reference_set_id = str(metadata.get("reference_set_id") or "")
    if not angle_key or not reference_set_id:
        raise HTTPException(status_code=422, detail="Scene reference is missing set metadata")

    angle = get_scene_reference_angle(angle_key)
    actor_identity = character_queries.get_active_actor_identity()
    if not actor_identity_is_ready(actor_identity):
        raise HTTPException(status_code=422, detail="ActorIdentity training is not complete")

    prompt = build_scene_reference_prompt_for_angle(
        actor_name=actor_identity.name,
        scene_key=str(reference.get("scene_key") or ""),
        wardrobe_key=str(reference.get("wardrobe_key") or ""),
        post_type="",
        angle_key=angle.key,
        provider_lora_name=actor_identity.provider_lora_name,
    )
    task = get_magnific_client().create_mystic_scene_reference(
        prompt=prompt,
        lora_id=str(actor_identity.provider_lora_id),
        strength=100,
        correlation_id=correlation_id,
        extra_options={"seed": angle.seed_offset + 1000},
    )
    character_queries.create_scene_reference_candidate(
        actor_identity_id=actor_identity.id,
        post_id=str(reference["post_id"]),
        scene_key=str(reference.get("scene_key") or ""),
        wardrobe_key=str(reference.get("wardrobe_key") or ""),
        provider_task_id=str(task.get("task_id") or ""),
        image_url=_extract_mystic_image_url(task),
        prompt=prompt,
        provider_metadata={
            "task": task,
            "angle_key": angle.key,
            "angle_label": angle.label,
            "reference_set_id": reference_set_id,
            "reference_set_status": "pending_review",
            "regenerated_from_reference_id": reference_id,
        },
        reference_set_id=reference_set_id,
        angle_key=angle.key,
        correlation_id=correlation_id,
    )
    character_queries.record_scene_reference_gate(
        reference_id=reference_id,
        gate_result=pending_manual_gate("This reference was superseded by an individual regeneration"),
        status="rejected",
        correlation_id=correlation_id,
    )
    return RedirectResponse(url=f"/batches/{_post_batch_id(str(reference.get('post_id')))}", status_code=303)
```

Also import `get_scene_reference_angle`.

- [ ] **Step 5: Add regenerate-all endpoint**

In `app/features/characters/handlers.py`, add:

```python
@router.post("/character/posts/{post_id}/scene-reference/regenerate-all")
def regenerate_scene_reference_set(post_id: str):
    return generate_scene_reference(post_id)
```

This creates a fresh `reference_set_id` with three new rows and preserves old rows for audit/history.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_reference.py tests/test_actor_identity_training.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/features/characters/handlers.py app/features/characters/scene_reference.py tests/test_actor_identity_scene_reference.py
git commit -m "feat: generate three actor scene reference angles"
```

## Task 4: Require Approved Three-Image Set Before Video

**Files:**
- Modify: `app/features/characters/actor_identity.py`
- Modify: `app/features/videos/handlers.py`
- Test: `tests/test_character_consistency_mode.py`

- [ ] **Step 1: Write failing guard tests**

Add this test to `tests/test_character_consistency_mode.py`.

```python
def test_actor_identity_batch_blocks_video_without_complete_reference_set():
    from app.core.errors import FlowForgeException
    from app.features.characters.actor_identity import ensure_video_scene_reference_set_ready
    from app.features.characters.schemas import SceneReferenceSetSummary

    batch = {"id": "batch-1", "creation_mode": "character_consistency", "actor_identity_id": "actor-1"}
    post = {"id": "post-1", "batch_id": "batch-1"}
    summary = SceneReferenceSetSummary.from_rows(
        post_id="post-1",
        reference_set_id="set-1",
        rows=[
            {
                "id": "ref-1",
                "status": "approved",
                "image_url": "https://cdn.example.com/front.png",
                "provider_metadata": {"angle_key": "front_mid"},
                "identity_gate_result": {"status": "passed", "reason": "ok", "gate_type": "manual"},
            }
        ],
    )

    with pytest.raises(FlowForgeException) as exc:
        ensure_video_scene_reference_set_ready(batch=batch, post=post, scene_reference_set=summary, route="short")

    assert "three approved SceneReferenceImages" in exc.value.message
```

- [ ] **Step 2: Run guard test to verify failure**

Run:

```bash
python3 -m pytest -q tests/test_character_consistency_mode.py::test_actor_identity_batch_blocks_video_without_complete_reference_set
```

Expected: FAIL because `ensure_video_scene_reference_set_ready` does not exist.

- [ ] **Step 3: Add set-aware guard helper**

In `app/features/characters/actor_identity.py`, import `SceneReferenceSetSummary` and add:

```python
def ensure_video_scene_reference_set_ready(
    *,
    batch: dict[str, Any],
    post: dict[str, Any],
    scene_reference_set: Optional[SceneReferenceSetSummary],
    route: Optional[str],
) -> dict[str, Any]:
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
    if scene_reference_set is None or not scene_reference_set.is_ready:
        raise FlowForgeException(
            code=ErrorCode.VALIDATION_ERROR,
            message="ActorIdentity video generation requires three approved SceneReferenceImages before submit.",
            details={
                "post_id": post.get("id"),
                "batch_id": batch.get("id"),
                "missing_angle_keys": scene_reference_set.missing_angle_keys if scene_reference_set else [],
            },
            status_code=422,
        )
    return {
        "source": "actor_identity_scene_reference_set",
        "compatible": True,
        "route": route,
        "scene_reference_set": scene_reference_set,
    }
```

Keep the existing `ensure_video_scene_reference_ready(...)` for backward compatibility until all callers are moved.

- [ ] **Step 4: Replace single-reference lookup in single-post video submission**

In `app/features/videos/handlers.py`, change the import:

```python
from app.features.characters.actor_identity import ensure_video_scene_reference_set_ready
```

In the single-post submit path around the existing `approved_scene_reference = None`, replace with:

```python
        approved_scene_reference_set = None
        scene_reference_check = ensure_video_scene_reference_set_ready(
            batch=batch,
            post=post,
            scene_reference_set=character_queries.get_approved_scene_reference_set_for_post(post_id),
            route=route,
        )
        if scene_reference_check.get("source") == "actor_identity_scene_reference_set":
            approved_scene_reference_set = scene_reference_check["scene_reference_set"]
```

Pass `scene_reference_set=approved_scene_reference_set` into `_submit_video_request(...)` instead of `scene_reference=approved_scene_reference`.

- [ ] **Step 5: Replace single-reference lookup in generate-all path**

In `app/features/videos/handlers.py`, find the batch/generate-all path that currently calls `ensure_video_scene_reference_ready(...)`. Replace it with:

```python
            scene_reference_check = ensure_video_scene_reference_set_ready(
                batch=batch,
                post=post,
                scene_reference_set=character_queries.get_approved_scene_reference_set_for_post(post_id),
                route=route,
            )
            approved_scene_reference_set = (
                scene_reference_check["scene_reference_set"]
                if scene_reference_check.get("source") == "actor_identity_scene_reference_set"
                else None
            )
```

Store `"scene_reference_set": approved_scene_reference_set` in each queued item instead of `"scene_reference": approved_scene_reference`.

- [ ] **Step 6: Run guard tests**

Run:

```bash
python3 -m pytest -q tests/test_character_consistency_mode.py::test_actor_identity_batch_blocks_video_without_complete_reference_set tests/test_character_consistency_mode.py::test_actor_identity_batch_blocks_video_without_approved_scene_reference
```

Expected: PASS. The old single-reference test can stay as a compatibility test, but no active video submission caller should rely on the old helper.

- [ ] **Step 7: Commit**

```bash
git add app/features/characters/actor_identity.py app/features/videos/handlers.py tests/test_character_consistency_mode.py
git commit -m "feat: require complete actor scene reference set for video"
```

## Task 5: Load Three Approved References Into Existing Submit Rules

**Files:**
- Modify: `app/features/videos/handlers.py`
- Test: `tests/test_character_consistency_mode.py`

- [ ] **Step 1: Write failing video payload test**

Replace or add a new test beside `test_submit_video_request_attaches_actor_scene_reference_to_vertex` in `tests/test_character_consistency_mode.py`.

```python
def test_submit_video_request_attaches_three_actor_scene_references_to_vertex(monkeypatch):
    from app.features.characters.schemas import SceneReferenceSetSummary
    from app.features.videos import handlers as video_handlers

    captured = {}

    class FakeVertexClient:
        def submit_text_video(self, **kwargs):
            captured.update(kwargs)
            return {
                "operation_id": "projects/test/locations/us-central1/publishers/google/models/veo-3.1-generate-001/operations/actor-ref-set",
                "status": "submitted",
                "provider_model": kwargs.get("model") or "veo-3.1-generate-001",
            }

    monkeypatch.setattr(video_handlers, "get_vertex_ai_client", lambda: FakeVertexClient())
    monkeypatch.setattr(video_handlers, "get_settings", lambda: type("S", (), {"vertex_ai_output_gcs_uri": "gs://bucket/out/"})())
    monkeypatch.setattr(video_handlers, "_download_image_bytes", lambda url: b"image-" + url.encode("utf-8"))

    reference_set = SceneReferenceSetSummary.from_rows(
        post_id="post-1",
        reference_set_id="set-1",
        rows=[
            {
                "id": "scene-front",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": "https://cdn/front.png",
                "scene_key": "bathroom_adaptation",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {"reference_set_id": "set-1", "angle_key": "front_mid"},
                "identity_gate_result": {"status": "passed", "reason": "ok", "gate_type": "manual"},
            },
            {
                "id": "scene-left",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": "https://cdn/left.png",
                "scene_key": "bathroom_adaptation",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {"reference_set_id": "set-1", "angle_key": "left_three_quarter"},
                "identity_gate_result": {"status": "passed", "reason": "ok", "gate_type": "manual"},
            },
            {
                "id": "scene-profile",
                "actor_identity_id": "actor-1",
                "status": "approved",
                "image_url": "https://cdn/profile.png",
                "scene_key": "bathroom_adaptation",
                "wardrobe_key": "everyday_sweater",
                "provider_metadata": {"reference_set_id": "set-1", "angle_key": "right_profile"},
                "identity_gate_result": {"status": "passed", "reason": "ok", "gate_type": "manual"},
            },
        ],
    )

    result = video_handlers._submit_video_request(
        provider="vertex_ai",
        prompt_text="Prompt",
        negative_prompt=None,
        aspect_ratio="9:16",
        provider_aspect_ratio="9:16",
        requested_aspect_ratio="9:16",
        resolution="720p",
        seconds=8,
        size=None,
        correlation_id="corr-actor-ref-set",
        provider_duration_seconds=8,
        creation_mode="character_consistency",
        scene_reference_set=reference_set,
    )

    assert len(captured["reference_images"]) == 3
    assert result["provider_metadata"]["source"] == "actor_identity_scene_reference_set"
    assert result["provider_metadata"]["scene_reference_image_ids"] == ["scene-front", "scene-left", "scene-profile"]
    assert result["provider_metadata"]["reference_image_count"] == 3
```

- [ ] **Step 2: Run payload test to verify failure**

Run:

```bash
python3 -m pytest -q tests/test_character_consistency_mode.py::test_submit_video_request_attaches_three_actor_scene_references_to_vertex
```

Expected: FAIL because `_submit_video_request(...)` does not accept `scene_reference_set`.

- [ ] **Step 3: Replace single-reference asset loader with set loader**

In `app/features/videos/handlers.py`, add `SceneReferenceSetSummary` import:

```python
from app.features.characters.schemas import SceneReferenceSetSummary
```

Replace `_load_scene_reference_asset(...)` with:

```python
def _load_scene_reference_set_assets(
    *,
    scene_reference_set: Optional[SceneReferenceSetSummary],
    correlation_id: str,
) -> Optional[Dict[str, Any]]:
    if not scene_reference_set:
        return None
    reference_images: list[Dict[str, str]] = []
    scene_reference_ids: list[str] = []
    angle_keys: list[str] = []
    for row in scene_reference_set.approved_rows:
        image_url = row.get("image_url")
        if not image_url:
            raise FlowForgeException(
                code=ErrorCode.VALIDATION_ERROR,
                message="Approved SceneReferenceImage is missing an image URL.",
                details={"scene_reference_image_id": row.get("id")},
                status_code=422,
            )
        mime_type = mimetypes.guess_type(urlparse(str(image_url)).path)[0] or "image/png"
        if mime_type not in {"image/png", "image/jpeg"}:
            mime_type = "image/png"
        metadata = row.get("provider_metadata") if isinstance(row.get("provider_metadata"), dict) else {}
        reference_images.append(
            {
                "mime_type": mime_type,
                "data_base64": base64.b64encode(_download_image_bytes(str(image_url))).decode("ascii"),
            }
        )
        scene_reference_ids.append(str(row.get("id") or ""))
        angle_keys.append(str(metadata.get("angle_key") or ""))

    logger.info(
        "actor_scene_reference_set_loaded",
        correlation_id=correlation_id,
        reference_set_id=scene_reference_set.reference_set_id,
        reference_image_count=len(reference_images),
    )
    first_row = scene_reference_set.approved_rows[0] if scene_reference_set.approved_rows else {}
    return {
        "reference_images": reference_images,
        "metadata": {
            "reference_images_enabled": True,
            "reference_image_count": len(reference_images),
            "actor_identity_id": first_row.get("actor_identity_id"),
            "scene_reference_set_id": scene_reference_set.reference_set_id,
            "scene_reference_image_ids": scene_reference_ids,
            "scene_reference_angle_keys": angle_keys,
            "scene_key": first_row.get("scene_key"),
            "wardrobe_key": first_row.get("wardrobe_key"),
            "source": "actor_identity_scene_reference_set",
        },
    }
```

Keep `_load_scene_reference_asset(...)` only if needed by old tests; otherwise replace it completely.

- [ ] **Step 4: Update `_submit_video_request` signature and provider branches**

Change the signature:

```python
    scene_reference_set: Optional[SceneReferenceSetSummary] = None,
```

In the `veo_3_1` branch, replace `if scene_reference:` with:

```python
            if scene_reference_set:
                if veo_duration_seconds == 4:
                    raise FlowForgeException(
                        code=ErrorCode.VALIDATION_ERROR,
                        message="ActorIdentity video route cannot consume approved scene references on a 4s base request.",
                        details={"scene_reference_set_id": scene_reference_set.reference_set_id},
                        status_code=422,
                    )
                reference_bundle = _load_scene_reference_set_assets(
                    scene_reference_set=scene_reference_set,
                    correlation_id=correlation_id,
                )
```

In the `vertex_ai` branch, replace `if scene_reference:` with:

```python
            if scene_reference_set:
                if vertex_duration != 8:
                    raise FlowForgeException(
                        code=ErrorCode.VALIDATION_ERROR,
                        message="ActorIdentity video route cannot consume approved scene references unless the base request is 8 seconds.",
                        details={
                            "scene_reference_set_id": scene_reference_set.reference_set_id,
                            "provider_duration_seconds": vertex_duration,
                        },
                        status_code=422,
                    )
                reference_bundle = _load_scene_reference_set_assets(
                    scene_reference_set=scene_reference_set,
                    correlation_id=correlation_id,
                )
```

- [ ] **Step 5: Update submission metadata**

In single-post and generate-all paths, replace the old single-reference metadata block with:

```python
        if approved_scene_reference_set:
            submission_metadata["actor_identity_source"] = "actor_identity_scene_reference_set"
            submission_metadata["actor_identity_id"] = batch.get("actor_identity_id")
            submission_metadata["scene_reference_set_id"] = approved_scene_reference_set.reference_set_id
            submission_metadata["scene_reference_image_ids"] = [
                str(row.get("id") or "") for row in approved_scene_reference_set.approved_rows
            ]
            submission_metadata["scene_reference_angle_keys"] = [
                str((row.get("provider_metadata") or {}).get("angle_key") or "")
                for row in approved_scene_reference_set.approved_rows
            ]
```

For generate-all queued items, read `item.get("scene_reference_set")` and use the same metadata shape.

- [ ] **Step 6: Run routing tests**

Run:

```bash
python3 -m pytest -q tests/test_character_consistency_mode.py tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py
```

Expected: PASS. Existing duration behavior must remain unchanged.

- [ ] **Step 7: Commit**

```bash
git add app/features/videos/handlers.py tests/test_character_consistency_mode.py
git commit -m "feat: submit three actor scene references"
```

## Task 6: Update Manual Review UI For Individual And Full Regeneration

**Files:**
- Modify: `templates/batches/detail/_post_card.html`
- Modify: `app/features/batches/handlers.py`
- Test: `tests/test_actor_identity_scene_reference.py`

- [ ] **Step 1: Write failing grouping test for latest set display**

Add this pure test to `tests/test_actor_identity_scene_reference.py`.

```python
def test_reference_candidates_keep_latest_set_group_together():
    from app.features.characters.queries import filter_reference_rows_for_set, select_latest_reference_set_id

    rows = [
        {"id": "old-front", "created_at": "2026-05-21T10:00:00Z", "provider_metadata": {"reference_set_id": "old", "angle_key": "front_mid"}},
        {"id": "new-front", "created_at": "2026-05-21T10:10:00Z", "provider_metadata": {"reference_set_id": "new", "angle_key": "front_mid"}},
        {"id": "new-left", "created_at": "2026-05-21T10:10:01Z", "provider_metadata": {"reference_set_id": "new", "angle_key": "left_three_quarter"}},
        {"id": "new-profile", "created_at": "2026-05-21T10:10:02Z", "provider_metadata": {"reference_set_id": "new", "angle_key": "right_profile"}},
    ]

    latest = select_latest_reference_set_id(rows)

    assert latest == "new"
    assert [row["id"] for row in filter_reference_rows_for_set(rows, latest)] == ["new-front", "new-left", "new-profile"]
```

- [ ] **Step 2: Run grouping test**

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_reference.py::test_reference_candidates_keep_latest_set_group_together
```

Expected: PASS if Task 2 was completed.

- [ ] **Step 3: Normalize post-card data to show latest set first**

In `app/features/batches/handlers.py`, where `scene_references_by_post` is assigned to `scene_reference_candidates`, keep existing list behavior but ensure rows are already ordered latest first by current query. No code change is required if `list_scene_references_for_posts(...)` still orders `created_at desc=True`. If it was changed, restore:

```python
.order("created_at", desc=True)
```

- [ ] **Step 4: Update card actions**

In `templates/batches/detail/_post_card.html`, replace the current Actor Scene Reference action block with:

```html
<div class="flex flex-wrap gap-2">
  <button
    hx-post="/settings/character/posts/{{ post.id }}/scene-reference/generate"
    hx-trigger="click"
    hx-swap="none"
    hx-on::after-request="if (event.detail.successful) { window.location.reload(); }"
    class="inline-flex items-center rounded-md bg-amber-700 px-3 py-2 text-xs font-medium text-white hover:bg-amber-800"
  >
    Generate 3 refs
  </button>
  {% if scene_references %}
  <button
    hx-post="/settings/character/posts/{{ post.id }}/scene-reference/regenerate-all"
    hx-trigger="click"
    hx-swap="none"
    hx-on::after-request="if (event.detail.successful) { window.location.reload(); }"
    class="inline-flex items-center rounded-md bg-white px-3 py-2 text-xs font-medium text-amber-900 ring-1 ring-amber-300 hover:bg-amber-100"
  >
    Regenerate all
  </button>
  {% endif %}
</div>
```

- [ ] **Step 5: Add per-reference regenerate button**

Inside each reference card in `templates/batches/detail/_post_card.html`, add this button beside Approve and Poll:

```html
{% if reference.image_url %}
<button
  hx-post="/settings/character/scene-reference/{{ reference.id }}/regenerate"
  hx-trigger="click"
  hx-swap="none"
  hx-on::after-request="if (event.detail.successful) { window.location.reload(); }"
  class="inline-flex items-center rounded-md bg-white px-2 py-1 text-[11px] font-medium text-amber-900 ring-1 ring-amber-300 hover:bg-amber-100"
>
  Regenerate
</button>
{% endif %}
```

Show angle labels from metadata:

```html
{% set metadata = reference.provider_metadata or {} %}
<p class="mt-2 text-[11px] text-amber-900">
  {{ metadata.get('angle_label') or metadata.get('angle_key') or 'angle' }} · {{ reference.scene_key }} · {{ reference.status }}
</p>
```

- [ ] **Step 6: Run template-adjacent tests**

Run:

```bash
python3 -m pytest -q tests/test_actor_identity_scene_reference.py tests/test_character_consistency_mode.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add templates/batches/detail/_post_card.html app/features/batches/handlers.py tests/test_actor_identity_scene_reference.py
git commit -m "feat: review and regenerate actor reference sets"
```

## Task 7: Update Video Gate Metadata And Docs

**Files:**
- Modify: `workers/video_poller.py`
- Modify: `app/features/qa/handlers.py`
- Modify: `docs/character_consistency.md`
- Test: `tests/test_video_poller_batch_transition.py`
- Test: `tests/test_publish_caption_guard.py`

- [ ] **Step 1: Update poller source check**

In `workers/video_poller.py`, find:

```python
if existing_metadata.get("actor_identity_source") == "actor_identity_scene_reference":
```

Replace it with:

```python
if existing_metadata.get("actor_identity_source") in {
    "actor_identity_scene_reference",
    "actor_identity_scene_reference_set",
}:
```

- [ ] **Step 2: Update QA source check**

In `app/features/qa/handlers.py`, find:

```python
and video_metadata.get("actor_identity_source") == "actor_identity_scene_reference"
```

Replace it with:

```python
and video_metadata.get("actor_identity_source") in {
    "actor_identity_scene_reference",
    "actor_identity_scene_reference_set",
}
```

- [ ] **Step 3: Update docs**

In `docs/character_consistency.md`, replace the ActorIdentity MVP bullet that says one `SceneReferenceImage` is required with:

```markdown
- New ActorIdentity-backed posts must generate exactly three Mystic `SceneReferenceImage` rows for the approved script before video submission. The three images share one script-derived background and wardrobe, but use the fixed angles `front_mid`, `left_three_quarter`, and `right_profile`.
- Manual review is required per generated image. Operators can approve each image, regenerate one image, or regenerate the full three-image set. Video generation is blocked until the latest set has three approved images with passed manual gates.
- Video submission continues to follow the existing Character Consistency route rules. Where the current route accepts reference images, the approved three-image set is sent together; where the current route rejects or skips reference images, that existing behavior is preserved.
```

- [ ] **Step 4: Run regression tests**

Run:

```bash
python3 -m pytest -q tests/test_video_poller_batch_transition.py tests/test_publish_caption_guard.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add workers/video_poller.py app/features/qa/handlers.py docs/character_consistency.md
git commit -m "docs: document actor reference set gates"
```

## Task 8: Final Verification

**Files:**
- No code files unless previous tests expose a defect.

- [ ] **Step 1: Run focused ActorIdentity suite**

Run:

```bash
python3 -m pytest -q \
  tests/test_actor_identity_training.py \
  tests/test_magnific_actor_identity.py \
  tests/test_actor_identity_scene_reference.py \
  tests/test_character_consistency_mode.py
```

Expected: PASS.

- [ ] **Step 2: Run video route regression suite**

Run:

```bash
python3 -m pytest -q \
  tests/test_video_duration_routing.py \
  tests/test_veo_prompt_contract.py \
  tests/test_video_poller_batch_transition.py \
  tests/test_publish_caption_guard.py
```

Expected: PASS.

- [ ] **Step 3: Optional local browser smoke**

Run:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Manual observations:

- `/settings/actor` shows active LoRA training state and allows a new 8-20 image actor upload.
- A Character Consistency post after prompt/script approval shows `Generate 3 refs`.
- Generate creates three reference cards with labels: `Front`, `Left three-quarter`, and `Right profile`.
- Each card can be approved independently.
- Each card can be regenerated independently.
- `Regenerate all` creates a new three-image set.
- Video submission remains blocked until the latest set has all three images approved.

- [ ] **Step 4: Optional paid Magnific smoke**

Only run with a valid key and explicit operator approval:

```bash
AIUGC_LIVE_MAGNIFIC_SMOKE=1 python3 -m pytest -q tests/live/test_magnific_actor_identity_smoke.py
```

Expected: PASS listing LoRAs. Do not run paid train or paid Mystic generation unless separate explicit flags exist.

- [ ] **Step 5: Final commit if verification changed files**

If any verification fixes were needed:

```bash
git add app/features/characters app/features/videos workers/video_poller.py app/features/qa/handlers.py templates/batches/detail/_post_card.html tests docs/character_consistency.md
git commit -m "fix: stabilize actor reference set workflow"
```

## Self-Review

**Spec coverage:**

- Settings page showing LoRA training and actor upload is already implemented and preserved.
- Character Consistency mode after script approval generating reference pictures is covered by Tasks 3 and 6.
- Exactly three Mystic references per post are covered by Tasks 1 and 3.
- Manual review with individual and all regeneration is covered by Tasks 3 and 6.
- Three different angles with one script-derived background are covered by Tasks 1 and 3.
- Sending all three pictures to video generation while preserving current submission rules is covered by Tasks 4 and 5.
- Existing duration/provider behavior is explicitly preserved and tested in Tasks 5 and 8.

**Placeholder scan:**

- No `TBD`, `TODO`, or unspecified "add tests" steps remain.
- Every code-changing task includes concrete code snippets or exact replacement text.
- Every test step includes an exact command and expected result.

**Type consistency:**

- `SceneReferenceSetSummary` is defined before it is used in guards and video handlers.
- `reference_set_id` and `angle_key` live in `provider_metadata`, so no migration is required.
- `scene_reference_set` replaces `scene_reference` only in active video submission callers; the old single-reference helper remains temporarily compatible.
- `actor_identity_source` moves to `actor_identity_scene_reference_set`, and poller/QA checks accept both old and new values.
