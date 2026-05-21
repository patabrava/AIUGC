# Actor Identity Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit global active-actor selector on `/settings/actor` so training new ActorIdentity rows never silently changes which ready actor is used by future `character_consistency` batches.

**Architecture:** Keep `actor_identities.is_active` as the persisted source of truth, but make `set_active_actor_identity(...)` the only activation path. Existing batch creation, scene-reference generation, and video work continue to read `get_active_actor_identity()` and `actor_identity_is_ready(...)`; this slice changes how the active row is chosen and updates blocked-copy to point operators to the selector. Training creates inactive ActorIdentity rows, readiness is evaluated independently from active selection, and the settings page renders both the active summary and full roster.

**Tech Stack:** FastAPI, Jinja2 templates, Supabase/PostgREST, Pydantic, existing Magnific adapter, pytest, no new runtime dependencies.

---

## Context-Zero

- Operating system: macOS local checkout.
- Runtime observed for planning: `python3 --version` -> `Python 3.9.6`.
- Test runner observed for planning: `python3 -m pytest --version` -> `pytest 7.4.3`.
- Build identifier observed for planning: `git rev-parse --short HEAD` -> `365d8dd`.
- Working tree status during planning: dirty, with existing ActorIdentity implementation files already modified. Implementation workers must not revert unrelated changes.
- Existing app shape: FastAPI + Jinja + HTMX vertical-slice monolith.
- Non-functional requirements: no new dependencies; keep route behavior deterministic; keep settings page rendering even if roster loading fails; do not log private image URLs or provider secrets.

## Scope Check

The spec is one subsystem: global ActorIdentity selection for `character_consistency`. It does not require a separate plan for scene-reference generation, provider training, per-batch overrides, or video route changes.

## Budget

Required AGENTS budget: `{files: 8 total touched by implementation, LOC/file: app files stay <= 800 existing-file hard cap and added sections <= 260 LOC/file, deps: 0}`.

Implementation files:

| File | Action | Responsibility | Budget |
| --- | --- | --- | --- |
| `app/features/characters/actor_identity.py` | Modify | Separate training readiness from active readiness and provide roster ordering | <=90 LOC added |
| `app/features/characters/queries.py` | Modify | Add roster query, inactive training row creation, active switch helper, and safe restore path | <=260 LOC added |
| `app/features/characters/handlers.py` | Modify | Render roster context, add `POST /settings/actor/active`, and stop training from activating rows | <=180 LOC added |
| `templates/settings/actor.html` | Modify | Add active selector, full roster, and training copy that says training does not activate | <=260 LOC total target |
| `app/features/batches/queries.py` | Modify | Update blocked batch copy to point to active selection at `/settings/actor` | <=20 LOC changed |
| `templates/batches/list.html` | Modify | Update create-batch helper copy for Character Consistency | <=20 LOC changed |

Focused regression files:

| File | Action | Responsibility | Budget |
| --- | --- | --- | --- |
| `tests/test_characters_feature.py` | Modify | Cover roster UI, activation helper, and inactive training behavior | <=220 LOC added |
| `tests/test_character_consistency_mode.py` | Modify | Cover blocked batch copy | <=20 LOC changed |

## Task 1: Separate Training Readiness From Active Readiness

**Files:**
- Modify: `app/features/characters/actor_identity.py`
- Test: `tests/test_characters_feature.py`

- [ ] **Step 1: Add failing tests for inactive ready actors and roster ordering**

Add these helpers and tests to `tests/test_characters_feature.py` after `_png_bytes()`.

```python
def _actor_identity_record(
    *,
    actor_id: str,
    name: str,
    is_active: bool,
    training_status: str,
    training_phase: str,
    progress: int,
    provider_lora_id: str | None,
    updated_at: str,
):
    return ActorIdentityRecord(
        id=actor_id,
        name=name,
        is_active=is_active,
        provider="magnific",
        provider_lora_id=provider_lora_id,
        provider_lora_name=f"{name.lower().replace(' ', '_')}_lora" if provider_lora_id else None,
        provider_training_task_id=f"task-{actor_id}",
        training_status=training_status,
        training_phase=training_phase,
        training_progress_percent=progress,
        training_error=None,
        training_images=[f"https://cdn.example.com/{actor_id}/{idx}.png" for idx in range(8)],
        consent_source="operator",
        created_at="2026-05-21T10:00:00Z",
        updated_at=updated_at,
    )


def test_actor_identity_training_ready_does_not_require_active_selection():
    from app.features.characters.actor_identity import actor_identity_is_ready, actor_identity_training_ready

    inactive_ready = _actor_identity_record(
        actor_id="actor-ready",
        name="Ready Actor",
        is_active=False,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-ready",
        updated_at="2026-05-21T12:00:00Z",
    )

    assert actor_identity_training_ready(inactive_ready) is True
    assert actor_identity_is_ready(inactive_ready) is False


def test_actor_identity_roster_sorting_groups_active_ready_training_failed():
    from app.features.characters.actor_identity import sort_actor_identity_roster

    active = _actor_identity_record(
        actor_id="actor-active",
        name="Active Actor",
        is_active=True,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-active",
        updated_at="2026-05-21T10:00:00Z",
    )
    newest_ready = _actor_identity_record(
        actor_id="actor-ready-new",
        name="Newest Ready",
        is_active=False,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-ready-new",
        updated_at="2026-05-21T13:00:00Z",
    )
    older_ready = _actor_identity_record(
        actor_id="actor-ready-old",
        name="Older Ready",
        is_active=False,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-ready-old",
        updated_at="2026-05-21T11:00:00Z",
    )
    training = _actor_identity_record(
        actor_id="actor-training",
        name="Training Actor",
        is_active=False,
        training_status="processing",
        training_phase="training",
        progress=40,
        provider_lora_id=None,
        updated_at="2026-05-21T14:00:00Z",
    )
    failed = _actor_identity_record(
        actor_id="actor-failed",
        name="Failed Actor",
        is_active=False,
        training_status="failed",
        training_phase="failed",
        progress=80,
        provider_lora_id=None,
        updated_at="2026-05-21T15:00:00Z",
    )

    result = sort_actor_identity_roster([failed, training, older_ready, newest_ready, active])

    assert [row.id for row in result] == [
        "actor-active",
        "actor-ready-new",
        "actor-ready-old",
        "actor-training",
        "actor-failed",
    ]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py::test_actor_identity_training_ready_does_not_require_active_selection tests/test_characters_feature.py::test_actor_identity_roster_sorting_groups_active_ready_training_failed
```

