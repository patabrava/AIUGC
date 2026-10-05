-- Admit independent script images from the same batch. Keep two global claims,
-- per-post idempotency, paid-attempt budgets and existing worker lease fences.
-- Queue waiting has a one-hour bound; the eight-minute execution budget starts
-- on first claim and is never reset by a crash/reclaim.
BEGIN;
SET LOCAL lock_timeout = '5s';
DROP INDEX IF EXISTS public.semantic_scene_image_jobs_one_active_batch;

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
  target_post public.posts%ROWTYPE;
  active_job public.semantic_scene_image_jobs%ROWTYPE;
  active_run public.semantic_video_runs%ROWTYPE;
  inserted_job public.semantic_scene_image_jobs%ROWTYPE;
  terminalized_job public.semantic_scene_image_jobs%ROWTYPE;
  terminalized_run public.semantic_video_runs%ROWTYPE;
  terminalized_run_previous_revision INTEGER;
  terminalized_run_revision INTEGER;
  persisted_script TEXT;
  persisted_review_status TEXT;
  persisted_expected_run_id UUID;
  persisted_expected_revision INTEGER;
BEGIN
  IF p_post_id IS NULL
     OR NULLIF(pg_catalog.btrim(p_requested_by), '') IS NULL
     OR NULLIF(pg_catalog.btrim(p_correlation_id), '') IS NULL
     OR (p_expected_revision IS NOT NULL AND p_expected_revision < 0) THEN
    RAISE EXCEPTION USING
      ERRCODE = '22023',
      MESSAGE = 'semantic scene image enqueue contract is invalid';
  END IF;

  SELECT post.* INTO target_post
  FROM public.posts AS post
  WHERE post.id = p_post_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = 'P0002',
      MESSAGE = 'semantic scene image post does not exist';
  END IF;

  persisted_review_status := pg_catalog.lower(pg_catalog.btrim(
    COALESCE(target_post.seed_data ->> 'script_review_status', '')
  ));
  persisted_script := pg_catalog.btrim(COALESCE(
    target_post.seed_data ->> 'script',
    target_post.seed_data ->> 'dialog_script',
    target_post.topic_rotation,
    ''
  ));
  IF persisted_review_status IS DISTINCT FROM 'approved'
     OR NULLIF(persisted_script, '') IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '22023',
      MESSAGE = 'semantic scene image requires one approved non-empty script';
  END IF;

  -- Keep the short batch transaction lock for compatibility with in-flight old callers.
  -- Admission is idempotent per post; the claim RPC owns global render capacity.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'semantic-scene-image-batch:' || target_post.batch_id::TEXT,
      0
    )
  );

  UPDATE public.semantic_scene_image_jobs AS job
  SET status = 'failed',
      error = pg_catalog.jsonb_build_object(
        'code', CASE
          WHEN job.status = 'processing'
               AND job.lease_expires_at <= pg_catalog.clock_timestamp()
               AND job.deadline_at <= pg_catalog.clock_timestamp() + INTERVAL '4 minutes'
            THEN 'insufficient_execution_budget'
          WHEN job.deadline_at <= pg_catalog.clock_timestamp()
            THEN 'operation_deadline_exceeded'
          ELSE 'lease_attempts_exhausted'
        END,
        'message', 'Image generation stopped safely. Retry this script.'
      ),
      worker_id = NULL,
      lease_token = NULL,
      lease_expires_at = NULL,
      finished_at = pg_catalog.clock_timestamp(),
      updated_at = pg_catalog.clock_timestamp()
  WHERE job.post_id = p_post_id
    AND job.status IN ('queued', 'processing')
    AND (
      job.deadline_at <= pg_catalog.clock_timestamp()
      OR (
        job.status = 'processing'
        AND job.lease_expires_at <= pg_catalog.clock_timestamp()
        AND job.deadline_at <= pg_catalog.clock_timestamp() + INTERVAL '4 minutes'
      )
      OR (
        job.status = 'processing'
        AND job.lease_expires_at <= pg_catalog.clock_timestamp()
        AND job.attempt_count >= job.max_attempts
      )
    )
  RETURNING job.* INTO terminalized_job;

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

  -- The global claim sweep may have terminalized this job immediately before
  -- enqueue acquired the batch lock. With no active job for this post, recover that
  -- exact failed reservation here as well as one terminalized above. Scoping
  -- this fallback to the requested post prevents an old failure from clearing
  -- a different script's reservation.
  IF terminalized_job.id IS NULL THEN
    SELECT job.* INTO terminalized_job
    FROM public.semantic_scene_image_jobs AS job
    WHERE job.post_id = p_post_id
      AND job.batch_id = target_post.batch_id
      AND job.status = 'failed'
      AND job.run_id IS NOT NULL
      AND job.worker_id IS NULL
      AND job.lease_token IS NULL
      AND job.error ->> 'code' IN (
        'insufficient_execution_budget',
        'operation_deadline_exceeded',
        'lease_attempts_exhausted'
      )
      AND EXISTS (
        SELECT 1
        FROM public.semantic_video_runs AS run
        WHERE run.id = job.run_id
          AND run.post_id = job.post_id
          AND run.stage = 'awaiting_reference_approval'
          AND run.candidate_reservation_token IS NOT NULL
          AND pg_catalog.jsonb_array_length(
            CASE
              WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
                THEN run.master_snapshot -> 'candidates'
              ELSE '[]'::JSONB
            END
          ) = 0
      )
    ORDER BY job.finished_at DESC NULLS LAST, job.updated_at DESC, job.id DESC
    LIMIT 1
    FOR UPDATE;
  END IF;

  -- A terminalized lease cannot retain ownership of an empty run. Release its
  -- candidate token and revision-fence the displaced worker in this same batch
  -- admission transaction. A fresh retry's first claim cannot run attempt-two
  -- cleanup for the prior job's reservation.
  IF terminalized_job.run_id IS NOT NULL THEN
    SELECT run.* INTO terminalized_run
    FROM public.semantic_video_runs AS run
    WHERE run.id = terminalized_job.run_id
      AND run.post_id = terminalized_job.post_id
      AND run.stage = 'awaiting_reference_approval'
      AND pg_catalog.jsonb_array_length(
        CASE
          WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
            THEN run.master_snapshot -> 'candidates'
          ELSE '[]'::JSONB
        END
      ) = 0
    FOR UPDATE;

    IF FOUND AND terminalized_run.candidate_reservation_token IS NOT NULL THEN
      terminalized_run_previous_revision := terminalized_run.revision;
      UPDATE public.semantic_video_runs AS run
      SET candidate_reservation_owner = NULL,
          candidate_reservation_token = NULL,
          candidate_reservation_expires_at = NULL,
          revision = run.revision + 1,
          updated_at = pg_catalog.clock_timestamp()
      WHERE run.id = terminalized_run.id
        AND run.revision = terminalized_run.revision
      RETURNING run.revision INTO terminalized_run_revision;
    END IF;
  END IF;

  SELECT run.* INTO active_run
  FROM public.semantic_video_runs AS run
  WHERE run.post_id = p_post_id
    AND run.stage NOT IN ('completed', 'failed')
  ORDER BY run.created_at DESC, run.id DESC
  LIMIT 1;
  IF FOUND THEN
    IF p_expected_revision IS NULL
       OR (
         active_run.revision IS DISTINCT FROM p_expected_revision
         AND NOT (
           terminalized_job.post_id IS NOT DISTINCT FROM p_post_id
           AND terminalized_job.run_id IS NOT DISTINCT FROM active_run.id
           AND terminalized_run_previous_revision IS NOT DISTINCT FROM p_expected_revision
           AND terminalized_run_revision IS NOT DISTINCT FROM active_run.revision
         )
       ) THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: scene image revision is stale';
    END IF;
    persisted_expected_run_id := active_run.id;
    persisted_expected_revision := active_run.revision;
  ELSE
    -- A fresh progress projection historically reported revision zero. Accept
    -- that sentinel as no run while new clients send NULL explicitly.
    IF p_expected_revision IS NOT NULL AND p_expected_revision <> 0 THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: scene image run does not exist at the expected revision';
    END IF;
    persisted_expected_run_id := NULL;
    persisted_expected_revision := NULL;
  END IF;

  INSERT INTO public.semantic_scene_image_jobs (
    post_id,
    batch_id,
    expected_run_id,
    expected_revision,
    requested_by,
    correlation_id,
    deadline_at
  ) VALUES (
    p_post_id,
    target_post.batch_id,
    persisted_expected_run_id,
    persisted_expected_revision,
    p_requested_by,
    p_correlation_id,
    pg_catalog.clock_timestamp() + INTERVAL '1 hour'
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
  active_claim_count INTEGER;
  claimed_job public.semantic_scene_image_jobs%ROWTYPE;
  reclaimed_run_revision INTEGER;
BEGIN
  IF NULLIF(pg_catalog.btrim(p_worker_id), '') IS NULL
     OR p_lease_seconds IS NULL
     OR p_lease_seconds < 30
     OR p_lease_seconds > 900 THEN
    RAISE EXCEPTION 'semantic scene image worker lease is invalid';
  END IF;

  -- Migration-first cutover fence: the deployed v1 worker used this stable
  -- prefix and called the now-retired direct reservation RPC. Returning no
  -- work quiesces it without crashing or terminally failing queued jobs while
  -- v2 containers are rolling out.
  IF p_worker_id LIKE 'semantic-scene-image-v1-%' THEN
    RETURN;
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('semantic-scene-image-global-claims', 0)
  );

  UPDATE public.semantic_scene_image_jobs AS job
  SET status = 'failed',
      error = pg_catalog.jsonb_build_object(
        'code', CASE
          WHEN job.deadline_at <= pg_catalog.clock_timestamp() + INTERVAL '4 minutes'
               AND (
                 job.status = 'queued'
                 OR job.lease_expires_at <= pg_catalog.clock_timestamp()
               )
            THEN 'insufficient_execution_budget'
          WHEN job.deadline_at <= pg_catalog.clock_timestamp()
            THEN 'operation_deadline_exceeded'
          ELSE 'lease_attempts_exhausted'
        END,
        'message', 'Image generation stopped safely. Retry this script.'
      ),
      worker_id = NULL,
      lease_token = NULL,
      lease_expires_at = NULL,
      finished_at = pg_catalog.clock_timestamp(),
      updated_at = pg_catalog.clock_timestamp()
  WHERE job.status IN ('queued', 'processing')
    AND (
      (
        job.status = 'queued'
        AND job.started_at IS NOT NULL
        AND job.deadline_at <= pg_catalog.clock_timestamp() + INTERVAL '4 minutes'
      )
      OR job.deadline_at <= pg_catalog.clock_timestamp()
      OR (
        job.status = 'processing'
        AND job.lease_expires_at <= pg_catalog.clock_timestamp()
        AND job.deadline_at <= pg_catalog.clock_timestamp() + INTERVAL '4 minutes'
      )
      OR (
        job.status = 'processing'
        AND job.lease_expires_at <= pg_catalog.clock_timestamp()
        AND job.attempt_count >= job.max_attempts
      )
    );

  SELECT pg_catalog.count(*) INTO active_claim_count
  FROM public.semantic_scene_image_jobs AS job
  WHERE job.status = 'processing'
    AND job.lease_expires_at > pg_catalog.clock_timestamp()
    AND job.deadline_at > pg_catalog.clock_timestamp();
  IF active_claim_count >= 2 THEN
    RETURN;
  END IF;

  WITH claimable AS (
    SELECT job.id
    FROM public.semantic_scene_image_jobs AS job
    WHERE (
        job.status = 'queued'
        OR (
          job.status = 'processing'
          AND job.lease_expires_at <= pg_catalog.clock_timestamp()
        )
      )
      AND job.attempt_count < job.max_attempts
      AND job.deadline_at > pg_catalog.clock_timestamp()
      AND (job.started_at IS NULL
           OR job.deadline_at > pg_catalog.clock_timestamp() + INTERVAL '4 minutes')
    ORDER BY job.created_at, job.id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
  )
  UPDATE public.semantic_scene_image_jobs AS job
  SET status = 'processing',
      attempt_count = job.attempt_count + 1,
      worker_id = p_worker_id,
      lease_token = gen_random_uuid(),
      deadline_at = CASE WHEN job.started_at IS NULL
        THEN pg_catalog.clock_timestamp() + INTERVAL '8 minutes'
        ELSE job.deadline_at END,
      lease_expires_at = LEAST(
        CASE WHEN job.started_at IS NULL
          THEN pg_catalog.clock_timestamp() + INTERVAL '8 minutes'
          ELSE job.deadline_at END,
        pg_catalog.clock_timestamp() + pg_catalog.make_interval(secs => p_lease_seconds)
      ),
      heartbeat_at = pg_catalog.clock_timestamp(),
      started_at = COALESCE(job.started_at, pg_catalog.clock_timestamp()),
      updated_at = pg_catalog.clock_timestamp()
  FROM claimable
  WHERE job.id = claimable.id
  RETURNING job.* INTO claimed_job;

  IF FOUND THEN
    -- A reclaimed job is the sole current owner of its linked run. Clear the
    -- prior worker's candidate token in this same claim transaction so a
    -- process crash can recover after one lease, not after the legacy
    -- 30-minute reservation. The displaced worker remains fenced by both its
    -- job token and its candidate token.
    IF claimed_job.attempt_count > 1 AND claimed_job.run_id IS NOT NULL THEN
      UPDATE public.semantic_video_runs AS run
      SET candidate_reservation_owner = NULL,
          candidate_reservation_token = NULL,
          candidate_reservation_expires_at = NULL,
          revision = run.revision + 1,
          updated_at = pg_catalog.clock_timestamp()
      WHERE run.id = claimed_job.run_id
        AND run.post_id = claimed_job.post_id
        AND run.stage = 'awaiting_reference_approval'
        AND run.candidate_reservation_token IS NOT NULL
        AND pg_catalog.jsonb_array_length(
          CASE
            WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
              THEN run.master_snapshot -> 'candidates'
            ELSE '[]'::JSONB
          END
        ) = 0;

      -- The displaced handler may have released its candidate token in the
      -- narrow interval after lease expiry and before this claim. Always read
      -- the linked run's current revision, even when no token remained for the
      -- claim itself to clear.
      SELECT run.revision INTO reclaimed_run_revision
      FROM public.semantic_video_runs AS run
      WHERE run.id = claimed_job.run_id
        AND run.post_id = claimed_job.post_id
        AND run.stage = 'awaiting_reference_approval'
        AND pg_catalog.jsonb_array_length(
          CASE
            WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
              THEN run.master_snapshot -> 'candidates'
            ELSE '[]'::JSONB
          END
        ) = 0
      FOR UPDATE;

      IF FOUND THEN
        UPDATE public.semantic_scene_image_jobs AS job
        SET expected_revision = reclaimed_run_revision,
            updated_at = pg_catalog.clock_timestamp()
        WHERE job.id = claimed_job.id
        RETURNING job.* INTO claimed_job;
      END IF;
    END IF;
    RETURN NEXT claimed_job;
  END IF;
END;
$$;

REVOKE ALL ON FUNCTION public.enqueue_semantic_scene_image(UUID, INTEGER, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.enqueue_semantic_scene_image(UUID, INTEGER, TEXT, TEXT)
  TO service_role;
-- Only the existing v3 wrapper may expose claim to workers.
REVOKE ALL ON FUNCTION public.claim_semantic_scene_image(TEXT, INTEGER)
  FROM PUBLIC, anon, authenticated, service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
