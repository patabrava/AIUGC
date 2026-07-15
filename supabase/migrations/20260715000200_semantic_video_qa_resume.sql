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
BEGIN
  SELECT run.* INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;
  resume_stage := locked_run.failure_envelope ->> 'stage';
  IF NOT FOUND
     OR locked_run.revision IS DISTINCT FROM p_expected_revision
     OR locked_run.stage IS DISTINCT FROM 'retry_approval_required'
     OR locked_run.plan_hash IS DISTINCT FROM p_plan_hash
     OR resume_stage IS DISTINCT FROM 'transcript_qa' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: QA review resume contract is stale';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.semantic_video_takes AS take
    WHERE take.run_id = p_run_id
      AND take.submission_state = 'qa_failed'
  ) OR EXISTS (
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
      MESSAGE = 'semantic_video_conflict: QA review requires durable completed takes';
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
