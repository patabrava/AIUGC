-- One durable, asynchronous reference image per Semantic UGC script.

CREATE TABLE IF NOT EXISTS public.semantic_scene_image_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id UUID NOT NULL REFERENCES public.posts(id) ON DELETE CASCADE,
  expected_revision INTEGER,
  requested_by TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  worker_id TEXT,
  lease_token UUID,
  lease_expires_at TIMESTAMPTZ,
  run_id UUID REFERENCES public.semantic_video_runs(id) ON DELETE SET NULL,
  error JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE UNIQUE INDEX IF NOT EXISTS semantic_scene_image_jobs_one_active_post
  ON public.semantic_scene_image_jobs(post_id)
  WHERE status IN ('queued', 'processing');

CREATE INDEX IF NOT EXISTS semantic_scene_image_jobs_claim_order
  ON public.semantic_scene_image_jobs(status, created_at);

ALTER TABLE public.semantic_scene_image_jobs ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.enqueue_semantic_scene_image(
  p_post_id UUID,
  p_expected_revision INTEGER,
  p_requested_by TEXT,
  p_correlation_id TEXT
)
RETURNS SETOF public.semantic_scene_image_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  active_job public.semantic_scene_image_jobs%ROWTYPE;
  active_run public.semantic_video_runs%ROWTYPE;
  inserted_job public.semantic_scene_image_jobs%ROWTYPE;