Expected: FAIL with import errors for `actor_identity_training_ready` and `sort_actor_identity_roster`.

- [ ] **Step 3: Add readiness and roster helpers**

In `app/features/characters/actor_identity.py`, replace the existing `actor_identity_is_ready(...)` function with this block.

```python
def actor_identity_training_ready(identity: Optional[ActorIdentityRecord]) -> bool:
    if identity is None:
        return False
    return (
        identity.training_phase == "ready"
        and identity.training_progress_percent == 100
        and bool(identity.provider_lora_id)
        and not identity.training_error
    )


def actor_identity_is_ready(identity: Optional[ActorIdentityRecord]) -> bool:
    return identity is not None and identity.is_active is True and actor_identity_training_ready(identity)


def actor_identity_status_group(identity: ActorIdentityRecord) -> str:
    if actor_identity_is_ready(identity):
        return "active"
    if actor_identity_training_ready(identity):
        return "ready"
    failed_values = {"failed", "error"}
    if identity.training_status in failed_values or identity.training_phase in failed_values:
        return "failed"
    return "training"


def actor_identity_roster_sort_key(identity: ActorIdentityRecord) -> tuple[int, float]:
    group_order = {
        "active": 0,
        "ready": 1,
        "training": 2,
        "failed": 3,
    }
    updated_at = identity.updated_at.timestamp() if identity.updated_at else 0.0
    return (group_order[actor_identity_status_group(identity)], -updated_at)


def sort_actor_identity_roster(identities: list[ActorIdentityRecord]) -> list[ActorIdentityRecord]:
    return sorted(identities, key=actor_identity_roster_sort_key)
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py::test_actor_identity_training_ready_does_not_require_active_selection tests/test_characters_feature.py::test_actor_identity_roster_sorting_groups_active_ready_training_failed tests/test_actor_identity_training.py::test_ready_actor_identity_requires_completed_training
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/features/characters/actor_identity.py tests/test_characters_feature.py
git commit -m "test: define actor identity readiness and roster ordering"
```

## Task 2: Add Roster Query And Activation Helper

**Files:**
- Modify: `app/features/characters/queries.py`
- Test: `tests/test_characters_feature.py`

- [ ] **Step 1: Add failing tests for roster loading, activation, rejection, and restore**

Add these fake Supabase helpers to `tests/test_characters_feature.py` after `_fake_supabase(...)`.

```python
class _ActorIdentityQuery:
    def __init__(self, rows, calls, *, fail_activate=False):
        self.rows = rows
        self.calls = calls
        self.filters = []
        self.payload = None
        self._maybe_single = False
        self.fail_activate = fail_activate

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def insert(self, payload):
        self.payload = payload
        self.calls.append(("insert", payload, list(self.filters)))
        self.rows.append(dict(payload))
        return self

    def update(self, payload):
        self.payload = payload
        self.calls.append(("update", payload, list(self.filters)))
        return self

    def execute(self):
        if self.payload is not None:
            if self.fail_activate and self.payload.get("is_active") is True and ("id", "actor-ready") in self.filters:
                raise RuntimeError("activate failed")
            matched = self._matching_rows()
            for row in matched:
                row.update(self.payload)
            return _FakeResponse(matched)
        matched = self._matching_rows()
        if self._maybe_single:
            return _FakeResponse(matched[0] if matched else None)
        return _FakeResponse(matched)

    def _matching_rows(self):
        if not self.filters:
            return list(self.rows)
        return [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in self.filters)
        ]


class _ActorIdentityClient:
    def __init__(self, rows, *, fail_activate=False):
        self.rows = rows
        self.calls = []
        self.fail_activate = fail_activate

    def table(self, name):
        assert name == "actor_identities"
        return _ActorIdentityQuery(self.rows, self.calls, fail_activate=self.fail_activate)


def _actor_identity_supabase(rows, *, fail_activate=False):
    client = _ActorIdentityClient(rows, fail_activate=fail_activate)
    return SimpleNamespace(client=client), client
```

Add these tests to the same file.

```python
def test_list_actor_identities_returns_sorted_roster(monkeypatch):
    active = _actor_identity_record(
        actor_id="actor-active",
        name="Active Actor",
        is_active=True,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-active",
        updated_at="2026-05-21T10:00:00Z",
    )
    ready = _actor_identity_record(
        actor_id="actor-ready",
        name="Ready Actor",
        is_active=False,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-ready",
        updated_at="2026-05-21T12:00:00Z",
    )
    rows = [ready.model_dump(mode="json"), active.model_dump(mode="json")]
    supabase, _client = _actor_identity_supabase(rows)
    monkeypatch.setattr(character_queries, "get_supabase", lambda: supabase)

    result = character_queries.list_actor_identities()

    assert [row.id for row in result] == ["actor-active", "actor-ready"]


def test_set_active_actor_identity_rejects_training_actor(monkeypatch):
    training = _actor_identity_record(
        actor_id="actor-training",
        name="Training Actor",
        is_active=False,
        training_status="processing",
        training_phase="training",
        progress=40,
        provider_lora_id=None,
        updated_at="2026-05-21T12:00:00Z",
    )
    rows = [training.model_dump(mode="json")]
    supabase, client = _actor_identity_supabase(rows)
    monkeypatch.setattr(character_queries, "get_supabase", lambda: supabase)

    with pytest.raises(ValueError) as exc:
        character_queries.set_active_actor_identity("actor-training", correlation_id="test-corr")

    assert "ready" in str(exc.value).lower()
    assert client.calls == []


def test_set_active_actor_identity_switches_exactly_one_ready_actor(monkeypatch):
    active = _actor_identity_record(
        actor_id="actor-active",
        name="Active Actor",
        is_active=True,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-active",
        updated_at="2026-05-21T10:00:00Z",
    )
    ready = _actor_identity_record(
        actor_id="actor-ready",
        name="Ready Actor",
        is_active=False,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-ready",
        updated_at="2026-05-21T12:00:00Z",
    )
    rows = [active.model_dump(mode="json"), ready.model_dump(mode="json")]
    supabase, client = _actor_identity_supabase(rows)
    monkeypatch.setattr(character_queries, "get_supabase", lambda: supabase)

    result = character_queries.set_active_actor_identity("actor-ready", correlation_id="test-corr")

    assert result.id == "actor-ready"
    assert [row for row in rows if row["is_active"] is True][0]["id"] == "actor-ready"
    assert any(call[1].get("is_active") is False for call in client.calls)
    assert any(call[1].get("is_active") is True for call in client.calls)


def test_set_active_actor_identity_restores_previous_actor_on_activation_failure(monkeypatch):
    active = _actor_identity_record(
        actor_id="actor-active",
        name="Active Actor",
        is_active=True,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-active",
        updated_at="2026-05-21T10:00:00Z",
    )
    ready = _actor_identity_record(
        actor_id="actor-ready",
        name="Ready Actor",
        is_active=False,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-ready",
        updated_at="2026-05-21T12:00:00Z",
    )
    rows = [active.model_dump(mode="json"), ready.model_dump(mode="json")]
    supabase, _client = _actor_identity_supabase(rows, fail_activate=True)
    monkeypatch.setattr(character_queries, "get_supabase", lambda: supabase)

    with pytest.raises(RuntimeError):
        character_queries.set_active_actor_identity("actor-ready", correlation_id="test-corr")

    assert [row for row in rows if row["is_active"] is True][0]["id"] == "actor-active"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py::test_list_actor_identities_returns_sorted_roster tests/test_characters_feature.py::test_set_active_actor_identity_rejects_training_actor tests/test_characters_feature.py::test_set_active_actor_identity_switches_exactly_one_ready_actor tests/test_characters_feature.py::test_set_active_actor_identity_restores_previous_actor_on_activation_failure
```

