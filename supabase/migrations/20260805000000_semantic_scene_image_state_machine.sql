-- Make one-image scene preparation a lease-fenced, batch-serialized operation.

BEGIN;

-- Install the stable-name cutover fences before taking the data snapshot. An
-- old invocation already inside either function drains before the rename lock
-- is acquired; later invocations resolve to these fail-closed wrappers.
DO $$
BEGIN
  IF pg_catalog.to_regprocedure(
       'public.reserve_semantic_video_candidates_legacy_queue_impl(uuid,integer,jsonb,text,uuid,integer)'
     ) IS NULL THEN
    ALTER FUNCTION public.reserve_semantic_video_candidates(
      UUID, INTEGER, JSONB, TEXT, UUID, INTEGER
    ) RENAME TO reserve_semantic_video_candidates_legacy_queue_impl;
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.reserve_semantic_video_candidates(
  p_post_id UUID,
  p_expected_revision INTEGER,
  p_run_create JSONB,
  p_reservation_owner TEXT,
  p_reservation_token UUID,
  p_reservation_seconds INTEGER
)
RETURNS SETOF public.semantic_video_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  RAISE EXCEPTION USING
    ERRCODE = '40001',
    MESSAGE = 'semantic_video_conflict: direct candidate generation is retired; enqueue one scene image';
END;
$$;

