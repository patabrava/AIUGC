# CODEX-CONTRACT

## Task signal

Redesign the final publishing workspace so an operator can schedule a complete batch and still adjust individual social videos and blog posts without losing context.

## Product boundary

- The final publishing surface is rendered in `templates/batches/detail/_publish_panel.html` and driven by `batchPublishComponent` in `static/js/batches/detail.js`.
- Brand primitives live in `static/css/brand.css` and use Outfit for headings, Instrument Sans for interface text, deep navy, Lippe blue, orange, pale blue, and white surfaces.
- The current batch page is server-rendered Jinja with Alpine state and Tailwind utility classes.
- The selected direction is Option 2, the weekly calendar planner. It is implemented in the production Publish step.

## Binding behavior

- One final action schedules all editable social videos and enabled blog posts atomically.
- Batch-level choices: connected destinations, timezone, and a starting week or schedule rule.
- Per-video choices: social date/time, caption, optional destination override, immediate publish action, and TikTok settings.
- Per-blog choices: enabled state, content readiness, preview-image readiness, and publication date/time.
- TikTok settings may be prefilled from batch defaults, but title/privacy/disclosure/consent readiness is evaluated per video.
- Scheduled, publishing, and published items are immutable in the scheduling editor.
- Social dispatches require valid future times and at least 30 minutes of separation.
- The final review must name every blocking issue and take the operator directly to the source.
- Blog draft review remains reachable from the publishing workspace.

## Data and action contracts to preserve

- Final schedule submission uses the existing atomic batch arm boundary.
- Each submitted post retains its post id, caption, social time override, destination override, and optional blog publication timestamp.
- Connected Instagram, Facebook, and TikTok state remains authoritative.
- TikTok creator capability and available privacy options remain authoritative.
- Individual TikTok settings and consent continue to persist through the existing per-post settings boundary.
- Immediate publishing remains a separate per-video action with an explicit confirmation surface.

## Required states

- New unscheduled batch.
- Partially ready batch with one or more named issues.
- Fully ready batch awaiting final confirmation.
- Active schedule with all items locked.
- Mixed batch containing social-only and social-plus-blog items.
- Disconnected destination.
- Blog text or image not ready.
- TikTok settings inherited but consent not yet confirmed.
- Time conflict, past time, and invalid time.
- Long German titles and captions.

## Interaction requirements

- Make the batch schedule and individual overrides visible in one mental model.
- Distinguish inherited values from intentional overrides.
- Keep compliance detail secondary until it blocks scheduling.
- Show social and blog timing together for each content item.
- Keep one unmistakable final batch action visible after the operator scrolls.
- All primary actions need keyboard access, visible focus, explicit labels, and non-color status text.
- At narrow widths, preserve item identity and split social/blog time controls into stacked groups without horizontal scrolling.

## Design language

Mode: `code-only`.

The scheduling surface needs dense, legible controls and status relationships rather than illustrative assets. Preserve the recognizable Lippe Lift palette while reducing nested cards, excessive vertical disclosure, and repeated form chrome.

## Selected design direction

Calendar planner: a keyboard-accessible week canvas with paired social/blog events, a content queue, a focused item inspector, explicit TikTok readiness, and a sticky atomic batch review. The default view shows Monday through Friday; weekends can be revealed and appear automatically when they contain scheduled content.

## Validation path for later implementation

- Run the real batch detail page at the final Publish step.
- Verify batch destinations, timezone, auto-fill, per-item social and blog timing, TikTok readiness, issue navigation, post-now confirmation, final atomic scheduling, and locked scheduled state.
- Browser-check desktop and mobile widths, keyboard focus order, long-title wrapping, no clipped overlays, and no console or layout errors.