Expected: FAIL with missing `list_actor_identities` and `set_active_actor_identity`.

- [ ] **Step 3: Import readiness helpers in queries**

In `app/features/characters/queries.py`, add this import below the existing logging import.

```python
from app.features.characters.actor_identity import actor_identity_training_ready, sort_actor_identity_roster
```

- [ ] **Step 4: Add row helpers and roster query**

In `app/features/characters/queries.py`, add this block after `get_active_actor_identity()`.

```python
def _identity_response_rows(response) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _identity_response_row(response) -> Optional[dict[str, Any]]:
    rows = _identity_response_rows(response)
    return rows[0] if rows else None


def list_actor_identities() -> list[ActorIdentityRecord]:
    try:
        response = (
            get_supabase()
            .client.table("actor_identities")
            .select("*")
            .order("updated_at", desc=True)
            .execute()
        )
    except APIError as exc:
        if _is_missing_actor_identities_table(exc):
            logger.warning("actor_identities_table_missing", error=str(exc))
            return []
        raise
    records = [ActorIdentityRecord.model_validate(row) for row in _identity_response_rows(response)]
    logger.info("actor_identity_roster_loaded", actor_identity_count=len(records))
    return sort_actor_identity_roster(records)


def get_actor_identity_by_id(actor_identity_id: str) -> Optional[ActorIdentityRecord]:
    try:
        response = (
            get_supabase()
            .client.table("actor_identities")
            .select("*")
            .eq("id", actor_identity_id)
            .maybe_single()
            .execute()
        )
    except APIError as exc:
        if _is_missing_actor_identities_table(exc):
            logger.warning("actor_identities_table_missing", error=str(exc))
            return None
        raise
    row = _identity_response_row(response)
    return ActorIdentityRecord.model_validate(row) if row else None
```

- [ ] **Step 5: Make active lookup tolerant of multiple active rows**

Replace the current `get_active_actor_identity()` implementation in `app/features/characters/queries.py` with this version.

```python
def get_active_actor_identity() -> Optional[ActorIdentityRecord]:
    try:
        response = (
            get_supabase()
            .client.table("actor_identities")
            .select("*")
            .eq("is_active", True)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
    except APIError as exc:
        if _is_missing_actor_identities_table(exc):
            logger.warning("actor_identities_table_missing", error=str(exc))
            return None
        raise
    row = _identity_response_row(response)
    return ActorIdentityRecord.model_validate(row) if row else None
```

- [ ] **Step 6: Add inactive training row creation**

In `app/features/characters/queries.py`, add this helper after `get_actor_identity_by_id(...)`.

```python
def create_actor_identity(
    *,
    name: str,
    training_images: list[str],
    consent_source: Optional[str] = None,
    correlation_id: Optional[str] = None,
    provider: str = "magnific",
    provider_training_task_id: Optional[str] = None,
    provider_lora_id: Optional[str] = None,
    provider_lora_name: Optional[str] = None,
    training_status: str = "not_started",
    training_phase: str = "not_started",
    training_progress_percent: int = 0,
    training_error: Optional[str] = None,
    training_started_at: Optional[str] = None,
    training_completed_at: Optional[str] = None,
    is_active: bool = False,
) -> ActorIdentityRecord:
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "id": str(uuid4()),
        "name": name.strip() or "Default Actor",
        "provider": provider.strip() or "magnific",
        "training_images": training_images,
        "consent_source": (consent_source or "").strip() or None,
        "training_status": training_status,
        "training_phase": training_phase,
        "training_progress_percent": int(training_progress_percent),
        "training_error": (training_error or "").strip() or None,
        "provider_lora_id": provider_lora_id,
        "provider_lora_name": provider_lora_name,
        "provider_training_task_id": provider_training_task_id,
        "is_active": bool(is_active),
        "created_at": now,
        "updated_at": now,
        "training_started_at": training_started_at,
        "training_completed_at": training_completed_at,
    }
    get_supabase().client.table("actor_identities").insert(payload).execute()
    logger.info(
        "actor_identity_created",
        correlation_id=correlation_id,
        actor_identity_id=payload["id"],
        is_active=payload["is_active"],
        training_phase=payload["training_phase"],
    )
    return ActorIdentityRecord.model_validate(payload)
```

- [ ] **Step 7: Deprecate training-time active upsert**

Replace the body of `upsert_active_actor_identity(...)` in `app/features/characters/queries.py` with this compatibility wrapper. Keep the function signature unchanged so old imports do not fail, but do not make it activate rows.

