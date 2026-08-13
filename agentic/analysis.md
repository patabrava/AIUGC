# Analysis

## Task Signal

Complete the approved publishing calendar interaction model. Route: GENERAL + LIRA Design + EYE.

## Product Decision

The calendar is a direct-manipulation planner. The selected queue item can be placed by clicking a day/time or dragging. Social video and blog post have explicit placement modes. Existing events can be dragged to move them. Exact inputs remain the precision and keyboard fallback.

`Save item` persists one post's social and blog draft plan without arming dispatch. `Schedule all content` remains the only atomic transition that marks social and blog content scheduled. This preserves batch safety while allowing operators to save per-item work across sessions.

## Implementation Block

Goal: make calendar placement, movement, individual persistence, validation, and batch scheduling unambiguous.

{files, LOC/file, deps}:

- `templates/batches/detail/_publish_panel.html`: direct placement controls, draggable queue/events, responsive inspector, approximately 120 LOC changed.
- `static/js/batches/detail.js`: placement, drag/drop, dirty state, item persistence, approximately 220 LOC changed.
- `static/css/brand.css`: half-hour grid and drop state, approximately 25 LOC changed.
- `app/features/publish/schemas.py`, `handlers.py`: one validated per-item draft-plan boundary, approximately 90 LOC; no dependencies.
- `tests/test_publish_meta_flow.py`: UI and persistence regressions, approximately 100 LOC.

Contracts: future times, at least one connected destination, caption, blog draft text/image, TikTok readiness before batch arm, 30-minute social spacing, immutable active schedules, atomic `/publish/batches/{id}/arm` transition.

Validation: JavaScript syntax, focused publish tests, real browser click placement, drag movement, keyboard placement, item save/reload persistence, batch readiness gate, desktop and narrow viewport smoke.

Pass: an 8-second video with a ready blog can be placed and saved individually, reloaded without loss, moved, and presented to the unchanged atomic batch action without publishing during the test.
