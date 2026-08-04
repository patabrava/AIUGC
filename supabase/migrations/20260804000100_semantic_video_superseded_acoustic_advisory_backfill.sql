-- Remove stale acoustic advisories from already-completed deliveries only when
-- every current delivery gate proves that the earlier finding was superseded.
UPDATE public.semantic_video_runs AS run
SET artifact_manifest = pg_catalog.jsonb_set(
      run.artifact_manifest - 'qa_advisory',
      '{pipeline_manifest}',
      (run.artifact_manifest -> 'pipeline_manifest') - 'delivery_qa_advisory',
      true
    ),
    revision = run.revision + 1
WHERE run.stage = 'completed'
  AND pg_catalog.jsonb_typeof(run.artifact_manifest -> 'pipeline_manifest') = 'object'
  AND run.artifact_manifest -> 'pipeline_manifest' -> 'delivery_qa_advisory' ->> 'stage' = 'acoustic_qa'
  AND run.artifact_manifest -> 'pipeline_manifest' -> 'seam_qa' ->> 'passed' = 'true'
  AND run.artifact_manifest -> 'pipeline_manifest' -> 'acoustic_seam_qa' ->> 'passed' = 'true'
  AND run.artifact_manifest -> 'pipeline_manifest' -> 'delivery_visual_qa' ->> 'passed' = 'true';
