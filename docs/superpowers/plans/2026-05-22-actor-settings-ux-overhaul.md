# Actor Settings UX Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make actor selection and LoRA training understandable and low-friction by turning `/settings/actor` into the single canonical ActorIdentity surface, demoting legacy snapshot tools, and adding accessible, branded, inline recovery states.

**Architecture:** Keep the existing FastAPI + Jinja + HTMX stack and preserve the current Lippe Lift brand layer. Do not add a new frontend framework or state system. Reshape the UX by tightening the view-model in `app/features/characters/handlers.py`, extracting one HTMX partial for the live training card, and simplifying the settings information architecture so the user sees one primary workflow: current actor, readiness, train, then legacy fallback.

**Tech Stack:** FastAPI, Jinja2, HTMX, existing `static/css/brand.css`, pytest, FastAPI `TestClient`. No new dependencies.

**Scope Budget:** `{files: 6, LOC/file: <=350 target and <=550 hard, deps: 0}`

---

## Context-Zero

- Current canonical runtime files:
  - [app/features/characters/handlers.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/app/features/characters/handlers.py)
  - [templates/settings/actor.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/settings/actor.html)
  - [templates/settings/character.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/settings/character.html)
  - [templates/settings/base.html](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/templates/settings/base.html)
  - [static/css/brand.css](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/static/css/brand.css)
  - [tests/test_characters_feature.py](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/tests/test_characters_feature.py)
- Existing domain contract:
  - `ActorSettingsSurface` is the first page where training starts and progress is observed.
  - `TrainingProgressDisplay` must show phase and progress.
  - Legacy `CharacterSnapshot` batches stay valid, but new work should route through `ActorIdentity`.
- Current UX defects to fix:
  - Training exists on both `/settings/actor` and `/settings/character`.
  - `/settings/character/actor/poll` returns the full page template instead of a partial.
  - Form errors raise HTTP failures instead of rendering inline recovery.
  - Settings templates bypass much of the Lippe Lift brand layer and omit nav/progress accessibility semantics.

## File Structure

| File | Action | Responsibility |
| --- | --- | --- |
| `app/features/characters/handlers.py` | Modify | Canonical settings view-models, inline form error handling, partial polling response |
| `templates/settings/base.html` | Modify | Correct settings tab active state and `aria-current` semantics |
| `templates/settings/actor.html` | Modify | Canonical actor page layout, inline error surfaces, branded single-path workflow |
| `templates/settings/character.html` | Modify | Legacy-only snapshot maintenance surface with clear handoff to `/settings/actor` |
| `templates/settings/_actor_training_status.html` | Create | HTMX partial for the live training/readiness card |
| `tests/test_characters_feature.py` | Modify | Regression coverage for canonical flow, partial polling, inline errors, and nav semantics |

## Capability Map

1. One canonical actor settings page for selection and training.
2. Legacy character page kept available, but clearly secondary.
3. Accessible settings navigation state.
4. Accessible progress state with semantic progressbar markup.
5. Inline form recovery for activation and training errors.
6. HTMX polling that updates only the training card.
7. Branded settings surface aligned to the Lippe Lift visual layer.

## Pass / Fail Criteria

- `/settings/actor` is the only page containing the ActorIdentity training form.
- `/settings/character` no longer contains the ActorIdentity training form.
- `/settings/base.html` marks the current settings tab with `aria-current="page"`.
- The training status card can be polled via HTMX without returning a full page template.
- Posting too few training images or attempting to activate a non-ready actor re-renders the page with inline error copy instead of a raw HTTP error page.
- Training progress exposes `role="progressbar"` with readable value state.
- Focused regression tests pass.

---

### Task 1: Make `/settings/actor` The Canonical Workflow

**Files:**
- Modify: `templates/settings/actor.html`
- Modify: `templates/settings/character.html`
- Modify: `templates/settings/base.html`
- Test: `tests/test_characters_feature.py`

- [ ] **Step 1: Write the failing page-shape regressions**

Add these tests to `tests/test_characters_feature.py`.

