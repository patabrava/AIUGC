# Schema Cleanup & Publishing Architecture Formalization

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drop 6 dead columns from `topic_registry`, remove all Python code referencing them, and formalize the TikTok vs Meta publishing split with clear naming and documentation — without breaking any existing functionality.

**Architecture:** Two independent changes: (1) a DB migration dropping legacy columns + corresponding Python cleanup, (2) adding SQL comments and renaming internal references to make the dual-publishing-path explicit. Both changes are safe because the legacy columns are no longer written to, and the publishing tables already work correctly.

**Tech Stack:** PostgreSQL (Supabase), Python, pytest

---

## Task 1: Verify legacy columns are truly dead (read-only safety check)

**Files:**
- Read: `AIUGC/app/features/topics/queries.py`
- Read: `AIUGC/app/features/topics/hub.py`
- Read: `AIUGC/app/features/topics/handlers.py`

- [ ] **Step 1: Run the existing test suite to establish a green baseline**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all tests pass (or known failures unrelated to this work).

- [ ] **Step 2: Commit baseline (no changes yet)**

No commit needed — this is a read-only verification step.

---

## Task 2: Write the SQL migration to drop legacy columns

**Files:**
- Create: `AIUGC/supabase/migrations/017_drop_legacy_topic_registry_columns.sql`

- [ ] **Step 1: Write the migration**

```sql
-- Drop legacy columns from topic_registry that have been fully superseded by
-- topic_research_dossiers, topic_scripts, and topic_research_runs.
-- Migration 013 attempted this but was not applied to production.

ALTER TABLE public.topic_registry
  DROP COLUMN IF EXISTS script_bank,
  DROP COLUMN IF EXISTS seed_payloads,
  DROP COLUMN IF EXISTS source_bank,
  DROP COLUMN IF EXISTS research_payload,
  DROP COLUMN IF EXISTS target_length_tiers,
  DROP COLUMN IF EXISTS language;
```

- [ ] **Step 2: Apply the migration via Supabase MCP**

Use the `mcp__supabase__execute_sql` tool against project `qnvgiihzbihkedakggth` to run the SQL.

- [ ] **Step 3: Verify columns are gone**

```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'topic_registry'
ORDER BY ordinal_position;
```

Expected: only `id`, `title`, `use_count`, `first_seen_at`, `last_used_at`, `post_type`, `last_harvested_at`, `script`.

- [ ] **Step 4: Commit the migration file**

```bash
git add AIUGC/supabase/migrations/017_drop_legacy_topic_registry_columns.sql
git commit -m "chore: drop legacy topic_registry columns (script_bank, seed_payloads, source_bank, research_payload, target_length_tiers, language)"
```

---

## Task 3: Clean up `queries.py` — remove legacy column references

**Files:**
- Modify: `AIUGC/app/features/topics/queries.py`

The following code blocks reference the dropped columns and must be cleaned up:

- [ ] **Step 1: Clean `_normalize_registry_row` (lines 28-63)**

Remove lines 32-41 (script_bank fallback for script), lines 55-60 (normalizing dropped columns). The function should only normalize columns that still exist: `script`, `rotation`, `cta`, `title`, `post_type`, `first_seen_at`, `last_used_at`.

After:
```python
def _normalize_registry_row(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(row or {})
    script = str(normalized.get("script") or normalized.get("rotation") or "").strip()
    rotation = str(normalized.get("rotation") or "").strip()
    cta = str(normalized.get("cta") or "").strip()
    if script and (not rotation or not cta):
        derived_cta = _extract_cta(script)
        derived_rotation = script[: -len(derived_cta)].rstrip(" -–—,:;") if derived_cta and script.endswith(derived_cta) else script
        rotation = rotation or derived_rotation.strip() or script
        cta = cta or derived_cta or script

    normalized["script"] = script or rotation or cta
    normalized["rotation"] = rotation or normalized["script"]
    normalized["cta"] = cta or _extract_cta(normalized["script"])
    normalized["title"] = str(normalized.get("title") or "").strip()
    normalized["post_type"] = normalized.get("post_type")
    normalized["first_seen_at"] = normalized.get("first_seen_at") or normalized.get("created_at") or normalized.get("last_harvested_at")
    normalized["last_used_at"] = normalized.get("last_used_at") or normalized.get("updated_at") or normalized.get("last_harvested_at")
    return normalized
```

