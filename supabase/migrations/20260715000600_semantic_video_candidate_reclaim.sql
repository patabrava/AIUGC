CREATE OR REPLACE FUNCTION public.reclaim_semantic_video_candidate_reservation(
  p_run_id UUID,
  p_expected_revision INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
BEGIN
  SELECT run.* INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND
     OR locked_run.revision IS DISTINCT FROM p_expected_revision
     OR locked_run.stage IS DISTINCT FROM 'awaiting_reference_approval'
     OR locked_run.candidate_reservation_token IS NULL
     OR locked_run.candidate_reservation_expires_at IS NULL
     OR locked_run.candidate_reservation_expires_at > pg_catalog.clock_timestamp()
     OR pg_catalog.jsonb_typeof(
       pg_catalog.coalesce(locked_run.master_snapshot -> 'candidates', '[]'::JSONB)
     ) IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_array_length(
       pg_catalog.coalesce(locked_run.master_snapshot -> 'candidates', '[]'::JSONB)
     ) <> 0 THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate reservation is not safely reclaimable';
  END IF;

  UPDATE public.semantic_video_runs AS run
  SET candidate_reservation_owner = NULL,
      candidate_reservation_token = NULL,
      candidate_reservation_expires_at = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;

  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

REVOKE ALL ON FUNCTION public.reclaim_semantic_video_candidate_reservation(UUID, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reclaim_semantic_video_candidate_reservation(UUID, INTEGER)
  TO service_role;