```python
def test_actor_settings_is_canonical_training_surface(monkeypatch):
    active = ActorIdentityRecord.model_validate(_actor_row("active", is_active=True))
    monkeypatch.setattr(character_queries, "sync_actor_identity_roster_from_provider", lambda correlation_id: None)
    monkeypatch.setattr(character_queries, "refresh_active_actor_identity_status", lambda correlation_id: active)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: active)
    monkeypatch.setattr(character_queries, "list_actor_identities", lambda: [active])
    monkeypatch.setattr(character_queries, "refresh_actor_identity_roster_statuses", lambda identities, correlation_id: identities)

    response = TestClient(app, base_url="http://localhost").get("/settings/actor")

    assert response.status_code == 200
    assert "Train new actor" in response.text
    assert 'action="/settings/actor"' in response.text


def test_character_settings_hides_actor_training_form_and_links_to_actor_settings(monkeypatch):
    monkeypatch.setattr(character_queries, "get_active_character", lambda: None)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: None)
    monkeypatch.setattr(character_queries, "refresh_actor_identity_roster_statuses", lambda identities, correlation_id: [])
    monkeypatch.setattr(character_queries, "list_actor_identities", lambda: [])

    response = TestClient(app, base_url="http://localhost").get("/settings/character")

    assert response.status_code == 200
    assert "Train or Replace Active ActorIdentity" not in response.text
    assert 'href="/settings/actor"' in response.text
    assert "Actor Identity lives in its own settings flow" in response.text


def test_settings_nav_marks_current_page(monkeypatch):
    monkeypatch.setattr(character_queries, "sync_actor_identity_roster_from_provider", lambda correlation_id: None)
    monkeypatch.setattr(character_queries, "refresh_active_actor_identity_status", lambda correlation_id: None)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: None)
    monkeypatch.setattr(character_queries, "list_actor_identities", lambda: [])
    monkeypatch.setattr(character_queries, "refresh_actor_identity_roster_statuses", lambda identities, correlation_id: [])

    response = TestClient(app, base_url="http://localhost").get("/settings/actor")

    assert 'aria-current="page"' in response.text
```

Run: `python3 -m pytest -q tests/test_characters_feature.py -k "canonical_training_surface or hides_actor_training_form or settings_nav_marks_current_page"`

Expected: FAIL because the legacy page still contains actor training and the nav does not mark the active page.

- [ ] **Step 2: Add explicit settings-tab state in the base template**

Update `templates/settings/base.html` so the active tab is driven by a `settings_section` context key.

```jinja2
{% set settings_section = settings_section or "character" %}

<nav class="mb-8 flex gap-3 border-b border-[var(--lippe-border)] pb-4 text-sm" aria-label="Settings sections">
  <a
    href="/settings/actor"
    class="rounded-full px-4 py-2 font-medium transition {% if settings_section == 'actor' %}bg-[var(--lippe-orange)] text-white{% else %}text-[var(--lippe-deep-blue)] hover:bg-white/70{% endif %}"
    {% if settings_section == "actor" %}aria-current="page"{% endif %}
  >
    Actor Identity
  </a>
  <a
    href="/settings/character"
    class="rounded-full px-4 py-2 font-medium transition {% if settings_section == 'character' %}bg-[var(--lippe-orange)] text-white{% else %}text-[var(--lippe-deep-blue)] hover:bg-white/70{% endif %}"
    {% if settings_section == "character" %}aria-current="page"{% endif %}
  >
    Legacy Character Snapshot
  </a>
</nav>
```

- [ ] **Step 3: Remove the duplicate ActorIdentity form from the legacy page**

Replace the current ActorIdentity training section in `templates/settings/character.html` with a short guidance banner and keep only the legacy snapshot tooling.

```jinja2
<section class="brand-panel rounded-[1.75rem] p-5">
  <p class="brand-section-eyebrow">Actor Identity</p>
  <h1 class="mt-2 text-2xl font-brand-heading font-bold text-[var(--lippe-deep-blue)]">Legacy Character Snapshot</h1>
  <p class="mt-3 max-w-3xl text-sm text-[color:rgba(28,39,64,0.78)]">
    Actor Identity lives in its own settings flow. Use that page to select the active actor, start LoRA training, and monitor readiness for new character consistency batches.
  </p>
  <a href="/settings/actor" class="mt-4 inline-flex items-center rounded-full bg-[var(--lippe-orange)] px-4 py-2 text-sm font-semibold text-white">
    Open Actor Identity settings
  </a>
</section>
```

