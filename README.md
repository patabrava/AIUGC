# FLOW-FORGE UGC System

**Version:** 1.0  
**Date:** 2025-11-06

Deterministic UGC video production system for TikTok and Instagram.

Current social publishing support:
- Meta publish scheduling and dispatch in `S7_PUBLISH_PLAN`
- TikTok sandbox OAuth plus manual draft upload in `S7_PUBLISH_PLAN`
- TikTok direct posting via `video.publish` is not implemented yet

Current topic system support:
- Family-first topic bank with canonical `topic_registry` rows
- Unified topic worker that handles both research discovery and async audit promotion
- Batch seeding reuses audited families only and returns `coverage_pending` when the bank is short

## Quick Start

### Prerequisites
- Python 3.11+
- Supabase account
- OpenAI/Anthropic API keys

### Setup

1. **Verify Python 3.11 is available (install if missing):**
```bash
/opt/homebrew/bin/python3.11 -V  # should print Python 3.11.x
# if not installed, run: brew install python@3.11
```

2. **Create the local virtual environment (one time):**
```bash
/opt/homebrew/bin/python3.11 -m venv .venv
```

3. **Activate the virtual environment (run in every new shell):**
```bash
source .venv/bin/activate
```
> After activation your shell prompt will show `(.venv)`.

4. **Install pinned dependencies:**
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

5. **Create and populate environment variables:**
```bash
cp .env.example .env
# Open .env in your editor and fill in the required keys described below
```

6. **Run the FastAPI server (keep this terminal open for logs):**
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
> If `uvicorn` is not found, reactivate the virtualenv (step 3) or call it explicitly via `.venv/bin/uvicorn`.

7. **Run the video poller worker in a second terminal:**
```bash
# In a NEW terminal tab/window
cd /Users/camiloecheverri/Documents/AI/AIUGC
source .venv/bin/activate
python3 workers/video_poller.py
```
Keep this terminal open. You should see logs like `polling_videos` and `batch_transitioned_to_qa` when operations complete. Press `Ctrl+C` to stop the worker.

8. **Run the topic worker in a third terminal:**
```bash
# In a NEW terminal tab/window
cd /Users/camiloecheverri/Documents/AI/AIUGC
source .venv/bin/activate
python3 workers/topic_worker.py
```
Keep this terminal open. The topic worker drains pending audits and runs discovery on its own cadence.

9. **View runtime logs:**
* __In-terminal__: watch the shell running `uvicorn` for structured log lines (startup, requests, adapter calls).
* __Saved to file (optional)__:
  ```bash
  uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload \
    > uvicorn.log 2>&1
  tail -f uvicorn.log
  ```

10. **Stop services cleanly:**
```bash
# In each terminal that is running a service
Ctrl+C
```

The API will be available at `http://127.0.0.1:8000` while the server is running.

### Environment Variables

See `.env.example` for all required variables:
- `SUPABASE_URL`: Your Supabase project URL
- `SUPABASE_KEY`: Supabase anon key
- `SUPABASE_SERVICE_KEY`: Supabase service role key
- `OPENAI_API_KEY`: OpenAI API key
- `GEMINI_API_KEY`: Gemini API key for topic research and generation
- `ANTHROPIC_API_KEY`: Anthropic API key
- Additional keys for video providers, Cloudflare R2, and social platforms
- TikTok sandbox requires:
  - `TIKTOK_CLIENT_KEY`
  - `TIKTOK_CLIENT_SECRET`
  - `TIKTOK_REDIRECT_URI`
  - `TIKTOK_ENVIRONMENT=sandbox`
  - `TIKTOK_SANDBOX_ACCOUNT`
  - `APP_URL`
  - `PRIVACY_POLICY_URL`
  - `TERMS_URL`
  - `TOKEN_ENCRYPTION_KEY`

## Architecture

**Stack:** Python/FastAPI + Jinja2 + htmx + Alpine.js + Tailwind + Supabase

