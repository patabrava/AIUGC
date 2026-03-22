# Deep Research Architecture

Complete backend architecture for the FLOW-FORGE topic research, normalization, and script generation system.

---

## System Overview

FLOW-FORGE is a deterministic UGC (User-Generated Content) video production system that generates short-form vertical videos for TikTok and Instagram. The deep research pipeline discovers topics, performs Gemini-powered research, normalizes findings into structured dossiers, and generates tier-specific scripts (8s, 16s, 32s) for video production.

### High-Level Data Flow

```
Seed Topic Selection
    ↓
Gemini Deep Research (polling, 2-6 min)
    ↓
Raw Research Normalization → ResearchDossier
    ↓
Lane-by-Lane Script Generation (with retry)
    ↓
Deduplication + Validation
    ↓
Supabase Persistence (registry + scripts + dossiers)
```

---

## 1. Entry Points

### `harvest_topics_to_bank_sync()`
**File:** `app/features/topics/handlers.py`

Synchronous batch harvest for multiple post types. This is the primary entry point for populating the topic bank.

- Selects seed topics via `pick_topic_bank_topics()` using a deterministic seed
- Iterates over post types (`value`, `lifestyle`, `product`), harvesting each seed topic
- Creates a `topic_research_runs` row to track the operation
- Returns a summary with stored topic counts, IDs, and the run_id

### `launch_topic_research_run()`
**File:** `app/features/topics/handlers.py`

Async launcher for single-topic deep research, used by the UI.

- Creates a `topic_research_runs` record with status `running`
- Spawns a background `asyncio.create_task()` for the research
- Emits progress callbacks for real-time UI updates
- Resolves post_type from the topic registry if not provided

### `discover_topics_for_batch()`
**File:** `app/features/topics/handlers.py`

Discovers and assigns topics to posts in an existing batch.

- Reads the batch's `post_type_counts` and `target_length_tier`
- Pulls from the stored topic bank (or triggers on-demand harvest)
- Creates posts with `seed_data` payloads and advances batch to `S2_SEEDED`

### Progress Tracking

The handlers maintain an in-memory `_SEEDING_PROGRESS` dict that streams real-time status to the htmx UI via resumable feed events:
- `progress.update` — status changes (researching, normalizing, generating)
- `content.delta` — incremental content updates
- `progress.post_created` — post committed to batch

---

## 2. Hub Orchestration

**File:** `app/features/topics/hub.py`

The hub is the central orchestrator that coordinates research, script generation, deduplication, and persistence for a single seed topic.

### `_harvest_seed_topic_to_bank()`

Core harvest function. For each seed topic:

1. **Research** — Calls `generate_topic_research_dossier()` to get a `ResearchDossier`
2. **Lane iteration** — Iterates over `dossier.lane_candidates` (3-12 sub-angles per dossier)
3. **Script generation** — Calls `generate_topic_script_candidate()` per lane
4. **Topic conversion** — Converts `ResearchAgentItem` → `TopicData` (title, rotation, CTA)
5. **Deduplication** — Checks against existing + collected topics (Jaccard threshold: 0.35)
6. **Dialog scripts** — For lifestyle posts, generates 3-category scripts via `generate_dialog_scripts()`. For value posts, reuses the prompt1 script across all categories.
7. **Fact extraction** — Calls `extract_seed_strict_extractor()` for clean factual payload
8. **Seed building** — Constructs `seed_payload` via `build_seed_payload()`
9. **Persistence** — Writes to `topic_registry` + `topic_scripts` + `topic_research_dossiers`

### Value vs. Lifestyle Paths

| Aspect | Value Posts | Lifestyle Posts |
|--------|-----------|-----------------|
| Research | Gemini Deep Research → dossier | No deep research |
| Script source | PROMPT_1 (research-backed) | PROMPT_2 (template-based) |
| Dialog scripts | Reuse prompt1 script for all 3 categories | Separate script per category |
| Sources | External URLs from research | None (community content) |
| Templates | N/A | 5 fixed community templates |

---

## 3. Three-Stage Pipeline

**File:** `app/features/topics/pipeline.py`

The pipeline makes the research → normalization → script generation boundary explicit.

### Stage 1: Raw Research Capture

```python
run_stage1_raw_research(seed_topic, post_type, target_length_tier)
→ RawResearchArtifact
```

