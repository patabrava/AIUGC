# Lippe Lift UI Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the app UI so the shared shell, Topics Hub, and Batch Detail views align with Lippe Lift corporate identity using the provided palette and typography.

**Architecture:** Keep the existing FastAPI + Jinja + HTMX + Alpine stack and refactor the UI through one centralized brand stylesheet plus a small set of high-impact template updates. Replace the current purple Material-style defaults with Lippe Lift tokens, then restyle the highest-traffic screens first so the new visual system is visible everywhere without creating a sprawling design-system rewrite.

**Tech Stack:** FastAPI, Jinja2 templates, HTMX, Alpine.js, Tailwind CDN utilities, vanilla CSS, pytest

---

## Scope And Budgets

- Locality budget: `{files: 8, LOC/file: <= 220 target, deps: 0}`
- Brand palette:
  - `--lippe-blue: #006AAB`
  - `--lippe-blue-light: #B7CCE7`
  - `--lippe-deep-blue: #1C2740`
  - `--lippe-orange: #E58434`
  - `--lippe-apricot: #FFD9A0`
  - `--lippe-cream: #FFF2E2`
- Typography:
  - Headings: `Outfit`
  - Body/UI copy: `Instrument Sans`
- Refactor surfaces in this plan:
  - Global app shell in `templates/base.html`
  - Topics Hub in `templates/topics/hub.html`
  - Batch Detail progress + workflow banners in `templates/batches/detail/_progress_stepper.html`, `templates/batches/detail/_workflow_panels.html`, and `templates/batches/detail/_view_macros.html`
  - HTML regression coverage in `tests/test_topics_hub.py` and `tests/test_batches_status_progress.py`

## File Structure

- Create: `static/css/brand.css`
  - Single source of truth for Lippe Lift tokens, typography imports, semantic utility classes, focus styles, shell backgrounds, panels, badges, and CTA button recipes.
- Modify: `templates/base.html`
  - Load fonts and the brand stylesheet, replace the purple/MD3 root tokens, restyle nav/footer, and define the global shell treatment.
- Modify: `templates/topics/hub.html`
  - Refactor the main two-column page into the new brand language with warm cream surfaces, deep-blue headings, Lippe blue actions, and orange/apricot accents.
- Modify: `templates/batches/detail/_view_macros.html`
  - Centralize progress-chip and badge color logic so progress/status visuals match the new palette.
- Modify: `templates/batches/detail/_progress_stepper.html`
  - Rebuild the stepper card styling around the new token classes and typography hierarchy.
- Modify: `templates/batches/detail/_workflow_panels.html`
  - Replace the current purple/green gradient banners with branded message panels and CTA buttons.
- Modify: `tests/test_topics_hub.py`
  - Add HTML-level assertions for brand stylesheet load and core Topics Hub class hooks.
- Modify: `tests/test_batches_status_progress.py`
  - Add HTML-level assertions for branded stepper/workflow classes.

## Visual Direction

- Avoid generic SaaS purple, default gray cards, and interchangeable dashboard chrome.
- Use `Outfit` with assertive weight contrast for page titles, panel titles, and metrics.
- Use `Instrument Sans` for body copy, labels, chips, and navigation.
- Make `#1C2740` the anchor text color, `#006AAB` the primary interactive color, `#E58434` the action accent, and `#FFF2E2`/`#FFD9A0` the warmth layer for backgrounds and highlights.
- Preserve accessibility:
  - visible keyboard focus on all controls
  - reduced-motion safe transitions
  - maintain semantic HTML and existing HTMX behavior
  - keep contrast acceptable on all CTA and status combinations

## Task 1: Establish The Shared Lippe Lift Brand Layer

**Files:**
- Create: `static/css/brand.css`
- Modify: `templates/base.html`
- Test: `tests/test_topics_hub.py`

- [ ] **Step 1: Write the failing test**

```python
def test_topics_layout_loads_lippe_lift_brand_assets(monkeypatch):
    monkeypatch.setattr(
        topic_handlers,
        "build_topic_hub_payload",
        lambda request: {
            "filters": {"search": "", "post_type": None, "target_length_tier": None, "topic_id": None, "run_id": None, "status": None, "only_with_scripts": False},
            "topics": [],
            "total_topics": 0,
            "scripts": [],
            "selected_topic": None,
            "selected_scripts": [],
            "runs": [],
            "active_runs": [],
            "completed_runs": [],
        },
    )

    client = _build_test_client()
    response = client.get("/topics", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert '/static/css/brand.css?v=' in response.text
    assert "fonts.googleapis.com" in response.text
    assert 'class="app-shell' in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_topics_hub.py::test_topics_layout_loads_lippe_lift_brand_assets -v`
