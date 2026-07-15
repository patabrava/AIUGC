BEGIN;

CREATE OR REPLACE FUNCTION public.persist_semantic_video_provider_failure(
  p_run_id UUID,
  p_take_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_error JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE updated_run public.semantic_video_runs%ROWTYPE;
BEGIN
  PERFORM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, 'generating'
  );
  IF pg_catalog.jsonb_typeof(p_error) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'provider failure requires an error envelope';
  END IF;
  UPDATE public.semantic_video_takes AS take
  SET submission_state = 'failed',
      quota_state = CASE WHEN take.quota_state = 'reserved' THEN 'consumed' ELSE take.quota_state END,
      submission_error = p_error,
      retry_guidance = pg_catalog.jsonb_build_object(
        'guidance', CASE
          WHEN p_error ->> 'code' = 'provider_operation_failed' THEN
            'Preserve the original delivery exactly; the provider operation failed internally before producing a usable take.'
          ELSE
            'Preserve the exact approved semantic beat and retry after the provider failed before producing a usable take.'
        END,
        'source', CASE
          WHEN p_error ->> 'code' = 'provider_operation_failed' THEN 'provider_internal_failure'
          ELSE 'provider_failure'
        END
      )
  WHERE take.id = p_take_id
    AND take.run_id = p_run_id
    AND take.submission_state IN ('intent_persisted', 'submitted');
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'semantic_video_conflict: provider failure take is not active';
  END IF;
  UPDATE public.semantic_video_runs AS run
  SET stage = 'retry_approval_required',
      failure_envelope = p_error,
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;
  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

UPDATE public.semantic_video_takes AS take
SET retry_guidance = pg_catalog.jsonb_build_object(
  'guidance', CASE
    WHEN take.submission_error ->> 'code' = 'provider_operation_failed' THEN
      'Preserve the original delivery exactly; the provider operation failed internally before producing a usable take.'
    ELSE
      'Preserve the exact approved semantic beat and retry after the provider failed before producing a usable take.'
  END,
  'source', CASE
    WHEN take.submission_error ->> 'code' = 'provider_operation_failed' THEN 'provider_internal_failure'
    ELSE 'provider_failure'
  END
)
WHERE take.submission_state = 'failed'
  AND take.retry_guidance IS NULL
  AND pg_catalog.jsonb_typeof(take.submission_error) = 'object';

REVOKE ALL ON FUNCTION public.persist_semantic_video_provider_failure(UUID, UUID, TEXT, UUID, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.persist_semantic_video_provider_failure(UUID, UUID, TEXT, UUID, JSONB)
  TO service_role;

COMMIT;