- Builds a research prompt via `build_topic_research_prompt()`
- Calls `llm.generate_gemini_deep_research()` (2-6 min polling)
- Returns a frozen `RawResearchArtifact` with the raw text response
- **No parsing or validation** — just captures the raw output

### Stage 2: Normalization

```python
run_stage2_normalization(artifact)
→ ResearchDossier
```

- Three-tier fallback:
  1. Direct JSON/YAML parse via `parse_topic_research_response()`
  2. LLM normalization prompt (asks Gemini to fix the structure)
  3. Text synthesis fallback (`_synthesize_research_dossier_from_text()`)
- Returns a validated `ResearchDossier` Pydantic model

### Stage 3: Script Generation

```python
run_stage3_script_generation(topic, scripts_required, dossier, profile)
→ DialogScripts
```

- Generates scripts for 3 hook categories: problem-agitate-solution, testimonial, transformation
- Uses the dossier context + duration profile word bounds
- Returns validated `DialogScripts`

---

## 4. Research Runtime

**File:** `app/features/topics/research_runtime.py`

### `generate_topic_research_dossier()`

Orchestrates the full research + normalization flow:

1. Builds the research prompt with seed topic, post type, and tier
2. Calls Gemini Deep Research (with configured timeout, default 600s)
3. Normalizes via `normalize_topic_research_dossier()` with 3-tier fallback
4. Persists `topic_research_runs` + `topic_research_dossiers` rows
5. Returns `ResearchDossier`

### `normalize_topic_research_dossier()`

Three-tier normalization:

| Tier | Method | When Used |
|------|--------|-----------|
| 1 | Direct JSON parse | Response is valid JSON/YAML |
| 2 | LLM normalization prompt | JSON parse failed, asks Gemini to fix |
| 3 | Text synthesis | Both above failed, extracts fields from prose |

### `generate_topic_script_candidate()`

Generates a single script for a specific lane from the dossier. Has two retry phases:

**Phase 1 — Structured JSON (3 attempts):**
- Calls `llm.generate_gemini_json()` with the PROMPT_1 schema
- Parses via `parse_prompt1_response()` with tier-specific validation
- Enforces word envelope (e.g., 12-15 words for 8s)
- On failure: appends error feedback to prompt for next attempt

**Phase 2 — Text + Normalization (2 attempts):**
- Calls `llm.generate_gemini_text()` as fallback
- Parses via `_parse_prompt1_with_normalization()`
- Same validation and word envelope enforcement
- On failure: raises `ValidationError` after all retries exhausted

**Word Envelope Enforcement:**
- If script is too short: deterministic lane-grounded expansion using lane facts
- If script is too long: truncates to max_words and re-adds terminal punctuation
- Final check: raises `ValidationError` if word count outside `[min, max]`

### `generate_dialog_scripts()`

Generates scripts for all 3 hook categories. Same two-phase retry pattern as above, using PROMPT_2 templates.

### `extract_seed_strict_extractor()`

Extracts only factual claims from a topic for the seed payload:
- Uses `STRICT_EXTRACTOR_SYSTEM_PROMPT` — no hallucination, no embellishment
- Returns `SeedData` with a `facts` array and optional `source_context`

### System Prompts

| Prompt | Purpose |
|--------|---------|
| `PROMPT1_STAGE3_SYSTEM_PROMPT` | Script generation — follow prompt exactly, return valid JSON |
| `PROMPT1_RESEARCH_SYSTEM_PROMPT` | Deep research — return dense German prose dossier |
| `PROMPT1_RESEARCH_NORMALIZER_SYSTEM_PROMPT` | Convert raw research to JSON, derive lanes |
| `PROMPT1_NORMALIZER_SYSTEM_PROMPT` | Fix malformed JSON without inventing facts |
| `STRICT_EXTRACTOR_SYSTEM_PROMPT` | Extract only explicit facts, no hallucination |

---

## 5. LLM Client

**File:** `app/adapters/llm_client.py`

### `generate_gemini_deep_research()`

The core deep research method. Uses the Gemini Interactions API with long-polling.

**Polling Flow:**