Expected: FAIL because `templates/base.html` does not load `/static/css/brand.css`, does not load the requested fonts, and does not yet render an `app-shell` class.

- [ ] **Step 3: Write minimal implementation**

Create `static/css/brand.css` with the initial token layer and semantic classes:

```css
:root {
    --lippe-blue: #006AAB;
    --lippe-blue-light: #B7CCE7;
    --lippe-deep-blue: #1C2740;
    --lippe-orange: #E58434;
    --lippe-apricot: #FFD9A0;
    --lippe-cream: #FFF2E2;
    --lippe-surface: #FFFDF9;
    --lippe-border: rgba(28, 39, 64, 0.12);
    --lippe-shadow: 0 18px 48px rgba(28, 39, 64, 0.08);
    --font-heading: "Outfit", sans-serif;
    --font-body: "Instrument Sans", sans-serif;
}

html {
    font-family: var(--font-body);
    color: var(--lippe-deep-blue);
    background:
        radial-gradient(circle at top left, rgba(183, 204, 231, 0.4), transparent 32rem),
        linear-gradient(180deg, #fffdf9 0%, var(--lippe-cream) 100%);
}

body.app-shell {
    min-height: 100vh;
    color: var(--lippe-deep-blue);
}

h1, h2, h3, h4, h5, h6,
.font-brand-heading {
    font-family: var(--font-heading);
}

.brand-panel {
    border: 1px solid var(--lippe-border);
    background: rgba(255, 255, 255, 0.92);
    box-shadow: var(--lippe-shadow);
    backdrop-filter: blur(14px);
}

.brand-focus:focus-visible {
    outline: 3px solid rgba(229, 132, 52, 0.35);
    outline-offset: 3px;
}

.brand-button-primary {
    background: var(--lippe-blue);
    color: #fff;
}

.brand-button-primary:hover {
    background: #005586;
}

.brand-button-accent {
    background: var(--lippe-orange);
    color: var(--lippe-deep-blue);
}

.brand-button-accent:hover {
    background: #cf6d1a;
    color: #fff;
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        scroll-behavior: auto !important;
    }
}
```

