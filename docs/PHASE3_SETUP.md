# Phase 3: Video Prompt Assembly - Setup Guide

## Overview

Phase 3 implements **simple prompt assembly** that inserts Phase 2 dialogue into your video generation template. No LLM agents needed - just deterministic template population.

## What It Does

1. Takes `dialog_script` from Phase 2 seed data
2. Inserts it into the `audio.dialogue` field of your video generation template
3. Validates the complete prompt structure
4. Stores the assembled prompt in `video_prompt_json` field
5. Returns prompt ready for video generation API submission

## Database Migration

Run the migration to add the `video_prompt_json` field:

```bash
# Connect to Supabase and run:
psql $DATABASE_URL -f migrations/003_add_video_prompt_field.sql
```

Or via Supabase dashboard:
1. Go to SQL Editor
2. Paste contents of `migrations/003_add_video_prompt_field.sql`
3. Run query

## API Endpoint

### Build Video Prompt

**Endpoint:** `POST /posts/{post_id}/build-prompt`

**Request:**
```bash
curl -X POST http://localhost:8000/posts/{post_id}/build-prompt
```

**Response:**
```json
{
  "ok": true,
  "data": {
    "id": "post-uuid",
    "video_prompt": {
      "character_definition": "38-year-old German woman...",
      "action": "Sits in a wheelchair...",
      "style": "Smartphone selfie, UGC authenticity...",
      "camera_positioning_and_motion": "Medium close-up...",
      "composition": "Head-and-shoulders centered...",
      "focus_and_lens_effects": "Face-priority autofocus...",
      "ambiance": "Bright, soft, diffuse frontal...",
      "audio": {
        "dialogue": "YOUR PHASE 2 DIALOGUE HERE",
        "sfx": "Recorded through modern smartphone mic...",
        "ambient": "Faint, even room-tone bed..."
      },
      "style_modifiers_dos": "smartphone selfie, handheld realism...",
      "style_negatives_donts": "subtitles, captions, watermark...",
      "tech_specs": {
        "resolution": "720x1280",
        "fps": 30,
        "aspect_ratio": "9:16",
        "take_structure": "single continuous take",
        "no_overlays": true,
        "no_music": true
      }
    },
    "state_ready": "S5_PROMPTS_BUILT"
  }
}
```

## Template Structure

The video prompt template includes all fields from your specification:

- **character_definition**: Character appearance and filming style
- **action**: Character actions and behavior
- **style**: Visual style and aesthetic
- **camera_positioning_and_motion**: Camera setup and movement
- **composition**: Scene composition and framing
- **focus_and_lens_effects**: Focus and lens characteristics
- **ambiance**: Lighting and atmosphere
- **audio**: 
  - `dialogue`: **Inserted from Phase 2 dialog_script**
  - `sfx`: Audio processing notes
  - `ambient`: Ambient sound description
- **style_modifiers_dos**: Positive style modifiers
- **style_negatives_donts**: Negative style modifiers to avoid
- **tech_specs**: Technical specifications (resolution, fps, aspect ratio, etc.)

## Testing

Run the Phase 3 testscript:

```bash
python tests/testscript_phase3.py
```

**Prerequisites:**
- Server running on `http://localhost:8000`
- At least one batch in S2_SEEDED or S4_SCRIPTED state
- Posts have seed_data with dialog_script from Phase 2

**What it tests:**
1. Creates a batch
2. Builds video prompt for first post
3. Verifies dialogue insertion
4. Validates all required template fields
5. Confirms persistence to database

## Usage Flow

```
Phase 2: Topic Discovery → seed_data with dialog_script
    ↓
Phase 3: Build Prompt → video_prompt_json assembled
    ↓
Phase 4: Video Generation → Submit prompt to Veo/Sora API
```

## Code Structure

```
app/features/posts/
├── schemas.py           # VideoPrompt, AudioConfig, TechSpecs
├── prompt_builder.py    # build_video_prompt_from_seed()
└── handlers.py          # POST /posts/{id}/build-prompt
```

## Validation

All prompts are validated using Pydantic schemas:

- ✅ Required fields presence
- ✅ Audio.dialogue populated from seed_data
- ✅ Tech specs match template defaults
- ✅ Schema conformance before storage

## Error Handling

| Error | Code | Reason |
|-------|------|--------|
| Post not found | 404 | Invalid post_id |
| Missing seed_data | 422 | Phase 2 not completed |
| Missing dialogue | 422 | No dialog_script in seed_data |
| Validation failed | 422 | Prompt doesn't match schema |

## Next Steps

After Phase 3:
1. Prompts are stored in `video_prompt_json` field
2. Ready for Phase 4: Video Generation
3. Submit prompts to Veo 3.1 or Sora 2 API
4. Poll for completion and store video assets