1. **Submit** — POST `/v1beta/interactions` with the research prompt + agent config
2. **Poll** — GET `/v1beta/interactions/{id}` every `poll_interval_seconds` (default 5s)
3. **Track** — Monitor state: `SUBMITTED → IN_PROGRESS → COMPLETED`
4. **Extract** — Pull final text from completed interaction
5. **Return** — Plain text string (typically 20-35K characters)

**Retry Logic:**
- 5 consecutive retries for transient errors (429, 500, 502, 503, 504)
- Exponential backoff: `min(poll_interval * retries, 15)` seconds
- Counter resets on any successful poll
- Hard timeout: `timeout_seconds` (default 600s)

**Progress Callback:**
```python
{
    "provider_interaction_id": str,
    "provider_status": str,       # SUBMITTED, IN_PROGRESS, COMPLETED, FAILED
    "detail_message": str,        # Human-readable status
    "is_retrying": bool
}
```

### `generate_gemini_text()`

Standard text generation via Gemini `generateContent` endpoint.
- Merges system + user prompts
- Returns plain text string

### `generate_gemini_json()`

Structured JSON output via Gemini with `responseMimeType: "application/json"`.
- Converts JSON schema to Gemini `responseSchema` format
- Guarantees valid JSON conforming to schema
- Returns parsed dict

### Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `gemini_api_key` | — | API authentication |
| `gemini_topic_model` | `gemini-2.5-flash` | Model for text/JSON generation |
| `gemini_deep_research_agent` | `deep-research-pro-preview-12-2025` | Deep research agent ID |
| `gemini_topic_timeout_seconds` | 600 | Max wait for deep research |
| `gemini_topic_poll_seconds` | 5 | Polling interval |

---

## 6. Prompts

**Files:** `app/features/topics/prompts.py` + `app/features/topics/prompt_data/`

### Prompt Templates

| File | Used For |
|------|----------|
| `prompt1_research.txt` | Deep research dossier request |
| `prompt1_normalization.txt` | JSON normalization of raw research |
| `prompt1_8s.txt` | 8s tier script generation (12-15 words, 1 sentence) |
| `prompt1_16s.txt` | 16s tier script generation (26-36 words, 2 sentences) |
| `prompt1_32s.txt` | 32s tier script generation (54-74 words, 3-4 sentences) |
| `prompt2_8s.txt` | 8s tier dialog scripts (16-20 words) |
| `prompt2_16s.txt` | 16s tier dialog scripts (24-34 words) |
| `prompt2_32s.txt` | 32s tier dialog scripts (46-66 words) |
| `topic_bank.yaml` | Seed topic catalog |
| `hook_bank.yaml` | Hook families + banned patterns |

### Prompt Building Functions

- **`build_topic_research_dossier_prompt()`** — Injects seed_topic, post_type, tier into the research template. Instructs Gemini to produce raw German prose (no JSON).
- **`build_topic_normalization_prompt()`** — Wraps raw research output with instructions to convert to structured JSON.
- **`build_prompt1()`** — Builds tier-specific script prompt with dossier context and lane details.
- **`build_prompt2()`** — Builds dialog script prompt with topic, framework candidates, and tone.

### Template Variables

Prompts use `{variable}` placeholders filled at build time:
- `{seed_topic}`, `{post_type}`, `{target_length_tier}`
- `{desired_topics}` — number of scripts to generate
- `{research_context_section}` — injected dossier/lane context block

---

## 7. Data Models

**File:** `app/features/topics/schemas.py`

### Core Schemas

**`ResearchDossier`** — Structured output of Stage 2 normalization:
- `cluster_id` — Research cluster identifier
- `anchor_topic` — Primary topic name
- `lane_candidates` — List of `ResearchLaneCandidate` (3-12 sub-angles)
- `sources` — List of `ResearchAgentSource` with validated URLs
- `facts` — List of factual claims (1-20 items)
- `framework_candidates` — Suggested frameworks (PAL, Testimonial, Transformation)

**`ResearchLaneCandidate`** — A specific sub-angle within a dossier:
- `lane_key`, `lane_family` — Lane identifiers
- `title`, `angle` — What this lane covers
- `facts`, `risk_notes` — Lane-specific content
- `suggested_length_tiers` — Which tiers this lane suits

**`ResearchAgentItem`** — A single generated script:
- `topic` — Chosen topic label
- `script` — Spoken text (10-900 chars, tier-bounded)
- `caption` — Social media caption
- `framework` — One of PAL, Testimonial, Transformation
- `sources` — Up to 2 source references

