-- Experimental Runway Seedance 2.5 evaluation runs for Semantic UGC posts.
--
-- Evaluations are isolated from production delivery: these rows never write
-- posts.video_*, semantic_video_runs, semantic_video_takes, captions, or
-- publishing. Writes happen only through the SECURITY DEFINER functions below.
-- Spend uses the shared provider quota ledger (0211) with provider
-- 'runway_seedance_2_5' and Runway credits as reservation units, so Veo limits,
-- freezes, and application-level Veo bypass flags never apply to Runway.
--
-- Submission fence: reserved -> submitting -> submitted|submission_unknown.
-- Runway has no idempotency key; an ambiguous submission stays blocked in
-- submission_unknown and keeps its reservation so it can never buy a second task.

CREATE TABLE IF NOT EXISTS public.runway_video_evaluations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id UUID NOT NULL REFERENCES public.posts(id) ON DELETE CASCADE,
  batch_id UUID REFERENCES public.batches(id) ON DELETE SET NULL,
  semantic_run_id UUID NOT NULL REFERENCES public.semantic_video_runs(id) ON DELETE CASCADE,
  take_id UUID NOT NULL,
  take_index INTEGER NOT NULL CHECK (take_index >= 0),
  take_attempt INTEGER NOT NULL CHECK (take_attempt >= 1),
  provider TEXT NOT NULL DEFAULT 'runway' CHECK (provider = 'runway'),
  provider_model TEXT NOT NULL CHECK (provider_model = 'seedance2_5'),
  quota_provider TEXT NOT NULL DEFAULT 'runway_seedance_2_5' CHECK (quota_provider = 'runway_seedance_2_5'),
  status TEXT NOT NULL DEFAULT 'reserved' CHECK (
    status IN (
      'reserved',
      'submitting',
      'submitted',
      'processing',
      'completed',
      'failed',
      'cancelled',
      'submission_unknown'
    )
  ),
  requested_resolution TEXT NOT NULL CHECK (requested_resolution IN ('480p', '720p', '1080p')),
  requested_ratio TEXT NOT NULL CHECK (requested_ratio IN ('480:854', '720:1280', '1080:1920')),
  requested_duration_seconds INTEGER NOT NULL CHECK (requested_duration_seconds = 8),
  request_contract JSONB NOT NULL CHECK (pg_catalog.jsonb_typeof(request_contract) = 'object'),
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  source_provenance JSONB NOT NULL CHECK (pg_catalog.jsonb_typeof(source_provenance) = 'object'),
  estimated_credits INTEGER NOT NULL CHECK (estimated_credits > 0),
  estimated_usd NUMERIC(12, 4) NOT NULL CHECK (estimated_usd >= 0),
  provider_estimated_credits NUMERIC(12, 2),
  actual_credits INTEGER CHECK (actual_credits IS NULL OR actual_credits >= 0),
  reservation_key TEXT NOT NULL UNIQUE CHECK (reservation_key LIKE 'runway_seedance_2_5:%'),
  task_id TEXT UNIQUE,
  last_provider_status TEXT,
  provider_progress NUMERIC(5, 4),
  requested_by TEXT NOT NULL CHECK (pg_catalog.length(pg_catalog.btrim(requested_by)) > 0),
  poller_environment TEXT NOT NULL,
  poller_scope TEXT NOT NULL,
  submit_intent_at TIMESTAMPTZ,
  submitted_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  next_poll_at TIMESTAMPTZ,
  poll_count INTEGER NOT NULL DEFAULT 0,
  poll_error_count INTEGER NOT NULL DEFAULT 0,
  last_poll_error JSONB,
  lease_owner TEXT,
  lease_token UUID,
  lease_expires_at TIMESTAMPTZ,
  output_storage_key TEXT,
  output_url TEXT,
  output_sha256 TEXT CHECK (output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'),
  output_byte_length BIGINT CHECK (output_byte_length IS NULL OR output_byte_length > 0),
  output_probe JSONB,
  error_code TEXT,
  error_message TEXT,
  error_details JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT runway_video_evaluations_task_fence CHECK (
    (status IN ('reserved', 'submitting', 'submission_unknown') AND task_id IS NULL)
    OR (status IN ('submitted', 'processing', 'completed') AND task_id IS NOT NULL)
    OR status IN ('failed', 'cancelled')
  ),
  CONSTRAINT runway_video_evaluations_completion_fence CHECK (
    status <> 'completed'
    OR (
      output_storage_key IS NOT NULL
      AND output_url IS NOT NULL
      AND output_sha256 IS NOT NULL
      AND output_byte_length IS NOT NULL
    )
  )
);

