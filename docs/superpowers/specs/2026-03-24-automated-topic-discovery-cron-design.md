# Automated Topic Discovery Cron — Design Spec

Date: 2026-03-24
Status: Approved

## Problem

The script expansion cron generates new script variants for existing topics, but nothing automatically discovers *new* topics. Topic discovery currently requires manual action through the Topic Hub. The topic bank needs a steady supply of fresh, deeply-researched topics to keep content production flowing.

## Solution

A new Docker worker service (`workers/topic_researcher.py`) that runs once every 24 hours, discovers new topics, runs the full 3-stage deep research pipeline, and stores results in the topic bank — independent of any batch.

## Context

- Single niche: Schwerbehinderung, Treppenlifte, Barrierefreiheit (mobility aids / disability rights)
- Value post type only (deep research via PROMPT_1 path)
- All three length tiers (8s, 16s, 32s) per topic
- 3-5 new topics per day

## Architecture

### New Worker Service — `workers/topic_researcher.py`

Standalone Python script, same pattern as `video_poller.py`:

```
while True:
    1. Check if 24h have passed since last run.
       On startup: query topic_research_cron_runs for the latest completed run.
       If found → use its completed_at as _last_run (survives container restarts).
       If not found → _last_run = 0.0 (first-ever run, execute immediately).
    2. If yes:
       a. Create a cron run record in topic_research_cron_runs (status: running)
       b. Determine which topics to research:
          - Phase 1: pick seed topics from topic_bank.yaml that don't have dossiers yet
            (flatten categories → topics; if YAML is missing/malformed, log warning and skip to Phase 2)
          - Phase 2 (when bank exhausted): call Gemini to generate new seed topic ideas,
            deduplicated against existing topic_registry via weighted Jaccard similarity (threshold 0.7)
          - Top up to MAX_TOPICS_PER_RUN from either source
       c. For each seed topic (up to MAX_TOPICS_PER_RUN), sequentially:
          - Run full 3-stage deep research pipeline:
            Stage 1: Raw research — 1 Gemini deep research call per topic (not per tier)
            Stage 2: Normalization — parse raw text → structured ResearchDossier
            Stage 3: Script generation — run generate_topic_script_candidate() 3 times
                     (once for each tier: 8s, 16s, 32s), reusing the same dossier
          - Deduplicate against existing topic_registry
          - Store results in topic_registry + topic_research_dossiers + topic_scripts
          - Log progress per topic
          - On Gemini 429 (rate limit): exponential backoff (30s, 60s, 120s), max 3 retries per topic, then skip
          - On Gemini timeout (600s): skip topic, log as failed
       d. Update cron run record (status: completed/failed, summary stats)
       e. Update _last_run timestamp
    3. Sleep POLL_INTERVAL_SECONDS (60s)
```

**Expected wall-clock time:** ~5-10 minutes per topic (1 deep research call + 3 script generations). For 5 topics: ~25-50 minutes total. Well within the 24h window; no overlap risk.

Config constants at top of file:
- `RESEARCH_INTERVAL_SECONDS = 24 * 60 * 60`
- `MAX_TOPICS_PER_RUN = 5`
- `POLL_INTERVAL_SECONDS = 60`

Niche context from env var or config: `CRON_RESEARCH_NICHE` (default: "Schwerbehinderung, Treppenlifte, Barrierefreiheit")

### Topic Selection Strategy

**Phase 1 — Seed from `topic_bank.yaml`:**
- Load all seed topics from the existing YAML file (flatten nested categories → flat list of topic strings)
- If YAML is missing or malformed: log a warning and skip to Phase 2 (do not crash the run)
- Query `topic_registry` to find which seeds already have entries (match by title)
- Pick unresearched seeds first
- Runs until the bank is exhausted

**Phase 2 — LLM-generated seeds (when bank exhausted):**
- Call Gemini with a prompt: "Generate N new content topic ideas for [niche] that are distinct from these existing topics: [list of existing titles]"
- Pass existing `topic_registry` titles for deduplication context
- Apply weighted Jaccard similarity deduplication (threshold 0.7, matching `deduplication.py` defaults) to reject near-duplicates
- For title-only dedup of raw seed ideas before research, use a simplified title comparison (since rotation/cta are not yet available at this stage)
- Store accepted seeds, research them through the same 3-stage pipeline

**Per-run flow:**
1. Check how many unresearched seeds remain in `topic_bank.yaml`
2. If enough → use those
3. If not enough → top up with LLM-generated seeds to reach `MAX_TOPICS_PER_RUN`
4. Research each one through the 3-stage pipeline