Update the head and shell in `templates/base.html`:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/css/brand.css?v={{ static_version }}">
```

```html
<body class="app-shell" hx-history-elt>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_topics_hub.py::test_topics_layout_loads_lippe_lift_brand_assets -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add static/css/brand.css templates/base.html tests/test_topics_hub.py
git commit -m "feat: add lippe lift brand foundation"
```

## Task 2: Refactor The Global Shell, Navigation, And Footer

**Files:**
- Modify: `templates/base.html`
- Test: `tests/test_topics_hub.py`

- [ ] **Step 1: Write the failing test**

```python
def test_topics_layout_uses_branded_shell_navigation(monkeypatch):
    monkeypatch.setattr(
        topic_handlers,
        "build_topic_hub_payload",
        lambda request: {
            "filters": {"search": "", "post_type": None, "target_length_tier": None, "topic_id": None, "run_id": None, "status": None, "only_with_scripts": False},
            "topics": [],
            "total_topics": 0,
            "scripts": [],
            "selected_topic": None,
            "selected_scripts": [],
            "runs": [],
            "active_runs": [],
            "completed_runs": [],
        },
    )

    client = _build_test_client()
    response = client.get("/topics", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert "brand-nav" in response.text
    assert "brand-nav__link" in response.text
    assert "Lippe Lift Operations" in response.text
    assert "brand-footer" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_topics_hub.py::test_topics_layout_uses_branded_shell_navigation -v`
Expected: FAIL because the current shell still renders the default gray navigation/footer with no Lippe Lift copy or class hooks.

- [ ] **Step 3: Write minimal implementation**

Extend `static/css/brand.css` with shell classes:

```css
.brand-nav {
    position: sticky;
    top: 0;
    z-index: 30;
    border-bottom: 1px solid rgba(28, 39, 64, 0.08);
    background: rgba(255, 242, 226, 0.88);
    backdrop-filter: blur(18px);
}

.brand-nav__link {
    color: var(--lippe-deep-blue);
    font-weight: 600;
}

.brand-nav__link:hover,
.brand-nav__link:focus-visible {
    color: var(--lippe-blue);
}

.brand-footer {
    border-top: 1px solid rgba(28, 39, 64, 0.08);
    background: rgba(255, 255, 255, 0.86);
}
```

Refactor the nav/footer shell in `templates/base.html`:

```html
<nav class="brand-nav" x-data="{ mobileMenuOpen: false }" x-init="$nextTick(() => $store.accountsHub?.bootstrap())">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div class="flex min-h-[5rem] items-center justify-between gap-6">
            <div class="flex items-center gap-4">
                <a href="/" class="brand-focus inline-flex items-center rounded-xl">
                    <img
                        src="/static/images/logo-Lippe-Lift_01.png"
                        alt="Lippe Lift Operations"
                        class="h-10 w-auto sm:h-12"
                        loading="lazy"
                    >
                </a>
                <div class="hidden lg:block">
                    <p class="font-brand-heading text-xs font-semibold uppercase tracking-[0.32em] text-[#006AAB]">Lippe Lift</p>
                    <p class="text-sm text-[#1C2740]/72">Operations</p>
                </div>
            </div>
            <div class="hidden md:flex items-center gap-2">
                <a href="/topics" class="brand-nav__link brand-focus rounded-full px-4 py-2 text-sm">Topics</a>
                <a href="/batches" class="brand-nav__link brand-focus rounded-full px-4 py-2 text-sm">Batches</a>
                <a href="/docs" class="brand-nav__link brand-focus rounded-full px-4 py-2 text-sm">API Docs</a>
            </div>
        </div>
    </div>
</nav>
```

```html
<main class="max-w-7xl mx-auto px-4 py-8 sm:px-6 lg:px-8 lg:py-10">
```

```html
<footer class="brand-footer mt-12">
    <div class="max-w-7xl mx-auto px-4 py-5 sm:px-6 lg:px-8">
        <div class="flex flex-col gap-2 text-sm text-[#1C2740]/70 sm:flex-row sm:items-center sm:justify-between">
            <p>Lippe Lift Operations Hub</p>
            <a href="/health" class="brand-nav__link brand-focus rounded-md">System Health</a>
        </div>
    </div>
</footer>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_topics_hub.py::test_topics_layout_uses_branded_shell_navigation -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates/base.html static/css/brand.css tests/test_topics_hub.py
git commit -m "feat: reskin app shell for lippe lift"
```

## Task 3: Refactor The Topics Hub Into The Lippe Lift Visual Language

**Files:**
- Modify: `templates/topics/hub.html`
- Modify: `static/css/brand.css`
- Test: `tests/test_topics_hub.py`

- [ ] **Step 1: Write the failing test**

```python
def test_topics_hub_renders_lippe_lift_surface_classes(monkeypatch):
    monkeypatch.setattr(
        topic_handlers,
        "build_topic_hub_payload",
        lambda request: {
            "filters": {"search": "", "post_type": None, "target_length_tier": None, "topic_id": None, "run_id": None, "status": None, "only_with_scripts": False},
            "topics": [],
            "total_topics": 0,
            "scripts": [],
            "selected_topic": None,
            "selected_scripts": [],
            "runs": [],
            "active_runs": [],
            "completed_runs": [],
        },
    )

    client = _build_test_client()
    response = client.get("/topics", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert "brand-panel brand-topics-shell" in response.text
    assert "brand-section-eyebrow" in response.text
    assert "brand-filter-button" in response.text
    assert "brand-launch-panel" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_topics_hub.py::test_topics_hub_renders_lippe_lift_surface_classes -v`
Expected: FAIL because `templates/topics/hub.html` still uses slate/white utility styling and no branded section classes.

- [ ] **Step 3: Write minimal implementation**

Add Topics-specific surface classes to `static/css/brand.css`:

```css
.brand-topics-shell {
    overflow: hidden;
    border-radius: 2rem;
    background:
        linear-gradient(135deg, rgba(255, 242, 226, 0.94), rgba(255, 255, 255, 0.94)),
        rgba(255, 255, 255, 0.9);
}

.brand-section-eyebrow {
    font-family: var(--font-heading);
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.28em;
    text-transform: uppercase;
    color: var(--lippe-blue);
}

.brand-filter-button {
    border: 1px solid rgba(0, 106, 171, 0.14);
    background: rgba(183, 204, 231, 0.24);
    color: var(--lippe-deep-blue);
}

.brand-launch-panel {
    background:
        linear-gradient(180deg, rgba(183, 204, 231, 0.2), rgba(255, 217, 160, 0.22)),
        rgba(255, 255, 255, 0.72);
}
```

Refactor `templates/topics/hub.html`:

```html
<div x-data="{ drawerOpen: false }" @open-scripts-drawer.window="drawerOpen = true">
    <div class="brand-panel brand-topics-shell">
        <div class="grid lg:grid-cols-[1fr_0.9fr] lg:h-[80vh]">
            <div class="border-r border-[#1C2740]/10 p-5 sm:p-7 overflow-y-auto lg:h-full">
                <div class="flex items-center justify-between gap-4">
                    <div>
                        <p class="brand-section-eyebrow">Topic Inventory</p>
                        <h1 class="mt-2 text-2xl font-extrabold tracking-tight text-[#1C2740]">Topics</h1>
                    </div>
                    <div class="flex items-center gap-3">
                        <span class="text-xs font-medium text-[#1C2740]/60">{{ total_topics }} topic{{ 's' if total_topics != 1 else '' }}</span>
                        <div x-data="{ open: false }" class="relative">
                            <button @click="open = !open" class="brand-filter-button brand-focus rounded-full px-4 py-2 text-xs font-semibold transition">
                                Filter ▾
                            </button>
```

```html
            <div id="launch-panel" class="brand-launch-panel p-5 sm:p-7 overflow-y-auto lg:h-full min-h-0">
                {% include "topics/partials/launch_panel.html" %}
                {% include "topics/partials/active_runs.html" %}
            </div>
```

```html
        <div x-show="drawerOpen" x-cloak class="fixed inset-0 z-40">
            <div @click="drawerOpen = false" class="absolute inset-0 bg-[#1C2740]/28"></div>
            <div class="absolute right-0 top-0 h-full w-full max-w-2xl border-l border-[#1C2740]/10 bg-[#FFFDF9] shadow-2xl">
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_topics_hub.py::test_topics_hub_renders_lippe_lift_surface_classes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates/topics/hub.html static/css/brand.css tests/test_topics_hub.py
git commit -m "feat: refactor topics hub to lippe lift brand"
```

## Task 4: Rebuild Batch Progress And Status Macros Around Brand Tokens

**Files:**
- Modify: `templates/batches/detail/_view_macros.html`
- Modify: `templates/batches/detail/_progress_stepper.html`
- Test: `tests/test_batches_status_progress.py`

- [ ] **Step 1: Write the failing test**

```python
def test_batch_detail_progress_uses_lippe_lift_stepper_classes():
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("batches/detail/_progress_stepper.html")

    html = template.render(
        batch={"state": "S4_SCRIPTED"},
        batch_view={
            "progress_states": [
                {"code": "S1_SETUP", "label": "Setup"},
                {"code": "S2_SEEDED", "label": "Seeded"},
                {"code": "S4_SCRIPTED", "label": "Scripted"},
            ],
        },
    )

    assert "brand-panel brand-stepper-card" in html
    assert "brand-progress-step" in html
    assert "brand-progress-label" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batches_status_progress.py::test_batch_detail_progress_uses_lippe_lift_stepper_classes -v`
Expected: FAIL because the progress stepper still renders gray/purple utility classes and no brand-specific hooks.

- [ ] **Step 3: Write minimal implementation**

Replace macro outputs in `templates/batches/detail/_view_macros.html`:

```jinja
{% macro progress_step_classes(current_state, state_code) -%}
{% if current_state == state_code %}brand-progress-step brand-progress-step--current{% elif current_state > state_code %}brand-progress-step brand-progress-step--complete{% else %}brand-progress-step brand-progress-step--upcoming{% endif %}
{%- endmacro %}

{% macro progress_label_classes(current_state, state_code) -%}
{% if current_state == state_code %}brand-progress-label brand-progress-label--current{% else %}brand-progress-label{% endif %}
{%- endmacro %}

{% macro connector_classes(current_state, state_code) -%}
{% if current_state > state_code %}brand-progress-connector brand-progress-connector--complete{% else %}brand-progress-connector{% endif %}
{%- endmacro %}
```

Add stepper classes to `static/css/brand.css`:

```css
.brand-stepper-card {
    border-radius: 1.5rem;
    padding: 1.5rem;
}

.brand-progress-step {
    display: flex;
    height: 2.75rem;
    width: 2.75rem;
    align-items: center;
    justify-content: center;
    border-radius: 999px;
    font-family: var(--font-heading);
    font-weight: 700;
}

.brand-progress-step--current {
    background: var(--lippe-blue);
    color: #fff;
}

.brand-progress-step--complete {
    background: var(--lippe-orange);
    color: #fff;
}

.brand-progress-step--upcoming {
    background: rgba(183, 204, 231, 0.32);
    color: var(--lippe-deep-blue);
}

.brand-progress-label {
    color: rgba(28, 39, 64, 0.64);
}

.brand-progress-label--current {
    color: var(--lippe-blue);
}
```

Refactor `templates/batches/detail/_progress_stepper.html`:

```html
<div class="brand-panel brand-stepper-card mb-6">
    <div class="mb-4 flex items-center justify-between gap-4">
        <div>
            <p class="brand-section-eyebrow">Workflow</p>
            <h3 class="mt-2 text-xl font-bold text-[#1C2740]">Progress</h3>
        </div>
    </div>
```

```html
            <span class="mt-3 text-xs font-semibold uppercase tracking-[0.18em] {{ progress_label_classes(batch.state, progress_state.code) }}">
                {{ progress_state.label }}
            </span>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_batches_status_progress.py::test_batch_detail_progress_uses_lippe_lift_stepper_classes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates/batches/detail/_view_macros.html templates/batches/detail/_progress_stepper.html static/css/brand.css tests/test_batches_status_progress.py
git commit -m "feat: brand batch progress stepper"
```

## Task 5: Refactor Batch Workflow Panels And CTA States

**Files:**
- Modify: `templates/batches/detail/_workflow_panels.html`
- Modify: `templates/batches/detail/_view_macros.html`
- Modify: `static/css/brand.css`
- Test: `tests/test_batches_status_progress.py`

- [ ] **Step 1: Write the failing test**

```python
def test_batch_detail_workflow_panels_use_branded_status_classes():
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("batches/detail/_workflow_panels.html")

    html = template.render(
        batch={"id": "batch-1", "state": "S2_SEEDED"},
        batch_view={
            "review_summary": {
                "approved_scripts_count": 1,
                "removed_scripts_count": 0,
                "pending_scripts_count": 0,
            },
            "prompt_ready_count": 0,
            "active_posts_count": 1,
            "qa_passed_count": 0,
        },
    )

    assert "brand-panel brand-workflow-banner" in html
    assert "brand-workflow-banner--review" in html
    assert "brand-button-primary" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batches_status_progress.py::test_batch_detail_workflow_panels_use_branded_status_classes -v`
Expected: FAIL because the workflow banners still use purple/green gradient utility classes and no brand CTA classes.

- [ ] **Step 3: Write minimal implementation**

Add banner and CTA classes to `static/css/brand.css`:

```css
.brand-workflow-banner {
    border-radius: 1.5rem;
    padding: 1.5rem;
}

.brand-workflow-banner--review {
    background: linear-gradient(135deg, rgba(183, 204, 231, 0.42), rgba(255, 242, 226, 0.94));
}

.brand-workflow-banner--production {
    background: linear-gradient(135deg, rgba(183, 204, 231, 0.32), rgba(255, 217, 160, 0.34));
}

.brand-workflow-banner--qa {
    background: linear-gradient(135deg, rgba(255, 217, 160, 0.28), rgba(255, 255, 255, 0.94));
}
```

Refactor `templates/batches/detail/_workflow_panels.html`:

```html
{% if batch.state == 'S2_SEEDED' %}
<div class="brand-panel brand-workflow-banner brand-workflow-banner--review mb-6">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div class="flex-1">
            <p class="brand-section-eyebrow">Script Review</p>
            <h3 class="mt-2 text-xl font-bold text-[#1C2740]">Ready for Script Approval</h3>
            <p class="mt-2 text-sm text-[#1C2740]/72">
                Review each generated script below. Every post must be approved or removed before the batch can advance.
            </p>
        </div>
        <button
            hx-put="/batches/{{ batch.id }}/approve-scripts"
            hx-swap="none"
            {% if batch_view.review_summary.pending_scripts_count > 0 or batch_view.review_summary.approved_scripts_count == 0 %}disabled{% endif %}
            class="brand-button-primary brand-focus rounded-full px-5 py-3 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50"
        >
            Approve Scripts
        </button>
    </div>
</div>
{% endif %}
```

```html
<div id="video-workflow" class="brand-panel brand-workflow-banner brand-workflow-banner--production mb-6">
```

```html
<div id="qa-workflow" class="brand-panel brand-workflow-banner brand-workflow-banner--qa mb-6">
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_batches_status_progress.py::test_batch_detail_workflow_panels_use_branded_status_classes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add templates/batches/detail/_workflow_panels.html static/css/brand.css tests/test_batches_status_progress.py
git commit -m "feat: refactor batch workflow banners for lippe lift"
```

## Task 6: Run The Focused UI Regression Suite

**Files:**
- Modify: `tests/test_topics_hub.py`
- Modify: `tests/test_batches_status_progress.py`

- [ ] **Step 1: Add the final grouped regression assertions**

Append one compact regression to `tests/test_topics_hub.py`:

```python
def test_topics_hub_brand_copy_and_visual_hooks_stay_present(monkeypatch):
    monkeypatch.setattr(
        topic_handlers,
        "build_topic_hub_payload",
        lambda request: {
            "filters": {"search": "", "post_type": None, "target_length_tier": None, "topic_id": None, "run_id": None, "status": None, "only_with_scripts": False},
            "topics": [],
            "total_topics": 0,
            "scripts": [],
            "selected_topic": None,
            "selected_scripts": [],
            "runs": [],
            "active_runs": [],
            "completed_runs": [],
        },
    )

    client = _build_test_client()
    response = client.get("/topics", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert "Lippe Lift Operations" in response.text
    assert "brand-topics-shell" in response.text
    assert "brand-launch-panel" in response.text
```

Append one compact regression to `tests/test_batches_status_progress.py`:

```python
def test_batch_macros_keep_lippe_lift_badge_classes():
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("batches/detail/_view_macros.html")

    rendered = template.module.review_status_chip("approved", False)

    assert "brand-status-chip" in rendered
    assert "brand-status-chip--approved" in rendered
```

- [ ] **Step 2: Run the focused UI regression suite**

Run: `pytest tests/test_topics_hub.py tests/test_batches_status_progress.py -v`
Expected: PASS with all new and existing route/template assertions green.

- [ ] **Step 3: Run one broader safety check**

Run: `pytest tests/test_auth.py tests/test_posts_script_review.py -v`
Expected: PASS, confirming the shared shell and batch review template changes did not break adjacent auth or script-review flows.

- [ ] **Step 4: Record visual QA checklist in the commit message body before committing**

Use this commit body:

```text
- verified desktop nav/footer shell
- verified topics two-column layout
- verified batch progress and workflow banners
- verified focus visibility and reduced-motion rules
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_topics_hub.py tests/test_batches_status_progress.py
git commit -m "test: lock lippe lift ui regressions" -m "- verified desktop nav/footer shell
- verified topics two-column layout
- verified batch progress and workflow banners
- verified focus visibility and reduced-motion rules"
```

## Implementation Notes

- Keep dependencies at `0`. Do not add Tailwind plugins, component kits, or JS animation libraries.
- Do not create a separate design-system package. The scope is one stylesheet plus template refactors.
- Keep all HTMX attributes, Alpine state, and route behavior unchanged unless a visual refactor requires a class rename only.
- Do not touch `static/js/batches/detail.js` in this pass unless a visual class hook proves impossible without a JS change.
- If additional batch partials need cleanup once these files are restyled, capture them in a follow-up plan rather than expanding this implementation block.

## Self-Review

### Spec Coverage

- Lippe Lift colors: covered in Task 1 token layer and reused in Tasks 2-5.
- Lippe Lift typography: covered in Task 1 via `Outfit` and `Instrument Sans` font loading and heading/body assignments.
- World-class frontend refactor plan: covered through shared shell, Topics Hub, Batch Detail progress, and workflow banners.
- Corporate identity alignment: covered by the centralized brand classes, visual direction section, and regression hooks.
- Fewer files / low dependency / vanilla-first: covered by the locality budget and eight-file plan with `deps: 0`.

### Placeholder Scan

- No `TODO`, `TBD`, or “implement later” placeholders remain.
- Every task includes explicit file paths, code snippets, commands, and expected outcomes.

### Type Consistency

- Shared brand class names are consistent across tasks:
  - `brand-panel`
  - `brand-topics-shell`
  - `brand-launch-panel`
  - `brand-progress-step`
  - `brand-workflow-banner`
  - `brand-button-primary`
- Test names and template targets match the files listed in each task.

Plan complete and saved to `docs/superpowers/plans/2026-04-09-lippe-lift-ui-refactor.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