```python
    logger.warning(
        "upsert_active_actor_identity_deprecated",
        correlation_id=correlation_id,
        reason="training creates inactive ActorIdentity rows; activation uses set_active_actor_identity",
    )
    return create_actor_identity(
        name=name,
        provider=provider,
        provider_training_task_id=provider_training_task_id,
        provider_lora_id=provider_lora_id,
        provider_lora_name=provider_lora_name,
        training_status=training_status,
        training_phase=training_phase,
        training_progress_percent=training_progress_percent,
        training_images=training_images,
        consent_source=consent_source,
        training_error=training_error,
        training_started_at=training_started_at,
        training_completed_at=training_completed_at,
        correlation_id=correlation_id,
        is_active=False,
    )
```

- [ ] **Step 8: Extend training status updates with provider task id**

In `app/features/characters/queries.py`, add this optional parameter to `update_actor_training_status(...)`.

```python
    provider_training_task_id: Optional[str] = None,
```

Inside the same function, after the `provider_lora_name` block, add:

```python
    if provider_training_task_id:
        payload["provider_training_task_id"] = provider_training_task_id
```

- [ ] **Step 9: Add activation helper**

In `app/features/characters/queries.py`, add this function after `update_actor_training_status(...)`.

```python
def set_active_actor_identity(*, actor_identity_id: str, correlation_id: str) -> ActorIdentityRecord:
    target = get_actor_identity_by_id(actor_identity_id)
    if target is None:
        logger.warning(
            "actor_identity_activation_rejected_missing",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
        )
        raise ValueError("ActorIdentity not found")
    if not actor_identity_training_ready(target):
        logger.warning(
            "actor_identity_activation_rejected_not_ready",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
            training_status=target.training_status,
            training_phase=target.training_phase,
            training_progress_percent=target.training_progress_percent,
        )
        raise ValueError("Only ready ActorIdentity rows can be activated")

    previous = get_active_actor_identity()
    now = datetime.now(timezone.utc).isoformat()
    client = get_supabase().client
    cleared = False
    try:
        client.table("actor_identities").update(
            {"is_active": False, "updated_at": now}
        ).eq("is_active", True).execute()
        cleared = True
        client.table("actor_identities").update(
            {"is_active": True, "updated_at": now}
        ).eq("id", target.id).execute()
    except Exception:
        restored = False
        if cleared and previous is not None:
            try:
                client.table("actor_identities").update(
                    {"is_active": True, "updated_at": datetime.now(timezone.utc).isoformat()}
                ).eq("id", previous.id).execute()
                restored = True
            except Exception as restore_exc:
                logger.error(
                    "actor_identity_activation_restore_failed",
                    correlation_id=correlation_id,
                    previous_actor_identity_id=previous.id,
                    error=str(restore_exc),
                )
        logger.exception(
            "actor_identity_activation_failed",
            correlation_id=correlation_id,
            target_actor_identity_id=target.id,
            previous_actor_identity_id=previous.id if previous else None,
            restored=restored,
        )
        raise

    refreshed = get_actor_identity_by_id(target.id)
    logger.info(
        "actor_identity_active_switched",
        correlation_id=correlation_id,
        actor_identity_id=target.id,
        previous_actor_identity_id=previous.id if previous else None,
    )
    return refreshed or target.model_copy(update={"is_active": True})
```

- [ ] **Step 10: Run focused tests**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py::test_list_actor_identities_returns_sorted_roster tests/test_characters_feature.py::test_set_active_actor_identity_rejects_training_actor tests/test_characters_feature.py::test_set_active_actor_identity_switches_exactly_one_ready_actor tests/test_characters_feature.py::test_set_active_actor_identity_restores_previous_actor_on_activation_failure
```

Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add app/features/characters/queries.py tests/test_characters_feature.py
git commit -m "feat: add actor identity roster activation helper"
```

## Task 3: Update Settings Routes And Training Semantics

**Files:**
- Modify: `app/features/characters/handlers.py`
- Test: `tests/test_characters_feature.py`

- [ ] **Step 1: Add failing route tests**

Add these tests to `tests/test_characters_feature.py`.