- [ ] **Step 4: Tighten the actor page copy around the user task**

Update the top of `templates/settings/actor.html` so the page leads with the user decision instead of provider mechanics.

```jinja2
<div class="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
  <div>
    <p class="brand-section-eyebrow">Character Consistency</p>
    <h1 class="mt-2 text-3xl font-brand-heading font-bold text-[var(--lippe-deep-blue)]">Select the actor for new batches</h1>
    <p class="mt-3 max-w-3xl text-sm text-[color:rgba(28,39,64,0.78)]">
      Choose the current actor, start training for a new one, and track when character consistency is ready to use.
    </p>
  </div>
  <a href="/settings/character" class="inline-flex items-center rounded-full border border-[var(--lippe-border)] bg-white/70 px-4 py-2 text-sm font-medium text-[var(--lippe-deep-blue)]">
    Open legacy snapshot tools
  </a>
</div>
```

- [ ] **Step 5: Run the focused regressions and commit**

Run: `python3 -m pytest -q tests/test_characters_feature.py -k "canonical_training_surface or hides_actor_training_form or settings_nav_marks_current_page"`

Expected: PASS

Commit:

```bash
git add templates/settings/base.html templates/settings/actor.html templates/settings/character.html tests/test_characters_feature.py
git commit -m "feat: make actor settings the canonical training surface"
```

---

### Task 2: Add Inline Recovery States In The Settings Handlers

**Files:**
- Modify: `app/features/characters/handlers.py`
- Modify: `templates/settings/actor.html`
- Test: `tests/test_characters_feature.py`

- [ ] **Step 1: Write the failing inline-error tests**

Add these tests to `tests/test_characters_feature.py`.

```python
def test_activate_actor_identity_re_renders_form_with_inline_error(monkeypatch):
    active = ActorIdentityRecord.model_validate(_actor_row("active", is_active=True))

    monkeypatch.setattr(character_queries, "sync_actor_identity_roster_from_provider", lambda correlation_id: None)
    monkeypatch.setattr(character_queries, "refresh_active_actor_identity_status", lambda correlation_id: active)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: active)
    monkeypatch.setattr(character_queries, "list_actor_identities", lambda: [active])
    monkeypatch.setattr(character_queries, "refresh_actor_identity_roster_statuses", lambda identities, correlation_id: identities)
    monkeypatch.setattr(character_queries, "set_active_actor_identity", lambda **kwargs: (_ for _ in ()).throw(ValueError("Only ready actors can be activated.")))

    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor/active",
        data={"actor_identity_id": "training"},
    )

    assert response.status_code == 422
    assert "Only ready actors can be activated." in response.text
    assert 'name="actor_identity_id"' in response.text


def test_upload_actor_identity_re_renders_inline_error_for_invalid_image_count():
    response = TestClient(app, base_url="http://localhost").post(
        "/settings/actor",
        data={"name": "Actor", "gender": "woman", "quality": "high", "consent_source": "operator"},
        files=[("training_images", ("front.png", _png_bytes(), "image/png"))],
    )

    assert response.status_code == 422
    assert "Upload between 8 and 20 images." in response.text
    assert 'value="Actor"' in response.text
```

Run: `python3 -m pytest -q tests/test_characters_feature.py -k "inline_error"`

Expected: FAIL because the handlers currently raise raw HTTP errors instead of rendering the template with inline state.

- [ ] **Step 2: Add small form-state helpers to the handler**

In `app/features/characters/handlers.py`, add a lightweight helper for actor-page template state.

```python
def _actor_form_defaults(request: Request) -> dict:
    return {
        "name": request.query_params.get("name") or "AYRA Actor Identity",
        "quality": request.query_params.get("quality") or "high",
        "gender": request.query_params.get("gender") or "woman",
        "consent_source": request.query_params.get("consent_source") or "Operator-provided reference set",
        "description": request.query_params.get("description") or "",
    }


def _actor_settings_response(
    *,
    request: Request,
    correlation_id: str,
    status_code: int = 200,
    actor_form_error: str | None = None,
    actor_activation_error: str | None = None,
    actor_form_values: dict | None = None,
):
    context = _actor_settings_context(request=request, correlation_id=correlation_id)
    context.update(
        {
            "settings_section": "actor",
            "actor_form_error": actor_form_error,
            "actor_activation_error": actor_activation_error,
            "actor_form_values": actor_form_values or _actor_form_defaults(request),
        }
    )
    return templates.TemplateResponse("settings/actor.html", context, status_code=status_code)
```

