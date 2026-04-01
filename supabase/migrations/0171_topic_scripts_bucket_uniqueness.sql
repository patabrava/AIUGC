-- Value-topic banking stores one row per bucket. The legacy unique constraint
-- on (topic_registry_id, target_length_tier, script) collapses identical
-- bucket copies into a single row and breaks the 3-variant tier contract.

WITH ranked AS (
  SELECT
    id,
    ROW_NUMBER() OVER (
      PARTITION BY topic_registry_id, target_length_tier, COALESCE(bucket, ''), script
      ORDER BY use_count DESC, updated_at DESC, created_at DESC, id DESC
    ) AS rn
  FROM public.topic_scripts
)
DELETE FROM public.topic_scripts
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

ALTER TABLE public.topic_scripts
DROP CONSTRAINT IF EXISTS topic_scripts_topic_registry_id_target_length_tier_script_key;

DROP INDEX IF EXISTS public.topic_scripts_topic_registry_id_target_length_tier_script_key;

CREATE UNIQUE INDEX IF NOT EXISTS topic_scripts_registry_tier_bucket_script_key
ON public.topic_scripts (topic_registry_id, target_length_tier, bucket, script);