```python
def test_actor_settings_page_renders_ready_selector_and_full_roster(monkeypatch):
    active = _actor_identity_record(
        actor_id="actor-active",
        name="Active Actor",
        is_active=True,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-active",
        updated_at="2026-05-21T10:00:00Z",
    )
    ready = _actor_identity_record(
        actor_id="actor-ready",
        name="Ready Actor",
        is_active=False,
        training_status="completed",
        training_phase="ready",
        progress=100,
        provider_lora_id="lora-ready",
        updated_at="2026-05-21T12:00:00Z",
    )
    training = _actor_identity_record(
        actor_id="actor-training",
        name="Training Actor",
        is_active=False,
        training_status="processing",
        training_phase="training",
        progress=40,
        provider_lora_id=None,
        updated_at="2026-05-21T13:00:00Z",
    )
    monkeypatch.setattr(character_queries, "refresh_active_actor_identity_status", lambda correlation_id: active)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: active)
    monkeypatch.setattr(character_queries, "list_actor_identities", lambda: [active, ready, training])

    response = TestClient(app, base_url="http://localhost").get("/settings/actor")

    assert response.status_code == 200
    assert 'name="actor_identity_id"' in response.text
    assert 'value="actor-active" selected' in response.text
    assert 'value="actor-ready"' in response.text
    assert 'value="actor-training"' not in response.text
    assert "Training Actor" in response.text


def test_actor_settings_active_post_calls_activation_helper(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        character_queries,
        "set_active_actor_identity",
        lambda **kwargs: captured.update(kwargs) or _actor_identity_record(
            actor_id=kwargs["actor_identity_id"],
            name="Ready Actor",
            is_active=True,
            training_status="completed",
            training_phase="ready",
            progress=100,
            provider_lora_id="lora-ready",
            updated_at="2026-05-21T12:00:00Z",
        ),
    )

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor/active",
        data={"actor_identity_id": "actor-ready"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings/actor?active_actor_updated=1"
    assert captured["actor_identity_id"] == "actor-ready"
    assert captured["correlation_id"]


def test_actor_settings_active_post_rejects_non_ready_actor(monkeypatch):
    def reject(**_kwargs):
        raise ValueError("Only ready ActorIdentity rows can be activated")

    monkeypatch.setattr(character_queries, "set_active_actor_identity", reject)

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor/active",
        data={"actor_identity_id": "actor-training"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert "Only ready ActorIdentity rows can be activated" in response.text


def test_upload_actor_identity_creates_inactive_training_row(monkeypatch):
    uploaded = []
    training_payloads = []
    created = []
    status_updates = []

    class _FakeStorage:
        def upload_image(self, **kwargs):
            uploaded.append(kwargs)
            return {
                "url": f"https://cdn.example.com/{kwargs['file_name']}",
                "storage_key": f"images/{kwargs['file_name']}",
            }

    class _FakeMagnific:
        def train_character_lora(self, **kwargs):
            training_payloads.append(kwargs)
            from app.adapters.magnific_client import MagnificTrainingStatus

            return MagnificTrainingStatus(
                provider_training_task_id="task-123",
                provider_lora_id="lora-123",
                provider_lora_name="ayra_actor_test",
                training_status="queued",
                training_phase="queued",
                training_progress_percent=10,
            )

    def fake_create(**kwargs):
        created.append(kwargs)
        return _actor_identity_record(
            actor_id="actor-new",
            name=kwargs["name"],
            is_active=kwargs["is_active"],
            training_status=kwargs["training_status"],
            training_phase=kwargs["training_phase"],
            progress=kwargs["training_progress_percent"],
            provider_lora_id=kwargs["provider_lora_id"],
            updated_at="2026-05-21T12:00:00Z",
        )

    monkeypatch.setattr(character_handlers, "get_storage_client", lambda: _FakeStorage())
    monkeypatch.setattr(character_handlers.character_queries, "create_actor_identity", fake_create)
    monkeypatch.setattr(character_handlers.character_queries, "update_actor_training_status", lambda **kwargs: status_updates.append(kwargs))
    monkeypatch.setattr(character_handlers.character_queries, "get_actor_identity_by_id", lambda actor_identity_id: None)
    monkeypatch.setattr("app.adapters.magnific_client.get_magnific_client", lambda: _FakeMagnific())

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor",
        data={
            "name": "AYRA Actor Identity",
            "quality": "high",
            "gender": "woman",
            "consent_source": "operator",
            "description": "Test run",
        },
        files=[
            ("training_images", ("img1.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img2.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img3.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img4.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img5.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img6.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img7.png", io.BytesIO(_png_bytes()), "image/png")),
            ("training_images", ("img8.png", io.BytesIO(_png_bytes()), "image/png")),
        ],
        follow_redirects=False,
    )

    assert response.status_code in {200, 303}, response.text
    assert len(uploaded) == 8
    assert len(training_payloads) == 1
    assert created[0]["is_active"] is False
    assert status_updates[0]["actor_identity_id"] == "actor-new"
    assert status_updates[0]["provider_training_task_id"] == "task-123"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py::test_actor_settings_page_renders_ready_selector_and_full_roster tests/test_characters_feature.py::test_actor_settings_active_post_calls_activation_helper tests/test_characters_feature.py::test_actor_settings_active_post_rejects_non_ready_actor tests/test_characters_feature.py::test_upload_actor_identity_creates_inactive_training_row
```

Expected: FAIL because the route context, activation route, and inactive training flow do not exist.

- [ ] **Step 3: Import training readiness in handlers**

In `app/features/characters/handlers.py`, replace the current actor identity import line with:

```python
from app.features.characters.actor_identity import (
    actor_identity_is_ready,
    actor_identity_training_ready,
    passed_manual_gate,
    pending_manual_gate,
)
```

- [ ] **Step 4: Add settings-page context helper**

In `app/features/characters/handlers.py`, add this function after `_actor_identity_context(...)`.

```python
def _actor_settings_context(*, request: Request, correlation_id: str) -> dict:
    actor = _actor_identity_context(correlation_id=correlation_id)
    roster_error = None
    try:
        actors = character_queries.list_actor_identities()
    except Exception as exc:  # noqa: BLE001 - settings page must keep training form available
        actors = []
        roster_error = "Actor roster could not be loaded. Training form is still available."
        logger.warning(
            "actor_identity_roster_load_failed",
            correlation_id=correlation_id,
            error=str(exc),
        )
    ready_actors = [row for row in actors if actor_identity_training_ready(row)]
    return {
        "request": request,
        "actor": actor,
        "actors": actors,
        "ready_actors": ready_actors,
        "actor_roster_error": roster_error,
        "active_actor_updated": request.query_params.get("active_actor_updated") == "1",
    }
```

- [ ] **Step 5: Update GET `/settings/actor`**

Replace the current `actor_settings(...)` route in `app/features/characters/handlers.py` with:

```python
@router.get("/actor")
def actor_settings(request: Request):
    correlation_id = f"actor_settings_get_{uuid4()}"
    return templates.TemplateResponse(
        "settings/actor.html",
        _actor_settings_context(request=request, correlation_id=correlation_id),
    )
```

- [ ] **Step 6: Add POST `/settings/actor/active`**

In `app/features/characters/handlers.py`, add this route after `actor_settings(...)`.

```python
@router.post("/actor/active")
def activate_actor_identity(
    request: Request,
    actor_identity_id: str = Form(...),
):
    correlation_id = str(uuid4())
    try:
        character_queries.set_active_actor_identity(
            actor_identity_id=actor_identity_id,
            correlation_id=correlation_id,
        )
    except ValueError as exc:
        logger.warning(
            "actor_identity_activation_rejected",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
            error=str(exc),
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "actor_identity_activation_route_failed",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
        )
        raise HTTPException(
            status_code=500,
            detail="ActorIdentity activation failed; the previous active actor was restored when possible.",
        ) from exc
    return RedirectResponse(url="/settings/actor?active_actor_updated=1", status_code=303)
```

- [ ] **Step 7: Update `/settings/actor` training branch**

Inside `train_actor_identity(...)`, replace the entire `if request.url.path == "/settings/actor":` branch with this code.

