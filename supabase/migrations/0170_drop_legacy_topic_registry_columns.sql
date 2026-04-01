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
