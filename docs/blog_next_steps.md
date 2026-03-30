# Blog Feature — Next Steps

Tracked improvements and additions for the blog post feature.

## Critical (from code review)

- [ ] Fix broken tests: `_parse_blog_response` references should be `_parse_blog_json` with dict argument
- [ ] Add blog status transition validation (guard map like batch state machine in `states.py`)

## Important (from code review)

- [ ] Add `Idempotency-Key` support on POST endpoints (`generate`, `generate-all`, `publish`)
- [ ] Replace `ValueError` with `FlowForgeException` in `blog_runtime.py`
- [ ] Use `httpx.AsyncClient` or `asyncio.to_thread()` for Webflow client and LLM calls
- [ ] Add error isolation in `generate-all` (per-post try/except so one failure doesn't stop the batch)

## Feature Additions

- [ ] Scheduled blog publishing (date picker, worker/cron to publish at scheduled time, `blog_scheduled_at` column)
- [ ] Full-screen editor modal (currently placeholder `_blog_modal.html`)
- [ ] Blog status visibility in S7 publish panel
- [ ] Blog toggle available beyond S2_SEEDED (currently restricted to S2 only)

## UX Improvements

- [ ] Visual feedback on `saveField` failure (toast or border flash instead of silent console error)
- [ ] Progress indicator during blog generation
- [ ] Configurable LLM temperature/max_tokens (currently hardcoded in `blog_runtime.py`)

## Housekeeping

- [ ] Add rollback SQL to migration 020
- [ ] Make `_load_post_for_blog` a public function (used across module boundaries)