- [ ] **Step 2: Delete dead merge helpers (lines 111-154)**

Remove `_merge_unique_source_bank`, `_merge_script_bank`, and `_merge_seed_payloads` — these only operated on dropped columns.

- [ ] **Step 3: Clean `add_topic_to_registry` signature (lines 180-243)**

Remove parameters: `research_payload`, `source_bank`, `script_bank`, `seed_payloads`, `target_length_tiers`, `language`. The function already only writes `title`, `script`, `use_count`, `post_type`, `last_harvested_at`.

After signature:
```python
def add_topic_to_registry(
    title: str,
    rotation: Optional[str] = None,
    cta: Optional[str] = None,
    *,
    script: Optional[str] = None,
    post_type: Optional[str] = None,
    last_harvested_at: Optional[datetime] = None,
) -> Dict[str, Any]:
```

- [ ] **Step 4: Clean `_registry_row_to_topic_suggestion` (lines 246-275)**

Remove keys: `target_length_tiers`, `script_bank`, `source_bank`, `seed_payloads`, `research_payload`. Keep `source_urls` but source it from the topic_scripts path (already the primary path).

After:
```python
def _registry_row_to_topic_suggestion(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_registry_row(row)
    script = normalized["script"]
    return {
        "id": normalized["id"],
        "topic_registry_id": normalized["id"],
        "title": normalized["title"],
        "rotation": normalized["rotation"],
        "cta": normalized["cta"],
        "script": script,
        "spoken_duration": normalized.get("spoken_duration")
        or max(1, int(round(max(len(script.split()), 1) / 2.6))),
        "post_type": normalized.get("post_type"),
        "source_urls": [],
        "last_harvested_at": normalized.get("last_harvested_at"),
        "created_at": normalized.get("created_at"),
        "updated_at": normalized.get("updated_at"),
    }
```

- [ ] **Step 5: Clean `_hydrate_script_suggestion` (lines 278-307)**

Remove lines setting: `target_length_tiers`, `script_bank`, `source_bank`, `seed_payloads`, `research_payload`, and the `seed_payload` derivation from `seed_payloads`. The script row already carries `seed_payload` and `source_urls` directly.

After:
```python
def _hydrate_script_suggestion(
    script_row: Dict[str, Any],
    registry_row: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    hydrated = dict(script_row)
    registry = _normalize_registry_row(registry_row or {})
    hydrated["id"] = hydrated.get("id") or registry.get("id")
    hydrated["topic_registry_id"] = hydrated.get("topic_registry_id") or registry.get("id")
    hydrated["title"] = str(hydrated.get("title") or registry.get("title") or "").strip()
    hydrated["rotation"] = registry.get("rotation") or hydrated.get("script") or ""
    hydrated["cta"] = registry.get("cta") or _extract_cta(str(hydrated.get("script") or ""))
    hydrated["source_urls"] = hydrated.get("source_urls") or []
    hydrated["seed_payload"] = hydrated.get("seed_payload") or {}
    hydrated["spoken_duration"] = hydrated.get("estimated_duration_s") or max(
        1, int(round(max(len(str(hydrated.get("script") or "").split()), 1) / 2.6))
    )
    hydrated["last_harvested_at"] = registry.get("last_harvested_at")
    hydrated["created_at"] = hydrated.get("created_at") or registry.get("created_at")
    hydrated["updated_at"] = hydrated.get("updated_at") or registry.get("updated_at")
    return hydrated
```

