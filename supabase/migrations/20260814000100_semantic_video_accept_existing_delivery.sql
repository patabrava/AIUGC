CREATE OR REPLACE FUNCTION public.resume_semantic_video_qa_review(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_plan_hash TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  resume_stage TEXT;
  failed_take_indexes INTEGER[];
  accept_existing_delivery_as_is BOOLEAN;
BEGIN
  SELECT run.* INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  resume_stage := locked_run.failure_envelope ->> 'stage';
  accept_existing_delivery_as_is :=
    locked_run.artifact_manifest #>> '{qa_failure,retry_mode}' = 'localized_paid_take';
  IF NOT FOUND
     OR locked_run.revision IS DISTINCT FROM p_expected_revision
     OR locked_run.stage IS DISTINCT FROM 'retry_approval_required'
     OR locked_run.plan_hash IS DISTINCT FROM p_plan_hash
     OR resume_stage NOT IN (
       'transcript_qa',
       'identity_qa',
       'voice_qa',
       'acoustic_qa'
     ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: advisory QA resume contract is stale';
  END IF;

  SELECT pg_catalog.array_agg(latest.take_index ORDER BY latest.take_index)
  INTO failed_take_indexes
  FROM (
    SELECT DISTINCT ON (take.take_index)
      take.take_index,
      take.submission_state,
      take.raw_artifact_uri,
      take.raw_artifact_sha256
    FROM public.semantic_video_takes AS take
    WHERE take.run_id = p_run_id
    ORDER BY take.take_index, take.attempt DESC
  ) AS latest
  WHERE latest.submission_state = 'qa_failed';

  IF coalesce(pg_catalog.cardinality(failed_take_indexes), 0) = 0
     OR EXISTS (
       SELECT 1
       FROM (
         SELECT DISTINCT ON (take.take_index) take.*
         FROM public.semantic_video_takes AS take
         WHERE take.run_id = p_run_id
         ORDER BY take.take_index, take.attempt DESC
       ) AS latest
       WHERE latest.submission_state NOT IN ('completed', 'qa_failed')
          OR NULLIF(pg_catalog.btrim(latest.raw_artifact_uri), '') IS NULL
          OR latest.raw_artifact_sha256 !~ '^[0-9a-f]{64}$'
     ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: advisory QA requires durable completed takes';
  END IF;

  WITH latest AS (
    SELECT DISTINCT ON (take.take_index) take.id
    FROM public.semantic_video_takes AS take
    WHERE take.run_id = p_run_id
    ORDER BY take.take_index, take.attempt DESC
  )
  UPDATE public.semantic_video_takes AS take
  SET submission_state = 'completed',
      retry_guidance = NULL
  FROM latest
  WHERE take.id = latest.id
    AND take.submission_state = 'qa_failed';

  UPDATE public.semantic_video_runs AS run
  SET stage = resume_stage,
      failure_envelope = NULL,
      artifact_manifest = CASE
        WHEN resume_stage = 'transcript_qa'
        THEN (
          coalesce(run.artifact_manifest, '{}'::JSONB) - 'qa_failure'
        ) || pg_catalog.jsonb_build_object(
          'qa_advisory',
          pg_catalog.jsonb_build_object(
            'required', TRUE,
            'stage', 'transcript_qa',
            'failed_take_indexes', pg_catalog.to_jsonb(failed_take_indexes),
            'message', coalesce(
              run.artifact_manifest #>> '{qa_failure,message}',
              'Automated transcript QA requires manual review.'
            ),
            'paid_retry_required', FALSE
          )
        )
        WHEN resume_stage = 'identity_qa'
        THEN pg_catalog.jsonb_set(
          coalesce(run.artifact_manifest, '{}'::JSONB) - 'identity_qa',
          '{pipeline_manifest}',
          (
            coalesce(
              run.artifact_manifest -> 'pipeline_manifest',
              '{}'::JSONB
            )
            - 'contact_sheet'
            - 'actor_identity_qa'
            - 'scene_continuity_qa'
            - 'visual_qa'
          ) || pg_catalog.jsonb_build_object('status', 'transcript_qa_passed'),
          TRUE
        )
        WHEN resume_stage = 'acoustic_qa'
        THEN (
          pg_catalog.jsonb_set(
            coalesce(run.artifact_manifest, '{}'::JSONB) - 'qa_failure',
            '{pipeline_manifest}',
            (
              coalesce(
                run.artifact_manifest -> 'pipeline_manifest',
                '{}'::JSONB
              )
              - 'acoustic_plan_failure'
              - 'acoustic_preroll_normalization'
            ) || pg_catalog.jsonb_build_object('status', 'voice_qa_passed'),
            TRUE
          )
        ) || pg_catalog.jsonb_build_object(
          'qa_advisory',
          pg_catalog.jsonb_build_object(
            'required', TRUE,
            'stage', 'acoustic_qa',
            'failed_take_indexes', pg_catalog.to_jsonb(failed_take_indexes),
            'message', coalesce(
              run.artifact_manifest #>> '{qa_failure,message}',
              'Automated delivery QA requires manual review.'
            ),
            'paid_retry_required', FALSE,
            'accept_existing_delivery_as_is', accept_existing_delivery_as_is
          )
        )
        ELSE run.artifact_manifest
      END,
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;

  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

REVOKE ALL ON FUNCTION public.resume_semantic_video_qa_review(UUID, INTEGER, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.resume_semantic_video_qa_review(UUID, INTEGER, TEXT)
  TO service_role;
