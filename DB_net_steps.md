# DB Net Steps (What Was Built + What's Missing)

Date: 2026-03-22

## Goal
Stabilize Topics persistence with a zero-breakage schema simplification:
- Keep the 4-table model (`topic_registry`, `topic_research_runs`, `topic_research_dossiers`, `topic_scripts`)
- Make scripts reference the normalized dossier as canonical provenance (`topic_scripts.topic_research_dossier_id`)
- Add compatibility view for future read-path switching
- Backfill existing script rows to reduce drift

## What I Developed (Code)
Changes are already implemented locally to support the new provenance link while keeping the app working:

- `topic_scripts` writes now include `topic_research_dossier_id` (nullable until DB migration is applied).
  - File: `app/features/topics/queries.py`
  - File: `app/features/topics/hub.py`
- `topic_research_runs` updates now support persisting `dossier_id` on the run row (canonical pointer).
  - File: `app/features/topics/queries.py`
  - File: `app/features/topics/research_runtime.py`
- Stage-3 lane handling is hardened: accepts Pydantic models or dicts (no `.get()` on Pydantic).
  - File: `app/features/topics/research_runtime.py`
- `TopicScriptVariant` schema now includes `topic_research_dossier_id` (optional).
  - File: `app/features/topics/schemas.py`

Tests updated/added:
- Hub harvest test asserts the dossier id is threaded into `upsert_topic_script_variants`.
  - File: `tests/test_topics_hub.py`
- Gemini flow test updated for the current behavior (one deep research dossier per generated topic) and stage-3 JSON shape.
  - File: `tests/test_topics_gemini_flow.py`

Local verification:
- `python3 -m pytest -q tests/test_topics_hub.py tests/test_topics_gemini_flow.py` passes (24 tests).

## What I Developed (DB Migration File)
New additive migration is prepared locally:
- File: `supabase/migrations/014_topic_scripts_dossier_fk.sql`

It does:
- Adds `topic_scripts.topic_research_dossier_id uuid` (nullable)
- Adds FK: `topic_scripts(topic_research_dossier_id) -> topic_research_dossiers(id)` with `ON DELETE SET NULL`
- Adds index: `topic_scripts_topic_research_dossier_id_idx`
- Adds uniqueness guard: `(topic_research_dossier_id, target_length_tier, bucket, lane_key)`
- Creates compatibility view: `public.v_topic_scripts_resolved` joining scripts -> dossier -> run

## What’s Missing / Blocked (DB + Networking)
### 1) Supabase MCP in this Codex session is not callable
Even though your UI shows MCP servers enabled, this Codex session cannot call them.
Symptoms:
- Tool lookup fails for `supabase-aiugc` / `supabase_aiugc` / `supabase`
- `list_mcp_resources` returns either `unknown MCP server` or `Method not found`

Likely root cause (observed locally):
- The MCP config I could read contains a Supabase server named `supabase-mcp-server`, not `supabase-aiugc`.
- Another MCP config file (`~/.gemini/antigravity/mcp_config.json`) is empty.
- Net: the UI is reading a different MCP registry/config than this session is using, or server names do not match.

### 2) Supabase CLI cannot push migrations cleanly from repo root due to `.env` parse error
`supabase link` fails when run in the repo directory because the CLI tries to parse `.env` and hits an invalid line.
Observed:
- `.env` contains a bare API key line (no `NAME=value`) which breaks parsing.

Workaround used:
- Run Supabase CLI from a clean temp directory and use `env -i ...` to avoid `.env` parsing.

### 3) Remote migration history is divergent + local versions collide
The CLI push path is currently blocked by:
- Duplicate local migration versions (`005_*.sql` and `006_*.sql` both exist twice)
- Remote history entries that were not present locally (repaired as `reverted`)
- Applying local migrations in bulk causes collisions and/or failures because the remote schema already differs.

Concrete failures observed:
- `db push` with `--include-all` fails on duplicate `schema_migrations_pkey` for version `005`
- Replaying older migrations fails because remote schema does not match expected columns (example: `rotation` column missing in `topic_registry` during a backfill migration)

### 4) Applying ONLY the new additive migration (014) still requires DB password
To apply `014_topic_scripts_dossier_fk.sql` without relying on migration-history replay, we need a direct DB connection.
Currently missing:
- The actual Postgres database password (not the JWT API keys).

Notes:
- `SUPABASE_KEY` / `SUPABASE_SERVICE_KEY` are API keys (JWT) for PostgREST/Auth, not the Postgres password.
- The CLI can list projects with `SUPABASE_ACCESS_TOKEN`, but applying SQL/migrations to the remote DB still requires the DB password unless using the MCP.

## Next Steps (Recommended Order)
### A) Make Supabase MCP callable from this session (preferred)
This is the cleanest path because it avoids DB password requirements and avoids migration-history replay.
- Ensure the MCP server name exposed to Codex matches what we call (`supabase-aiugc`).
- Ensure this Codex session is loading the same MCP config as the UI.
- Restart Codex after config changes if required.

Then:
- Apply `014_topic_scripts_dossier_fk.sql` via MCP migration tool
- Run a backfill SQL via MCP
- Run an end-to-end deep-research -> normalize -> stage-3 generation and verify 100% persistence success

### B) If we must use Supabase CLI, provide the real DB password
Once we have the Postgres password, do:
- Apply ONLY the `014_topic_scripts_dossier_fk.sql` change (do not replay old migrations)
- Run backfill SQL (single deterministic UPDATE)
- Verify via SQL checks

### C) Backfill plan (after 014 applied)
Backfill `topic_scripts.topic_research_dossier_id` for existing rows using a deterministic rule:
- For each script row with NULL dossier id, choose the latest dossier matching:
  - `topic_registry_id`, `post_type`, `target_length_tier`
  - prefer matching `cluster_id` when available

Then verify:
- Coverage ratio by tier/post_type
- No uniqueness violations for `(dossier_id, tier, bucket, lane_key)`

## Safety / Security Notes
- Do not commit or paste real secrets into logs or markdown artifacts.
- Rotate the pasted `SUPABASE_ACCESS_TOKEN` and service keys after the DB work is done.

