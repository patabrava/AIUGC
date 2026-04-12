# Manual Batch Mode Design

Date: 2026-04-12
Scope: batch creation flow with a manual drafting path alongside the current automated research-agent path
Status: Draft

## Summary

Add a batch creation mode toggle so the user can choose either:

- `Automated`: current research-agent seeding flow
- `Manual`: user-authored drafts with editable script and prompt basis

Manual mode keeps the batch lifecycle, review screens, prompt assembly, QA, and publishing pipeline intact. The difference is only how the batch is seeded and edited at the start.

The manual path must not prefill post type. Instead, it creates blank manual draft posts and lets the user define the post type themselves while editing the script and prompt.

## Why This Shape

This is the smallest clean extension of the current app:

- It avoids splitting the product into two unrelated workflows.
- It reuses the current batch container and downstream state machine.
- It keeps the existing automated path stable.
- It gives manual users full control over post type, script, and prompt without adding a separate editor system.

## Goals

- Let the user choose `Automated` or `Manual` when creating a batch.
- Preserve the current automated research-agent batch flow.
- Add a manual drafting flow that creates editable draft posts.
- Allow manual posts to define their own post type instead of assigning it up front.
- Reuse the existing prompt-builder basis from `app/features/posts/prompt_builder.py`.
- Reuse the current batch detail and post editing surfaces as much as possible.
- Keep the downstream prompt/video/QA/publish lifecycle unchanged.

## Non-Goals

- No separate manual-only state machine.
- No separate manual-only product area.
- No rewrite of the automated topic research pipeline.
- No redesign of prompt generation itself.
- No new external dependencies.

## Proposed User Flow

### Automated Batch

1. User creates a batch.
2. User selects `Automated`.
3. User enters the usual batch inputs.
4. The system runs the current research-agent seeding flow.
5. The batch lands in the existing seeded/review stage.

### Manual Batch

1. User creates a batch.
2. User selects `Manual`.
3. User enters:
   - batch brand
   - target length tier
   - number of manual draft posts
4. The system creates that many blank draft posts.
5. Each draft starts with a prompt-builder basis template, but no predefined post type.
6. The user edits:
   - the post type
   - the script
   - the prompt basis / prompt sections
7. After saving, the batch continues through the normal review and generation flow.

## Data Model

### Batch Fields

Add a batch-level mode field:

- `creation_mode: automated | manual`

Add a manual count field for the initial blank drafts:

- `manual_post_count: integer`

Behavior:

- Automated batches continue to use the existing `post_type_counts`.
- Manual batches use `manual_post_count` instead of `post_type_counts` for initial creation.
- Manual batches still store the batch as a normal batch row so the rest of the app can load it normally.

### Manual Post Shape

Manual draft posts should start with:

- empty or null post type
- editable `seed_data.script`
- prompt-builder basis values preloaded from the existing prompt defaults
- `script_review_status = pending`
- no topic research metadata

The post type becomes a user-chosen freeform field, not a pre-seeded classification. The UI may offer suggestions, but it must not require the user to pick from the automated `value / lifestyle / product` set.

## Backend Design

### Batch Creation

Extend batch creation validation to accept a `creation_mode`.

- If `creation_mode = automated`, require the current type-count payload.
- If `creation_mode = manual`, require `manual_post_count` and ignore type counts.

The batch creation handler should branch early:

- automated mode calls the current discovery scheduler
- manual mode creates blank draft posts and does not start topic research

### Draft Initialization

Manual drafts should be initialized from a small reusable template helper that:

- loads the prompt-builder basis for the selected target length tier
- creates a blank script field for the user to edit
- leaves post type unset
- stores only the minimum metadata needed for later prompt generation

This helper should stay close to the batch/post feature boundary, not inside the topic research code.

### Manual Editing

Manual post editing should reuse the current post update endpoints where possible:

- script edits should continue to invalidate any stale prompt JSON
- prompt edits should continue to update the provider-ready prompt JSON
- post type changes should be saved on the post record alongside the script data

If a new endpoint is needed, it should be narrow and only cover manual draft initialization or manual post metadata updates.

### State Machine

Manual batches should remain inside the existing batch lifecycle.

Recommended behavior:

- batch starts in `S1_SETUP`
- manual drafts are created
- batch moves into the same seeded/review state used by the current script review flow

The important rule is that manual mode does not introduce a separate long-lived parallel state machine.

## UI Design

### Batch Create Modal

Add a mode toggle:

- `Automated`
- `Manual`

When `Automated` is selected:

- show the existing value/lifestyle/product count inputs

When `Manual` is selected:

- hide the type-count inputs
- show a single `manual_post_count` input
- keep brand and target length inputs

### Manual Draft Editing Surface

Manual batch detail should show each blank draft as editable.

The draft card should support:

- defining the post type as freeform text
- entering or rewriting the script
- adjusting the prompt basis before prompt generation

The prompt basis should remain clearly tied to the existing prompt-builder structure so the user understands they are editing a template, not a provider-specific final prompt.

## Error Handling

- Manual batch creation must fail fast if `manual_post_count` is missing or invalid.
- Manual posts must reject empty scripts before they are allowed to proceed.
- If a manual draft is missing its prompt basis, the system should rebuild it from the prompt-builder defaults instead of failing the whole batch.
- Automated batch behavior should remain unchanged if manual validation fails.

## Testing

Add coverage for:

- batch creation validation for both modes
- manual batch initialization
- manual draft default shape
- script edit invalidation of prompt JSON
- prompt editing path on a manual draft
- no topic research scheduling for manual mode
- automated path remains unchanged

Suggested test locations:

- `tests/test_batches_create_manual_mode.py`
- `tests/test_manual_batch_drafts.py`
- `tests/test_posts_manual_editing.py`

## Implementation Budget

{files: 5-7, LOC/file: <=250 target, <=500 hard, deps: 0}

Likely files:

1. `app/features/batches/schemas.py`
2. `app/features/batches/queries.py`
3. `app/features/batches/handlers.py`
4. `app/features/posts/handlers.py`
5. `templates/batches/list.html`
6. `templates/batches/detail/_post_card.html`
7. one small test file if the existing coverage can’t be extended in place

## Recommendation

Use `manual_post_count` and create the blank drafts immediately after the manual batch is created. That keeps the UX short and avoids a second setup step.
