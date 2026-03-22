# Multi-Script Variant Expansion

Date: 2026-03-22
Status: Active

## Overview

The variant expansion system maximizes the number of unique scripts per topic in the script bank. Instead of generating one script per deep research lane, it produces multiple scripts per topic by varying the **framework** (PAL, Testimonial, Transformation) and **hook style** (from the hook bank YAML) for each generation.

This allows the system to run for months without script repetition, even with a finite topic bank.

## How It Works

### The Diversity Matrix

Each script is identified by a unique combination of:

```
(topic_registry_id, target_length_tier, post_type, framework, hook_style)
```

For a value topic with 3 frameworks and 6 hook styles, that's up to **18 unique scripts per tier** — and with 3 tiers (8s, 16s, 32s), up to **54 scripts per topic**.

A configurable cap (`max_scripts_per_topic = 20` per topic/tier/post_type combination) prevents over-generation.

### Diversity-First Selection

When generating a new variant, `pick_next_variant()` picks the **most diverse unused combination**:

1. Query all existing scripts for the topic/tier/post_type
2. Extract used (framework, hook_style) pairs
3. Compute unused combinations from available frameworks × hook styles
4. Rank by: least-represented framework first, then least-represented hook style
5. Return the top pick, or `None` if exhausted

This ensures early scripts cover the widest variety — even if the cron only gets through half the combinations, the bank has diverse content.

### Two Generation Paths

**Value posts (PROMPT_1 pipeline):**
1. Load the stored research dossier (no new deep research call)
2. Pick the lane_candidate matching the target framework
3. Build a variant prompt via `build_prompt1_variant()` — injects hook bank + forced framework/hook constraints
4. Call Gemini with structured JSON output
5. Parse and store in `topic_scripts`

**Lifestyle posts (PROMPT_2 pipeline):**
1. No dossier needed — uses topic title as context
2. Build PROMPT_2 with forced framework/hook constraints appended
3. Call Gemini with structured JSON output
4. Parse and store in `topic_scripts`

Both paths are **completely isolated** from the existing pipelines. The original `build_prompt1()`, `generate_topic_script_candidate()`, and `generate_dialog_scripts()` are untouched.

## Available Frameworks and Hook Styles

### Value Posts

**Frameworks** — from each topic's research dossier (`framework_candidates`):
- PAL (Problem-Agitate-Lösung)
- Testimonial
- Transformation

**Hook styles** — from `app/features/topics/prompt_data/hook_bank.yaml`:
- Fragen ("Kennst du...?", "Weißt du...?")
- Direkte Aussagen ("Check mal...", "Schau dir an...")
- Empathische Hooks ("Stell dir vor...", "Ich zeig dir...")
- Kontrast- und Mythos-Hooks ("Die größte Lüge über...", "Fast alle denken...")
- Konsequenz- und Friktions-Hooks ("Wenn du...ignorierst...", "Dieser kleine Fehler...")
- Aha- und Handlungs-Hooks ("Bevor du...", "Alles verändert sich...")

### Lifestyle Posts

**Frameworks:** PAL, Testimonial, Transformation (predefined constants)

**Hook styles:** personal_story, daily_tip, community_moment, challenge, humor (predefined constants)

## Triggers

### 1. Daily Cron (Automatic)

Runs inside the worker container every 24 hours. See `docs/cron-script-expansion.md` for full details.

- Generates up to 30 scripts per run
- Prioritizes topics with fewest existing scripts
- Iterates all tiers (8s, 16s, 32s)
- First run triggers on container startup

### 2. API Endpoint (Manual)

```
POST /topics/expand-variants
Content-Type: application/json

{
    "topic_registry_id": "8c016e68-...",
    "count": 5,
    "target_length_tier": 8
}
```

Returns:
```json
{
    "topic_registry_id": "8c016e68-...",
    "post_type": "value",
    "target_length_tier": 8,
    "generated": 5,
    "total_existing": 12,
    "details": [
        {"framework": "Testimonial", "hook_style": "Fragen", "script": "Wusstest du..."},
        {"framework": "Transformation", "hook_style": "Direkte Aussagen", "script": "Schau dir an..."}
    ]
}
```

### 3. CLI Test Script (Development)

```bash
# Dry run — show what would be generated
python scripts/test_variant_expansion.py --dry-run

# Generate 3 variants for value topics, tier 8s
python scripts/test_variant_expansion.py --count 3 --post-type value --tier 8

# Generate 5 variants, all post types
python scripts/test_variant_expansion.py --count 5
```

## Database Schema

### topic_scripts Table (Key Columns)

| Column | Type | Constraint | Purpose |
|--------|------|------------|---------|
| `topic_registry_id` | uuid | FK → topic_registry | Parent topic |
| `target_length_tier` | integer | 8, 16, or 32 | Video duration tier |
| `post_type` | text | NOT NULL | "value" or "lifestyle" |
| `framework` | text | NOT NULL | PAL, Testimonial, or Transformation |
| `hook_style` | text | NOT NULL | Hook family name from hook bank |
| `script` | text | NOT NULL | The generated German script text |
| `use_count` | integer | default 0 | Tracks how often this script has been used |

