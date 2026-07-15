CREATE OR REPLACE FUNCTION public.persist_semantic_video_worker_exception(
  p_run_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_stage TEXT,
  p_error JSONB
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
  IF pg_catalog.jsonb_typeof(p_error) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'semantic video worker exception must be an object';
  END IF;
  SELECT * INTO locked_run
  FROM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, p_stage
  );
  UPDATE public.semantic_video_runs AS run
  SET failure_envelope = p_error || pg_catalog.jsonb_build_object(
        'stage', p_stage,
        'recorded_at', now()
      ),
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;
  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

REVOKE ALL ON FUNCTION public.persist_semantic_video_worker_exception(UUID, TEXT, UUID, TEXT, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.persist_semantic_video_worker_exception(UUID, TEXT, UUID, TEXT, JSONB)
  TO service_role;
