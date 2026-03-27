# Captions Changes

## What changed
- Separated the canonical research topic from the richer dossier/lane title.
- Added `canonical_topic` and `research_title` to seed payloads so caption generation uses the real topic, not the display heading.
- Moved the caption prompt into `app/features/topics/prompt_data/captions_prompt.txt`.
- Tightened the prompt so captions:
  - do not copy the topic title verbatim
  - start with different hook shapes
  - use bullet or structured formats
  - use at most one emoji
- Reworked deterministic fallback captions so they:
  - vary the opening line across families
  - avoid generic lead-ins like `diesem Thema` or `Kontext`
  - stay inside the required length buckets
- Updated the batch review UI so the canonical topic is visible separately from the richer research title.
- Updated publish/review flows so the selected caption is the one persisted and shown for approval.

## Files touched
- `app/features/topics/captions.py`
- `app/features/topics/seed_builders.py`
- `app/features/topics/handlers.py`
- `app/features/topics/hub.py`
- `app/features/topics/prompt_data/captions_prompt.txt`
- `app/features/batches/handlers.py`
- `templates/batches/detail/_post_card.html`
- `templates/batches/detail/_publish_panel.html`
- `static/js/batches/detail.js`
- `tests/test_caption_generation.py`
- `tests/test_batches_status_progress.py`
- `tests/test_topics_hub.py`
- `tests/test_lifestyle_generation_regression.py`
- `tests/test_video_duration_routing.py`
- `AGENTS.md`

## Result
- Captions no longer leak dossier headings like `Forschungsdossier`, `Zielsetzung`, or `Rechtliche Grundlagen`.
- The UI now separates the canonical topic from the research title.
- Fallback captions are still valid when Gemini output fails, but they are less repetitive and more natural in German.