### Unique Constraint

```sql
UNIQUE (topic_registry_id, target_length_tier, post_type, framework, hook_style)
```

Prevents duplicate variant combinations. Also acts as an idempotency guard — re-running the cron after a partial failure won't create duplicates.

### Legacy Rows

Rows created before variant expansion have `hook_style` values like `"general"`, `"action"`, `"consequence"` (from the original `_build_script_variants` in hub.py). These don't collide with the hook bank vocabulary and are safe to leave as-is.

## Configuration

| Setting | Default | Location | Description |
|---------|---------|----------|-------------|
| `DEFAULT_MAX_SCRIPTS_PER_TOPIC` | 20 | `variant_expansion.py` | Cap per (topic, post_type, tier) |
| `DEFAULT_MAX_SCRIPTS_PER_CRON_RUN` | 30 | `variant_expansion.py` | Max scripts per daily cron run |
| `EXPANSION_INTERVAL_SECONDS` | 86400 (24h) | `video_poller.py` | How often the cron runs |
| `EXPANSION_MAX_SCRIPTS_PER_RUN` | 30 | `video_poller.py` | Passed to expand_script_bank |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Worker Container                       │
│                                                           │
│  video_poller.py main loop (every 10s)                   │
│    ├── poll_pending_videos()        [unchanged]          │
│    └── _maybe_expand_script_bank()  [NEW - daily]        │
│           │                                               │
│           ▼                                               │
│  variant_expansion.py                                     │
│    ├── expand_script_bank()         [cron entry point]   │
│    │     └── for each topic × tier:                      │
│    │           expand_topic_variants()                    │
│    │                                                      │
│    ├── expand_topic_variants()      [orchestration]      │
│    │     ├── pick_next_variant()    [diversity logic]    │
│    │     ├── build_prompt1_variant() [value prompt]      │
│    │     │   or generate_dialog_scripts_variant()        │
│    │     │                          [lifestyle prompt]   │
│    │     ├── Gemini LLM call                             │
│    │     └── upsert_topic_script_variants()  [DB store]  │
│    │                                                      │
│    └── pick_next_variant()          [pure function]      │
│          └── diversity ranking by framework × hook_style  │
│                                                           │
├───────────────────────────────────────────────────────────┤
│                    Existing (Untouched)                    │
│                                                           │
│  research_runtime.py    — generate_topic_script_candidate │
│  handlers.py            — batch seeding logic             │
│  lifestyle_runtime.py   — generate_lifestyle_topics       │
│  prompts.py:build_prompt1() — original prompt builder     │
└───────────────────────────────────────────────────────────┘
```

## Files

| File | What It Does |
|------|-------------|
| `app/features/topics/variant_expansion.py` | Core module: `pick_next_variant`, `expand_topic_variants`, `expand_script_bank`, `generate_dialog_scripts_variant` |
| `app/features/topics/prompts.py` | Added `build_prompt1_variant()` (existing functions untouched) |
| `app/features/topics/queries.py` | Added `get_existing_variant_pairs()`, safety fallbacks on inserts |
| `app/features/topics/handlers.py` | Added `POST /topics/expand-variants` endpoint |
| `workers/video_poller.py` | Added `_maybe_expand_script_bank()` daily trigger |
| `app/features/topics/prompt_data/hook_bank.yaml` | Hook style definitions (6 families + banned patterns) |
| `supabase/migrations/016_enforce_framework_hook_style.sql` | Backfill, dedup, NOT NULL + unique constraint |
| `scripts/test_variant_expansion.py` | CLI test with `--dry-run` and `--count` flags |
| `docs/cron-script-expansion.md` | Cron service documentation |

## Tests

| Test File | Coverage |
|-----------|----------|
| `tests/test_variant_expansion.py` | `pick_next_variant` diversity logic (7 tests) |
| `tests/test_prompt1_variant.py` | `build_prompt1_variant` hook bank injection (3 tests) |
| `tests/test_lifestyle_variant.py` | `generate_dialog_scripts_variant` constraint injection (1 test) |
| `tests/test_expand_topic_variants.py` | `expand_topic_variants` orchestration + exhaustion (2 tests) |
| `tests/test_expand_script_bank.py` | `expand_script_bank` cron cap enforcement (1 test) |
| `tests/test_expand_variants_endpoint.py` | API endpoint response (1 test) |

Run all: `python -m pytest tests/test_variant_expansion.py tests/test_prompt1_variant.py tests/test_lifestyle_variant.py tests/test_expand_topic_variants.py tests/test_expand_script_bank.py tests/test_expand_variants_endpoint.py -v`