```python
    if request.url.path == "/settings/actor":
        from app.adapters import magnific_client as magnific_adapter

        identity = character_queries.create_actor_identity(
            name=name,
            provider="magnific",
            provider_training_task_id=None,
            provider_lora_id=None,
            provider_lora_name=None,
            training_status="queued",
            training_phase="queued",
            training_progress_percent=10,
            training_images=training.images,
            consent_source=training.consent_source,
            training_error=None,
            training_started_at=None,
            training_completed_at=None,
            correlation_id=correlation_id,
            is_active=False,
        )
        status = magnific_adapter.get_magnific_client().train_character_lora(
            name=name,
            quality=quality,
            gender=gender,
            image_urls=training.images,
            correlation_id=correlation_id,
            description=description or None,
        )
        character_queries.update_actor_training_status(
            actor_identity_id=identity.id,
            provider_training_task_id=status.provider_training_task_id,
            provider_lora_id=status.provider_lora_id,
            provider_lora_name=status.provider_lora_name,
            training_status=str(status.training_status or status.raw_status),
            training_phase=str(status.training_phase or status.phase),
            training_progress_percent=int(status.training_progress_percent or status.progress_percent or 0),
            training_error=status.training_error,
            correlation_id=correlation_id,
        )
        refreshed = character_queries.get_actor_identity_by_id(identity.id)
        identity = refreshed or identity
        redirect_url = "/settings/actor"
```

- [ ] **Step 8: Update `/settings/character/actor` legacy training branch**

Inside `train_actor_identity(...)`, replace the first call in the `else:` branch from `character_queries.upsert_active_actor_identity(...)` to `character_queries.create_actor_identity(...)` and add `is_active=False`.

```python
        identity = character_queries.create_actor_identity(
            name=name,
            training_images=training.images,
            consent_source=training.consent_source,
            correlation_id=correlation_id,
            is_active=False,
        )
```

Keep the existing provider-name generation, `submit_character_training(...)`, and `mark_actor_training_submitted(...)` calls below that block.

- [ ] **Step 9: Run focused route tests**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py::test_actor_settings_page_renders_ready_selector_and_full_roster tests/test_characters_feature.py::test_actor_settings_active_post_calls_activation_helper tests/test_characters_feature.py::test_actor_settings_active_post_rejects_non_ready_actor tests/test_characters_feature.py::test_upload_actor_identity_creates_inactive_training_row tests/test_characters_feature.py::test_upload_actor_identity_submits_training_set tests/test_actor_identity_training.py::test_actor_training_endpoint_uploads_public_urls_before_magnific
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add app/features/characters/handlers.py tests/test_characters_feature.py tests/test_actor_identity_training.py
git commit -m "feat: separate actor training from active selection"
```

## Task 4: Replace Actor Settings UI With Selector And Roster

**Files:**
- Modify: `templates/settings/actor.html`
- Test: `tests/test_characters_feature.py`

- [ ] **Step 1: Run settings-page test before template replacement**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py::test_actor_settings_page_renders_ready_selector_and_full_roster
```

Expected: FAIL until the template contains the active selector and roster.

- [ ] **Step 2: Replace actor settings template**

Replace the full contents of `templates/settings/actor.html` with this template.