- [ ] **Step 6: Clean `list_topic_suggestions` fallback (lines 449-465)**

Remove the `target_length_tiers` filtering in the registry-only fallback path (line 454). This fallback now only filters by `post_type`.

- [ ] **Step 7: Clean `store_topic_bank_entry` signature (lines 575-613)**

Remove parameters: `source_bank`, `script_bank`, `seed_payloads`. These are no longer written. The function already routes data to `create_topic_research_dossier`.

After signature:
```python
def store_topic_bank_entry(
    *,
    title: str,
    topic_script: str,
    post_type: str,
    target_length_tier: int,
    research_payload: Dict[str, Any],
    language: str = "de",
    topic_research_run_id: Optional[str] = None,
    topic_research_dossier_id: Optional[str] = None,
    raw_prompt: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> Dict[str, Any]:
```

Note: `research_payload` is kept here because it's unpacked and passed to `create_topic_research_dossier` (lines 600-606) — it's not a DB column reference, it's a function parameter carrying dossier data.

- [ ] **Step 8: Run tests**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: PASS (or only pre-existing failures).

- [ ] **Step 9: Commit**

```bash
git add AIUGC/app/features/topics/queries.py
git commit -m "refactor: remove legacy column refs from topic queries"
```

---

## Task 4: Clean up callers — `hub.py`, `handlers.py`, test scripts

**Files:**
- Modify: `AIUGC/app/features/topics/hub.py`
- Modify: `AIUGC/app/features/topics/handlers.py`
- Modify: `AIUGC/agents/testscripts/testscript_topic_bank_e2e.py`

**DO NOT modify:**
- `AIUGC/app/features/topics/variant_expansion.py` — its `target_length_tiers` param is a runtime config option, NOT a DB column reference. Leave it as-is.
- `AIUGC/tests/test_expand_script_bank.py` — its `target_length_tiers=[8]` call is valid, testing the runtime param. Leave it as-is.
- `AIUGC/tests/test_topics_hub.py` lines 332-333 — these assert on `research_payload` as a **function kwarg** to `store_topic_bank_entry`, which still accepts it. Leave as-is.

- [ ] **Step 1: Clean `hub.py` — remove dead legacy helpers and params**

- **Delete** `_source_bank_from_dossier` helper entirely (line ~389-394) — it only feeds the removed `source_bank` param.
- Remove `source_bank=source_bank`, `script_bank={}`, `seed_payloads={}` from all `store_topic_bank_entry()` / `add_topic_to_registry()` calls.
- Remove the `source_bank = _source_bank_from_dossier(research_dossier)` assignment.

- [ ] **Step 2: Clean `hub.py` — fix `_topic_has_tier` (line ~172)**

This function reads `topic.get("target_length_tiers")` which no longer exists on registry rows. Update it to query `topic_scripts` for available tiers, or remove the tier-filtering logic if it's no longer needed at this level.

- [ ] **Step 3: Clean `hub.py` — fix `_topic_search_match` (line ~160)**

Remove `"language"` from the search haystack — the column no longer exists.

- [ ] **Step 4: Clean `handlers.py`**