**`DialogScripts`** — Three-category script output:
- `problem_agitate_solution` — List of scripts
- `testimonial` — List of scripts
- `transformation` — List of scripts
- `description` — Social caption

**`TopicScriptVariant`** — Database row for a persisted script:
- Foreign keys: `topic_registry_id`, `topic_research_dossier_id`
- Script metadata: `bucket`, `hook_style`, `framework`, `tone`, `estimated_duration_s`
- Lane metadata: `lane_key`, `lane_family`, `cluster_id`, `anchor_topic`
- Source metadata: `primary_source_url`, `source_urls`
- Usage tracking: `use_count`, `last_used_at`, `quality_score`

---

## 8. Validation

**File:** `app/features/topics/topic_validation.py`

### Duration Validation

`estimate_script_duration_seconds(text)` — Estimates spoken duration: `words / 2.6` (rounded up), cross-checked against `chars / 17.0`.

`validate_duration(item)` — Checks script against tier bounds:
- Word count within `[min_words, max_words]`
- Character count (no spaces) within `max_chars_no_spaces`
- Estimated duration within `[min_seconds, max_seconds]`

### German Content Validation

`validate_german_content(item)` — Ensures all fields are German:
- Tokenizes text into words
- Checks against 42 English signal words and 38 German signal words
- Strict mode for short fields (topic, tone, disclaimer)
- Accepts known loan phrases (e.g., "peer-support")

### Script Trimming & Punctuation

In `response_parsers.py`, scripts that exceed `max_seconds` or `max_chars_no_spaces` are trimmed word-by-word from the end. After trimming, terminal punctuation (`.!?`) is re-added to prevent "incomplete fragment" validation failures. This is particularly important for the 8s tier where the tight word budget (12-15 words) frequently triggers trimming.

### Other Validators

| Validator | Purpose |
|-----------|---------|
| `validate_summary()` | Ensures source_summary differs from script (bigram Jaccard < 0.25) |
| `validate_unique_ctas()` | Prevents CTA reuse within a batch |
| `validate_round_robin()` | Ensures batch has topic variety |
| `validate_sources_accessible()` | HEAD/GET check on source URLs (non-blocking, 8s timeout) |

---

## 9. Deduplication

**File:** `app/features/topics/deduplication.py`

### Algorithm

Weighted Jaccard similarity across three fields:

| Field | Weight | Rationale |
|-------|--------|-----------|
| Title | 0.50 | Primary identifier |
| Rotation | 0.30 | Script content |
| CTA | 0.20 | Call to action |

```python
similarity = 0.5 * jaccard(title1, title2) +
             0.3 * jaccard(rotation1, rotation2) +
             0.2 * jaccard(cta1, cta2)
```

### Thresholds

- **0.70** — Default threshold for `is_duplicate_topic()` and `deduplicate_topics()`
- **0.35** — Conservative threshold used during hub harvesting to catch near-duplicates early

### Functions

- `tokenize(text)` — Lowercase, remove punctuation, split into word set
- `jaccard_similarity(set1, set2)` — Standard intersection/union metric
- `calculate_topic_similarity()` — Weighted multi-field comparison
- `deduplicate_topics()` — Filters new topics against existing + already-accepted topics

---

## 10. Database Schema

### Tables & Relationships

```
topic_registry (1) ──→ (M) topic_scripts
topic_registry (1) ──→ (M) topic_research_dossiers
topic_registry (1) ──→ (M) topic_research_runs

topic_research_runs (1) ──→ (M) topic_research_dossiers
topic_research_dossiers (1) ──→ (M) topic_scripts
```

### `topic_registry`

The durable topic bank. One row per unique topic.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | |
| `title` | TEXT NOT NULL | Topic label (unique with script) |
| `script` | TEXT | Primary script text |
| `post_type` | TEXT | value, lifestyle, product |
| `use_count` | INT | Times used in batches |
| `language` | TEXT | Default `de` |
| `last_harvested_at` | TIMESTAMPTZ | Last research timestamp |
| `target_length_tiers` | INTEGER[] | Supported tiers |

### `topic_research_runs`

