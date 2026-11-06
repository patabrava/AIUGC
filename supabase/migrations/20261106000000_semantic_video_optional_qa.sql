-- Optional evaluators run only after durable delivery, using the existing run lease.
CREATE OR REPLACE FUNCTION public.queue_semantic_video_optional_qa()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  IF NEW.stage = 'completed'
     AND NEW.artifact_manifest #> '{pipeline_manifest,optional_qa_deferred}' = 'true'::jsonb
     AND (OLD.stage IS DISTINCT FROM 'completed'
          OR OLD.final_caption_sha256 IS DISTINCT FROM NEW.final_caption_sha256) THEN
    NEW.artifact_manifest := NEW.artifact_manifest || pg_catalog.jsonb_build_object(
      'optional_qa', pg_catalog.jsonb_build_object(
        'status', 'pending', 'attempts', 0, 'delivery_sha256', NEW.final_caption_sha256,
        'findings', '[]'::jsonb));
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS semantic_video_queue_optional_qa ON public.semantic_video_runs;
CREATE TRIGGER semantic_video_queue_optional_qa BEFORE UPDATE ON public.semantic_video_runs
FOR EACH ROW EXECUTE FUNCTION public.queue_semantic_video_optional_qa();
REVOKE ALL ON FUNCTION public.queue_semantic_video_optional_qa() FROM PUBLIC, anon, authenticated, service_role;

CREATE INDEX IF NOT EXISTS semantic_video_optional_qa_pending
ON public.semantic_video_runs(updated_at, id)
WHERE stage = 'completed' AND artifact_manifest #>> '{optional_qa,status}' IN ('pending', 'running');

CREATE OR REPLACE FUNCTION public.claim_semantic_video_optional_qa(p_worker_id TEXT, p_lease_seconds INTEGER)
RETURNS SETOF public.semantic_video_runs
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE selected public.semantic_video_runs%ROWTYPE;
BEGIN
  IF NULLIF(pg_catalog.btrim(p_worker_id), '') IS NULL
     OR p_lease_seconds IS NULL OR p_lease_seconds < 1 OR p_lease_seconds > 3600 THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid optional QA lease';
  END IF;
  -- One global optional evaluator slot; production slots never wait on it.
  PERFORM pg_catalog.pg_advisory_xact_lock(611060001);
  UPDATE public.semantic_video_runs AS run
  SET artifact_manifest = pg_catalog.jsonb_set(run.artifact_manifest, '{optional_qa,status}', '"failed"'),
      lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
  WHERE run.stage = 'completed'
    AND run.artifact_manifest #>> '{optional_qa,status}' = 'running'
    AND run.lease_expires_at <= now()
    AND (run.artifact_manifest #>> '{optional_qa,attempts}')::integer >= 3;
  IF EXISTS (SELECT 1 FROM public.semantic_video_runs AS run
      WHERE run.stage = 'completed'
        AND run.artifact_manifest #>> '{optional_qa,status}' IN ('pending', 'running')
        AND run.lease_expires_at > now()) THEN RETURN; END IF;
  SELECT run.* INTO selected FROM public.semantic_video_runs AS run
  WHERE run.stage = 'completed'
    AND run.artifact_manifest #>> '{optional_qa,status}' IN ('pending', 'running')
    AND (run.lease_expires_at IS NULL OR run.lease_expires_at <= now())
    AND run.final_caption_sha256 = run.artifact_manifest #>> '{optional_qa,delivery_sha256}'
  ORDER BY run.updated_at, run.id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN; END IF;
  RETURN QUERY UPDATE public.semantic_video_runs AS run
  SET lease_owner = p_worker_id, lease_token = gen_random_uuid(),
      lease_expires_at = now() + pg_catalog.make_interval(secs => p_lease_seconds),
      artifact_manifest = pg_catalog.jsonb_set(run.artifact_manifest, '{optional_qa}',
        run.artifact_manifest->'optional_qa' || pg_catalog.jsonb_build_object(
          'status', 'running', 'attempts', (run.artifact_manifest #>> '{optional_qa,attempts}')::integer + 1))
  WHERE run.id = selected.id RETURNING run.*;
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_semantic_video_optional_qa(
  p_run_id UUID, p_worker_id TEXT, p_lease_token UUID, p_delivery_sha256 TEXT, p_report JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE locked public.semantic_video_runs%ROWTYPE;
DECLARE result public.semantic_video_runs%ROWTYPE;
BEGIN
  SELECT * INTO locked FROM public.require_semantic_video_worker_lease(p_run_id,p_worker_id,p_lease_token,'completed');
  IF locked.final_caption_sha256 IS DISTINCT FROM p_delivery_sha256
     OR locked.artifact_manifest #>> '{optional_qa,delivery_sha256}' IS DISTINCT FROM p_delivery_sha256
     OR locked.artifact_manifest #>> '{optional_qa,status}' IS DISTINCT FROM 'running' THEN
    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'optional QA result is stale';
  END IF;
  IF pg_catalog.jsonb_typeof(p_report) IS DISTINCT FROM 'object'
     OR COALESCE(p_report->>'status','') NOT IN ('completed','failed')
     OR pg_catalog.jsonb_typeof(p_report->'findings') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid optional QA report';
  END IF;
  UPDATE public.semantic_video_runs AS run
  SET artifact_manifest = pg_catalog.jsonb_set(run.artifact_manifest, '{optional_qa}',
        (run.artifact_manifest->'optional_qa') || pg_catalog.jsonb_build_object(
          'status',p_report->>'status', 'findings',p_report->'findings',
          'checks',COALESCE(p_report->'checks','{}'), 'finished_at',now())),
      lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL
  WHERE run.id = p_run_id RETURNING run.* INTO result;
  -- No main-stage/revision/paid-take/post/final-artifact mutation.
  RETURN pg_catalog.to_jsonb(result);
END;
$$;
REVOKE ALL ON FUNCTION public.claim_semantic_video_optional_qa(TEXT,INTEGER) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_semantic_video_optional_qa(UUID,TEXT,UUID,TEXT,JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_semantic_video_optional_qa(TEXT,INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_semantic_video_optional_qa(UUID,TEXT,UUID,TEXT,JSONB) TO service_role;
NOTIFY pgrst, 'reload schema';