- Remove `source_bank=[]`, `script_bank={}`, `seed_payloads={}`, `research_payload={}` from all `add_topic_to_registry()` calls (lines ~677-680 and ~1013-1016).
- For `store_topic_bank_entry()` calls: remove `source_bank=[]`, `script_bank={}`, `seed_payloads={}` but **keep** `research_payload={}` (it's still a valid param).

- [ ] **Step 5: Clean `testscript_topic_bank_e2e.py`**

- Line ~75: Remove `"tiers": row.get("target_length_tiers")` from debug print (will always be None).
- Line ~175: Remove assertion checking `stored_row.get("script_bank") or stored_row.get("seed_payloads") or stored_row.get("research_payload")` — these DB columns no longer exist.
- Line ~220: Remove `if "script_bank" in seed_data` check.

- [ ] **Step 5: Run tests**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: remove legacy column refs from hub, handlers, and tests"
```

---

## Task 5: Formalize the TikTok vs Meta publishing split

**Files:**
- Create: `AIUGC/supabase/migrations/018_document_publishing_architecture.sql`
- Modify: `AIUGC/app/features/publish/schemas.py` (add docstrings only)

- [ ] **Step 1: Write the documentation migration**

This migration adds SQL COMMENTs to make the architecture explicit — no schema changes.

```sql
-- Formalize the dual publishing architecture:
-- TikTok: connected_accounts → media_assets → publish_jobs (dedicated tables)
-- Meta (Facebook/Instagram): batches.meta_connection → posts.publish_results (inline)

COMMENT ON TABLE public.connected_accounts IS
  'TikTok-only OAuth credentials. Meta uses batches.meta_connection instead.';

COMMENT ON TABLE public.publish_jobs IS
  'TikTok-only publish job tracking. Meta publishing is tracked inline on posts.publish_results.';

COMMENT ON TABLE public.media_assets IS
  'TikTok-only media asset storage for video uploads.';

COMMENT ON COLUMN public.batches.meta_connection IS
  'Meta (Facebook/Instagram) OAuth connection, reachable pages, and selected targets. This is the Meta equivalent of connected_accounts — scoped per batch.';

COMMENT ON COLUMN public.posts.publish_results IS
  'Per-network publish results keyed by network name (e.g. {"tiktok": {...}, "instagram": {...}}). Meta results stored here; TikTok results also mirrored here from publish_jobs.';

COMMENT ON COLUMN public.posts.social_networks IS
  'Selected target networks for this post: tiktok, instagram, facebook. Unified across both publishing paths.';
```

- [ ] **Step 2: Apply the migration via Supabase MCP**

Use the `mcp__supabase__execute_sql` tool to run the SQL.

- [ ] **Step 3: Commit**

```bash
git add AIUGC/supabase/migrations/018_document_publishing_architecture.sql
git commit -m "docs: add SQL comments formalizing dual TikTok/Meta publishing paths"
```

---

## Task 6: Update documentation

**Files:**
- Modify: `AIUGC/AGENTS.md` (remove references to legacy `script_bank`/`seed_payloads` writes)
- Modify: `AIUGC/deep-research-flow.md` (update if it references dropped columns)
- Modify: `AIUGC/Deep Research Architecture.md` (update table schema section and remove merge function docs)

- [ ] **Step 1: Update AGENTS.md**

Remove or update line ~94 referencing `script_bank/seed_payloads writes` — these columns no longer exist.

- [ ] **Step 2: Update Deep Research Architecture.md**

- Remove `target_length_tiers` from the topic_registry column table (~line 442).
- Remove `_merge_script_bank`, `_merge_seed_payloads`, `_merge_unique_source_bank` from function docs (~lines 619-632).
- Update `store_topic_bank_entry` signature docs to remove dropped params.

- [ ] **Step 3: Commit**

```bash
git add AIUGC/AGENTS.md AIUGC/deep-research-flow.md "AIUGC/Deep Research Architecture.md"
git commit -m "docs: update architecture docs after legacy column removal"
```

---

## Task 7: Final verification

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/camiloecheverri/Documents/AI/AIUGC/AIUGC && python -m pytest tests/ -x -q 2>&1 | tail -20
```

- [ ] **Step 2: Verify DB schema is clean**

```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'topic_registry'
ORDER BY ordinal_position;
```

- [ ] **Step 3: Verify publishing tables have comments**

```sql
SELECT obj_description('public.connected_accounts'::regclass);
SELECT obj_description('public.publish_jobs'::regclass);
SELECT obj_description('public.media_assets'::regclass);
```

- [ ] **Step 4: Smoke-test the app manually**

Start the app and verify:
1. Topic hub loads and displays topics correctly
2. Topic research runs can be created
3. Publishing page loads with correct Meta/TikTok status
