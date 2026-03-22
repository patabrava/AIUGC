-- Backfill existing NULL values with identifiable defaults.
-- NOTE: Some existing rows have hook_style set to bucket names like
-- 'problem-agitate-solution', 'testimonial', 'transformation' (from
-- _build_script_variants in hub.py). These are NOT real hook styles
-- but they won't collide with the hook bank vocabulary used by variant
-- expansion, so they're safe to leave as-is. They identify legacy rows.
UPDATE public.topic_scripts
SET framework = 'PAL'
WHERE framework IS NULL;

UPDATE public.topic_scripts
SET hook_style = 'default'
WHERE hook_style IS NULL;

-- Deduplicate: keep the row with highest use_count (then most recent updated_at)
-- per (topic_registry_id, target_length_tier, post_type, framework, hook_style).
WITH ranked AS (
  SELECT id,
    ROW_NUMBER() OVER (
      PARTITION BY topic_registry_id, target_length_tier, post_type, framework, hook_style
      ORDER BY use_count DESC, updated_at DESC
    ) as rn
  FROM public.topic_scripts
)
DELETE FROM public.topic_scripts
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- Enforce NOT NULL
ALTER TABLE public.topic_scripts
ALTER COLUMN framework SET NOT NULL,
ALTER COLUMN hook_style SET NOT NULL;

-- Add unique constraint for variant expansion dedup
-- (separate from existing unique on topic_registry_id, target_length_tier, script)
CREATE UNIQUE INDEX IF NOT EXISTS topic_scripts_variant_unique_idx
ON public.topic_scripts (topic_registry_id, target_length_tier, post_type, framework, hook_style);