- [ ] **Step 3: Convert activation failures into inline page responses**

Update `activate_actor_identity(...)` to re-render the page on recoverable errors.

```python
    except FlowForgeException as exc:
        logger.warning(
            "actor_identity_activation_rejected",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
            error=exc.message,
        )
        return _actor_settings_response(
            request=request,
            correlation_id=correlation_id,
            status_code=exc.status_code,
            actor_activation_error=exc.message,
        )
    except ValueError as exc:
        logger.warning(
            "actor_identity_activation_rejected",
            correlation_id=correlation_id,
            actor_identity_id=actor_identity_id,
            error=str(exc),
        )
        return _actor_settings_response(
            request=request,
            correlation_id=correlation_id,
            status_code=422,
            actor_activation_error=str(exc),
        )
```

- [ ] **Step 4: Convert training-form validation failures into inline responses**

At the top of `train_actor_identity(...)`, replace the count error with a template response and preserve user-entered values.

```python
    actor_form_values = {
        "name": name,
        "gender": gender,
        "quality": quality,
        "consent_source": consent_source,
        "description": description,
    }
    if len(training_images) < 8 or len(training_images) > 20:
        return _actor_settings_response(
            request=request,
            correlation_id=correlation_id,
            status_code=422,
            actor_form_error="Upload between 8 and 20 images.",
            actor_form_values=actor_form_values,
        )
```

Render those messages in `templates/settings/actor.html` directly above the activation form and training form:

```jinja2
{% if actor_activation_error %}
<div class="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
  {{ actor_activation_error }}
</div>
{% endif %}

{% if actor_form_error %}
<div class="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
  {{ actor_form_error }}
</div>
{% endif %}
```

Use `actor_form_values` for each input’s value binding.

- [ ] **Step 5: Run the focused regressions and commit**

Run: `python3 -m pytest -q tests/test_characters_feature.py -k "inline_error"`

Expected: PASS

Commit:

```bash
git add app/features/characters/handlers.py templates/settings/actor.html tests/test_characters_feature.py
git commit -m "feat: add inline recovery states to actor settings"
```

---

### Task 3: Replace Full-Page Polling With A Training Status Partial

**Files:**
- Create: `templates/settings/_actor_training_status.html`
- Modify: `templates/settings/actor.html`
- Modify: `app/features/characters/handlers.py`
- Test: `tests/test_characters_feature.py`

- [ ] **Step 1: Write the failing polling regression**

Add this test to `tests/test_characters_feature.py`.

```python
def test_actor_training_poll_returns_partial_with_progressbar(monkeypatch):
    active = ActorIdentityRecord.model_validate(_actor_row("active", is_active=True, phase="training", progress=45, lora_id=None))

    monkeypatch.setattr(character_queries, "refresh_active_actor_identity_status", lambda correlation_id: active)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: active)

    response = TestClient(app, base_url="http://localhost").post("/settings/actor/poll")

    assert response.status_code == 200
    assert "Character consistency is blocked until training finishes." in response.text
    assert 'role="progressbar"' in response.text
    assert "<html" not in response.text.lower()
```

Run: `python3 -m pytest -q tests/test_characters_feature.py -k "poll_returns_partial"`

Expected: FAIL because no `/settings/actor/poll` endpoint or partial exists.

- [ ] **Step 2: Extract the training status card into a partial**

Create `templates/settings/_actor_training_status.html`.

