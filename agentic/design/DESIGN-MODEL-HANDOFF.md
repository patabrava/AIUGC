# Scheduling Workspace Design Handoff

## Product

Lippe Lift Studio helps an editor prepare and publish a batch of short social videos. Some videos also have a related blog post. The editor reaches this screen after the content is produced and approved.

## User and setting

The user is an experienced content operator working on a desktop. They may schedule one item or a larger batch. Their priority is certainty: they need to know what will publish, where it will publish, when each social video and blog post will go live, and what still blocks the final action.

## Core job

Set shared publishing choices once, inspect the resulting schedule for every item, make individual exceptions when needed, and schedule the complete batch with one final confirmation.

## Content relationship

Each item always has a social video. An item may also have a blog post. Social and blog times belong together because the editor thinks of them as one content package, even when they publish at different times.

## Important rules

- Instagram, Facebook, and TikTok can be selected for the batch when connected.
- A timezone applies to the full schedule.
- Every social video needs a future date and time.
- Social videos need at least 30 minutes between them.
- A blog post needs ready text, a ready image, and its own future publication time.
- TikTok has shared defaults, but every video must show whether its individual title, privacy, disclosure, and consent are ready.
- Already scheduled or published items appear as locked evidence, not editable controls.
- The final action schedules all ready social videos and enabled blogs together.

## Experience goals

The workspace should feel calm, exact, and operational. The operator should understand the full batch in one scan, then focus only on exceptions. Shared settings should visibly flow into items. Individual overrides should be obvious and reversible. Compliance detail should remain compact until attention is required.

## Visual direction

Use the existing Lippe Lift identity: crisp white surfaces, deep navy text, confident blue controls, orange reserved for immediate publishing or urgent attention, and pale blue for selected or inherited states. Use Outfit for section headings and Instrument Sans for interface text. Favor restrained borders and flat surfaces over nested cards, glass effects, or decorative gradients.

## Accessibility

Meet WCAG AA. Keep form labels visible, target sizes comfortable, focus states clear, and statuses understandable without color. Provide keyboard alternatives for any visual calendar or reordering interaction. Avoid relying on truncated titles to identify an item.

## Mockup directions

### Option 1: Unified schedule table

A compact top toolbar holds destinations, timezone, start date, and auto-fill. Below it, one table row represents one content package with title, status, social time, blog time, TikTok readiness, and an edit action. A sticky right-side review summary lists blockers and contains the final batch action.

### Option 2: Calendar planner

A weekly calendar makes publishing cadence visible. Social video and blog events use related shapes and are connected by item identity. A narrow item list and focused inspector support precise keyboard-friendly date, time, caption, and TikTok edits. The final batch summary remains visible.

### Option 3: Batch rules and exceptions

The operator first defines a simple batch recipe: destinations, first publication, cadence, blog timing relative to each video, and TikTok defaults. A compact review list then shows the generated result for every item. Each line states “uses batch rule” or “custom,” and only exceptions expand. Final confirmation summarizes inherited and custom schedules.

## Reference images

- `references/representative-view.png`: Option 1, unified schedule table.
- `references/design-system.png`: Option 2, weekly calendar planner.
- `references/design-style.png`: Option 3, batch rules and exceptions.

## Output request

Create three polished, high-fidelity desktop mockups using realistic German content and the attached current-page screenshots only as product evidence. Each mockup must show a complete scheduling workspace, not an isolated component. Preserve the same underlying product behavior while making the three interaction models visibly different.

## Selection

Option 2, the weekly calendar planner, was selected for implementation. `references/design-system.png` remains the visual intent reference.
