CREATE OR REPLACE FUNCTION public.renew_semantic_video_lease(
  p_run_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_lease_seconds INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  updated_run public.semantic_video_runs%ROWTYPE;
BEGIN
  IF p_lease_seconds IS NULL
     OR p_lease_seconds <= 0 OR p_lease_seconds > 3600 THEN
    RAISE EXCEPTION 'p_lease_seconds must be between 1 and 3600';
  END IF;

  PERFORM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, NULL
  );

  UPDATE public.semantic_video_runs AS run
  SET lease_expires_at = now() + pg_catalog.make_interval(secs => p_lease_seconds)
  WHERE run.id = p_run_id
    AND run.lease_owner = p_worker_id
    AND run.lease_token = p_lease_token
  RETURNING run.* INTO updated_run;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: worker lease renewal was fenced';
  END IF;

  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

REVOKE ALL ON FUNCTION public.renew_semantic_video_lease(UUID, TEXT, UUID, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.renew_semantic_video_lease(UUID, TEXT, UUID, INTEGER)
  TO service_role;