```jinja2
<section
  id="actor-training-status"
  class="brand-panel mt-6 rounded-[1.75rem] p-5"
  {% if actor and not actor_ready %}
  hx-post="/settings/actor/poll"
  hx-trigger="every 10s"
  hx-swap="outerHTML"
  {% endif %}
>
  <div class="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
    <div>
      <p class="brand-section-eyebrow">Readiness</p>
      <h2 class="mt-2 text-2xl font-brand-heading font-bold text-[var(--lippe-deep-blue)]">
        {% if actor %}{{ actor.name }}{% else %}No actor selected yet{% endif %}
      </h2>
      <p class="mt-2 text-sm text-[color:rgba(28,39,64,0.78)]">
        {% if actor_ready %}
          Character consistency is ready for new batches.
        {% else %}
          Character consistency is blocked until training finishes.
        {% endif %}
      </p>
    </div>
    <span class="rounded-full px-3 py-1 text-xs font-semibold {% if actor_ready %}bg-emerald-100 text-emerald-900{% else %}bg-amber-100 text-amber-900{% endif %}">
      {% if actor_ready %}Ready{% else %}Blocked{% endif %}
    </span>
  </div>

  {% if actor %}
  <div class="mt-5">
    <div class="flex items-center justify-between text-xs font-medium text-[color:rgba(28,39,64,0.72)]">
      <span>{{ actor.training_phase|replace("_", " ")|title }}</span>
      <span>{{ actor.training_progress_percent }}%</span>
    </div>
    <div
      class="mt-2 h-3 overflow-hidden rounded-full bg-[rgba(28,39,64,0.08)]"
      role="progressbar"
      aria-label="Actor training progress"
      aria-valuemin="0"
      aria-valuemax="100"
      aria-valuenow="{{ actor.training_progress_percent }}"
    >
      <div class="h-full rounded-full bg-[var(--lippe-orange)]" style="width: {{ actor.training_progress_percent }}%"></div>
    </div>
  </div>
  {% endif %}
</section>
```

- [ ] **Step 3: Mount the partial on `/settings/actor` and add a new poll endpoint**

In `templates/settings/actor.html`, replace the inline training card markup with:

```jinja2
{% include "settings/_actor_training_status.html" %}
```

In `app/features/characters/handlers.py`, add:

```python
@router.post("/actor/poll")
def poll_actor_settings_training(request: Request):
    correlation_id = str(uuid4())
    actor = _actor_identity_context(correlation_id=correlation_id)
    return templates.TemplateResponse(
        "settings/_actor_training_status.html",
        {
            "request": request,
            "actor": actor,
            "actor_ready": actor_identity_is_ready(actor),
        },
    )
```

Also pass `settings_section="actor"` and `actor_ready=actor_identity_is_ready(actor)` from the main actor-page response builder.

- [ ] **Step 4: Remove the legacy-page ActorIdentity polling block**

Delete the HTMX polling section from `templates/settings/character.html` that currently posts to `/settings/character/actor/poll`, and remove `poll_actor_identity_training(...)` from `app/features/characters/handlers.py`.

```python
# Delete the old /settings/character/actor/poll route entirely.
```

- [ ] **Step 5: Run the focused regressions and commit**

Run: `python3 -m pytest -q tests/test_characters_feature.py -k "poll_returns_partial"`

Expected: PASS

Commit:

```bash
git add app/features/characters/handlers.py templates/settings/actor.html templates/settings/character.html templates/settings/_actor_training_status.html tests/test_characters_feature.py
git commit -m "feat: move actor training updates to a partial poll surface"
```

---

### Task 4: Normalize The Visual Layer And Lock The Regression Suite

**Files:**
- Modify: `templates/settings/actor.html`
- Modify: `templates/settings/character.html`
- Modify: `tests/test_characters_feature.py`

- [ ] **Step 1: Write the failing brand-and-accessibility assertions**

Add these tests to `tests/test_characters_feature.py`.

```python
def test_actor_settings_uses_brand_panel_and_semantic_status_copy(monkeypatch):
    active = ActorIdentityRecord.model_validate(_actor_row("active", is_active=True))

    monkeypatch.setattr(character_queries, "sync_actor_identity_roster_from_provider", lambda correlation_id: None)
    monkeypatch.setattr(character_queries, "refresh_active_actor_identity_status", lambda correlation_id: active)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: active)
    monkeypatch.setattr(character_queries, "list_actor_identities", lambda: [active])
    monkeypatch.setattr(character_queries, "refresh_actor_identity_roster_statuses", lambda identities, correlation_id: identities)

    response = TestClient(app, base_url="http://localhost").get("/settings/actor")

    assert "brand-panel" in response.text
    assert "Select the actor for new batches" in response.text
    assert "LoRA ID" not in response.text
    assert "Task ID" not in response.text


def test_character_settings_keeps_legacy_snapshot_actions(monkeypatch):
    monkeypatch.setattr(character_queries, "get_active_character", lambda: None)
    monkeypatch.setattr(character_queries, "get_active_actor_identity", lambda: None)
    monkeypatch.setattr(character_queries, "refresh_actor_identity_roster_statuses", lambda identities, correlation_id: [])
    monkeypatch.setattr(character_queries, "list_actor_identities", lambda: [])

    response = TestClient(app, base_url="http://localhost").get("/settings/character")

    assert "Legacy Three-Image Character Snapshot" in response.text
    assert 'action="/settings/character"' in response.text
```

