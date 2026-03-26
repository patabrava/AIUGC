## Deep Research Flow (Value/Product)

### Stage 1 — Raw research capture
- Prompt: `app/features/topics/prompt_data/prompt1_research.txt` (raw-first instructions, no JSON)
- Code: `app/features/topics/research_runtime.py` → `generate_topic_research_dossier`
- LLM client: `app.adapters.llm_client.LLMClient.generate_gemini_deep_research`
- Seed topic catalog: `app/features/topics/prompts.get_topic_seed_catalog()`
- Output: stored raw text (example saved as `results_deep_research.md`)

### Stage 2 — Normalization agent
- Prompt: `app/features/topics/prompt_data/prompt1_normalization.txt`
- Builder: `app/features/topics/prompts.build_topic_normalization_prompt`
- Code: `app/features/topics/research_runtime.normalize_topic_research_dossier`
- Fallback chain: direct JSON parse → Gemini text-to-JSON normalization → raw text synthesis (`response_parsers._synthesize_research_dossier_from_text`)
- Output: structured `ResearchDossier` with lane candidates

### Stage 3 — Script generation (value posts)
- Prompt: tiered `app/features/topics/prompt_data/prompt1_{tier}s.txt` (8s/16s/32s)
- Builder: `app/features/topics/prompts.build_prompt1`
- Runner: `app/features/topics/research_runtime.generate_topic_script_candidate`
- Seed payload builder: `app/features/topics/seed_builders.build_seed_payload`
- Captures: script text + source metadata (saved as `stage3_prompt1_8s.md`)

### Stage 4 — Topic bank harvesting
- Code: `app/features/topics/hub._harvest_seed_topic_to_bank`
- Persists topics and script variants to `topic_registry` and `topic_scripts` tables
- Deduplication: `app/features/topics/deduplication.deduplicate_topics`
- No caption generation at this stage — captions are generated later at post creation time

### Stage 5 — Caption generation (at post creation)
- Code: `app/features/topics/captions.generate_caption_bundle` (called via `handlers._attach_publish_captions`)
- Prompt: `app/features/topics/prompt_data/captions_prompt.txt` (text-first, marker format)
- LLM: `generate_gemini_text` with `[short_paragraph]`, `[medium_bullets]`, `[long_structured]` markers
- Parser: `captions._parse_text_variants` converts marker text to structured bundle
- Validation: char ranges, hashtags, emoji count, language check, per-variant fallback
- Fallback: `captions._synthesize_fallback_bundle` (deterministic template captions)
- Selected variant stored in `seed_data.caption_bundle` and `posts.publish_caption`
- Triggered only during batch seeding (UI "New Batch" or manual seed), not during harvesting

### Lifestyle path (parallel, prompt2-based)
- Prompt: tiered `app/features/topics/prompt_data/prompt2_{tier}s.txt`
- Builder: `app/features/topics/prompts.build_prompt2`
- Code: `app/features/topics/agents.generate_lifestyle_topics` → `research_runtime.generate_dialog_scripts`
- No stage-1 deep research; scripts generated directly
- Seed payload builder: `app/features/topics/seed_builders.build_lifestyle_seed_payload`

### Supporting components
- Handlers: `app/features/topics/handlers._discover_topics_for_batch_sync` routes value vs. lifestyle
- Supabase persistence: `app/features/topics/queries.add_topic_to_registry` and `create_post_for_batch`
- Script bank expansion: `app/features/topics/variant_expansion` (run by `workers/video_poller.py`)
- Tests: regression suite covering prompts, normalization, and pipeline (`tests/test_topic_prompt_templates.py`, `tests/test_topic_pipeline.py`, `tests/test_topics_gemini_flow.py`, `tests/test_lifestyle_generation_regression.py`, `tests/test_caption_generation.py`)

### Artifacts saved
- `results_deep_research.md` (raw output)
- `normalization.md` (normalized dossier)
- `stage3_prompt1_8s.md` (final script + metadata)