```html
{% extends "settings/base.html" %}

{% block settings_content %}
<div class="flex items-start justify-between gap-4">
  <div>
    <h1 class="text-2xl font-semibold text-gray-900">Actor Identity</h1>
    <p class="mt-2 max-w-3xl text-sm text-gray-600">
      Select the global ActorIdentity used by new `character_consistency` batches. Training creates a new actor row, but it does not change the active selection.
    </p>
  </div>
  <a href="/settings/character" class="rounded-full border border-gray-200 px-3 py-2 text-xs font-medium text-gray-600 hover:border-[#E58434]/40 hover:text-[#E58434]">
    Reference images
  </a>
</div>

{% if active_actor_updated %}
<div class="mt-5 rounded-xl border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
  Active actor updated. Future `character_consistency` batches will use this ActorIdentity.
</div>
{% endif %}

{% if actor_roster_error %}
<div class="mt-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
  {{ actor_roster_error }}
</div>
{% endif %}

<section class="mt-6 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
  <div class="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
    <div>
      <p class="text-xs font-semibold uppercase tracking-[0.2em] text-gray-500">Current Active Actor</p>
      {% if actor %}
      <h2 class="mt-1 text-xl font-semibold text-gray-900">{{ actor.name }}</h2>
      <dl class="mt-3 grid grid-cols-1 gap-2 text-sm text-gray-600 sm:grid-cols-2">
        <div><dt class="inline font-medium text-gray-800">Provider</dt> <dd class="inline">{{ actor.provider }}</dd></div>
        <div><dt class="inline font-medium text-gray-800">Status</dt> <dd class="inline">{{ actor.training_status }}</dd></div>
        <div><dt class="inline font-medium text-gray-800">Phase</dt> <dd class="inline">{{ actor.training_phase }}</dd></div>
        <div><dt class="inline font-medium text-gray-800">Progress</dt> <dd class="inline">{{ actor.training_progress_percent }}%</dd></div>
        <div><dt class="inline font-medium text-gray-800">LoRA ID</dt> <dd class="inline break-all">{{ actor.provider_lora_id or "pending" }}</dd></div>
        <div><dt class="inline font-medium text-gray-800">Task ID</dt> <dd class="inline break-all">{{ actor.provider_training_task_id or "pending" }}</dd></div>
      </dl>
      {% if actor.training_error %}
      <p class="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
        {{ actor.training_error }}
      </p>
      {% endif %}
      <p class="mt-3 text-xs text-gray-500">Consent source: {{ actor.consent_source or "not recorded" }}</p>
      <p class="mt-1 text-xs text-gray-500">Uploaded training images: {{ actor.training_images | length }}</p>
      {% if not (actor.training_phase == "ready" and actor.training_progress_percent == 100 and actor.provider_lora_id) %}
      <p class="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
        `character_consistency` is blocked until a ready actor is selected.
      </p>
      {% endif %}
      {% else %}
      <h2 class="mt-1 text-xl font-semibold text-gray-900">No active actor selected</h2>
      <p class="mt-3 text-sm text-gray-600">
        Select a ready actor below. If no ready actor exists, train one and wait for it to finish.
      </p>
      {% endif %}
    </div>

    <form method="post" action="/settings/actor/active" class="w-full rounded-xl bg-gray-50 p-4 lg:max-w-sm">
      <label class="block">
        <span class="text-sm font-medium text-gray-800">Active actor</span>
        <select
          name="actor_identity_id"
          {% if ready_actors | length == 0 %}disabled{% endif %}
          class="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-[#E58434] focus:outline-none focus:ring-2 focus:ring-[#E58434]/20 disabled:bg-gray-100 disabled:text-gray-400"
        >
          {% if ready_actors | length == 0 %}
          <option value="">No ready actors available</option>
          {% elif actor and not (actor.training_phase == "ready" and actor.training_progress_percent == 100 and actor.provider_lora_id) %}
          <option value="">Choose a ready actor</option>
          {% endif %}
          {% for row in ready_actors %}
          <option value="{{ row.id }}" {% if actor and actor.id == row.id %}selected{% endif %}>
            {{ row.name }}{% if row.provider_lora_id %} - {{ row.provider_lora_id }}{% endif %}
          </option>
          {% endfor %}
        </select>
      </label>
      <p class="mt-2 text-xs text-gray-500">
        Only ready actors can be selected. Training and failed actors remain visible in the roster below.
      </p>
      <button
        type="submit"
        {% if ready_actors | length == 0 %}disabled{% endif %}
        class="mt-4 rounded-lg bg-[#E58434] px-4 py-2 text-sm font-medium text-white hover:bg-[#d27a2f] focus:outline-none focus:ring-2 focus:ring-[#E58434]/30 disabled:bg-gray-300"
      >
        Set active
      </button>
    </form>
  </div>
</section>

<section class="mt-6 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
  <div class="flex items-center justify-between gap-4">
    <div>
      <h2 class="text-lg font-semibold text-gray-900">Actor roster</h2>
      <p class="mt-1 text-sm text-gray-600">Active first, then ready actors, training actors, and failed actors.</p>
    </div>
    <span class="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-600">{{ actors | length }} total</span>
  </div>

  {% if actors | length == 0 %}
  <p class="mt-4 rounded-xl border border-dashed border-gray-300 px-4 py-4 text-sm text-gray-600">
    No ActorIdentity rows exist yet. Train a new actor below.
  </p>
  {% else %}
  <div class="mt-4 divide-y divide-gray-100">
    {% for row in actors %}
    {% set row_ready = row.training_phase == "ready" and row.training_progress_percent == 100 and row.provider_lora_id %}
    <article class="py-4">
      <div class="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="text-sm font-semibold text-gray-900">{{ row.name }}</h3>
            {% if row.is_active %}
            <span class="rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-green-700">Active</span>
            {% elif row_ready %}
            <span class="rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-blue-700">Ready</span>
            {% elif row.training_status in ["failed", "error"] or row.training_phase in ["failed", "error"] %}
            <span class="rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-red-700">Failed</span>
            {% else %}
            <span class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber-700">Training</span>
            {% endif %}
          </div>
          <p class="mt-1 text-xs text-gray-500">
            LoRA: {{ row.provider_lora_id or row.provider_lora_name or "pending" }}
          </p>
          {% if row.training_error %}
          <p class="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{{ row.training_error }}</p>
          {% endif %}
        </div>
        <div class="min-w-[12rem]">
          <div class="h-2 overflow-hidden rounded-full bg-gray-200">
            <div class="h-full rounded-full bg-[#E58434]" style="width: {{ row.training_progress_percent }}%"></div>
          </div>
          <p class="mt-2 text-xs text-gray-500">
            {{ row.training_phase }} - {{ row.training_progress_percent }}%
          </p>
        </div>
      </div>
    </article>
    {% endfor %}
  </div>
  {% endif %}
</section>

<section class="mt-8 rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
  <h2 class="text-lg font-semibold text-gray-900">Train new actor</h2>
  <p class="mt-2 text-sm text-gray-600">
    This submits a Magnific character LoRA training job. The new actor starts inactive and will not replace the current active actor.
  </p>

  <form method="post" action="/settings/actor" enctype="multipart/form-data" class="mt-6 space-y-5">
    <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
      <label class="block">
        <span class="text-sm font-medium text-gray-800">Name</span>
        <input type="text" name="name" value="AYRA Actor Identity" class="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-[#E58434] focus:outline-none focus:ring-2 focus:ring-[#E58434]/20" />
      </label>
      <label class="block">
        <span class="text-sm font-medium text-gray-800">Quality</span>
        <select name="quality" class="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-[#E58434] focus:outline-none focus:ring-2 focus:ring-[#E58434]/20">
          <option value="high" selected>High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </label>
      <label class="block">
        <span class="text-sm font-medium text-gray-800">Gender</span>
        <select name="gender" class="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-[#E58434] focus:outline-none focus:ring-2 focus:ring-[#E58434]/20">
          <option value="woman" selected>Woman</option>
          <option value="man">Man</option>
          <option value="non-binary">Non-binary</option>
        </select>
      </label>
      <label class="block">
        <span class="text-sm font-medium text-gray-800">Consent source</span>
        <input type="text" name="consent_source" value="Operator-provided reference set" class="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-[#E58434] focus:outline-none focus:ring-2 focus:ring-[#E58434]/20" />
      </label>
    </div>

    <label class="block">
      <span class="text-sm font-medium text-gray-800">Description</span>
      <textarea name="description" rows="3" class="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-[#E58434] focus:outline-none focus:ring-2 focus:ring-[#E58434]/20" placeholder="Optional training description or guardrails."></textarea>
    </label>

    <label class="block">
      <span class="text-sm font-medium text-gray-800">Training images</span>
      <input type="file" name="training_images" accept="image/png,image/jpeg" multiple required class="mt-1 w-full text-sm" />
      <p class="mt-2 text-xs text-gray-500">Upload between 8 and 20 PNG or JPEG images. They are uploaded to R2 first and then sent to Magnific as public URLs.</p>
    </label>

    <div class="rounded-xl bg-amber-50 p-4 text-sm text-amber-900">
      Training completion does not auto-switch the active actor. Select the ready actor above when you want future batches to use it.
    </div>

    <button type="submit" class="rounded-lg bg-[#E58434] px-4 py-2 text-sm font-medium text-white hover:bg-[#d27a2f] focus:outline-none focus:ring-2 focus:ring-[#E58434]/30">
      Start training
    </button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 3: Run focused settings-page tests**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py::test_actor_settings_page_renders_ready_selector_and_full_roster tests/test_characters_feature.py::test_actor_settings_page_renders_active_actor
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add templates/settings/actor.html tests/test_characters_feature.py
git commit -m "feat: add actor settings selector and roster"
```

## Task 5: Update Character Consistency Blocked Copy

**Files:**
- Modify: `app/features/batches/queries.py`
- Modify: `templates/batches/list.html`
- Test: `tests/test_character_consistency_mode.py`

- [ ] **Step 1: Update failing blocked-copy assertion**

