# Batch Detail Component Map

## Goal

Keep the batch detail page behavior and visual output stable while making future edits local and predictable.

## Render Tree

1. `templates/batches/detail.html`
2. `templates/batches/detail/_breadcrumbs.html`
3. `templates/batches/detail/_progress_stepper.html`
4. `templates/batches/detail/_workflow_panels.html`
5. `templates/batches/detail/_posts_section.html`
6. `templates/batches/detail/_post_card.html`
7. `templates/batches/detail/_post_modals.html`
8. `templates/batches/detail/_video_settings.html`

## File Responsibilities

- `templates/batches/detail.html`
  - Page shell only
  - Root HTMX polling boundary
  - Partial composition order
  - Page-local script inclusion

- `templates/batches/detail/_breadcrumbs.html`
  - Breadcrumb and batch header only

- `templates/batches/detail/_progress_stepper.html`
  - Stage visualization only
  - Uses macro helpers for repeated class logic

- `templates/batches/detail/_workflow_panels.html`
  - Batch-level state banners and stage actions
  - No per-post markup

- `templates/batches/detail/_posts_section.html`
  - Posts container, counters, and top-level publish-arm action
  - Loops visible posts and delegates each card

- `templates/batches/detail/_post_card.html`
  - One post’s full surface area
  - Script review
  - Prompt generation snapshot
  - Video status and QA
  - Publish planning block

- `templates/batches/detail/_post_modals.html`
  - Expanded prompt modal only

- `templates/batches/detail/_video_settings.html`
  - Batch-level video generation controls only

- `templates/batches/detail/_view_macros.html`
  - Repeated class and chip rendering helpers local to this page slice

- `static/js/batches/detail.js`
  - Page-local browser behavior
  - Keeps existing global Alpine factory names stable:
    - `window.promptModalComponent`
    - `window.publishSchedulerComponent`
    - `window.videoSettingsComponent`
  - Also owns playback guards and time-zone helpers used by the page

## Template Context Rules

- `batch`
  - Canonical backend payload for the page
  - Do not reshape this aggressively in the template

- `batch_view`
  - Template-only derived data
  - Current members:
    - `should_poll_prompts`
    - `progress_states`
    - `visible_posts`
    - `active_posts_count`
    - `prompt_ready_count`
    - `qa_passed_count`
    - `scheduled_count`
    - `review_summary`
    - `meta_publish_state`
    - `tiktok_publish_state`

## Naming Rules

- Prefer descriptive, domain-specific names over short aliases.
- Use `*_count` for numbers, not overloaded labels like `active`.
- Use `*_state` for provider readiness/state payloads.
- Keep per-post derived locals inside `_post_card.html`; do not push every small display concern into the handler.

## Edit Guide

- Change batch-level banners or counters in `_workflow_panels.html`.
- Change per-post script, prompt, QA, or publish UI in `_post_card.html`.
- Change modal-only markup in `_post_modals.html`.
- Change browser behavior in `static/js/batches/detail.js`.
- Change template-only counters or visibility rules in `app/features/batches/handlers.py` under `_build_batch_detail_view(...)`.

## Guardrails

- Do not reintroduce inline `<script>` blocks into `templates/batches/detail.html`.
- Do not create shared cross-page abstractions from this slice unless the same pattern exists at least three times.
- Keep HTMX attributes explicit in the markup that owns the server-rendering boundary.
