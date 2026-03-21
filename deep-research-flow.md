## Deep Research Flow (Value/Product)

### Stage 1 — Raw research capture
- Prompt: `app/features/topics/prompt_data/prompt1_research.txt` (raw-first instructions, no JSON)
- Code: `app/features/topics/pipeline.py` → `run_stage1_raw_research`
- LLM client: `app.adapters.llm_client.LLMClient.generate_gemini_deep_research`
- Seed topic catalog: `app/features/topics/prompts.get_topic_seed_catalog()`
- Output: stored raw text (example saved as `results_deep_research.md`)

### Stage 2 — Normalization agent
- Prompt: `app/features/topics/prompt_data/prompt1_normalization.txt`
- Builder: `app/features/topics/prompts.build_topic_normalization_prompt`
- Code: `app/features/topics/agents.normalize_topic_research_dossier` (and `_normalize_topic_research_response_to_json` fallback)
- Schema for cleanup: uses `PROMPT1_RESEARCH_JSON_SCHEMA` when possible, else text fallback
- Output: structured dossier JSON (`normalization.md`)

### Stage 3 — Script generation (value posts)
- Prompt: tiered `app/features/topics/prompt_data/prompt1_{tier}s.txt` (8s/16s/32s)
- Builder: `app/features/topics/prompts.build_prompt1`
- Runner: `app.features/topics.agents.generate_topic_script_candidate`
- Seed payload builder: `app.features/topics.agents.build_seed_payload`
- Captures: script text + source metadata (saved as `stage3_prompt1_8s.md`)

### Lifestyle path (parallel, prompt2-based)
- Prompt: tiered `app/features/topics/prompt_data/prompt2_{tier}s.txt`
- Builder: `app.features/topics.prompts.build_prompt2`
- Code: `app.features/topics.agents.generate_lifestyle_topics` → `generate_dialog_scripts`
- No stage-1 deep research; scripts generated directly
- Seed payload builder: `build_lifestyle_seed_payload`

### Supporting components
- Handlers: `app/features/topics/handlers._discover_topics_for_batch_sync` routes value vs. lifestyle
- Supabase persistence: `app/features/topics/queries.add_topic_to_registry` and `create_post_for_batch`
- Tests: regression suite covering prompts, normalization, and pipeline (`tests/test_topic_prompt_templates.py`, `tests/test_topic_pipeline.py`, `tests/test_topics_gemini_flow.py`, `tests/test_lifestyle_generation_regression.py`)

### Artifacts saved
- `results_deep_research.md` (raw output)
- `normalization.md` (normalized dossier)
- `stage3_prompt1_8s.md` (final script + metadata)
