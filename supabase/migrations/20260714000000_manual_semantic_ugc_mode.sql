-- Extend the Semantic UGC duration authority to manual-script batches.

ALTER TABLE public.batches
  DROP CONSTRAINT IF EXISTS batches_creation_mode_check,
  DROP CONSTRAINT IF EXISTS batches_duration_authority_check,
  DROP CONSTRAINT IF EXISTS batches_semantic_pipeline_route_check;

ALTER TABLE public.batches
  ADD CONSTRAINT batches_creation_mode_check CHECK (
    creation_mode IN (
      'automated',
      'manual',
      'manual_character_consistency',
      'character_consistency',
      'character_consistency_light',
      'character_consistency_mid',
      'semantic_ugc',
      'manual_semantic_ugc'
    )
  ),
  ADD CONSTRAINT batches_duration_authority_check CHECK (
    (
      creation_mode IN ('semantic_ugc', 'manual_semantic_ugc')
      AND target_length_tier IS NULL
      AND target_duration_seconds IS NOT NULL
    )
    OR
    (
      creation_mode NOT IN ('semantic_ugc', 'manual_semantic_ugc')
      AND target_length_tier IS NOT NULL
      AND target_duration_seconds IS NULL
    )
  ),
  ADD CONSTRAINT batches_semantic_pipeline_route_check CHECK (
    (
      creation_mode IN ('semantic_ugc', 'manual_semantic_ugc')
      AND video_pipeline_route = 'semantic_ugc'
    )
    OR
    (
      creation_mode NOT IN ('semantic_ugc', 'manual_semantic_ugc')
      AND video_pipeline_route IS DISTINCT FROM 'semantic_ugc'
    )
  );