Tracks each research execution.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | |
| `trigger_source` | TEXT | manual, hub_deep_research, batch_harvest |
| `status` | TEXT | running, completed, failed |
| `target_length_tier` | INTEGER | 8, 16, or 32 |
| `requested_counts` | JSONB | `{"value": N, "lifestyle": M}` |
| `result_summary` | JSONB | Stored topic IDs and counts |
| `provider_interaction_id` | TEXT | Gemini interaction ID |
| `dossier_id` | UUID FK | → topic_research_dossiers |
| `error_message` | TEXT | Failure details |

### `topic_research_dossiers`

Persisted normalized research output.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | |
| `topic_research_run_id` | UUID FK | → topic_research_runs |
| `topic_registry_id` | UUID FK | → topic_registry |
| `seed_topic` | TEXT NOT NULL | Original seed |
| `post_type` | TEXT NOT NULL | |
| `target_length_tier` | INTEGER | 8, 16, or 32 |
| `cluster_id` | TEXT NOT NULL | Research cluster |
| `normalized_payload` | JSONB | Full ResearchDossier |
| `prompt_name` | TEXT | Default `prompt1_research` |

### `topic_scripts`

Individual script variants linked to topics and dossiers.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID PK | |
| `topic_registry_id` | UUID FK | → topic_registry (CASCADE) |
| `topic_research_dossier_id` | UUID FK | → topic_research_dossiers |
| `script` | TEXT NOT NULL | Spoken text |
| `target_length_tier` | INTEGER | 8, 16, or 32 |
| `bucket` | TEXT | problem, testimonial, transformation |
| `hook_style` | TEXT | Hook category |
| `lane_key`, `lane_family` | TEXT | Lane identifiers |
| `seed_payload` | JSONB | Full seed for batch use |
| `use_count` | INT | Times assigned to a post |
| `quality_score` | NUMERIC | QA rating |

**Unique constraint:** `(topic_registry_id, target_length_tier, script)`

---

## 11. Duration Tier System

**File:** `app/core/video_profiles.py`

Three frozen `DurationProfile` configurations control the entire pipeline's word budgets, video routing, and Veo chaining.

| | 8s Tier | 16s Tier | 32s Tier |
|---|---------|----------|----------|
| **Route** | `short` | `veo_extended` | `veo_extended` |
| **Veo Base** | 8s | 4s | 4s |
| **Extension Hops** | 0 | 2 (7s each) | 4 (7s each) |
| **PROMPT_1 Words** | 12-15 | 26-36 | 54-74 |
| **PROMPT_1 Seconds** | 5-6 | 12-14 | 24-28 |
| **PROMPT_1 Max Chars** | 90 | 220 | 430 |
| **PROMPT_1 Sentences** | 1 | 2 | 3-4 |
| **PROMPT_2 Words** | 16-20 | 24-34 | 46-66 |

These profiles are referenced by:
- Prompt templates (word count instructions)
- `parse_prompt1_response()` (trimming and validation)
- `_enforce_prompt1_word_envelope()` (expansion and capping)
- `validate_duration()` (acceptance checks)
- Video submission routing (short vs. extended pipeline)

---

## 12. Batch State Machine

**File:** `app/core/states.py`

Batches progress through explicit states:

```
S1_SETUP ──→ S2_SEEDED ──→ S4_SCRIPTED ──→ S5_PROMPTS_BUILT ──→ S6_QA ──→ S7_PUBLISH_PLAN ──→ S8_COMPLETE
                                                                   ↓
                                                          S4_SCRIPTED (regenerate)
                                                          S5_PROMPTS_BUILT (rebuild prompts)
```

| Transition | Trigger |
|-----------|---------|
| S1 → S2 | Topic discovery + seeding complete |
| S2 → S4 | Scripts generated for all posts |
| S4 → S5 | Video prompts built |
| S5 → S6 | QA review |
| S6 → S7 | All posts approved |
| S6 → S4/S5 | Rejection (loop back for regeneration) |
| S7 → S8 | Publish plan executed |

---

## 13. Lifestyle Runtime

**File:** `app/features/topics/lifestyle_runtime.py`

Lifestyle posts skip deep research entirely. Instead:

