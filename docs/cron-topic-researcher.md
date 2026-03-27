# Cron Job: Daily Topic Discovery

Date: 2026-03-24
Status: Active

## What It Does

Automatically discovers new topics via Gemini deep research, once per day. Reads the configured niche from `CRON_RESEARCH_NICHE`, selects candidate topics through a two-phase process (YAML seed bank + LLM-generated seeds), then runs each through a 3-stage research pipeline before storing the results in the topic registry.

## Where It Runs

Inside the dedicated **topic-researcher** Docker container (`workers/topic_researcher.py`) on Hostinger. Runs as a separate service alongside the web and worker containers.

## How It Works

```
topic_researcher main loop (every 60 seconds):
  1. Has 24 hours passed since last run?
       |
       NO  -> sleep and re-check (no-op)
       |
       YES -> run_topic_research()
               |
               +-- Phase 1: YAML seed selection
               |     Load candidate topics from seed bank YAML
               |     Filter out topics already in topic_registry
               |
               +-- Phase 2: LLM-generated seeds
               |     If Phase 1 yields fewer than MAX_TOPICS_PER_RUN,
               |     ask Gemini for fresh topic ideas in the niche
               |     Filter out duplicates against DB + Phase 1
               |
               +-- 3-stage pipeline (per topic):
               |     Stage 1: Deep research via Gemini (dossier generation)
               |     Stage 2: Extract framework candidates + metadata
               |     Stage 3: Store in topic_registry + generate initial scripts
               |
               +-- Log summary
```

## Timing

| Parameter | Value | Config Location |
|-----------|-------|-----------------|
| Check interval | 60 seconds | `POLL_INTERVAL_SECONDS` in topic_researcher.py |
| Research interval | 24 hours | `RESEARCH_INTERVAL_SECONDS` in topic_researcher.py |
| Max topics per run | 5 | `MAX_TOPICS_PER_RUN` in topic_researcher.py |

## First Run Behavior

On container startup, the worker calls `get_latest_cron_run()` to check the database for the last successful run timestamp. If no previous run exists (fresh deployment), research runs immediately on the first loop iteration. This DB-based recovery means restarts do not cause duplicate runs or missed windows.

## Topic Selection Phases

**Phase 1 — YAML seed bank:**
- Pre-curated topic candidates stored in a YAML file
- Filtered against existing `topic_registry` entries to avoid duplicates
- Provides a stable baseline of high-quality topics

**Phase 2 — LLM-generated seeds:**
- Only invoked when Phase 1 yields fewer than `MAX_TOPICS_PER_RUN` candidates
- Gemini generates fresh topic ideas within the configured niche (`CRON_RESEARCH_NICHE`)
- Deduplicated against both the database and Phase 1 results

## Error Handling

- Each topic is processed independently — if one fails, the cron logs it and moves to the next
- If Gemini rate-limits or errors, the topic is skipped (logged as `topic_research_failed`)
- Restart recovery is DB-based via `get_latest_cron_run()`, so no work is lost or duplicated on container restart
- Rate limit backoff: the worker respects Gemini rate limits with exponential backoff before retrying
- If the entire research run crashes, the completed topics are already persisted; only the remaining topics are retried on the next cycle

## Log Events

| Event | Meaning |
|-------|---------|
| `topic_research_starting` | Daily run begins |
| `topic_research_phase1_seeds` | YAML seed selection complete |
| `topic_research_phase2_seeds` | LLM seed generation complete |
| `topic_research_processing` | Processing one topic through the pipeline |
| `topic_research_stored` | One topic successfully researched and stored |
| `topic_research_failed` | One topic failed (skipped) |
| `topic_research_complete` | Daily run finished with summary |
| `topic_research_run_failed` | Entire run crashed (will retry) |

## Files

| File | Role |
|------|------|
| `workers/topic_researcher.py` | Worker loop + research orchestration |
| `app/features/topics/topic_discovery.py` | 3-stage pipeline, seed selection, LLM seed generation |
| `app/features/topics/prompt_data/topic_seeds.yaml` | Phase 1 YAML seed bank |
| `docker-compose.yml` | Service definition (`topic-researcher`) |
| `docker-compose.yaml` | Service definition (`topic-researcher`, env_file variant) |

## Health Endpoint

`GET /topics/cron-status` — returns the last run timestamp, next scheduled run, and status of the topic researcher cron job.

## Deployment Note

This job should be scheduled on the Hostinger worker path. Vercel should only serve the API surface for manual triggering and status checks.