In `tests/test_character_consistency_mode.py`, replace the final assertion in `test_character_consistency_requires_ready_actor_identity_for_new_batches(...)` with:

```python
    assert "/settings/actor" in exc.value.message
    assert "select a ready actor" in exc.value.message.lower()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
python3 -m pytest -q tests/test_character_consistency_mode.py::test_character_consistency_requires_ready_actor_identity_for_new_batches
```

Expected: FAIL because the current message still says to upload training images at `/settings/character`.

- [ ] **Step 3: Update backend validation copy**

In `app/features/batches/queries.py`, replace the `ValidationError(...)` message inside the `creation_mode == "character_consistency"` block with:

```python
            raise ValidationError(
                "Cannot create a Character Consistency batch: no ready active ActorIdentity is selected. "
                "Open /settings/actor, select a ready actor, then create the batch again.",
                {"creation_mode": "character_consistency", "settings_url": "/settings/actor"},
            )
```

- [ ] **Step 4: Update batch form helper copy**

In `templates/batches/list.html`, replace this copy:

```html
                            Character Consistency uses the saved reference character and full Veo 3.1.
                            <a href="/settings/character" class="text-[#E58434] underline">Manage character</a>
```

With:

```html
                            Character Consistency uses the selected ready ActorIdentity and full Veo 3.1.
                            <a href="/settings/actor" class="text-[#E58434] underline">Choose actor</a>
```

Then replace this copy:

```html
                            Uses the active character snapshot from Settings. Scenes are varied per post type during prompt generation.
```

With:

```html
                            Uses the active ready ActorIdentity from Settings. If blocked, choose a ready actor at /settings/actor.
```

- [ ] **Step 5: Run focused blocked-copy test**

Run:

```bash
python3 -m pytest -q tests/test_character_consistency_mode.py::test_character_consistency_requires_ready_actor_identity_for_new_batches
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/features/batches/queries.py templates/batches/list.html tests/test_character_consistency_mode.py
git commit -m "fix: point character consistency block to actor selector"
```

## Task 6: Final Regression And Runtime Verification

**Files:**
- Verify: `app/features/characters/actor_identity.py`
- Verify: `app/features/characters/queries.py`
- Verify: `app/features/characters/handlers.py`
- Verify: `templates/settings/actor.html`
- Verify: `app/features/batches/queries.py`
- Verify: `templates/batches/list.html`
- Verify: `tests/test_characters_feature.py`
- Verify: `tests/test_character_consistency_mode.py`

- [ ] **Step 1: Run focused actor and batch tests**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py tests/test_actor_identity_training.py tests/test_character_consistency_mode.py
```

Expected: PASS.

- [ ] **Step 2: Run surrounding ActorIdentity regression tests**

Run:

```bash
python3 -m pytest -q tests/test_magnific_actor_identity.py tests/test_actor_identity_scene_reference.py tests/test_video_duration_routing.py tests/test_veo_prompt_contract.py
```

Expected: PASS.

- [ ] **Step 3: Run import smoke for the modified app surface**

Run:

```bash
python3 - <<'PY'
from app.features.characters.actor_identity import actor_identity_is_ready, actor_identity_training_ready
from app.features.characters.queries import get_active_actor_identity, list_actor_identities, set_active_actor_identity
from app.features.characters.handlers import router
from app.features.batches.queries import create_batch
print("actor selection imports ok")
PY
```

Expected output:

```text
actor selection imports ok
```

- [ ] **Step 4: Start local app for browser verification**

Run:

```bash
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: server starts and logs application startup without import errors.

- [ ] **Step 5: Verify actor settings page in browser**

Open:

```text
http://127.0.0.1:8000/settings/actor
```

Expected observations:
- Page renders without a 500.
- The top area shows current active actor or a clear no-active message.
- The `Active actor` selector contains ready actors only.
- In-progress and failed actors appear in the roster but not as selector options.
- Training form copy says new training does not replace the active actor.

- [ ] **Step 6: Verify batch form copy in browser**

Open:

```text
http://127.0.0.1:8000/batches
```

Expected observations:
- Batch Mode includes `Character Consistency`.
- The helper copy links to `/settings/actor`.
- The character consistency hint tells the operator to choose a ready actor if blocked.

- [ ] **Step 7: Commit verification-only docs if no code changed**

If verification does not require changes, no commit is needed. If a small template/test fix was needed during verification, commit it with:

```bash
git add app/features/characters/actor_identity.py app/features/characters/queries.py app/features/characters/handlers.py templates/settings/actor.html app/features/batches/queries.py templates/batches/list.html tests/test_characters_feature.py tests/test_actor_identity_training.py tests/test_character_consistency_mode.py
git commit -m "fix: stabilize actor identity selection verification"
```

## Pass/Fail Criteria

Pass:
- `/settings/actor` lets the operator choose one specific ready actor.
- Only ready actors appear in the selector.
- All actors appear in the roster with visible Active, Ready, Training, or Failed state.
- Training a new actor creates an inactive ActorIdentity row.
- Training completion does not auto-switch `is_active`.
- `set_active_actor_identity(...)` clears previous active rows and activates exactly one ready target.
- Activation rejects missing, training, or failed actors with HTTP 422 on the route.
- If activation fails after clearing active rows, the previous active row is restored when possible.
- New `character_consistency` batches still use `get_active_actor_identity()` and block unless the active actor is ready.
- Blocked batch copy points to `/settings/actor` and tells the operator to select a ready actor.

Fail:
- `/settings/actor` training changes the active row.
- A training or failed actor can be selected.
- The selector includes in-progress actors.
- Multiple rows remain active after activation.
- Batch creation introduces a per-batch actor override.
- The batch blocked message tells the operator only to train or upload images.

## Self-Review

- Spec coverage: manual active selection, exactly one active actor, persistent global source of truth, many-actor roster, non-ready visibility without selection, training without auto-switch, dedicated activation route, activation failure semantics, blocked-copy update, and focused tests are mapped to tasks.
- Placeholder scan: no placeholder markers, deferred implementation wording, or unspecified validation steps remain in this plan.
- Type consistency: `actor_identity_training_ready`, `actor_identity_is_ready`, `sort_actor_identity_roster`, `list_actor_identities`, `get_actor_identity_by_id`, `create_actor_identity`, `set_active_actor_identity`, and `update_actor_training_status(... provider_training_task_id=...)` are defined before use.