**Pattern:** Vanilla Vertical-Slice Monolith with State Machine Core

**Deployment:** VPS-capable app host (API + Worker) + Cloudflare R2

## Project Structure

```
flow-forge/
├── app/
│   ├── main.py              # FastAPI application
│   ├── core/                # Config, logging, errors, state machine
│   ├── features/            # Vertical slices (batches, posts, topics, etc.)
│   ├── adapters/            # External service clients
│   └── jobs/                # Background jobs
├── templates/               # Jinja2 templates
├── static/                  # CSS/JS assets
├── migrations/              # Supabase SQL migrations
├── tests/                   # E2E testscripts
└── workers/                 # Video polling, topic, caption, and expansion workers
```

## Development

### Running Tests
```bash
python3 -m pytest tests/
```

### Running Testscripts
```bash
# Phase 0: Foundation
python3 tests/testscript_phase0.py

# Phase 1: Batch Management
python3 tests/testscript_phase1.py

# Phase 2: Topic Discovery
python3 tests/testscript_phase2.py

# Phase 3: Video Prompt Assembly
python3 tests/testscript_phase3.py
```

### Debug Mode
```bash
DEBUG=1 LOG_LEVEL=DEBUG python3 app/main.py
```

## State Machine

Batches progress through these states:
1. **S1_SETUP** - Initial configuration
2. **S2_SEEDED** - Topics fetched and seeds extracted
3. **S4_SCRIPTED** - Scripts approved
4. **S5_PROMPTS_BUILT** - Video prompts generated
5. **S6_QA** - Videos generated, awaiting review
6. **S7_PUBLISH_PLAN** - Publish plan created
7. **S8_COMPLETE** - Published to channels

## API Documentation

Once running, visit:
- **API Docs:** http://localhost:8000/docs
- **Health Check:** http://localhost:8000/health

## Deployment

### Vercel (API)
```bash
vercel link
vercel env add
vercel --prod
```

### Railway (Worker)
1. Create new project on Railway
2. Connect GitHub repo
3. Set root directory: `/workers`
4. Add environment variables
5. Deploy

### Hostinger VPS with Docker
Use a Hostinger VPS product with Docker access, not shared hosting.

Production requirement:
- Set `APP_URL` to the public HTTPS URL of the deployment before starting containers.
- `APP_URL` is required in production because the app uses it to build the trusted host allowlist.
- The app will refuse to start in production if `APP_URL` is missing.

Topic deployment:
- `web`
- `worker` for video polling
- `caption-worker`
- `topic-worker` for research discovery plus audit promotion
- `expansion-worker` if script-bank expansion remains enabled

```bash
docker compose build
docker compose up -d
```

This repository runs as multiple long-lived services:
- `web`: FastAPI app on port `8000`
- `worker`: `workers/video_poller.py`
- `topic-worker`: research discovery plus audit promotion
- `caption-worker`: caption post-processing

All services use the same image and the same `.env` file. The video worker is mandatory because video completion is asynchronous and polling-driven; the topic worker is mandatory if you want fresh topic discovery and audit promotion to continue in production.

## Testing Strategy

Each feature phase has a whole-app testscript that:
- Runs against the real application
- Exercises features end-to-end
- Re-verifies all prior phases (regression guard)
- Provides explicit pass/fail criteria

Current topic-flow reference:
- [`docs.md`](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/docs.md)
- [`agents/topic_system_flow.md`](/Users/camiloecheverri/Documents/AI/AIUGC/AIUGC/agents/topic_system_flow.md)

## Documentation

- **CANON.md** - Complete system specification
- **IMPLEMENTATION_GUIDE.md** - Phase-by-phase implementation guide
- **constitution.md** - Development rules and best practices
- **docs.md** - Current topic research / audit / seeding flow
- **docs/PHASE3_SETUP.md** - Phase 3 setup and usage guide
- **PROGRESS.md** - Current implementation status

## License

Proprietary - All rights reserved

## Support

For issues or questions, refer to the Canon documentation.
