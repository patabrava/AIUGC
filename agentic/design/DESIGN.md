# Publishing Calendar Design

## Implemented result

The final Publish step is a calendar-first scheduling workspace. Operators choose shared destinations and timezone once, inspect every content package in a queue, place social videos and related blog posts on the same week canvas, edit the selected item precisely, resolve TikTok readiness, and schedule the complete batch through one sticky final action.

## Code fundamentals

- `templates/batches/detail/_publish_panel.html` owns the rendered calendar workspace, selected-item inspector, TikTok batch/per-video surfaces, sticky readiness summary, and Post now confirmation.
- `static/js/batches/detail.js` owns calendar state and derived behavior inside `batchPublishComponent`: selected item, weekdays/weekends, week navigation, social and blog events, precise time editing, readiness, conflicts, and the unchanged atomic arm request.
- `static/css/brand.css` provides the calendar hour grid and bounded long-title treatment in addition to the existing brand tokens and focus behavior.
- `app/features/batches/handlers.py::_build_publish_post_view` remains the data projection. Video URL/metadata, blog readiness, persisted schedules, captions, networks, and TikTok settings enter the client through this boundary.
- `app/features/publish/arm.py` remains the server truth. One validated request schedules social videos and ready blog posts through `arm_batch_content_schedule`.

## Design-system fundamentals

- Typography: Outfit headings and Instrument Sans interface text.
- Color: deep navy for hierarchy, Lippe blue for selected state and primary scheduling actions, orange for immediate publishing or attention, pale blue for calendar events, green/amber/red semantic states with text labels.
- Geometry: thin cool-blue borders, white and near-white flat surfaces, 8–12px control radii, restrained shadow only for the sticky final bar and scheduled events.
- Layout: shared controls in the header; queue, calendar, and inspector form the desktop workspace; sections stack on smaller screens; the calendar owns horizontal overflow so the page never does.
- Density: five weekdays by default within the existing app shell. A 7-day control exposes weekends, and weekend dates appear automatically when scheduled content needs them.

## Interaction model

- Queue buttons identify every content package and its video/blog composition.
- Calendar events are real buttons with descriptive accessible names. Dragging is not required.
- The inspector provides native date, time, and datetime controls for exact keyboard-accessible editing.
- Social and blog events share the content title and selection state, keeping their relationship visible.
- Batch TikTok defaults stay secondary. Per-video caption and TikTok settings expand only when the selected item needs detail.
- “Apply item changes” validates local readiness. The final “Schedule all content” action persists the complete batch atomically.
- “Post now” remains a separate orange action with explicit confirmation.

## States and recovery

- Missing or past times, 30-minute conflicts, incomplete captions, unready blogs, missing TikTok settings, and disconnected destinations produce named issues.
- “Open first item to fix” selects the exact blocked item and opens detailed fields when required.
- Scheduled, publishing, and published items retain evidence while their scheduling controls remain disabled.
- Empty blog state reads “Video only”; incomplete blog text/image stays blocking when the blog is enabled.
- Long German titles clamp in the inspector with the full value available as the title tooltip.
- Five-day and seven-day layouts preserve valid weekend schedules without forcing permanent calendar clutter.

## Accessibility and responsive behavior

- Native buttons, labels, fieldsets, date/time inputs, and semantic regions preserve keyboard operation.
- `brand-focus` supplies visible focus rings; status meaning always includes text.
- The calendar container is independently scrollable at narrow widths. Browser validation at 390px confirmed no page-level horizontal overflow.
- The existing reduced-motion rule applies to transitions.
- The final action and issue summary remain together in a sticky footer.

## Validation record

- JavaScript syntax: `node --check static/js/batches/detail.js`.
- Focused publishing suite: 56 tests passed for publish UI and atomic arm behavior.
- Broader related suite: 145 tests passed, with one unrelated cron-auth test deselected because the loaded development secret intentionally differs from that test's hard-coded bearer.
- An 8-second video plus ready blog fixture is projected into the calendar contract and scheduled atomically in regression tests.
- Real browser: `/batches/e4e911cf-c055-4eb0-9c05-644c755957de#publish-workflow` on the isolated local server at port 8001.
- Exercised social date/time, blog datetime, destination change, paired event rendering, selected event, item validation, readiness transition, weekdays/7-day switch, 1600px desktop, 390px mobile, and console errors.
- The live external scheduling button was not submitted because it would arm connected publishing accounts; the final request path is covered through the transactional fake-database test.

## Reference history

- `references/design-system.png`: selected calendar-planner visual source.
- `references/representative-view.png`: earlier unified-table alternative.
- `references/design-style.png`: earlier rules-and-exceptions alternative.

The integrated code is now the operational source of truth. The selected image remains the visual intent reference.
