CREATE OR REPLACE FUNCTION public.release_semantic_video_candidate_reservation(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_reservation_token UUID
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
     OR locked_run.candidate_reservation_token IS DISTINCT FROM p_reservation_token
     OR CASE
       WHEN NOT (locked_run.master_snapshot ? 'candidates') THEN FALSE
       WHEN pg_catalog.jsonb_typeof(locked_run.master_snapshot -> 'candidates')
         IS DISTINCT FROM 'array' THEN TRUE
       ELSE pg_catalog.jsonb_array_length(locked_run.master_snapshot -> 'candidates') <> 0
     END THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate reservation release contract is stale';
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

REVOKE ALL ON FUNCTION public.release_semantic_video_candidate_reservation(UUID, INTEGER, UUID)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_semantic_video_candidate_reservation(UUID, INTEGER, UUID)
  TO service_role;
