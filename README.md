# FLOW-FORGE UGC System

**Version:** 1.0  
**Date:** 2025-11-06

Deterministic UGC video production system for TikTok and Instagram.

## Quick Start

### Prerequisites
- Python 3.11+
- Supabase account
- OpenAI/Anthropic API keys

### Setup

1. **Clone and install dependencies:**
```bash
python3 -m pip install -r requirements.txt
```

2. **Configure environment:**
```bash
cp .env.example .env
# Edit .env with your credentials
```

3. **Run the application:**
```bash
python3 -m app.main
```

The application will start on `http://localhost:8000`

### Environment Variables

See `.env.example` for all required variables:
- `SUPABASE_URL`: Your Supabase project URL
- `SUPABASE_KEY`: Supabase anon key
- `SUPABASE_SERVICE_KEY`: Supabase service role key
- `OPENAI_API_KEY`: OpenAI API key
- `ANTHROPIC_API_KEY`: Anthropic API key
- Additional keys for video providers and social platforms

## Architecture

**Stack:** Python/FastAPI + Jinja2 + htmx + Alpine.js + Tailwind + Supabase

**Pattern:** Vanilla Vertical-Slice Monolith with State Machine Core

**Deployment:** Vercel (API) + Railway (Worker)

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
└── workers/                 # Video polling worker
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

## Testing Strategy

Each feature phase has a whole-app testscript that:
- Runs against the real application
- Exercises features end-to-end
- Re-verifies all prior phases (regression guard)
- Provides explicit pass/fail criteria

## Documentation

- **CANON.md** - Complete system specification
- **IMPLEMENTATION_GUIDE.md** - Phase-by-phase implementation guide
- **constitution.md** - Development rules and best practices
- **docs/PHASE3_SETUP.md** - Phase 3 setup and usage guide
- **PROGRESS.md** - Current implementation status

## License

Proprietary - All rights reserved

## Support

For issues or questions, refer to the Canon documentation.
