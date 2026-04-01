# Editable VEO Prompt Sections

**Date:** 2026-04-01
**Phase:** S4_SCRIPTED / S5_PROMPTS_BUILT
**Status:** Approved

## Problem

After auto-generating a video prompt (S4 → S5), users have no way to modify the prompt sections before video generation. The prompt is built from hardcoded template values and the dialogue from the scripting phase. Users need the ability to fine-tune individual sections (Character, Scene, Action, etc.) while preserving the structured format that VEO 3.1 expects.

## Principle

**"What you see is what VEO gets."** The structured sections the user edits are reassembled into the flat `veo_prompt` text using the same `OPTIMIZED_PROMPT_TEMPLATE`. The downstream video generation path is completely untouched.

## Design

### Flow

1. User clicks "Generate Prompt" (existing) — auto-builds prompt with hardcoded defaults, stores in `video_prompt_json`
2. User opens "Maximize Prompt" modal (existing) — sees read-only sections
3. User clicks "Edit Prompt" (new) — sections become editable textareas
4. User modifies sections, clicks "Save"
5. `PATCH /posts/{post_id}/prompt` receives edits, merges into structured fields, rebuilds `veo_prompt` and `optimized_prompt` from the same template with user values, stores updated `video_prompt_json`
6. Video generation reads `veo_prompt` from `video_prompt_json` exactly as before

### Editable Sections

| UI Section | VideoPrompt Field | Template Placeholder |
|---|---|---|
| Character | `character` | `{character}` (new) |
| Style | `style` | `{style}` (new) |
| Action | `action` | `{action_direction}` |
| Scene | `scene` | `{scene}` (new) |
| Cinematography | `cinematography` | `{cinematography}` (new) |
| Dialogue | `audio.dialogue` | `{dialogue}` |
| Ending | `ending_directive` (new field, see below) | `{ending}` |
| Audio | `audio.capture` | `{audio}` |
| Negatives | `universal_negatives` | `{negatives_section}` |
| VEO Negative Prompt | `veo_negative_prompt` | Sent as separate VEO API param |

**Ending field note:** Currently the ending directive is determined by `_get_prompt_contract()` based on `prompt_mode` and is not stored as a separate field in `VideoPrompt`. During initial prompt generation, the ending value (`STANDARD_FINAL_ENDING_DIRECTIVE`) will be stored as a new `ending_directive` field in `video_prompt_json` so it can be displayed and edited. A new optional `ending_directive` field is added to the `VideoPrompt` schema with the standard default.

Sections NOT shown in the edit UI (don't flow into the VEO prompt): `lighting`, `color_and_grade`, `resolution_and_aspect_ratio`, `camera_positioning_and_motion`, `composition`, `focus_and_lens_effects`, `atmosphere`, `authenticity_modifiers`.

### Backend

#### New Endpoint: `PATCH /posts/{post_id}/prompt`

**Request body:**
```json
{
  "character": "...",
  "style": "...",
  "action": "...",
  "scene": "...",
  "cinematography": "...",
  "dialogue": "...",
  "ending": "...",
  "audio_capture": "...",
  "universal_negatives": "...",
  "veo_negative_prompt": "..."
}
```

**Handler logic:**
1. Fetch post, verify `video_prompt_json` exists (can't edit what hasn't been generated)
2. Validate all fields are non-empty strings
3. Merge edits into existing `video_prompt_json` structured fields
4. Rebuild `veo_prompt` via `build_optimized_prompt()` with edited values and `negative_constraints=None`
5. Rebuild `optimized_prompt` via `build_optimized_prompt()` with edited values and `negative_constraints=edited_negatives`
6. Update `veo_negative_prompt` from the edited value
7. Store updated `video_prompt_json`
8. Return success with updated prompt data

**No state transitions triggered. No batch reconciliation. The prompt was already built.**

#### New Schema: `UpdatePromptRequest`

Pydantic model in `app/features/posts/schemas.py` with all 10 editable fields as required `str` fields.

#### Template Parameterization

`OPTIMIZED_PROMPT_TEMPLATE` in `prompt_builder.py` is updated to parameterize all editable sections:

```python
OPTIMIZED_PROMPT_TEMPLATE = (
    "Character:\n{character}\n\n"
    "Style:\n{style}\n\n"
    "Action:\n{action_direction}\n\n"
    "Scene:\n{scene}\n\n"
    "Cinematography:\n{cinematography}\n\n"
    "Dialogue:\n\"{dialogue}\"\n\n"
    "Ending:\n{ending}\n\n"
    "{audio}{negatives_section}"
)
```

`build_optimized_prompt()` gains keyword arguments for `character`, `style`, `scene`, `cinematography` with current hardcoded values as defaults. Existing callers pass no extra args and get identical output.

### Frontend

#### Modal Edit Mode (`_post_modals.html`)

Alpine.js `editing` state variable controls the mode:

- **View mode** (default): Read-only `<p>` elements, "Edit Prompt" + "Close" buttons
- **Edit mode**: `<textarea>` elements auto-sized to content, "Save" + "Cancel" buttons

"Save" sends HTMX `PATCH` to `/posts/{post_id}/prompt` with all section values. On success, reloads page to update the snapshot card. On error, shows inline message, stays in edit mode.

"Cancel" discards changes, returns to view mode.

"Regenerate Prompt" button (existing) resets to auto-generated defaults — serves as the undo path.

## What Does NOT Change

- `build_video_prompt_from_seed()` auto-generation path (same defaults)
- Video generation handler (`POST /videos/{post_id}/generate`)
- VEO submission logic (`_build_veo_prompt_text()`, `_build_veo_negative_prompt()`, `_build_provider_prompt_request()`)
- State machine transitions and batch reconciliation
- Database schema (same `video_prompt_json` JSONB column, same shape)

## Files Changed

| File | Change |
|---|---|
| `app/features/posts/prompt_builder.py` | Parameterize `OPTIMIZED_PROMPT_TEMPLATE`, update `build_optimized_prompt()` signature |
| `app/features/posts/handlers.py` | New `PATCH /posts/{post_id}/prompt` endpoint |
| `app/features/posts/schemas.py` | New `UpdatePromptRequest` model |
| `templates/batches/detail/_post_modals.html` | Edit/view toggle, textareas, Save/Cancel buttons |
