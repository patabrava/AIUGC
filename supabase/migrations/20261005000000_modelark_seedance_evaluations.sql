-- Direct BytePlus ModelArk with rolling-compatible Runway history and RPC-only writes.
-- The legacy table/RPC names remain; provider/model/quota tuples are fenced together.
BEGIN;
ALTER TABLE public.runway_video_evaluations
  DROP CONSTRAINT runway_video_evaluations_provider_check,
  DROP CONSTRAINT runway_video_evaluations_provider_model_check,
  DROP CONSTRAINT runway_video_evaluations_quota_provider_check,
  DROP CONSTRAINT runway_video_evaluations_reservation_key_check,
  DROP CONSTRAINT runway_video_evaluations_requested_ratio_check;
ALTER TABLE public.runway_video_evaluations ADD CONSTRAINT seedance_host_contract CHECK (
  (provider = 'runway' AND provider_model = 'seedance2_5'
    AND quota_provider = 'runway_seedance_2_5' AND reservation_key LIKE 'runway_seedance_2_5:%'
    AND ((requested_resolution = '480p' AND requested_ratio = '480:854')
      OR (requested_resolution = '720p' AND requested_ratio = '720:1280')
      OR (requested_resolution = '1080p' AND requested_ratio = '1080:1920')))
  OR (provider = 'modelark' AND provider_model = 'dreamina-seedance-2-5-260628'
    AND quota_provider = 'modelark_seedance_2_5' AND reservation_key LIKE 'modelark_seedance_2_5:%'
    AND ((requested_resolution = '480p' AND requested_ratio = '480:854')
      OR (requested_resolution = '720p' AND requested_ratio = '720:1280')))
);
COMMENT ON COLUMN public.runway_video_evaluations.estimated_credits IS
  'Host-scoped budget units: Runway credits; ModelArk integer USD microdollars.';

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
  v_provider TEXT;
  v_model TEXT;
  v_quota_provider TEXT;
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

  v_provider := COALESCE(p_payload ->> 'provider', 'runway');
  v_model := p_payload ->> 'provider_model';
  v_quota_provider := CASE v_provider WHEN 'runway' THEN 'runway_seedance_2_5'
    WHEN 'modelark' THEN 'modelark_seedance_2_5' ELSE NULL END;
  IF v_quota_provider IS NULL
     OR (v_provider = 'runway' AND v_model IS DISTINCT FROM 'seedance2_5')
     OR (v_provider = 'modelark' AND v_model IS DISTINCT FROM 'dreamina-seedance-2-5-260628')
     OR (p_payload ? 'quota_provider' AND p_payload ->> 'quota_provider' IS DISTINCT FROM v_quota_provider)
     OR COALESCE(p_payload ->> 'reservation_key', '') NOT LIKE v_quota_provider || ':%' THEN
    RAISE EXCEPTION 'seedance provider/model/quota contract is invalid';
  END IF;
  v_post_id := (p_payload ->> 'post_id')::UUID;
  v_run_id := (p_payload ->> 'semantic_run_id')::UUID;
  v_reservation_key := p_payload ->> 'reservation_key';
  v_estimated_credits := (p_payload ->> 'estimated_credits')::INTEGER;
  IF v_post_id IS NULL OR v_run_id IS NULL OR v_reservation_key IS NULL OR COALESCE(v_estimated_credits, 0) <= 0 THEN
    RAISE EXCEPTION 'runway evaluation admission payload is incomplete';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('seedance_video_evaluations:admission:' || v_provider));

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
  WHERE evaluation.provider = v_provider
    AND evaluation.status IN ('reserved', 'submitting', 'submitted', 'processing', 'submission_unknown');
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
  FROM public.runway_video_evaluations AS evaluation
  WHERE evaluation.provider = v_provider;
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
    v_quota_provider,
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
    provider,
    quota_provider,
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
    v_provider,
    v_quota_provider,
    v_model,
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
    WHERE evaluation.provider = 'runway'
      AND evaluation.status IN ('submitted', 'processing')
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

CREATE OR REPLACE FUNCTION public.claim_seedance_video_evaluations(
  p_provider TEXT,
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
  IF p_provider IS NULL OR p_provider NOT IN ('runway', 'modelark') THEN
    RAISE EXCEPTION 'invalid Seedance provider';
  END IF;
  IF COALESCE(pg_catalog.btrim(p_worker_id), '') = '' THEN
    RAISE EXCEPTION 'runway evaluation worker id is required';
  END IF;
  RETURN QUERY
  WITH candidates AS (
    SELECT evaluation.id
    FROM public.runway_video_evaluations AS evaluation
    WHERE evaluation.provider = p_provider
      AND evaluation.status IN ('submitted', 'processing')
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

-- The submission reservation is conservative. Final returned token usage reconciles
-- ModelArk spend, including zero for terminal provider failures, inside the same transaction.
CREATE OR REPLACE FUNCTION public.settle_modelark_evaluation_quota()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_quota public.video_provider_quota_reservations%ROWTYPE;
  v_units INTEGER;
BEGIN
  IF NEW.provider <> 'modelark' OR NEW.status NOT IN ('completed','failed','cancelled')
     OR NEW.actual_credits IS NULL
     OR (OLD.status = NEW.status AND OLD.actual_credits IS NOT DISTINCT FROM NEW.actual_credits) THEN
    RETURN NEW;
  END IF;
  SELECT * INTO v_quota FROM public.video_provider_quota_reservations
    WHERE reservation_key = NEW.reservation_key FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ModelArk quota reservation missing'; END IF;
  v_units := NEW.actual_credits;
  UPDATE public.video_provider_quota_reservations
    SET reserved_units = GREATEST(reserved_units, v_units), consumed_units = v_units,
        released_units = GREATEST(reserved_units - v_units, 0), status = NEW.status
    WHERE id = v_quota.id;
  INSERT INTO public.video_provider_quota_events
    (reservation_id, provider, reservation_key, event_type, units, details)
    VALUES (v_quota.id, NEW.quota_provider, NEW.reservation_key,
      CASE WHEN v_units > v_quota.consumed_units THEN 'consumed' ELSE 'released' END,
      ABS(v_units - v_quota.consumed_units),
      pg_catalog.jsonb_build_object('reason','modelark_actual_usage_settlement', 'actual_usd_microdollars',v_units));
  IF v_units > v_quota.reserved_units THEN
    PERFORM public.freeze_video_provider_quota(NEW.quota_provider, now() + INTERVAL '24 hours',
      'ModelArk actual spend exceeded reserved cost; review configured pricing before new paid work.');
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.settle_modelark_evaluation_quota() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER settle_modelark_evaluation_quota
AFTER UPDATE OF status, actual_credits ON public.runway_video_evaluations
FOR EACH ROW EXECUTE FUNCTION public.settle_modelark_evaluation_quota();

REVOKE ALL ON FUNCTION public.claim_seedance_video_evaluations(TEXT, TEXT, INTEGER, INTEGER, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_seedance_video_evaluations(TEXT, TEXT, INTEGER, INTEGER, TEXT, TEXT)
  TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