CREATE INDEX IF NOT EXISTS idx_runway_video_evaluations_poll
  ON public.runway_video_evaluations(status, next_poll_at);

CREATE INDEX IF NOT EXISTS idx_runway_video_evaluations_post
  ON public.runway_video_evaluations(post_id, created_at DESC);

DROP TRIGGER IF EXISTS runway_video_evaluations_touch_updated_at
  ON public.runway_video_evaluations;
CREATE TRIGGER runway_video_evaluations_touch_updated_at
BEFORE UPDATE ON public.runway_video_evaluations
FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

ALTER TABLE public.runway_video_evaluations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.runway_video_evaluations FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON TABLE public.runway_video_evaluations TO service_role;


-- Admission: concurrency cap, submission pacing, run ownership, and the credit
-- reservation commit together or not at all.
CREATE OR REPLACE FUNCTION public.create_runway_video_evaluation(
  p_payload JSONB,
  p_daily_credit_limit INTEGER,
  p_max_active INTEGER,
  p_min_submit_interval_seconds INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_post_id UUID;
  v_run_id UUID;
  v_batch_id UUID;
  v_reservation_key TEXT;
  v_estimated_credits INTEGER;
  v_active INTEGER;
  v_last_created TIMESTAMPTZ;
  v_quota JSONB;
  v_row public.runway_video_evaluations%ROWTYPE;
BEGIN
  IF pg_catalog.jsonb_typeof(p_payload) IS DISTINCT FROM 'object'
     OR COALESCE(p_daily_credit_limit, 0) <= 0
     OR COALESCE(p_max_active, 0) <= 0 THEN
    RAISE EXCEPTION 'runway evaluation admission contract is invalid';
  END IF;

  v_post_id := (p_payload ->> 'post_id')::UUID;
  v_run_id := (p_payload ->> 'semantic_run_id')::UUID;
  v_reservation_key := p_payload ->> 'reservation_key';
  v_estimated_credits := (p_payload ->> 'estimated_credits')::INTEGER;
  IF v_post_id IS NULL OR v_run_id IS NULL OR v_reservation_key IS NULL OR COALESCE(v_estimated_credits, 0) <= 0 THEN
    RAISE EXCEPTION 'runway evaluation admission payload is incomplete';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('runway_video_evaluations:admission'));

  SELECT run.batch_id
  INTO v_batch_id
  FROM public.semantic_video_runs AS run
  WHERE run.id = v_run_id
    AND run.post_id = v_post_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'runway evaluation run does not belong to the post';
  END IF;

  SELECT pg_catalog.count(*)
  INTO v_active
  FROM public.runway_video_evaluations AS evaluation
  WHERE evaluation.status IN ('reserved', 'submitting', 'submitted', 'processing', 'submission_unknown');
  IF v_active >= p_max_active THEN
    RETURN pg_catalog.jsonb_build_object(
      'allowed', false,
      'reason', 'concurrency_limit_reached',
      'active_count', v_active,
      'max_active', p_max_active
    );
  END IF;

  SELECT pg_catalog.max(evaluation.created_at)
  INTO v_last_created
  FROM public.runway_video_evaluations AS evaluation;
  IF COALESCE(p_min_submit_interval_seconds, 0) > 0
     AND v_last_created IS NOT NULL
     AND v_last_created > now() - pg_catalog.make_interval(secs => p_min_submit_interval_seconds) THEN
    RETURN pg_catalog.jsonb_build_object(
      'allowed', false,
      'reason', 'submission_paced',
      'retry_after_seconds', pg_catalog.ceil(
        EXTRACT(EPOCH FROM (v_last_created + pg_catalog.make_interval(secs => p_min_submit_interval_seconds) - now()))
      )
    );
  END IF;

  v_quota := public.reserve_video_provider_quota(
    'runway_seedance_2_5',
    v_reservation_key,
    v_estimated_credits,
    v_post_id,
    v_batch_id,
    p_daily_credit_limit,
    0,
    0,
    'video_chain',
    false
  );
  IF NOT COALESCE((v_quota ->> 'allowed')::BOOLEAN, false) THEN
    RETURN pg_catalog.jsonb_build_object(
      'allowed', false,
      'reason', COALESCE(v_quota ->> 'reason', 'daily_quota_exhausted'),
      'quota', v_quota
    );
  END IF;

  INSERT INTO public.runway_video_evaluations (
    post_id,
    batch_id,
    semantic_run_id,
    take_id,
    take_index,
    take_attempt,
    provider_model,
    status,
    requested_resolution,
    requested_ratio,
    requested_duration_seconds,
    request_contract,
    request_hash,
    source_provenance,
    estimated_credits,
    estimated_usd,
    reservation_key,
    requested_by,
    poller_environment,
    poller_scope
  )
  VALUES (
    v_post_id,
    v_batch_id,
    v_run_id,
    (p_payload ->> 'take_id')::UUID,
    (p_payload ->> 'take_index')::INTEGER,
    (p_payload ->> 'take_attempt')::INTEGER,
    p_payload ->> 'provider_model',
    'reserved',
    p_payload ->> 'requested_resolution',
    p_payload ->> 'requested_ratio',
    (p_payload ->> 'requested_duration_seconds')::INTEGER,
    p_payload -> 'request_contract',
    p_payload ->> 'request_hash',
    p_payload -> 'source_provenance',
    v_estimated_credits,
    (p_payload ->> 'estimated_usd')::NUMERIC,
    v_reservation_key,
    p_payload ->> 'requested_by',
    p_payload ->> 'poller_environment',
    p_payload ->> 'poller_scope'
  )
  RETURNING * INTO v_row;

  RETURN pg_catalog.jsonb_build_object(
    'allowed', true,
    'evaluation', pg_catalog.to_jsonb(v_row),
    'quota', v_quota
  );
