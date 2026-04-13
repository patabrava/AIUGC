# Expansion Worker Deployment Guide

## What Changed

A new worker `workers/expansion_worker.py` was extracted from the video poller. It runs script bank expansion (Gemini API calls to generate script variants for topics) on a 24-hour cycle.

Previously this ran inside the video poller, blocking video polling for 5+ minutes during each expansion run.

## New Worker

**File:** `workers/expansion_worker.py`

**Command:** `python workers/expansion_worker.py`

**Behavior:**
- Runs `expand_script_bank()` immediately on startup
- Sleeps 24 hours
- Repeats

**Dependencies:** Same as the API server — uses `app.features.topics.variant_expansion`, requires Supabase and Gemini API keys.

**Resource needs:** Low CPU/memory, but makes many Gemini API calls during expansion (~5 minutes of activity every 24 hours, idle the rest of the time).

## All Workers (Current)

| Worker | Command | Frequency | Purpose |
|--------|---------|-----------|---------|
| API Server | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | Always running | HTTP API + htmx frontend |
| Video Poller | `python workers/video_poller.py` | Polls every 10s | Checks Veo/Sora operations, downloads completed videos |
| Caption Worker | `python workers/caption_worker.py` | Polls every 10s | Transcribes + burns captions onto completed videos |
| **Expansion Worker** (NEW) | `python workers/expansion_worker.py` | Runs once per 24h | Generates script variants via Gemini for the topic bank |

## Dockerfile

The current Dockerfile only runs the API server by default:

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

To deploy the expansion worker, add a service/container that runs:

```dockerfile
CMD ["python", "workers/expansion_worker.py"]
```

It uses the same Docker image — same `requirements.txt`, same codebase. Only the `CMD` differs.

## Docker Compose

Add a dedicated service that reuses the same image as the API server:

```yaml
expansion-worker:
  build:
    context: .
    dockerfile: Dockerfile
  command: ["python", "workers/expansion_worker.py"]
  env_file:
    - .env
  restart: unless-stopped
  depends_on:
    web:
      condition: service_healthy
```

If you deploy with `docker-compose.yml`, keep the same service shape but replace `env_file` with the shared `environment: *Lippe Lift Studio-env` anchor used by the other services.

## Environment Variables

The expansion worker needs the same env vars as the API server:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `GEMINI_API_KEY` (canonical Gemini key)
- `ENVIRONMENT` (development/production)

## Notes

- The expansion worker is optional — the system works without it, scripts just won't auto-expand
- It's safe to restart at any time — expansion is idempotent (duplicate scripts are skipped via DB unique constraints)
- If it crashes, it simply retries on the next 24-hour cycle
