# Cron Job: Daily Script Bank Expansion

Date: 2026-03-22
Status: Active

## What It Does

Automatically generates new script variants for all topics in the topic bank, once per day. Uses the stored deep research dossiers (no new research calls) to produce scripts with diverse framework x hook_style combinations.

## Where It Runs

Inside the existing **worker** Docker container (`workers/video_poller.py`). No separate container or external scheduler needed.

## How It Works

```
Worker main loop (every 10 seconds):
  1. poll_pending_videos()              <- existing, unchanged
  2. _maybe_expand_script_bank()        <- NEW
       |
       +-- Has 24 hours passed since last run?
            |
            NO  -> return immediately (no-op)
            |
            YES -> expand_script_bank()
                    |
                    +-- Fetch all topics from topic_registry
                    +-- Sort by fewest scripts first (neediest topics get priority)
                    +-- For each topic:
                    |     For each tier (8s, 16s, 32s):
                    |       1. Query existing (framework, hook_style) pairs
                    |       2. pick_next_variant() -> most diverse unused combo
                    |       3. Generate script via Gemini (value: PROMPT_1, lifestyle: PROMPT_2)
                    |       4. Store in topic_scripts with framework + hook_style
                    |       5. Repeat up to 3 times per topic/tier
                    +-- Stop after 30 total scripts
                    +-- Log summary
```

## Timing

| Parameter | Value | Config Location |
|-----------|-------|-----------------|
| Check interval | 10 seconds | `POLL_INTERVAL_SECONDS` in video_poller.py |
| Expansion interval | 24 hours | `EXPANSION_INTERVAL_SECONDS` in video_poller.py |
| Max scripts per run | 30 | `EXPANSION_MAX_SCRIPTS_PER_RUN` in video_poller.py |
| Max scripts per topic | 20 | `DEFAULT_MAX_SCRIPTS_PER_TOPIC` in variant_expansion.py |
| Scripts per topic/tier per run | 3 | Hardcoded in `expand_script_bank()` |

## First Run Behavior

On container startup, `_last_expansion` is set to `0.0`, so the expansion runs immediately on the first loop iteration. After that, it runs every 24 hours.

## What It Generates

For each topic, the system picks the most diverse unused (framework, hook_style) combination:

**Value posts** use:
- Frameworks from the topic's research dossier (`framework_candidates`: PAL, Testimonial, Transformation)
- Hook styles from the hook bank YAML (`hook_bank.yaml`: Fragen, Direkte Aussagen, Empathische Hooks, Kontrast- und Mythos-Hooks, Konsequenz- und Friktions-Hooks, Aha- und Handlungs-Hooks)
- Prompt: `build_prompt1_variant()` with hook bank + forced constraints
- LLM: Gemini (structured JSON output)

**Lifestyle posts** use:
- Frameworks: PAL, Testimonial, Transformation (predefined)
- Hook styles: personal_story, daily_tip, community_moment, challenge, humor (predefined)
- Prompt: `build_prompt2()` with forced constraints appended
- LLM: Gemini (structured JSON output)

## Diversity Logic

`pick_next_variant()` ensures maximum variety:
1. Count how many scripts each framework already has
2. Count how many scripts each hook_style already has
3. Pick the combination where the framework is least represented, then the hook_style is least represented
4. Skip any (framework, hook_style) pair that already exists (enforced by DB unique constraint)
5. Return `None` when all combinations are exhausted or the per-topic cap is reached

## Error Handling

- Each script generation is independent — if one fails, the cron logs it and moves to the next
- If Gemini rate-limits or errors, the script is skipped (logged as `variant_expansion_failed`)
- If the entire expansion crashes, the timestamp is NOT updated, so it retries on the next 10-second cycle
- The unique constraint on `topic_scripts` prevents duplicate inserts even if the cron runs twice
- Video polling is never blocked — expansion runs after `poll_pending_videos()` returns

## Log Events

| Event | Meaning |
|-------|---------|
| `script_bank_expansion_starting` | Daily run begins |
| `expand_script_bank_topic` | Processing one topic/tier |
| `variant_expansion_generated` | One script successfully created |
| `variant_expansion_exhausted` | Topic has no more unused combos |
| `variant_expansion_failed` | One script generation failed (skipped) |
| `script_bank_expansion_complete` | Daily run finished with summary |
| `script_bank_expansion_failed` | Entire run crashed (will retry) |

## Files

| File | Role |
|------|------|
| `workers/video_poller.py` | Worker loop + `_maybe_expand_script_bank()` |
| `app/features/topics/variant_expansion.py` | `expand_script_bank()`, `expand_topic_variants()`, `pick_next_variant()` |
| `app/features/topics/prompts.py` | `build_prompt1_variant()` (value), `build_prompt2()` (lifestyle) |
| `app/features/topics/prompt_data/hook_bank.yaml` | Hook styles for value scripts |
| `scripts/test_variant_expansion.py` | Manual CLI test (`--dry-run` / `--count N`) |

## Manual Trigger

Besides the daily cron, expansion can be triggered manually:

- **CLI:** `python scripts/test_variant_expansion.py --count 5 --post-type value`
- **API:** `POST /topics/expand-variants` with `{"topic_registry_id": "...", "count": 3}`
- **Hub UI:** "Expand variants" button per topic (calls the same API endpoint)
