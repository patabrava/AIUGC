# Multi-Script Variant Expansion

Date: 2026-03-22
Status: Draft

## Problem

The topic bank is finite. Once the system cycles through all topics, scripts repeat. The system needs to run for months without duplicates. Today, each deep research run produces one script per lane_candidate, yielding at most 12 scripts per research call. This ceiling is too low for long-term content variety.

## Goal

Maximize unique scripts per topic by generating multiple variants per lane using a structured framework x hook matrix. Support both value (PROMPT_1) and lifestyle (PROMPT_2) post types from a unified script bank. Achieve this without modifying the existing proven pipelines.

## Approach: Stateless Diversity Query

No new tables. When the system needs a new script variant for a topic, it queries existing `topic_scripts` for that topic, determines which `(framework, hook_style)` combinations are unused, picks the most diverse next combination, and generates a script constrained to that combination.

## 1. Unified Script Bank

`topic_scripts` is the single source of scripts for all post types.

**Schema enforcement:**
- `topic_scripts.framework` — must be populated on every insert (NOT NULL)
- `topic_scripts.hook_style` — must be populated on every insert (NOT NULL)
- New unique constraint: `(topic_registry_id, target_length_tier, post_type, framework, hook_style)` — prevents duplicate combinations

**Migration plan for NOT NULL enforcement:**
1. Backfill existing rows: set `framework = 'PAL'` and `hook_style = 'default'` where NULL (preserves existing data, marks legacy rows as identifiable)
2. Update all existing insert code paths in `queries.py` and `hub.py` to always provide `framework` and `hook_style` values
3. Apply NOT NULL constraint and unique constraint after backfill
4. Rollback: migration is reversible — drop constraint and restore NULL allowance if needed

**Batch seeder behavior:**
- Queries `topic_scripts WHERE post_type = ? AND use_count = (lowest)` to pick the least-used script, ensuring rotation before repetition.

## 2. Diversity-First Variant Selection

A pure function `pick_next_variant()` determines what to generate next.

**Inputs:**
- `topic_registry_id`
- `target_length_tier`
- `post_type`

**Logic:**
1. Query all existing `topic_scripts` for `(topic_registry_id, target_length_tier, post_type)`
2. Extract the set of used `(framework, hook_style)` pairs
3. Determine available combinations:
   - **Value posts:** frameworks from `dossier.framework_candidates`, hook styles from the hook bank YAML (`get_hook_bank()`). Note: `dossier.angle_options` are content angles (factual perspectives), not hook styles (opening patterns). The hook bank provides stylistically distinct openings (e.g., question, bold claim, story opener, myth-buster). These are two different axes — angles vary the content focus, hooks vary the delivery style.
   - **Lifestyle posts:** predefined frameworks (PAL, Testimonial, Transformation) and lifestyle hook styles (personal story, daily tip, community moment, challenge, humor)
4. Compute full matrix of possible combinations, subtract already-generated pairs
5. **Diversity ranking:** prefer the framework with the fewest existing scripts, then within that, the hook style least represented
6. Return the top-ranked `(framework, hook_style)` pair, or `None` if exhausted

**Exhaustion signal:** `None` return means all viable combinations are generated or the cap has been reached.

**Configuration:**
- `max_scripts_per_topic` (default 20) — hard cap per `(topic_registry_id, post_type, target_length_tier)` combination. A topic with 2 post types and 3 tiers could have up to 120 scripts total (20 × 6).

## 3. Script Generation Paths

Two isolated paths. The existing pipeline functions are not modified.

### Value Scripts (PROMPT_1 pipeline)

When `pick_next_variant()` returns a `(framework, hook_style)` for a value topic:

1. Load the stored dossier from `topic_research_dossiers` (no new deep research call)
2. Pick the lane_candidate whose `framework_candidates` list contains the target framework. If multiple lanes match, pick the one with the fewest existing scripts. If no lane matches, fall back to the first lane (the framework constraint in the prompt will still guide the LLM).
3. Build the prompt via a **new** `build_prompt1_variant()` function — extends the existing prompt template with:
   - Hook bank context (loaded via `get_hook_bank()`)
   - Forced framework and hook_style constraints
4. Call `generate_topic_script_candidate()` with the variant prompt
5. Store result in `topic_scripts` with `framework` and `hook_style` populated

**Isolation guarantee:** `build_prompt1()` is never modified. The existing `_harvest_seed_topic_to_bank()` flow continues to call `build_prompt1()` with `hook_bank_section=""` exactly as today.

### Lifestyle Scripts (PROMPT_2 pipeline)

When `pick_next_variant()` returns a pair for a lifestyle topic:

1. No dossier needed — use topic title and registry metadata as context
2. Call a **new** `generate_dialog_scripts_variant()` wrapper that builds the PROMPT_2 prompt with forced `framework` and `hook_style` constraints injected into the prompt text. The existing `generate_dialog_scripts()` function is not modified.
3. Store result in `topic_scripts` with `post_type = "lifestyle"`

**Isolation guarantee:** `generate_dialog_scripts()` and `generate_lifestyle_topics()` are not modified. The existing lifestyle batch seeding path works as-is.