REVOKE ALL ON FUNCTION public.reserve_semantic_video_candidates_legacy_queue_impl(
  UUID, INTEGER, JSONB, TEXT, UUID, INTEGER
) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.reserve_semantic_video_candidates(
  UUID, INTEGER, JSONB, TEXT, UUID, INTEGER
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reserve_semantic_video_candidates(
  UUID, INTEGER, JSONB, TEXT, UUID, INTEGER
) TO service_role;

DO $$
BEGIN
  IF pg_catalog.to_regprocedure(
       'public.finalize_semantic_video_candidates_scene_contract_impl(uuid,integer,uuid,jsonb)'
     ) IS NULL THEN
    ALTER FUNCTION public.finalize_semantic_video_candidates(
      UUID, INTEGER, UUID, JSONB
    ) RENAME TO finalize_semantic_video_candidates_scene_contract_impl;
  END IF;
END;
$$;

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
BEGIN
  RAISE EXCEPTION USING
    ERRCODE = '40001',
    MESSAGE = 'semantic_video_conflict: direct candidate finalization is retired; use the atomic scene-image RPC';
END;
$$;

REVOKE ALL ON FUNCTION public.finalize_semantic_video_candidates_scene_contract_impl(
  UUID, INTEGER, UUID, JSONB
) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.finalize_semantic_video_candidates(
  UUID, INTEGER, UUID, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finalize_semantic_video_candidates(
  UUID, INTEGER, UUID, JSONB
) TO service_role;

-- A stable-name call can resolve the old function OID while the rename is
-- uncommitted and resume that old body later. This trigger is the durable DML
-- fence such a stale OID cannot bypass. Only the token-fenced v2 RPCs set the
-- transaction-local admission marker before invoking the audited internals.
CREATE OR REPLACE FUNCTION public.enforce_semantic_scene_image_queue_writes()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  requires_gate BOOLEAN := FALSE;
BEGIN
  IF TG_OP = 'INSERT' THEN
    requires_gate := NEW.candidate_reservation_token IS NOT NULL;
  ELSIF TG_OP = 'UPDATE' THEN
    requires_gate := (
      OLD.candidate_reservation_token IS NULL
      AND NEW.candidate_reservation_token IS NOT NULL
    ) OR (
      OLD.candidate_reservation_token IS NOT NULL
      AND NEW.master_snapshot IS DISTINCT FROM OLD.master_snapshot
    );
  END IF;
  IF requires_gate AND COALESCE(
    pg_catalog.current_setting('app.semantic_scene_image_queue_write', TRUE),
    ''
  ) IS DISTINCT FROM 'v2' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: stale direct candidate mutation is fenced';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS semantic_scene_image_queue_write_fence
  ON public.semantic_video_runs;
CREATE TRIGGER semantic_scene_image_queue_write_fence
BEFORE INSERT OR UPDATE ON public.semantic_video_runs
FOR EACH ROW
EXECUTE FUNCTION public.enforce_semantic_scene_image_queue_writes();

REVOKE ALL ON FUNCTION public.enforce_semantic_scene_image_queue_writes()
  FROM PUBLIC, anon, authenticated, service_role;

COMMIT;
BEGIN;

SELECT pg_catalog.set_config(
  'app.semantic_scene_image_queue_write',
  'v2',
  TRUE
);

ALTER TABLE public.semantic_scene_image_jobs
  ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES public.batches(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS expected_run_id UUID REFERENCES public.semantic_video_runs(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3,
  ADD COLUMN IF NOT EXISTS provider_attempt_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;

-- Establish a quiescent cutover snapshot. These locks wait for any old
-- reserve/finalize/finish transaction, then prevent another v1 mutation from
-- starting until every lease and token below has been reconciled or fenced.
LOCK TABLE public.semantic_scene_image_jobs IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.semantic_video_runs IN ACCESS EXCLUSIVE MODE;

UPDATE public.semantic_scene_image_jobs AS job
SET batch_id = post.batch_id
FROM public.posts AS post
WHERE post.id = job.post_id
  AND job.batch_id IS NULL;

UPDATE public.semantic_scene_image_jobs
SET deadline_at = created_at + INTERVAL '8 minutes'
WHERE deadline_at IS NULL;

-- Discover any v1 run reserved before the migration. The cutover below either
-- preserves already-durable one-image success or retires the reservation. No
-- v1 mutation remains live after this transaction commits.
WITH candidate_links AS (
  SELECT job.id AS job_id,
         run.id AS run_id,
         run.revision,
         pg_catalog.row_number() OVER (
           PARTITION BY job.id
           ORDER BY run.created_at DESC, run.id DESC
         ) AS ordinal
  FROM public.semantic_scene_image_jobs AS job
  JOIN public.semantic_video_runs AS run ON run.post_id = job.post_id
  WHERE job.status IN ('processing', 'completed', 'failed')
    AND job.run_id IS NULL
    AND run.stage = 'awaiting_reference_approval'
    AND run.candidate_reservation_token IS NOT NULL
)
UPDATE public.semantic_scene_image_jobs AS job
SET run_id = candidate_links.run_id,
    expected_run_id = candidate_links.run_id,
    expected_revision = candidate_links.revision,
    updated_at = pg_catalog.clock_timestamp()
FROM candidate_links
WHERE job.id = candidate_links.job_id
  AND candidate_links.ordinal = 1;

-- A v1 worker may have durably finalized its one image before acknowledging
-- the queue. Reconcile that evidence before any legacy-state cleanup can mark
-- the operation failed.
UPDATE public.semantic_video_runs AS run
SET candidate_generation_progress = pg_catalog.jsonb_build_object(
      'phase', 'ready',
      'details', pg_catalog.jsonb_build_object('candidate_count', 1),
      'updated_at', pg_catalog.clock_timestamp()
    ),
    candidate_reservation_owner = NULL,
    candidate_reservation_token = NULL,
    candidate_reservation_expires_at = NULL,
    updated_at = pg_catalog.clock_timestamp()
FROM public.semantic_scene_image_jobs AS job
WHERE job.status IN ('processing', 'completed', 'failed')
  AND job.run_id = run.id
  AND run.stage = 'awaiting_reference_approval'
  AND pg_catalog.jsonb_array_length(
    CASE
      WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
        THEN run.master_snapshot -> 'candidates'
      ELSE '[]'::JSONB
    END
  ) = 1;

UPDATE public.semantic_scene_image_jobs AS job
SET status = 'completed',
    worker_id = NULL,
    lease_token = NULL,
    lease_expires_at = NULL,
    error = NULL,
    finished_at = pg_catalog.clock_timestamp(),
    updated_at = pg_catalog.clock_timestamp()
FROM public.semantic_video_runs AS run
WHERE job.status IN ('processing', 'completed', 'failed')
  AND job.run_id = run.id
  AND run.stage = 'awaiting_reference_approval'
  AND pg_catalog.jsonb_array_length(
    CASE
      WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
        THEN run.master_snapshot -> 'candidates'
      ELSE '[]'::JSONB
    END
  ) = 1;

-- Quiesce every remaining v1 queue operation at the database boundary. A
-- rolling old container may still return from a provider call, but its lease
-- and candidate token are retired here and the public reserve/finalize RPCs
-- below reject that stale continuation. The operator sees one explicit retry.
UPDATE public.semantic_scene_image_jobs
SET status = 'failed',
    error = pg_catalog.jsonb_build_object(
      'code', 'worker_contract_upgraded',
      'message', 'Image generation was safely reset during the reliability upgrade. Retry this script.'
    ),
    worker_id = NULL,
    lease_token = NULL,
    lease_expires_at = NULL,
    finished_at = pg_catalog.clock_timestamp(),
    updated_at = pg_catalog.clock_timestamp()
WHERE status IN ('queued', 'processing');

UPDATE public.semantic_scene_image_jobs
SET attempt_count = LEAST(attempt_count, 5),
    provider_attempt_count = LEAST(GREATEST(provider_attempt_count, 0), 3),
    max_attempts = GREATEST(
      3,
      LEAST(attempt_count, 5)
    );

-- Older queue rows could legally contain an impossible processing state. Make
-- those rows terminal before installing the state-shape constraint.
UPDATE public.semantic_scene_image_jobs
SET status = 'failed',
    error = pg_catalog.jsonb_build_object(
      'code', 'invalid_legacy_lease',
      'message', 'The previous image worker lost its durable lease. Retry generation.'
    ),
    worker_id = NULL,
    lease_token = NULL,
    lease_expires_at = NULL,
    finished_at = pg_catalog.clock_timestamp(),
    updated_at = pg_catalog.clock_timestamp()
WHERE status = 'processing'
  AND (worker_id IS NULL OR lease_token IS NULL OR lease_expires_at IS NULL);

UPDATE public.semantic_scene_image_jobs
SET worker_id = NULL,
    lease_token = NULL,
    lease_expires_at = NULL
WHERE status IN ('queued', 'completed', 'failed');

-- Every job made terminal by the upgrade must also retire the linked empty
-- candidate reservation. Otherwise the displaced v1 handler could finalize
-- after its job lost admission, or a legitimate retry would wait 30 minutes.
UPDATE public.semantic_video_runs AS run
SET candidate_reservation_owner = NULL,
    candidate_reservation_token = NULL,
    candidate_reservation_expires_at = NULL,
    revision = run.revision + 1,
    updated_at = pg_catalog.clock_timestamp()
FROM public.semantic_scene_image_jobs AS job
WHERE job.status = 'failed'
  AND job.run_id = run.id
  AND run.stage = 'awaiting_reference_approval'
  AND run.candidate_reservation_token IS NOT NULL
  AND pg_catalog.jsonb_array_length(
    CASE
      WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
        THEN run.master_snapshot -> 'candidates'
      ELSE '[]'::JSONB
    END
  ) = 0;

UPDATE public.semantic_scene_image_jobs AS job
SET expected_revision = run.revision,
    updated_at = pg_catalog.clock_timestamp()
FROM public.semantic_video_runs AS run
WHERE job.status = 'failed'
  AND job.run_id = run.id;

-- Fence every old direct HTTP candidate request, including one whose image
-- finalization committed but response/queue acknowledgement was lost. Preserve
-- durable one/three-image masters, mark them ready, and retire every replay
-- token before v2 admission.
UPDATE public.semantic_video_runs AS run
SET candidate_generation_progress = CASE
      WHEN pg_catalog.jsonb_array_length(
        CASE
          WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
            THEN run.master_snapshot -> 'candidates'
          ELSE '[]'::JSONB
        END
      ) IN (1, 3)
        THEN pg_catalog.jsonb_build_object(
          'phase', 'ready',
          'details', pg_catalog.jsonb_build_object(
            'candidate_count', pg_catalog.jsonb_array_length(
              run.master_snapshot -> 'candidates'
            )
          ),
          'updated_at', pg_catalog.clock_timestamp()
        )
      ELSE run.candidate_generation_progress
    END,
    candidate_reservation_owner = NULL,
    candidate_reservation_token = NULL,
    candidate_reservation_expires_at = NULL,
    revision = run.revision + 1,
    updated_at = pg_catalog.clock_timestamp()
WHERE run.stage = 'awaiting_reference_approval'
  AND run.candidate_reservation_token IS NOT NULL;

ALTER TABLE public.semantic_scene_image_jobs
  ALTER COLUMN batch_id SET NOT NULL,
  ALTER COLUMN deadline_at SET DEFAULT (pg_catalog.clock_timestamp() + INTERVAL '8 minutes'),
  ALTER COLUMN deadline_at SET NOT NULL;

ALTER TABLE public.semantic_scene_image_jobs
  DROP CONSTRAINT IF EXISTS semantic_scene_image_jobs_attempt_contract,
  DROP CONSTRAINT IF EXISTS semantic_scene_image_jobs_state_shape;

ALTER TABLE public.semantic_scene_image_jobs
  ADD CONSTRAINT semantic_scene_image_jobs_attempt_contract
    CHECK (
      max_attempts BETWEEN 1 AND 5
      AND attempt_count <= max_attempts
      AND provider_attempt_count BETWEEN 0 AND 3
    ),
  ADD CONSTRAINT semantic_scene_image_jobs_state_shape
    CHECK (
      (
        status = 'processing'
        AND worker_id IS NOT NULL
        AND lease_token IS NOT NULL
        AND lease_expires_at IS NOT NULL
      )
      OR (
        status IN ('queued', 'completed', 'failed')
        AND worker_id IS NULL
        AND lease_token IS NULL
        AND lease_expires_at IS NULL
      )
    );

CREATE UNIQUE INDEX IF NOT EXISTS semantic_scene_image_jobs_one_active_batch
  ON public.semantic_scene_image_jobs(batch_id)
  WHERE status IN ('queued', 'processing');

CREATE INDEX IF NOT EXISTS semantic_scene_image_jobs_live_claim_order
  ON public.semantic_scene_image_jobs(status, deadline_at, created_at);

CREATE TABLE IF NOT EXISTS public.semantic_scene_image_worker_heartbeats (
  worker_id TEXT PRIMARY KEY,
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.clock_timestamp(),
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB
    CHECK (pg_catalog.jsonb_typeof(metadata) = 'object')
);

ALTER TABLE public.semantic_scene_image_worker_heartbeats ENABLE ROW LEVEL SECURITY;

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
  sibling_job public.semantic_scene_image_jobs%ROWTYPE;
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

  -- This lock makes the database, not browser timing, the batch admission owner.
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
  WHERE job.batch_id = target_post.batch_id
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

  SELECT job.* INTO sibling_job
  FROM public.semantic_scene_image_jobs AS job
  WHERE job.batch_id = target_post.batch_id
    AND job.status IN ('queued', 'processing')
  ORDER BY job.created_at, job.id
  LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: another script image is already generating for this batch';
  END IF;

  -- The global claim sweep may have terminalized this job immediately before
  -- enqueue acquired the batch lock. With no active batch owner, recover that
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
    pg_catalog.clock_timestamp() + INTERVAL '8 minutes'
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
      AND job.deadline_at > pg_catalog.clock_timestamp() + INTERVAL '4 minutes'
    ORDER BY job.created_at, job.id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
  )
  UPDATE public.semantic_scene_image_jobs AS job
  SET status = 'processing',
      attempt_count = job.attempt_count + 1,
      worker_id = p_worker_id,
      lease_token = gen_random_uuid(),
      lease_expires_at = LEAST(
        job.deadline_at,
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

CREATE OR REPLACE FUNCTION public.renew_semantic_scene_image(
  p_job_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_lease_seconds INTEGER
)
RETURNS SETOF public.semantic_scene_image_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  renewed_job public.semantic_scene_image_jobs%ROWTYPE;
BEGIN
  IF p_job_id IS NULL
     OR NULLIF(pg_catalog.btrim(p_worker_id), '') IS NULL
     OR p_lease_token IS NULL
     OR p_lease_seconds IS NULL
     OR p_lease_seconds < 30
     OR p_lease_seconds > 300 THEN
    RAISE EXCEPTION 'semantic scene image renewal contract is invalid';
  END IF;

  UPDATE public.semantic_scene_image_jobs AS job
  SET lease_expires_at = LEAST(
        job.deadline_at,
        pg_catalog.clock_timestamp() + pg_catalog.make_interval(secs => p_lease_seconds)
      ),
      heartbeat_at = pg_catalog.clock_timestamp(),
      updated_at = pg_catalog.clock_timestamp()
  WHERE job.id = p_job_id
    AND job.status = 'processing'
    AND job.worker_id = p_worker_id
    AND job.lease_token = p_lease_token
    AND job.lease_expires_at > pg_catalog.clock_timestamp()
    AND job.deadline_at > pg_catalog.clock_timestamp()
  RETURNING job.* INTO renewed_job;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image renewal lost its lease';
  END IF;

  IF renewed_job.run_id IS NOT NULL THEN
    UPDATE public.semantic_video_runs AS run
    SET candidate_reservation_expires_at = GREATEST(
          run.candidate_reservation_expires_at,
          renewed_job.lease_expires_at
        )
    WHERE run.id = renewed_job.run_id
      AND run.candidate_reservation_token IS NOT NULL;
  END IF;

  RETURN NEXT renewed_job;
END;
$$;

CREATE OR REPLACE FUNCTION public.authorize_semantic_scene_image_provider_attempt(
  p_job_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID
)
RETURNS SETOF public.semantic_scene_image_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  authorized_job public.semantic_scene_image_jobs%ROWTYPE;
BEGIN
  UPDATE public.semantic_scene_image_jobs AS job
  SET provider_attempt_count = job.provider_attempt_count + 1,
      updated_at = pg_catalog.clock_timestamp()
  WHERE job.id = p_job_id
    AND job.status = 'processing'
    AND job.worker_id = p_worker_id
    AND job.lease_token = p_lease_token
    AND job.lease_expires_at > pg_catalog.clock_timestamp()
    AND job.deadline_at > pg_catalog.clock_timestamp()
    AND job.provider_attempt_count < 3
  RETURNING job.* INTO authorized_job;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image provider-attempt budget or lease is exhausted';
  END IF;
  RETURN NEXT authorized_job;
END;
$$;

CREATE OR REPLACE FUNCTION public.reserve_semantic_scene_image_candidates(
  p_job_id UUID,
  p_worker_id TEXT,
  p_job_lease_token UUID,
  p_post_id UUID,
  p_expected_revision INTEGER,
  p_run_create JSONB,
  p_reservation_owner TEXT,
  p_reservation_token UUID,
  p_reservation_seconds INTEGER
)
RETURNS SETOF public.semantic_video_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_job public.semantic_scene_image_jobs%ROWTYPE;
  current_run public.semantic_video_runs%ROWTYPE;
  reserved_run public.semantic_video_runs%ROWTYPE;
BEGIN
  SELECT job.* INTO locked_job
  FROM public.semantic_scene_image_jobs AS job
  WHERE job.id = p_job_id
  FOR UPDATE;
  IF NOT FOUND
     OR locked_job.post_id IS DISTINCT FROM p_post_id
     OR locked_job.status IS DISTINCT FROM 'processing'
     OR locked_job.worker_id IS DISTINCT FROM p_worker_id
     OR locked_job.lease_token IS DISTINCT FROM p_job_lease_token
     OR locked_job.lease_expires_at <= pg_catalog.clock_timestamp()
     OR locked_job.deadline_at <= pg_catalog.clock_timestamp() THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image reservation lost its job lease';
  END IF;

  SELECT run.* INTO current_run
  FROM public.semantic_video_runs AS run
  WHERE run.post_id = p_post_id
    AND run.stage NOT IN ('completed', 'failed')
  ORDER BY run.created_at DESC, run.id DESC
  LIMIT 1
  FOR UPDATE;
  IF FOUND AND current_run.id IS DISTINCT FROM COALESCE(
    locked_job.run_id,
    locked_job.expected_run_id
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: queued scene image no longer owns the current run';
  END IF;

  PERFORM pg_catalog.set_config(
    'app.semantic_scene_image_queue_write',
    'v2',
    TRUE
  );

  SELECT * INTO reserved_run
  FROM public.reserve_semantic_video_candidates_legacy_queue_impl(
    p_post_id,
    p_expected_revision,
    p_run_create,
    p_reservation_owner,
    p_reservation_token,
    p_reservation_seconds
  );

  -- The candidate token and the queue lease are one ownership contract. The
  -- heartbeat renews both; if heartbeats stop, both expire together and the
  -- next token-fenced claim can recover immediately.
  UPDATE public.semantic_video_runs AS run
  SET candidate_reservation_expires_at = locked_job.lease_expires_at,
      updated_at = pg_catalog.clock_timestamp()
  WHERE run.id = reserved_run.id
    AND run.candidate_reservation_token = p_reservation_token
  RETURNING run.* INTO reserved_run;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image reservation lost its candidate token';
  END IF;

  UPDATE public.semantic_scene_image_jobs AS job
  SET run_id = reserved_run.id,
      expected_run_id = reserved_run.id,
      expected_revision = reserved_run.revision,
      updated_at = pg_catalog.clock_timestamp()
  WHERE job.id = locked_job.id
    AND job.lease_token = p_job_lease_token;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image run linkage lost its job lease';
  END IF;

  RETURN NEXT reserved_run;
END;
$$;

CREATE OR REPLACE FUNCTION public.finalize_semantic_scene_image_job(
  p_job_id UUID,
  p_worker_id TEXT,
  p_job_lease_token UUID,
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
  locked_job public.semantic_scene_image_jobs%ROWTYPE;
  finalized_run public.semantic_video_runs%ROWTYPE;
BEGIN
  SELECT job.* INTO locked_job
  FROM public.semantic_scene_image_jobs AS job
  WHERE job.id = p_job_id
  FOR UPDATE;
  IF NOT FOUND
     OR locked_job.status IS DISTINCT FROM 'processing'
     OR locked_job.worker_id IS DISTINCT FROM p_worker_id
     OR locked_job.lease_token IS DISTINCT FROM p_job_lease_token
     OR locked_job.lease_expires_at <= pg_catalog.clock_timestamp()
     OR locked_job.deadline_at <= pg_catalog.clock_timestamp()
     OR locked_job.run_id IS DISTINCT FROM p_run_id THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image finalization lost its job lease';
  END IF;

  IF pg_catalog.jsonb_typeof(p_run_update #> '{master_snapshot,candidates}')
       IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_array_length(
       CASE
         WHEN pg_catalog.jsonb_typeof(p_run_update #> '{master_snapshot,candidates}') = 'array'
           THEN p_run_update #> '{master_snapshot,candidates}'
         ELSE '[]'::JSONB
       END
     ) IS DISTINCT FROM 1 THEN
    RAISE EXCEPTION 'atomic scene-image finalization requires exactly one candidate';
  END IF;

  PERFORM pg_catalog.set_config(
    'app.semantic_scene_image_queue_write',
    'v2',
    TRUE
  );

  SELECT * INTO finalized_run
  FROM public.finalize_semantic_video_candidates_scene_contract_impl(
    p_run_id,
    p_reserved_revision,
    p_reservation_token,
    p_run_update
  );

  UPDATE public.semantic_video_runs AS run
  SET candidate_generation_progress = pg_catalog.jsonb_build_object(
        'phase', 'ready',
        'details', pg_catalog.jsonb_build_object('candidate_count', 1),
        'updated_at', pg_catalog.clock_timestamp()
      ),
      candidate_reservation_owner = NULL,
      candidate_reservation_token = NULL,
      candidate_reservation_expires_at = NULL,
      updated_at = pg_catalog.clock_timestamp()
  WHERE run.id = finalized_run.id
  RETURNING run.* INTO finalized_run;

  UPDATE public.semantic_scene_image_jobs AS job
  SET status = 'completed',
      worker_id = NULL,
      lease_token = NULL,
      lease_expires_at = NULL,
      error = NULL,
      finished_at = pg_catalog.clock_timestamp(),
      updated_at = pg_catalog.clock_timestamp()
  WHERE job.id = locked_job.id
    AND job.status = 'processing'
    AND job.lease_token = p_job_lease_token;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image completion lost its job lease';
  END IF;

  RETURN NEXT finalized_run;
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
  locked_job public.semantic_scene_image_jobs%ROWTYPE;
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

  SELECT job.* INTO locked_job
  FROM public.semantic_scene_image_jobs AS job
  WHERE job.id = p_job_id
  FOR UPDATE;
  IF NOT FOUND
     OR locked_job.status IS DISTINCT FROM 'processing'
     OR locked_job.worker_id IS DISTINCT FROM p_worker_id
     OR locked_job.lease_token IS DISTINCT FROM p_lease_token
     OR locked_job.lease_expires_at <= pg_catalog.clock_timestamp() THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: scene image job lease is stale';
  END IF;

  IF p_status = 'completed' AND NOT EXISTS (
    SELECT 1
    FROM public.semantic_video_runs AS run
    WHERE run.id = p_run_id
      AND run.post_id = locked_job.post_id
      AND run.stage = 'awaiting_reference_approval'
      AND pg_catalog.jsonb_array_length(
        CASE
          WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
            THEN run.master_snapshot -> 'candidates'
          ELSE '[]'::JSONB
        END
      ) = 1
  ) THEN
    RAISE EXCEPTION 'semantic scene image completion requires its valid one-image run';
  END IF;

  UPDATE public.semantic_scene_image_jobs AS job
  SET status = p_status,
      run_id = COALESCE(p_run_id, job.run_id),
      error = p_error,
      worker_id = NULL,
      lease_token = NULL,
      lease_expires_at = NULL,
      finished_at = pg_catalog.clock_timestamp(),
      updated_at = pg_catalog.clock_timestamp()
  WHERE job.id = locked_job.id
  RETURNING job.* INTO finished_job;

  IF p_status = 'failed' AND finished_job.run_id IS NOT NULL THEN
    UPDATE public.semantic_video_runs AS run
    SET candidate_generation_progress = pg_catalog.jsonb_build_object(
          'phase', 'failed',
          'details', (
            CASE
              WHEN pg_catalog.jsonb_typeof(
                run.candidate_generation_progress -> 'details'
              ) = 'object'
                THEN run.candidate_generation_progress -> 'details'
              ELSE '{}'::JSONB
            END
          ) || pg_catalog.jsonb_build_object(
              'candidate_count', 1,
              'completed_candidates', CASE
                WHEN pg_catalog.jsonb_typeof(
                  run.candidate_generation_progress #> '{details,partial_candidates}'
                ) = 'array'
                  THEN pg_catalog.jsonb_array_length(
                    run.candidate_generation_progress #> '{details,partial_candidates}'
                  )
                ELSE 0
              END,
              'retryable', TRUE,
              'failure_code', COALESCE(p_error ->> 'code', 'generation_failed')
            ),
          'updated_at', pg_catalog.clock_timestamp()
        ),
        candidate_reservation_owner = NULL,
        candidate_reservation_token = NULL,
        candidate_reservation_expires_at = NULL,
        updated_at = pg_catalog.clock_timestamp()
    WHERE run.id = finished_job.run_id
      AND run.stage = 'awaiting_reference_approval'
      AND pg_catalog.jsonb_array_length(
        CASE
          WHEN pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') = 'array'
            THEN run.master_snapshot -> 'candidates'
          ELSE '[]'::JSONB
        END
      ) = 0;
  END IF;

  RETURN NEXT finished_job;
END;
$$;

CREATE OR REPLACE FUNCTION public.heartbeat_semantic_scene_image_worker(
  p_worker_id TEXT,
  p_metadata JSONB
)
RETURNS SETOF public.semantic_scene_image_worker_heartbeats
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  heartbeat public.semantic_scene_image_worker_heartbeats%ROWTYPE;
BEGIN
  IF NULLIF(pg_catalog.btrim(p_worker_id), '') IS NULL
     OR pg_catalog.jsonb_typeof(p_metadata) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'semantic scene image worker heartbeat contract is invalid';
  END IF;

  DELETE FROM public.semantic_scene_image_worker_heartbeats
  WHERE last_seen_at < pg_catalog.clock_timestamp() - INTERVAL '7 days';

  INSERT INTO public.semantic_scene_image_worker_heartbeats (
    worker_id, last_seen_at, metadata
  ) VALUES (
    p_worker_id, pg_catalog.clock_timestamp(), p_metadata
  )
  ON CONFLICT (worker_id) DO UPDATE
  SET last_seen_at = EXCLUDED.last_seen_at,
      metadata = EXCLUDED.metadata
  RETURNING * INTO heartbeat;

  RETURN NEXT heartbeat;
END;
$$;

-- A rendered image must checkpoint its deterministic R2 destination before
-- the PUT side effect, so an ambiguous upload acknowledgement can resume the
-- exact bytes without purchasing another render.
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
       'uploading_candidate',
       'saving_candidates',
       'failed',
       'ready'
     )
     OR pg_catalog.jsonb_typeof(
       COALESCE(p_progress -> 'details', '{}'::JSONB)
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

CREATE OR REPLACE FUNCTION public.probe_semantic_scene_image_queue()
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  -- NOWAIT makes an operational/DDL lock visible to readiness immediately.
  LOCK TABLE public.semantic_scene_image_jobs IN ROW SHARE MODE NOWAIT;
  PERFORM job.id
  FROM public.semantic_scene_image_jobs AS job
  ORDER BY job.created_at
  LIMIT 1;
  RETURN 'semantic-scene-image-v2';
END;
$$;

REVOKE ALL ON TABLE public.semantic_scene_image_worker_heartbeats
  FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.semantic_scene_image_worker_heartbeats TO service_role;

REVOKE ALL ON TABLE public.semantic_scene_image_jobs
  FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.semantic_scene_image_jobs TO service_role;

REVOKE ALL ON FUNCTION public.enqueue_semantic_scene_image(UUID, INTEGER, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.enqueue_semantic_scene_image(UUID, INTEGER, TEXT, TEXT)
  TO service_role;
REVOKE ALL ON FUNCTION public.claim_semantic_scene_image(TEXT, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_semantic_scene_image(TEXT, INTEGER)
  TO service_role;
REVOKE ALL ON FUNCTION public.renew_semantic_scene_image(UUID, TEXT, UUID, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.renew_semantic_scene_image(UUID, TEXT, UUID, INTEGER)
  TO service_role;
REVOKE ALL ON FUNCTION public.authorize_semantic_scene_image_provider_attempt(
  UUID, TEXT, UUID
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.authorize_semantic_scene_image_provider_attempt(
  UUID, TEXT, UUID
) TO service_role;
REVOKE ALL ON FUNCTION public.reserve_semantic_scene_image_candidates(
  UUID, TEXT, UUID, UUID, INTEGER, JSONB, TEXT, UUID, INTEGER
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reserve_semantic_scene_image_candidates(
  UUID, TEXT, UUID, UUID, INTEGER, JSONB, TEXT, UUID, INTEGER
) TO service_role;
REVOKE ALL ON FUNCTION public.finalize_semantic_scene_image_job(
  UUID, TEXT, UUID, UUID, INTEGER, UUID, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finalize_semantic_scene_image_job(
  UUID, TEXT, UUID, UUID, INTEGER, UUID, JSONB
) TO service_role;
REVOKE ALL ON FUNCTION public.finish_semantic_scene_image(UUID, TEXT, UUID, TEXT, UUID, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finish_semantic_scene_image(UUID, TEXT, UUID, TEXT, UUID, JSONB)
  TO service_role;
REVOKE ALL ON FUNCTION public.heartbeat_semantic_scene_image_worker(TEXT, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.heartbeat_semantic_scene_image_worker(TEXT, JSONB)
  TO service_role;
REVOKE ALL ON FUNCTION public.update_semantic_video_candidate_progress(
  UUID, INTEGER, UUID, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.update_semantic_video_candidate_progress(
  UUID, INTEGER, UUID, JSONB
) TO service_role;
REVOKE ALL ON FUNCTION public.probe_semantic_scene_image_queue()
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.probe_semantic_scene_image_queue()
  TO service_role;

COMMIT;