### Error Handling

- Each topic is independent — if one fails, log it and continue to the next
- If the entire run crashes, the `_last_run` timestamp is NOT updated, so it retries next 60-second cycle
- Deduplication via weighted Jaccard similarity + DB unique constraints prevents duplicate inserts even on retry
- Gemini rate limits (429): exponential backoff (30s → 60s → 120s), max 3 retries per topic, then skip
- Gemini timeout (600s per call): skip topic, log as failed
- Malformed `topic_bank.yaml`: log warning, fall through to Phase 2 (LLM-generated seeds)
- On container restart: last run time is recovered from DB, not in-memory — avoids duplicate runs
- Video polling and script expansion are never affected (separate container)

### Supabase Tracking Table — `topic_research_cron_runs`

| Column | Type | Description |
|--------|------|-------------|
| `id` | uuid (PK) | Auto-generated |
| `started_at` | timestamptz | When the run began |
| `completed_at` | timestamptz | When the run finished (null while running) |
| `status` | text | `running`, `completed`, `failed` |
| `topics_requested` | int | How many topics were attempted |
| `topics_completed` | int | How many succeeded |
| `topics_failed` | int | How many errored |
| `seed_source` | text | `yaml_bank`, `llm_generated`, `mixed` |
| `topic_ids` | jsonb | Array of `topic_registry` IDs created |
| `error_message` | text | Null on success, error details on failure |
| `details` | jsonb | Full run summary (per-topic status, timings, etc.) |
| `created_at` | timestamptz | Default `now()` |

### Health / Status Endpoint

**`GET /topics/cron-status`**

Reads the latest row from `topic_research_cron_runs` and returns:

```json
{
  "last_run": {
    "id": "...",
    "started_at": "2026-03-23T06:00:00Z",
    "completed_at": "2026-03-23T06:12:34Z",
    "status": "completed",
    "topics_completed": 4,
    "topics_failed": 1,
    "seed_source": "yaml_bank"
  },
  "next_expected_run": "2026-03-24T06:00:00Z",
  "total_runs": 12,
  "total_topics_researched": 47
}
```

No auth required (read-only status, no secrets).

### Docker Integration

New service in `docker-compose.yml`:

```yaml
  topic-researcher:
    build:
      context: .
      dockerfile: Dockerfile
    command: ["python", "workers/topic_researcher.py"]
    environment: *flow-forge-env
    restart: unless-stopped
    depends_on:
      web:
        condition: service_healthy
```

Same image, same env vars, same Dockerfile. Depends on `web` being healthy.

### Logging Events

| Event | When |
|-------|------|
| `topic_researcher_started` | Worker process boots up |
| `topic_research_cron_starting` | 24h interval triggered, run begins |
| `topic_research_seed_selection` | Seeds chosen (source, count) |
| `topic_research_topic_started` | Beginning research for one topic |
| `topic_research_topic_completed` | One topic fully researched + stored |
| `topic_research_topic_failed` | One topic errored (skipped, continues to next) |
| `topic_research_topic_duplicate` | Topic rejected by deduplication |
| `topic_research_cron_complete` | Run finished with summary |
| `topic_research_cron_failed` | Entire run crashed (retries next cycle) |
| `topic_researcher_stopped_by_user` | KeyboardInterrupt |

## Files

| File | Role |
|------|------|
| `workers/topic_researcher.py` | New worker script (main loop + orchestration) |
| `docker-compose.yml` | Add `topic-researcher` service |
| `app/features/topics/handlers.py` | Add `GET /topics/cron-status` endpoint |
| `app/features/topics/queries.py` | Add CRUD for `topic_research_cron_runs` table |
| Supabase migration | Create `topic_research_cron_runs` table |

## Reused Components (no changes needed)

| File | What's reused |
|------|---------------|
| `app/features/topics/research_runtime.py` | 3-stage pipeline (raw → normalize → scripts) |
| `app/features/topics/prompts.py` | `build_topic_research_dossier_prompt()`, `build_prompt1()` |
| `app/features/topics/deduplication.py` | Weighted Jaccard similarity deduplication |
| `app/features/topics/queries.py` | `add_topic_to_registry()`, existing DB operations |
| `app/features/topics/prompt_data/topic_bank.yaml` | Seed topic catalog |
| `app/core/logging.py` | Structured logging |
| `app/core/config.py` | Settings / env vars |
