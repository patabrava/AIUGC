CREATE OR REPLACE FUNCTION public.apply_semantic_video_visual_remediation(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_plan_hash TEXT,
  p_take_index INTEGER,
  p_expected_raw_sha256 TEXT,
  p_remediated_raw_uri TEXT,
  p_remediated_raw_sha256 TEXT,
  p_transformation JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  target_take public.semantic_video_takes%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  cleaned_manifest JSONB;
BEGIN
  SELECT run.* INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;
  IF NOT FOUND
     OR locked_run.revision IS DISTINCT FROM p_expected_revision
     OR locked_run.stage IS DISTINCT FROM 'retry_approval_required'
     OR locked_run.plan_hash IS DISTINCT FROM p_plan_hash
     OR locked_run.failure_envelope ->> 'stage' IS DISTINCT FROM 'identity_qa'
     OR p_take_index < 0
     OR p_expected_raw_sha256 !~ '^[0-9a-f]{64}$'
     OR p_remediated_raw_sha256 !~ '^[0-9a-f]{64}$'
     OR p_remediated_raw_sha256 = p_expected_raw_sha256
     OR NULLIF(pg_catalog.btrim(p_remediated_raw_uri), '') IS NULL
     OR pg_catalog.jsonb_typeof(p_transformation) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: visual remediation contract is stale';
  END IF;
  SELECT take.* INTO target_take
  FROM public.semantic_video_takes AS take
  WHERE take.run_id = p_run_id
    AND take.take_index = p_take_index
  ORDER BY take.attempt DESC
  LIMIT 1
  FOR UPDATE;
  IF NOT FOUND
     OR target_take.submission_state IS DISTINCT FROM 'qa_failed'
     OR target_take.raw_artifact_sha256 IS DISTINCT FROM p_expected_raw_sha256 THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: visual remediation target changed';
  END IF;
  IF EXISTS (
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
      MESSAGE = 'semantic_video_conflict: visual remediation requires durable takes';
  END IF;
  UPDATE public.semantic_video_takes AS take
  SET raw_artifact_uri = p_remediated_raw_uri,
      raw_artifact_sha256 = p_remediated_raw_sha256
  WHERE take.id = target_take.id;
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
  cleaned_manifest := COALESCE(locked_run.artifact_manifest, '{}'::JSONB);
  IF pg_catalog.jsonb_typeof(cleaned_manifest -> 'pipeline_manifest') = 'object' THEN
    cleaned_manifest := pg_catalog.jsonb_set(
      cleaned_manifest,
      '{pipeline_manifest}',
      ((cleaned_manifest -> 'pipeline_manifest') - 'contact_sheet' - 'visual_qa')
        || pg_catalog.jsonb_build_object('status', 'transcript_passed'),
      true
    );
  END IF;
  cleaned_manifest := cleaned_manifest || pg_catalog.jsonb_build_object(
    'visual_remediation',
    pg_catalog.jsonb_build_object(
      'take_index', p_take_index,
      'source_sha256', p_expected_raw_sha256,
      'output_sha256', p_remediated_raw_sha256,
      'output_uri', p_remediated_raw_uri,
      'transformation', p_transformation,
      'recorded_at', now()
    )
  );
  UPDATE public.semantic_video_runs AS run
  SET stage = 'identity_qa',
      artifact_manifest = cleaned_manifest,
      failure_envelope = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;
  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

REVOKE ALL ON FUNCTION public.apply_semantic_video_visual_remediation(
  UUID, INTEGER, TEXT, INTEGER, TEXT, TEXT, TEXT, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_semantic_video_visual_remediation(
  UUID, INTEGER, TEXT, INTEGER, TEXT, TEXT, TEXT, JSONB
) TO service_role;