## 4. Cron Job & Hub Integration

### Daily Cron (`expand_script_bank`)

1. Query all topics from `topic_registry`
2. For each topic, count existing scripts per `(post_type, target_length_tier)`
3. Compare against `max_scripts_per_topic`
4. Rank topics by gap (largest gap first — topics with fewest scripts get priority)
5. For each topic with remaining capacity:
   - Call `pick_next_variant()` to get next `(framework, hook_style)`
   - Generate via the appropriate path (value or lifestyle)
   - Store in `topic_scripts`
6. Stop after `max_scripts_per_cron_run` (default 30) to keep run time bounded

**Safeguards:**
- `max_scripts_per_topic` — hard cap per topic
- `max_scripts_per_cron_run` — caps total work per daily run
- Dry-run mode — logs what it would generate without writing to the bank
- Exhausted topics (where `pick_next_variant()` returns `None`) are skipped

### Hub Manual Trigger

- Existing topic hub page gets an "Expand variants" option per topic
- Calls the same `expand_topic_variants()` function the cron uses
- Shows progress and results in the UI

## 5. Lifestyle Compatibility

Lifestyle posts already persist to `topic_registry` + `topic_scripts` via `store_topic_bank_entry()` and `upsert_topic_script_variants()` in the batch seeding handler.

**What changes:**
- Cron variant expansion works for lifestyle topics using PROMPT_2 with forced constraints
- Lifestyle hook styles are a predefined set: personal story, daily tip, community moment, challenge, humor
- The 5 existing lifestyle templates can be expanded over time; variant expansion means even those 5 yield many more unique scripts

**What stays the same:**
- `generate_lifestyle_topics()` continues working as-is for batch seeding
- Variant expansion is additive — no changes to the existing lifestyle flow

## 6. Testing Strategy

### Layer 1 — Unit Tests

- `test_pick_next_variant()` — given existing scripts with known (framework, hook_style) pairs, assert it picks the most diverse unused combination
- `test_pick_next_variant_exhausted()` — returns `None` when all combinations are used or cap is reached
- `test_pick_next_variant_diversity_ranking()` — verify least-represented framework is chosen first

### Layer 2 — Integration Tests

- Value variant: mock the LLM, call `expand_topic_variants()` for a value topic with a stored dossier, verify `topic_scripts` row is created with correct framework/hook_style
- Lifestyle variant: same, but via PROMPT_2 path
- Regression: verify `_harvest_seed_topic_to_bank()` still works identically
- Regression: verify `generate_lifestyle_topics()` still works identically

### Layer 3 — Live CLI Test (`scripts/test_variant_expansion.py`)

A runnable script that hits real Gemini API and Supabase with terminal output:

- Prints each step: topic selected, variant picked, prompt sent, script generated, row stored
- Shows the generated script text for quality inspection
- Shows the `(framework, hook_style)` pair used and what was already in the bank
- Tests both value and lifestyle paths
- Prints summary table at the end: topic → total scripts → new scripts added → remaining capacity
- Optional `--dry-run` flag that does everything except the DB write

## Configuration Defaults

| Setting | Default | Description |
|---------|---------|-------------|
| `max_scripts_per_topic` | 20 | Hard cap per `(topic, post_type, tier)` combination |
| `max_scripts_per_cron_run` | 30 | Maximum scripts generated per daily cron execution |
| `dry_run` | `false` | When true, log actions without writing to database |

## Error Handling & Resilience

- Each script generation is independent. If one fails, log the error and continue to the next.
- LLM rate limits: back off and retry once, then skip and log.
- The unique constraint on `(topic_registry_id, target_length_tier, post_type, framework, hook_style)` acts as an idempotency guard — re-running the cron after a partial failure will not create duplicates; it will attempt the same combinations and either succeed or hit the constraint.
- Partial cron failures are acceptable: the next daily run picks up where the previous one left off by querying the current bank state.
- All generation steps emit structured logs: topic selected, variant picked, generation result, storage outcome.

## Files Affected

**New files:**
- `app/features/topics/variant_expansion.py` — `pick_next_variant()`, `expand_topic_variants()`, `generate_dialog_scripts_variant()`, cron entry point. Constants for lifestyle frameworks and hook styles live here.
- `scripts/test_variant_expansion.py` — live CLI test

**Modified files:**
- `app/features/topics/prompts.py` — add `build_prompt1_variant()` (new function, existing functions untouched)
- `app/features/topics/queries.py` — add query for existing (framework, hook_style) pairs per topic; update existing insert paths to always provide framework/hook_style values
- `app/features/topics/hub.py` — update `_build_value_dialog_scripts_from_prompt1()` and `_persist_topic_bank_row()` to always populate framework/hook_style on inserts; add hub route for "Expand variants" trigger
- Supabase migration — backfill NULLs, enforce NOT NULL on `framework`/`hook_style`, add unique constraint

**Untouched files (explicit isolation):**
- `app/features/topics/research_runtime.py` — no changes
- `app/features/topics/handlers.py` — no changes to batch seeding logic
- `app/features/topics/lifestyle_runtime.py` — no changes