Run: `python3 -m pytest -q tests/test_characters_feature.py -k "brand_panel or keeps_legacy_snapshot_actions"`

Expected: FAIL because the templates still use mostly generic gray stacks and expose raw provider fields prominently.

- [ ] **Step 2: Normalize the page sections onto the shared brand layer**

Replace the major wrapper sections in both settings templates with `brand-panel` styling and brand-tone copy.

```jinja2
<section class="brand-panel rounded-[1.75rem] p-5 sm:p-6">
  <p class="brand-section-eyebrow">Active Actor</p>
  <h2 class="mt-2 text-2xl font-brand-heading font-bold text-[var(--lippe-deep-blue)]">Current selection</h2>
  <p class="mt-2 text-sm text-[color:rgba(28,39,64,0.78)]">
    Future character consistency batches use the actor selected here.
  </p>
</section>
```

Keep provider-specific fields available only as secondary helper text, for example:

```jinja2
<details class="mt-4 text-xs text-[color:rgba(28,39,64,0.66)]">
  <summary class="cursor-pointer font-medium">Technical details</summary>
  <div class="mt-2 space-y-1">
    <p>Provider: {{ actor.provider }}</p>
    <p>LoRA ID: {{ actor.provider_lora_id or "pending" }}</p>
    <p>Training task: {{ actor.provider_training_task_id or "pending" }}</p>
  </div>
</details>
```

- [ ] **Step 3: Make the training form read better on narrow screens**

Update the form grid and helper copy in `templates/settings/actor.html` so the main CTA stays singular and the instructions read in a clear sequence.

```jinja2
<form method="post" action="/settings/actor" enctype="multipart/form-data" class="mt-6 space-y-6">
  <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
    ...
  </div>
  <ol class="space-y-2 text-sm text-[color:rgba(28,39,64,0.78)]">
    <li>1. Upload 8 to 20 clear training photos.</li>
    <li>2. Start training and wait until the actor is marked ready.</li>
    <li>3. Return here to switch the active actor for future batches.</li>
  </ol>
  <button type="submit" class="inline-flex min-h-11 items-center justify-center rounded-full bg-[var(--lippe-orange)] px-5 py-3 text-sm font-semibold text-white">
    Start training
  </button>
</form>
```

- [ ] **Step 4: Run the full focused suite and commit**

Run:

```bash
python3 -m pytest -q tests/test_characters_feature.py
python3 -m pytest -q tests/test_actor_identity_training.py tests/test_character_consistency_mode.py
```

Expected: PASS

Commit:

```bash
git add templates/settings/actor.html templates/settings/character.html tests/test_characters_feature.py
git commit -m "feat: align actor settings with brand and accessibility rules"
```

---

## Self-Review

### Spec Coverage

- One canonical `ActorSettingsSurface`: covered by Task 1.
- `TrainingProgressDisplay` on the settings page: covered by Task 3.
- Legacy compatibility without removing snapshot tools: covered by Tasks 1 and 4.
- User-friendly actor selection and training flow under existing branding: covered by Tasks 1, 2, and 4.
- Inline recoverability for common failure paths: covered by Task 2.

### Placeholder Scan

- No `TODO`, `TBD`, or “handle appropriately” placeholders remain.
- Every task includes exact files, code snippets, commands, and expected results.

### Type / Naming Consistency

- Canonical page key: `settings_section="actor"` and `settings_section="character"`.
- Canonical readiness boolean: `actor_ready`.
- Canonical partial route: `/settings/actor/poll`.
- Canonical template partial: `templates/settings/_actor_training_status.html`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-actor-settings-ux-overhaul.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