BEGIN
  IF p_post_id IS NULL
     OR NULLIF(pg_catalog.btrim(p_requested_by), '') IS NULL
     OR NULLIF(pg_catalog.btrim(p_correlation_id), '') IS NULL
     OR (p_expected_revision IS NOT NULL AND p_expected_revision < 0) THEN
    RAISE EXCEPTION 'semantic scene image enqueue contract is invalid';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('semantic-scene-image:' || p_post_id::TEXT, 0)
  );

  SELECT job.* INTO active_job
  FROM public.semantic_scene_image_jobs AS job
  WHERE job.post_id = p_post_id
    AND job.status IN ('queued', 'processing')
  ORDER BY job.created_at DESC
  LIMIT 1;
  IF FOUND THEN
    RETURN NEXT active_job;
    RETURN;
  END IF;

  SELECT run.* INTO active_run
  FROM public.semantic_video_runs AS run
  WHERE run.post_id = p_post_id
    AND run.stage NOT IN ('completed', 'failed')
  ORDER BY run.created_at DESC, run.id DESC
  LIMIT 1;
  IF FOUND AND (
    p_expected_revision IS NULL OR active_run.revision IS DISTINCT FROM p_expected_revision
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image revision is stale';
  ELSIF NOT FOUND AND p_expected_revision IS NOT NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image run does not exist at the expected revision';
  END IF;

  INSERT INTO public.semantic_scene_image_jobs (
    post_id, expected_revision, requested_by, correlation_id
  ) VALUES (
    p_post_id, p_expected_revision, p_requested_by, p_correlation_id
  ) RETURNING * INTO inserted_job;
  RETURN NEXT inserted_job;
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_semantic_scene_image(
  p_worker_id TEXT,
  p_lease_seconds INTEGER
)
RETURNS SETOF public.semantic_scene_image_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  claimed_job public.semantic_scene_image_jobs%ROWTYPE;
BEGIN
  IF NULLIF(pg_catalog.btrim(p_worker_id), '') IS NULL
     OR p_lease_seconds IS NULL
     OR p_lease_seconds < 30
     OR p_lease_seconds > 900 THEN
    RAISE EXCEPTION 'semantic scene image worker lease is invalid';
  END IF;

  WITH claimable AS (
    SELECT job.id
    FROM public.semantic_scene_image_jobs AS job
    WHERE job.status = 'queued'
       OR (job.status = 'processing' AND job.lease_expires_at <= pg_catalog.clock_timestamp())
    ORDER BY job.created_at, job.id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
  )
  UPDATE public.semantic_scene_image_jobs AS job
  SET status = 'processing',
      attempt_count = job.attempt_count + 1,
      worker_id = p_worker_id,
      lease_token = gen_random_uuid(),
      lease_expires_at = pg_catalog.clock_timestamp()
        + pg_catalog.make_interval(secs => p_lease_seconds),
      started_at = COALESCE(job.started_at, pg_catalog.clock_timestamp()),
      updated_at = pg_catalog.clock_timestamp(),
      error = NULL
  FROM claimable
  WHERE job.id = claimable.id
  RETURNING job.* INTO claimed_job;

  IF FOUND THEN
    RETURN NEXT claimed_job;
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.finish_semantic_scene_image(
  p_job_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_status TEXT,
  p_run_id UUID,
  p_error JSONB
)
RETURNS SETOF public.semantic_scene_image_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  finished_job public.semantic_scene_image_jobs%ROWTYPE;
BEGIN
  IF p_job_id IS NULL
     OR NULLIF(pg_catalog.btrim(p_worker_id), '') IS NULL
     OR p_lease_token IS NULL
     OR p_status NOT IN ('completed', 'failed')
     OR (p_status = 'completed' AND p_run_id IS NULL)
     OR (p_status = 'failed' AND pg_catalog.jsonb_typeof(p_error) IS DISTINCT FROM 'object') THEN
    RAISE EXCEPTION 'semantic scene image completion contract is invalid';
  END IF;

  UPDATE public.semantic_scene_image_jobs AS job
  SET status = p_status,
      run_id = p_run_id,
      error = p_error,
      lease_token = NULL,
      lease_expires_at = NULL,
      finished_at = pg_catalog.clock_timestamp(),
      updated_at = pg_catalog.clock_timestamp()
  WHERE job.id = p_job_id
    AND job.status = 'processing'
    AND job.worker_id = p_worker_id
    AND job.lease_token = p_lease_token
    AND job.lease_expires_at > pg_catalog.clock_timestamp()
  RETURNING job.* INTO finished_job;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image job lease is stale';
  END IF;
  RETURN NEXT finished_job;
END;
$$;

-- Preserve the audited legacy transaction while allowing the new one-image contract.
ALTER FUNCTION public.finalize_semantic_video_candidates(UUID, INTEGER, UUID, JSONB)
  RENAME TO finalize_semantic_video_candidates_legacy_set;

CREATE OR REPLACE FUNCTION public.finalize_semantic_video_candidates(
  p_run_id UUID,
  p_reserved_revision INTEGER,
  p_reservation_token UUID,
  p_run_update JSONB
)
RETURNS SETOF public.semantic_video_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  candidate_count INTEGER;
  candidate JSONB;
  compatibility_update JSONB;
  ignored_run public.semantic_video_runs%ROWTYPE;
  finalized_run public.semantic_video_runs%ROWTYPE;
BEGIN
  IF pg_catalog.jsonb_typeof(p_run_update #> '{master_snapshot,candidates}')
       IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'semantic video candidate run update is invalid';
  END IF;
  candidate_count := pg_catalog.jsonb_array_length(
    p_run_update #> '{master_snapshot,candidates}'
  );
  IF candidate_count = 3 THEN
    RETURN QUERY SELECT * FROM public.finalize_semantic_video_candidates_legacy_set(
      p_run_id, p_reserved_revision, p_reservation_token, p_run_update
    );
    RETURN;
  END IF;
  IF candidate_count IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'semantic video candidate run update requires one current image';
  END IF;

  candidate := p_run_update #> '{master_snapshot,candidates,0}';
  IF pg_catalog.jsonb_typeof(candidate) IS DISTINCT FROM 'object'
     OR (candidate ->> 'index')::INTEGER IS DISTINCT FROM 1
     OR NULLIF(pg_catalog.btrim(candidate ->> 'storage_uri'), '') IS NULL
     OR NULLIF(pg_catalog.btrim(candidate ->> 'sha256'), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video single-image candidate is invalid';
  END IF;

  compatibility_update := pg_catalog.jsonb_set(
    p_run_update,
    '{master_snapshot,candidates}',
    pg_catalog.jsonb_build_array(candidate, candidate, candidate)
  );
  SELECT * INTO ignored_run
  FROM public.finalize_semantic_video_candidates_legacy_set(
    p_run_id, p_reserved_revision, p_reservation_token, compatibility_update
  );

  UPDATE public.semantic_video_runs AS run
  SET master_snapshot = p_run_update -> 'master_snapshot'
  WHERE run.id = p_run_id
    AND run.revision = p_reserved_revision
    AND run.candidate_reservation_token = p_reservation_token
  RETURNING run.* INTO finalized_run;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: single scene image finalization lost its reservation';
  END IF;
  RETURN NEXT finalized_run;
END;
$$;

REVOKE ALL ON TABLE public.semantic_scene_image_jobs FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON TABLE public.semantic_scene_image_jobs TO service_role;
REVOKE ALL ON FUNCTION public.enqueue_semantic_scene_image(UUID, INTEGER, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.enqueue_semantic_scene_image(UUID, INTEGER, TEXT, TEXT) TO service_role;
REVOKE ALL ON FUNCTION public.claim_semantic_scene_image(TEXT, INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_semantic_scene_image(TEXT, INTEGER) TO service_role;
REVOKE ALL ON FUNCTION public.finish_semantic_scene_image(UUID, TEXT, UUID, TEXT, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finish_semantic_scene_image(UUID, TEXT, UUID, TEXT, UUID, JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.finalize_semantic_video_candidates(UUID, INTEGER, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finalize_semantic_video_candidates(UUID, INTEGER, UUID, JSONB) TO service_role;