END;
$$;


-- Persist the pre-call intent. After this commit, a crash is treated as an
-- ambiguous submission because the provider call may have been entered.
CREATE OR REPLACE FUNCTION public.mark_runway_video_evaluation_submitting(
  p_evaluation_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_row public.runway_video_evaluations%ROWTYPE;
BEGIN
  UPDATE public.runway_video_evaluations AS evaluation
  SET status = 'submitting',
      submit_intent_at = now()
  WHERE evaluation.id = p_evaluation_id
    AND evaluation.status = 'reserved'
  RETURNING * INTO v_row;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'runway_evaluation_conflict: evaluation is not reserved';
  END IF;
  RETURN pg_catalog.to_jsonb(v_row);
END;
$$;


-- Record provider acceptance and consume the reserved credits. A non-null
-- cancel reason records an accepted task that was immediately cancelled, for
-- example because the provider estimate exceeded the reservation.
CREATE OR REPLACE FUNCTION public.acknowledge_runway_video_evaluation(
  p_evaluation_id UUID,
  p_task_id TEXT,
  p_provider_estimated_credits NUMERIC DEFAULT NULL,
  p_cancel_reason TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_row public.runway_video_evaluations%ROWTYPE;
  v_status TEXT := CASE WHEN p_cancel_reason IS NULL THEN 'submitted' ELSE 'cancelled' END;
  v_quota JSONB;
BEGIN
  IF p_task_id IS NULL OR p_task_id !~ '^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$' THEN
    RAISE EXCEPTION 'runway task id is invalid';
  END IF;

  SELECT evaluation.*
  INTO v_row
  FROM public.runway_video_evaluations AS evaluation
  WHERE evaluation.id = p_evaluation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'runway evaluation was not found';
  END IF;

  IF v_row.task_id IS NOT NULL THEN
    IF v_row.task_id = p_task_id THEN
      RETURN pg_catalog.to_jsonb(v_row);
    END IF;
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'runway_evaluation_conflict: evaluation already has a different task';
  END IF;
  IF v_row.status NOT IN ('submitting', 'submission_unknown') THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'runway_evaluation_conflict: evaluation has no open submission intent';
  END IF;

  v_quota := public.consume_video_provider_quota(v_row.reservation_key, v_row.estimated_credits, p_task_id);

  UPDATE public.runway_video_evaluations AS evaluation
  SET status = v_status,
      task_id = p_task_id,
      submitted_at = now(),
      provider_estimated_credits = p_provider_estimated_credits,
      next_poll_at = CASE WHEN v_status = 'submitted' THEN now() + INTERVAL '10 seconds' ELSE NULL END,
      completed_at = CASE WHEN v_status = 'cancelled' THEN now() ELSE NULL END,
      error_code = p_cancel_reason,
      error_message = CASE WHEN p_cancel_reason IS NULL THEN NULL ELSE 'Accepted Runway task was cancelled before generation.' END,
      error_details = CASE
        WHEN COALESCE((v_quota ->> 'allowed')::BOOLEAN, false) THEN evaluation.error_details
        ELSE COALESCE(evaluation.error_details, '{}'::JSONB) || pg_catalog.jsonb_build_object('quota_consume_error', v_quota)
      END
  WHERE evaluation.id = p_evaluation_id
  RETURNING * INTO v_row;

  IF v_status = 'cancelled' THEN
    PERFORM public.release_video_provider_quota(v_row.reservation_key, p_cancel_reason, 'failed', p_cancel_reason);
  END IF;
  RETURN pg_catalog.to_jsonb(v_row);
END;
$$;


-- The caller proved that Runway never created a task (connection never
-- established or a definitive 4xx/429 rejection). Release the reservation.
CREATE OR REPLACE FUNCTION public.fail_runway_video_evaluation_before_submit(
  p_evaluation_id UUID,
  p_error_code TEXT,
  p_error_message TEXT,
  p_error_details JSONB DEFAULT '{}'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_row public.runway_video_evaluations%ROWTYPE;
BEGIN
  UPDATE public.runway_video_evaluations AS evaluation
  SET status = 'failed',
      error_code = COALESCE(NULLIF(pg_catalog.btrim(p_error_code), ''), 'submission_rejected'),
      error_message = pg_catalog.left(COALESCE(p_error_message, ''), 1000),
      error_details = COALESCE(p_error_details, '{}'::JSONB),
      completed_at = now()
  WHERE evaluation.id = p_evaluation_id
    AND evaluation.status IN ('reserved', 'submitting')
  RETURNING * INTO v_row;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'runway_evaluation_conflict: evaluation is past the pre-submit fence';
  END IF;
  PERFORM public.release_video_provider_quota(v_row.reservation_key, v_row.error_code, 'released', v_row.error_code);
  RETURN pg_catalog.to_jsonb(v_row);
END;
$$;


-- An ambiguous submission keeps its reservation and blocks forever until an
-- operator reconciles it with Runway; no code path may resubmit it.
CREATE OR REPLACE FUNCTION public.mark_runway_video_evaluation_submission_unknown(
  p_evaluation_id UUID,
  p_error_code TEXT,
  p_error_message TEXT,
  p_error_details JSONB DEFAULT '{}'::JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_row public.runway_video_evaluations%ROWTYPE;
BEGIN
  UPDATE public.runway_video_evaluations AS evaluation
  SET status = 'submission_unknown',
      error_code = COALESCE(NULLIF(pg_catalog.btrim(p_error_code), ''), 'submission_unknown'),
      error_message = pg_catalog.left(COALESCE(p_error_message, ''), 1000),
      error_details = COALESCE(p_error_details, '{}'::JSONB)
  WHERE evaluation.id = p_evaluation_id
    AND evaluation.status = 'submitting'
  RETURNING * INTO v_row;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'runway_evaluation_conflict: evaluation is not submitting';
  END IF;
  RETURN pg_catalog.to_jsonb(v_row);
END;
$$;


-- Crash recovery. 'reserved' rows never reached the provider and are released;
-- stale 'submitting' rows may have reached it and become submission_unknown.
CREATE OR REPLACE FUNCTION public.reconcile_stale_runway_video_evaluations(
  p_reserved_stale_seconds INTEGER,
  p_submitting_stale_seconds INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_row public.runway_video_evaluations%ROWTYPE;
  v_released INTEGER := 0;
  v_unknown INTEGER := 0;
BEGIN
  FOR v_row IN
    UPDATE public.runway_video_evaluations AS evaluation
    SET status = 'failed',
        error_code = 'abandoned_before_submit',
        error_message = 'The submitting process stopped before calling Runway.',
        completed_at = now()
    WHERE evaluation.status = 'reserved'
      AND evaluation.created_at < now() - pg_catalog.make_interval(secs => GREATEST(p_reserved_stale_seconds, 60))
    RETURNING evaluation.*
  LOOP
    PERFORM public.release_video_provider_quota(v_row.reservation_key, 'abandoned_before_submit', 'released', 'abandoned_before_submit');
    v_released := v_released + 1;
  END LOOP;

  UPDATE public.runway_video_evaluations AS evaluation
  SET status = 'submission_unknown',
      error_code = 'submit_acknowledgement_lost',
      error_message = 'The submitting process stopped after the provider call may have started.'
  WHERE evaluation.status = 'submitting'
    AND evaluation.submit_intent_at < now() - pg_catalog.make_interval(secs => GREATEST(p_submitting_stale_seconds, 300));
  GET DIAGNOSTICS v_unknown = ROW_COUNT;

  RETURN pg_catalog.jsonb_build_object('released', v_released, 'submission_unknown', v_unknown);
END;
$$;


CREATE OR REPLACE FUNCTION public.claim_runway_video_evaluations(
  p_worker_id TEXT,
  p_lease_seconds INTEGER,
  p_limit INTEGER,
  p_poller_environment TEXT,
  p_poller_scope TEXT
)
RETURNS SETOF public.runway_video_evaluations
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  IF COALESCE(pg_catalog.btrim(p_worker_id), '') = '' THEN
    RAISE EXCEPTION 'runway evaluation worker id is required';
  END IF;
  RETURN QUERY
  WITH candidates AS (
    SELECT evaluation.id
    FROM public.runway_video_evaluations AS evaluation
    WHERE evaluation.status IN ('submitted', 'processing')
      AND evaluation.poller_environment = p_poller_environment
      AND evaluation.poller_scope = p_poller_scope
      AND (evaluation.next_poll_at IS NULL OR evaluation.next_poll_at <= now())
      AND (evaluation.lease_expires_at IS NULL OR evaluation.lease_expires_at < now())
    ORDER BY evaluation.next_poll_at NULLS FIRST, evaluation.created_at
    FOR UPDATE SKIP LOCKED
    LIMIT GREATEST(LEAST(COALESCE(p_limit, 1), 20), 1)
  ), claimed AS (
    UPDATE public.runway_video_evaluations AS evaluation
    SET lease_owner = p_worker_id,
        lease_token = pg_catalog.gen_random_uuid(),
        lease_expires_at = now() + pg_catalog.make_interval(secs => GREATEST(LEAST(COALESCE(p_lease_seconds, 300), 900), 30))
    FROM candidates
    WHERE evaluation.id = candidates.id
    RETURNING evaluation.*
  )
  SELECT * FROM claimed;
END;
$$;


CREATE OR REPLACE FUNCTION public.record_runway_video_evaluation_poll(
  p_evaluation_id UUID,
  p_lease_token UUID,
  p_provider_status TEXT,
  p_progress NUMERIC,
  p_next_poll_at TIMESTAMPTZ,
  p_poll_error JSONB DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_row public.runway_video_evaluations%ROWTYPE;
BEGIN
  UPDATE public.runway_video_evaluations AS evaluation
  SET status = CASE
        WHEN p_poll_error IS NULL AND p_provider_status IN ('PENDING', 'THROTTLED', 'RUNNING') THEN 'processing'
        ELSE evaluation.status
      END,
      last_provider_status = COALESCE(p_provider_status, evaluation.last_provider_status),
      provider_progress = COALESCE(LEAST(GREATEST(p_progress, 0), 1), evaluation.provider_progress),
      poll_count = evaluation.poll_count + 1,
      poll_error_count = CASE WHEN p_poll_error IS NULL THEN 0 ELSE evaluation.poll_error_count + 1 END,
      last_poll_error = p_poll_error,
      next_poll_at = GREATEST(COALESCE(p_next_poll_at, now()), now() + INTERVAL '5 seconds'),
      lease_owner = NULL,
      lease_token = NULL,
      lease_expires_at = NULL
  WHERE evaluation.id = p_evaluation_id
    AND evaluation.lease_token = p_lease_token
    AND evaluation.status IN ('submitted', 'processing')
  RETURNING * INTO v_row;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'runway_evaluation_conflict: poll lease was lost';
  END IF;
  RETURN pg_catalog.to_jsonb(v_row);
END;
$$;


CREATE OR REPLACE FUNCTION public.complete_runway_video_evaluation(
  p_evaluation_id UUID,
  p_lease_token UUID,
  p_output JSONB,
  p_actual_credits INTEGER DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_row public.runway_video_evaluations%ROWTYPE;
BEGIN
  IF pg_catalog.jsonb_typeof(p_output) IS DISTINCT FROM 'object'
     OR COALESCE(p_output ->> 'storage_key', '') = ''
     OR COALESCE(p_output ->> 'url', '') NOT LIKE 'https://%'
     OR COALESCE(p_output ->> 'sha256', '') !~ '^[0-9a-f]{64}$'
     OR COALESCE((p_output ->> 'byte_length')::BIGINT, 0) <= 0
     OR pg_catalog.jsonb_typeof(p_output -> 'probe') IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'runway evaluation output contract is invalid';
  END IF;

  SELECT evaluation.*
  INTO v_row
  FROM public.runway_video_evaluations AS evaluation
  WHERE evaluation.id = p_evaluation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'runway evaluation was not found';
  END IF;
  IF v_row.status = 'completed' AND v_row.output_sha256 = p_output ->> 'sha256' THEN
    RETURN pg_catalog.to_jsonb(v_row);
  END IF;
  IF v_row.status NOT IN ('submitted', 'processing') OR v_row.lease_token IS DISTINCT FROM p_lease_token THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'runway_evaluation_conflict: completion lease was lost';
  END IF;

  UPDATE public.runway_video_evaluations AS evaluation
  SET status = 'completed',
      output_storage_key = p_output ->> 'storage_key',
      output_url = p_output ->> 'url',
      output_sha256 = p_output ->> 'sha256',
      output_byte_length = (p_output ->> 'byte_length')::BIGINT,
      output_probe = p_output -> 'probe',
      actual_credits = p_actual_credits,
      last_provider_status = 'SUCCEEDED',
      provider_progress = 1,
      completed_at = now(),
      next_poll_at = NULL,
      last_poll_error = NULL,
      lease_owner = NULL,
      lease_token = NULL,
      lease_expires_at = NULL
  WHERE evaluation.id = p_evaluation_id
  RETURNING * INTO v_row;

  PERFORM public.release_video_provider_quota(v_row.reservation_key, 'runway_evaluation_completed', 'completed', NULL);
  RETURN pg_catalog.to_jsonb(v_row);
END;
$$;


CREATE OR REPLACE FUNCTION public.fail_runway_video_evaluation_after_submit(
  p_evaluation_id UUID,
  p_lease_token UUID,
  p_final_status TEXT,
  p_error_code TEXT,
  p_error_message TEXT,
  p_error_details JSONB DEFAULT '{}'::JSONB,
  p_provider_status TEXT DEFAULT NULL,
  p_actual_credits INTEGER DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_row public.runway_video_evaluations%ROWTYPE;
BEGIN
  IF p_final_status NOT IN ('failed', 'cancelled') THEN
    RAISE EXCEPTION 'runway evaluation final status is invalid';
  END IF;
  UPDATE public.runway_video_evaluations AS evaluation
  SET status = p_final_status,
      error_code = COALESCE(NULLIF(pg_catalog.btrim(p_error_code), ''), 'provider_failed'),
      error_message = pg_catalog.left(COALESCE(p_error_message, ''), 1000),
      error_details = COALESCE(p_error_details, '{}'::JSONB),
      last_provider_status = COALESCE(p_provider_status, evaluation.last_provider_status),
      actual_credits = COALESCE(p_actual_credits, evaluation.actual_credits),
      completed_at = now(),
      next_poll_at = NULL,
      lease_owner = NULL,
      lease_token = NULL,
      lease_expires_at = NULL
  WHERE evaluation.id = p_evaluation_id
    AND evaluation.lease_token = p_lease_token
    AND evaluation.status IN ('submitted', 'processing')
  RETURNING * INTO v_row;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'runway_evaluation_conflict: failure lease was lost';
  END IF;
  PERFORM public.release_video_provider_quota(v_row.reservation_key, v_row.error_code, 'failed', v_row.error_code);
  RETURN pg_catalog.to_jsonb(v_row);
END;
$$;


REVOKE ALL ON FUNCTION public.create_runway_video_evaluation(JSONB, INTEGER, INTEGER, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_runway_video_evaluation(JSONB, INTEGER, INTEGER, INTEGER)
  TO service_role;
REVOKE ALL ON FUNCTION public.mark_runway_video_evaluation_submitting(UUID)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.mark_runway_video_evaluation_submitting(UUID)
  TO service_role;
REVOKE ALL ON FUNCTION public.acknowledge_runway_video_evaluation(UUID, TEXT, NUMERIC, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.acknowledge_runway_video_evaluation(UUID, TEXT, NUMERIC, TEXT)
  TO service_role;
REVOKE ALL ON FUNCTION public.fail_runway_video_evaluation_before_submit(UUID, TEXT, TEXT, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.fail_runway_video_evaluation_before_submit(UUID, TEXT, TEXT, JSONB)
  TO service_role;
REVOKE ALL ON FUNCTION public.mark_runway_video_evaluation_submission_unknown(UUID, TEXT, TEXT, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.mark_runway_video_evaluation_submission_unknown(UUID, TEXT, TEXT, JSONB)
  TO service_role;
REVOKE ALL ON FUNCTION public.reconcile_stale_runway_video_evaluations(INTEGER, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reconcile_stale_runway_video_evaluations(INTEGER, INTEGER)
  TO service_role;
REVOKE ALL ON FUNCTION public.claim_runway_video_evaluations(TEXT, INTEGER, INTEGER, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_runway_video_evaluations(TEXT, INTEGER, INTEGER, TEXT, TEXT)
  TO service_role;
REVOKE ALL ON FUNCTION public.record_runway_video_evaluation_poll(UUID, UUID, TEXT, NUMERIC, TIMESTAMPTZ, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_runway_video_evaluation_poll(UUID, UUID, TEXT, NUMERIC, TIMESTAMPTZ, JSONB)
  TO service_role;
REVOKE ALL ON FUNCTION public.complete_runway_video_evaluation(UUID, UUID, JSONB, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.complete_runway_video_evaluation(UUID, UUID, JSONB, INTEGER)
  TO service_role;
REVOKE ALL ON FUNCTION public.fail_runway_video_evaluation_after_submit(UUID, UUID, TEXT, TEXT, TEXT, JSONB, TEXT, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.fail_runway_video_evaluation_after_submit(UUID, UUID, TEXT, TEXT, TEXT, JSONB, TEXT, INTEGER)
  TO service_role;

NOTIFY pgrst, 'reload schema';
