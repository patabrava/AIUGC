ALTER TABLE public.semantic_video_runs
  ADD COLUMN IF NOT EXISTS candidate_generation_progress JSONB
  NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE public.semantic_video_runs
  DROP CONSTRAINT IF EXISTS semantic_video_runs_candidate_progress_object;

ALTER TABLE public.semantic_video_runs
  ADD CONSTRAINT semantic_video_runs_candidate_progress_object
  CHECK (
    pg_catalog.jsonb_typeof(candidate_generation_progress) = 'object'
  );

CREATE OR REPLACE FUNCTION public.update_semantic_video_candidate_progress(
  p_run_id UUID,
  p_reserved_revision INTEGER,
  p_reservation_token UUID,
  p_progress JSONB
)
RETURNS SETOF public.semantic_video_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  updated_run public.semantic_video_runs%ROWTYPE;
  progress_phase TEXT;
BEGIN
  progress_phase := p_progress ->> 'phase';
  IF p_run_id IS NULL
     OR p_reserved_revision IS NULL
     OR p_reserved_revision < 0
     OR p_reservation_token IS NULL
     OR pg_catalog.jsonb_typeof(p_progress) IS DISTINCT FROM 'object'
     OR NULLIF(pg_catalog.btrim(progress_phase), '') IS NULL
     OR progress_phase NOT IN (
       'preparing_references',
       'generating_images',
       'checking_diversity',
       'regenerating_duplicates',
       'checking_identity',
       'saving_candidates',
       'ready'
     )
     OR pg_catalog.jsonb_typeof(
       pg_catalog.coalesce(p_progress -> 'details', '{}'::JSONB)
     ) IS DISTINCT FROM 'object'
     OR pg_catalog.jsonb_typeof(p_progress -> 'updated_at') IS DISTINCT FROM 'string'
  THEN
    RAISE EXCEPTION 'semantic video candidate progress contract is invalid';
  END IF;

  UPDATE public.semantic_video_runs AS run
  SET candidate_generation_progress = p_progress
  WHERE run.id = p_run_id
    AND run.revision = p_reserved_revision
    AND run.stage = 'awaiting_reference_approval'
    AND run.candidate_reservation_token = p_reservation_token
    AND run.candidate_reservation_expires_at > pg_catalog.clock_timestamp()
  RETURNING run.* INTO updated_run;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate progress reservation is stale';
  END IF;

  RETURN NEXT updated_run;
END;
$$;

REVOKE ALL ON FUNCTION public.update_semantic_video_candidate_progress(
  UUID,
  INTEGER,
  UUID,
  JSONB
) FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.update_semantic_video_candidate_progress(
  UUID,
  INTEGER,
  UUID,
  JSONB
) TO service_role;
