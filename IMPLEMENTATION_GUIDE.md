# FLOW-FORGE Implementation Guide

**Version:** 1.0 | **Date:** 2025-11-05

## Quick Reference

**Architecture:** Vanilla Vertical-Slice Monolith  
**Deployment:** Vercel (API) + Vercel Cron + Railway (Worker)  
**Stack:** Python/FastAPI + Jinja2 + htmx + Alpine.js + Tailwind + Supabase

---

## Repository Structure

```
flow-forge/
├── api/index.py              # Vercel adapter
├── app/
│   ├── main.py               # FastAPI app
│   ├── core/                 # State machine, config, errors
│   ├── features/             # Vertical slices (batches, posts, topics, videos, publish)
│   ├── adapters/             # Singletons (Supabase, LLM, storage)
│   └── jobs/                 # Background jobs
├── templates/                # Jinja2 templates
├── static/                   # CSS/JS assets
├── migrations/               # Supabase SQL
├── tests/                    # E2E testscripts
├── workers/                  # Video polling worker (Railway)
├── vercel.json
├── requirements.txt
└── CANON.md
```

---

## Feature Phases

### Phase 0: Foundation
- FastAPI skeleton + config + logging
- Supabase schema + adapters
- Error envelopes + health endpoint
- **Testscript:** Verify health, DB connection, logs

### Phase 1: Batch Management
- State machine core
- Batch CRUD (create, list, get)
- Dashboard UI + batch detail with stepper
- **Testscript:** Create batch, verify state, view UI

### Phase 2: Topic Discovery
- PROMPT 1 (Research) + PROMPT 2 (Community Ads)
- Deduplication (Jaccard, registry)
- Vercel Cron endpoint
- Manual script override UI
- Approve scripts → advance S2 to S4
- **Testscript:** Run agent, verify 10 unique topics, check registry, approve scripts

### Phase 3: Video Generation
- ActionSynth + VeoPrompt agents
- Provider adapters (Veo 3.1, Sora 2)
- Video polling worker (Railway)
- Upload to Supabase Storage
- **Testscript:** Select provider, generate video, verify asset URL

### Phase 4: QA Review
- Auto QA checks (duration, resolution, audio)
- Manual review UI (video player, notes, checkboxes)
- Approve → advance to S7
- Regenerate paths (S6→S4 or S6→S5)
- **Testscript:** Review video, approve, verify all posts qa_pass

### Phase 5: Publish Planning
- Engagement Scheduler agent (time suggestions)
- Publish plan UI (table, inline editing, datetime picker)
- Validation (future times, spacing, overlaps)
- Dispatch to TikTok/Instagram APIs
- **Testscript:** Create plan, suggest times, confirm, verify platform IDs

### Phase 6: Dashboard Polish
- Batch summary (S8_COMPLETE)
- Duplicate/archive actions
- Responsive design
- Accessibility (keyboard nav, ARIA, reduced motion)
- **Testscript:** Complete full flow, verify summary, test accessibility

---

## Testing Strategy

### Testscript Format
Each phase has a **whole-app testscript** that:
1. Runs the real app (not isolated unit tests)
2. Exercises the feature end-to-end
3. Re-verifies all prior phases (regression guard)
4. Provides explicit pass/fail criteria
5. Specifies data capture points for Human-as-EYE

### Example Testscript Structure
```
Testscript: Phase 1 - Batch Management
Objective: Verify batch creation and state machine
Prerequisites: Phase 0 passing, dev server running
Steps:
  1. POST /batches with valid payload
  2. Verify batch created with S1_SETUP state
  3. GET /batches - verify batch in list
  4. Open /batches/{id} in browser
  5. Verify stepper shows "Setup" active
Pass/Fail: All steps succeed, UI renders correctly
Artifacts: API responses, browser screenshot
```

### Human-as-EYE Role
- Runs testscripts precisely as specified
- Observes behavior and captures artifacts
- Reports structured evidence (no speculation)
- LLM team uses evidence to debug/fix

---

## Deployment Guide

### Vercel Setup
1. Install Vercel CLI: `npm i -g vercel`
2. Link project: `vercel link`
3. Set env vars: `vercel env add` (all keys from .env.example)
4. Deploy: `vercel --prod`

### Railway Worker Setup
1. Create new project on Railway
2. Connect GitHub repo
3. Set root directory: `/workers`
4. Add env vars (same as Vercel + SUPABASE_URL)
5. Deploy

### Vercel Cron
- Configured in `vercel.json`
- Endpoint: `/api/cron/topic-discovery`
- Schedule: Every 6 hours
- Auth: Bearer token (CRON_SECRET)

---

## Key Implementation Notes

### State Machine
- Enforced at DB level (CHECK constraints)
- Validated in code before transitions
- Explicit guards per transition

### LLM Prompts
- Co-located with feature slices
- Explicit validation after generation
- Structured output (JSON schemas)

### Video Polling
- Worker polls provider APIs
- Updates Supabase when ready
- htmx polls UI every 5s for updates

### Idempotency
- All POST endpoints accept `Idempotency-Key` header
- Keys stored 24h in Supabase
- Return cached response if duplicate

### Validation
- Pydantic schemas at boundaries
- Runtime validation (scripts, prompts, plans)
- Standard error envelopes

---

## Next Steps

1. Review CANON.md for full specifications
2. Set up development environment
3. Implement Phase 0 (Foundation)
4. Run Testscript 0
5. Proceed phase-by-phase with testscripts

**Remember:** Each phase must pass its testscript before moving to the next phase. Re-run all prior testscripts to guard against regressions.