1. Select from 5 fixed community templates (e.g., "Rollstuhl-Alltag – Tipps & Tricks")
2. Shuffle and cycle through templates
3. For each: call `generate_dialog_scripts()` with the template as the topic
4. Extract rotation (hook) and CTA from the generated script
5. Track used hooks to avoid repetition across the batch

No external sources, no research dossier, no fact extraction.

---

## 14. Content Utilities

**File:** `app/features/topics/content_utils.py`

- **`extract_soft_cta(script)`** — Extracts the last sentence as the CTA. Falls back to last 4 words if no sentence markers found.
- **`strip_cta_from_script(script, cta)`** — Removes the CTA suffix from the full script to produce the rotation (hook).
- **`build_social_description(script, source_summary)`** — Returns source_summary if present, otherwise the script itself.

---

## 15. Seed Builders

**File:** `app/features/topics/seed_builders.py`

### `convert_research_item_to_topic(item)`

Converts a `ResearchAgentItem` into a `TopicData` by extracting the CTA from the script and computing spoken duration.

### `build_seed_payload(item, strict_seed, dialog_scripts, ...)`

Constructs the final `seed_data` JSON that gets stored on a post:

```json
{
  "script": "...",
  "caption": "...",
  "framework": "PAL",
  "tone": "direkt, freundlich, empowernd, du-Form",
  "target_length_tier": 16,
  "estimated_duration_s": 12,
  "cta": "...",
  "dialog_script": "...",
  "script_category": "problem",
  "strict_seed": { "facts": [...] },
  "description": "...",
  "source": { "title": "...", "url": "...", "summary": "..." }
}
```

### `build_lifestyle_seed_payload(topic_data, dialog_scripts)`

Simplified seed without external sources. Uses a synthetic fact (`"Community-basiertes Thema: {title}"`) and reuses dialog scripts across all framework categories.

---

## 16. Persistence Layer

**File:** `app/features/topics/queries.py`

### Key Operations

| Function | Purpose |
|----------|---------|
| `add_topic_to_registry()` | Insert or merge topic (handles unique constraint) |
| `store_topic_bank_entry()` | Insert with full payloads (script_bank, seed_payloads, sources) |
| `upsert_topic_script_variants()` | Insert script variants with dedup on (registry_id, tier, script) |
| `create_topic_research_run()` | Track research execution |
| `update_topic_research_run()` | Update status, result_summary, dossier_id |
| `create_topic_research_dossier()` | Persist normalized dossier |
| `get_topic_scripts_for_registry()` | Fetch scripts with optional tier filter |
| `list_topic_suggestions()` | List topics with script excerpts for UI |

### Merge Strategies

When upserting, the queries layer handles conflicts:
- **`_merge_script_bank()`** — Combines variant lists, deduplicates by script text
- **`_merge_seed_payloads()`** — Merges tier-keyed payloads
- **`_merge_unique_source_bank()`** — Deduplicates sources by (title, url)

---

## Appendix: File Index

| File | Lines | Purpose |
|------|-------|---------|
| `app/features/topics/handlers.py` | ~1,370 | Entry points + progress tracking |
| `app/features/topics/hub.py` | ~710 | Orchestration + lane processing |
| `app/features/topics/research_runtime.py` | ~670 | Research generation + retry logic |
| `app/features/topics/queries.py` | ~700 | Database persistence layer |
| `app/features/topics/response_parsers.py` | ~740 | JSON/YAML parsing + synthesis |
| `app/features/topics/prompts.py` | ~500 | Prompt templates + topic bank |
| `app/features/topics/pipeline.py` | ~90 | Three-stage explicit boundaries |
| `app/features/topics/schemas.py` | ~480 | Pydantic models + JSON schemas |
| `app/features/topics/deduplication.py` | ~200 | Jaccard similarity algorithm |
| `app/features/topics/topic_validation.py` | ~280 | Duration, language, accessibility validation |
| `app/features/topics/lifestyle_runtime.py` | ~85 | Lifestyle template-based generation |
| `app/features/topics/content_utils.py` | ~44 | CTA extraction + social descriptions |
| `app/features/topics/seed_builders.py` | ~122 | Seed payload construction |
| `app/adapters/llm_client.py` | ~1,000+ | Gemini + OpenAI adapters |
| `app/core/video_profiles.py` | ~164 | 8/16/32s tier profiles |
| `app/core/states.py` | ~69 | Batch state machine |
