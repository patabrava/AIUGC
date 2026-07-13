-- Lease-fenced Semantic UGC worker transactions and paid quota accounting.

ALTER TABLE public.semantic_video_runs
  ADD COLUMN IF NOT EXISTS lease_token UUID,
  ADD COLUMN IF NOT EXISTS artifact_manifest JSONB NOT NULL DEFAULT '{}'::JSONB,
  ADD COLUMN IF NOT EXISTS max_submission_count INTEGER,
  ADD COLUMN IF NOT EXISTS max_estimated_cost_usd NUMERIC(12, 4),
  ADD COLUMN IF NOT EXISTS reserved_submission_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS reserved_estimated_cost_usd NUMERIC(12, 4) NOT NULL DEFAULT 0;

ALTER TABLE public.semantic_video_takes
  ADD COLUMN IF NOT EXISTS approval_id UUID REFERENCES public.semantic_video_approvals(id) ON DELETE RESTRICT,
  ADD COLUMN IF NOT EXISTS quota_reservation_key UUID,
  ADD COLUMN IF NOT EXISTS quota_state TEXT NOT NULL DEFAULT 'unreserved',
  ADD COLUMN IF NOT EXISTS quota_cost_usd NUMERIC(12, 4),
  ADD COLUMN IF NOT EXISTS operation_accepted_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS submission_error JSONB,
  ADD COLUMN IF NOT EXISTS provider_video_uri TEXT;

ALTER TABLE public.semantic_video_approvals
  ADD COLUMN IF NOT EXISTS contract_snapshot JSONB NOT NULL DEFAULT '{}'::JSONB;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conname = 'semantic_video_runs_lease_complete_check'
      AND conrelid = 'public.semantic_video_runs'::pg_catalog.regclass
  ) THEN
    ALTER TABLE public.semantic_video_runs
      ADD CONSTRAINT semantic_video_runs_lease_complete_check CHECK (
        (lease_owner IS NULL AND lease_expires_at IS NULL AND lease_token IS NULL)
        OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND lease_token IS NOT NULL)
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conname = 'semantic_video_runs_worker_caps_check'
      AND conrelid = 'public.semantic_video_runs'::pg_catalog.regclass
  ) THEN
    ALTER TABLE public.semantic_video_runs
      ADD CONSTRAINT semantic_video_runs_worker_caps_check CHECK (
        (max_submission_count IS NULL OR max_submission_count >= 0)
        AND (
          max_estimated_cost_usd IS NULL
          OR (
            max_estimated_cost_usd >= 0
            AND max_estimated_cost_usd::TEXT NOT IN ('NaN', 'Infinity', '-Infinity')
          )
        )
        AND reserved_submission_count >= 0
        AND reserved_estimated_cost_usd >= 0
        AND reserved_estimated_cost_usd::TEXT NOT IN ('NaN', 'Infinity', '-Infinity')
        AND (max_submission_count IS NULL OR reserved_submission_count <= max_submission_count)
        AND (
          max_estimated_cost_usd IS NULL
          OR reserved_estimated_cost_usd <= max_estimated_cost_usd
        )
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conname = 'semantic_video_takes_quota_state_check'
      AND conrelid = 'public.semantic_video_takes'::pg_catalog.regclass
  ) THEN
    ALTER TABLE public.semantic_video_takes
      ADD CONSTRAINT semantic_video_takes_quota_state_check CHECK (
        quota_state IN ('unreserved', 'reserved', 'consumed')
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conname = 'semantic_video_takes_quota_contract_check'
      AND conrelid = 'public.semantic_video_takes'::pg_catalog.regclass
  ) THEN
    ALTER TABLE public.semantic_video_takes
      ADD CONSTRAINT semantic_video_takes_quota_contract_check CHECK (
        (
          quota_state = 'unreserved'
          AND quota_reservation_key IS NULL
          AND quota_cost_usd IS NULL
        )
        OR (
          quota_state IN ('reserved', 'consumed')
          AND quota_reservation_key IS NOT NULL
          AND quota_cost_usd IS NOT NULL
          AND quota_cost_usd > 0
          AND quota_cost_usd::TEXT NOT IN ('NaN', 'Infinity', '-Infinity')
        )
      );
  END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS semantic_video_takes_quota_reservation_key_unique
  ON public.semantic_video_takes (quota_reservation_key)
  WHERE quota_reservation_key IS NOT NULL;

CREATE OR REPLACE FUNCTION public.prepare_semantic_video_approval_contract()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  NEW.contract_snapshot := pg_catalog.jsonb_build_object(
    'approval_type', NEW.approval_type,
    'run_revision', NEW.run_revision,
    'contract_hash', NEW.contract_hash,
    'approved_take_indexes', NEW.approved_take_indexes,
    'approved_provider_seconds', NEW.approved_provider_seconds,
    'quota_units', NEW.quota_units,
    'estimated_cost_usd', NEW.estimated_cost_usd::TEXT
  );
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS semantic_video_approvals_prepare_contract
  ON public.semantic_video_approvals;
CREATE TRIGGER semantic_video_approvals_prepare_contract
BEFORE INSERT ON public.semantic_video_approvals
FOR EACH ROW
EXECUTE FUNCTION public.prepare_semantic_video_approval_contract();

CREATE OR REPLACE FUNCTION public.apply_semantic_video_approval_quota()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  IF NEW.estimated_cost_usd::TEXT IN ('NaN', 'Infinity', '-Infinity') THEN
    RAISE EXCEPTION 'semantic video approval cost must be finite';
  END IF;

  UPDATE public.semantic_video_runs AS run
  SET max_submission_count = COALESCE(run.max_submission_count, 0) + NEW.quota_units,
      max_estimated_cost_usd = COALESCE(run.max_estimated_cost_usd, 0) + NEW.estimated_cost_usd
  WHERE run.id = NEW.run_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'semantic video approval run does not exist';
  END IF;

  UPDATE public.semantic_video_takes AS take
  SET approval_id = NEW.id
  WHERE take.run_id = NEW.run_id
    AND take.approval_id IS NULL
    AND take.take_index = ANY(NEW.approved_take_indexes)
    AND (
      (NEW.approval_type = 'initial_plan' AND take.attempt = 1)
      OR (
        NEW.approval_type = 'retry'
        AND take.attempt > 1
        AND take.created_at >= pg_catalog.transaction_timestamp()
      )
    );
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS semantic_video_approvals_apply_quota
  ON public.semantic_video_approvals;
CREATE TRIGGER semantic_video_approvals_apply_quota
AFTER INSERT ON public.semantic_video_approvals
FOR EACH ROW
EXECUTE FUNCTION public.apply_semantic_video_approval_quota();

UPDATE public.semantic_video_approvals AS approval
SET contract_snapshot = pg_catalog.jsonb_build_object(
  'approval_type', approval.approval_type,
  'run_revision', approval.run_revision,
  'contract_hash', approval.contract_hash,
  'approved_take_indexes', approval.approved_take_indexes,
  'approved_provider_seconds', approval.approved_provider_seconds,
  'quota_units', approval.quota_units,
  'estimated_cost_usd', approval.estimated_cost_usd::TEXT
)
WHERE approval.contract_snapshot = '{}'::JSONB;

WITH approval_totals AS (
  SELECT approval.run_id,
         COALESCE(pg_catalog.sum(approval.quota_units), 0)::INTEGER AS quota_units,
         COALESCE(pg_catalog.sum(approval.estimated_cost_usd), 0)::NUMERIC AS estimated_cost
  FROM public.semantic_video_approvals AS approval
  GROUP BY approval.run_id
)
UPDATE public.semantic_video_runs AS run
SET max_submission_count = totals.quota_units,
    max_estimated_cost_usd = totals.estimated_cost
FROM approval_totals AS totals
WHERE run.id = totals.run_id
  AND run.reserved_submission_count = 0;

UPDATE public.semantic_video_takes AS take
SET approval_id = approval.id
FROM public.semantic_video_approvals AS approval
WHERE take.approval_id IS NULL
  AND approval.run_id = take.run_id
  AND take.take_index = ANY(approval.approved_take_indexes)
  AND (
    (approval.approval_type = 'initial_plan' AND take.attempt = 1)
    OR (
      approval.approval_type = 'retry'
      AND take.attempt > 1
      AND approval.id = (
        SELECT candidate.id
        FROM public.semantic_video_approvals AS candidate
        WHERE candidate.run_id = take.run_id
          AND candidate.approval_type = 'retry'
          AND take.take_index = ANY(candidate.approved_take_indexes)
        ORDER BY candidate.created_at DESC, candidate.id DESC
        LIMIT 1
      )
    )
  );

CREATE OR REPLACE FUNCTION public.require_semantic_video_worker_lease(
  p_run_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_expected_stage TEXT DEFAULT NULL
)
RETURNS public.semantic_video_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
BEGIN
  SELECT run.*
  INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;
  IF NOT FOUND
     OR NULLIF(pg_catalog.btrim(p_worker_id), '') IS NULL
     OR p_lease_token IS NULL
     OR locked_run.lease_owner IS DISTINCT FROM p_worker_id
     OR locked_run.lease_token IS DISTINCT FROM p_lease_token
     OR locked_run.lease_expires_at IS NULL
     OR locked_run.lease_expires_at <= now()
     OR (
       p_expected_stage IS NOT NULL
       AND locked_run.stage IS DISTINCT FROM p_expected_stage
     ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: worker lease is stale or fenced';
  END IF;
  RETURN locked_run;
END;
$$;

DROP FUNCTION IF EXISTS public.claim_semantic_video_run(TEXT, INTEGER);
CREATE OR REPLACE FUNCTION public.claim_semantic_video_run(
  worker_id TEXT,
  lease_seconds INTEGER,
  requested_run_id UUID DEFAULT NULL
)
RETURNS SETOF public.semantic_video_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  IF NULLIF(pg_catalog.btrim(worker_id), '') IS NULL THEN
    RAISE EXCEPTION 'worker_id is required';
  END IF;
  IF lease_seconds IS NULL OR lease_seconds <= 0 OR lease_seconds > 3600 THEN
    RAISE EXCEPTION 'lease_seconds must be between 1 and 3600';
  END IF;

  RETURN QUERY
  WITH candidate AS (
    SELECT run.id
    FROM public.semantic_video_runs AS run
    WHERE run.stage IN (
      'generating', 'transcript_qa', 'identity_qa', 'voice_qa',
      'acoustic_qa', 'composing', 'uploading'
    )
      AND (requested_run_id IS NULL OR run.id = requested_run_id)
      AND (run.lease_expires_at IS NULL OR run.lease_expires_at <= now())
      AND (
        run.stage <> 'generating'
        OR (
          EXISTS (
            SELECT 1
            FROM (
              SELECT DISTINCT ON (take.take_index) take.submission_state
              FROM public.semantic_video_takes AS take
              WHERE take.run_id = run.id
              ORDER BY take.take_index, take.attempt DESC
            ) AS latest
          )
          AND NOT EXISTS (
            SELECT 1
            FROM (
              SELECT DISTINCT ON (take.take_index) take.submission_state
              FROM public.semantic_video_takes AS take
              WHERE take.run_id = run.id
              ORDER BY take.take_index, take.attempt DESC
            ) AS latest
            WHERE latest.submission_state IN ('intent_persisted', 'submission_unknown')
          )
          AND (
            EXISTS (
              SELECT 1
              FROM (
                SELECT DISTINCT ON (take.take_index) take.submission_state
                FROM public.semantic_video_takes AS take
                WHERE take.run_id = run.id
                ORDER BY take.take_index, take.attempt DESC
              ) AS latest
              WHERE latest.submission_state IN ('planned', 'reserved', 'submitted')
            )
            OR NOT EXISTS (
              SELECT 1
              FROM (
                SELECT DISTINCT ON (take.take_index) take.submission_state
                FROM public.semantic_video_takes AS take
                WHERE take.run_id = run.id
                ORDER BY take.take_index, take.attempt DESC
              ) AS latest
              WHERE latest.submission_state IS DISTINCT FROM 'completed'
            )
          )
        )
      )
    ORDER BY run.updated_at, run.created_at, run.id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
  ), claimed AS (
    UPDATE public.semantic_video_runs AS run
    SET lease_owner = worker_id,
        lease_token = pg_catalog.gen_random_uuid(),
        lease_expires_at = now() + pg_catalog.make_interval(secs => lease_seconds),
        revision = run.revision + 1
    FROM candidate
    WHERE run.id = candidate.id
    RETURNING run.*
  )
  SELECT * FROM claimed;
END;
$$;

CREATE OR REPLACE FUNCTION public.reserve_semantic_video_submission(
  p_run_id UUID,
  p_take_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  locked_take public.semantic_video_takes%ROWTYPE;
  approval public.semantic_video_approvals%ROWTYPE;
  price NUMERIC;
  take_cost NUMERIC;
BEGIN
  SELECT * INTO locked_run
  FROM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, 'generating'
  );
  SELECT take.* INTO locked_take
  FROM public.semantic_video_takes AS take
  WHERE take.id = p_take_id AND take.run_id = p_run_id
  FOR UPDATE;
  IF NOT FOUND
     OR locked_take.submission_state IS DISTINCT FROM 'planned'
     OR locked_take.quota_state IS DISTINCT FROM 'unreserved'
     OR locked_take.approval_id IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: take is not an approved unsubmitted request';
  END IF;
  SELECT item.* INTO approval
  FROM public.semantic_video_approvals AS item
  WHERE item.id = locked_take.approval_id
  FOR SHARE;
  IF NOT FOUND
     OR approval.run_id IS DISTINCT FROM p_run_id
     OR NOT locked_take.take_index = ANY(approval.approved_take_indexes)
     OR (
       approval.approval_type = 'initial_plan'
       AND approval.contract_hash IS DISTINCT FROM locked_run.plan_hash
     )
     OR approval.approval_type NOT IN ('initial_plan', 'retry') THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: paid request approval is missing or stale';
  END IF;
  IF pg_catalog.jsonb_typeof(locked_run.plan_snapshot) IS DISTINCT FROM 'object'
     OR pg_catalog.jsonb_typeof(locked_run.plan_snapshot -> 'price_per_provider_second_usd') IS DISTINCT FROM 'string' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: paid request price contract is missing';
  END IF;
  price := NULLIF(pg_catalog.btrim(locked_run.plan_snapshot ->> 'price_per_provider_second_usd'), '')::NUMERIC;
  take_cost := pg_catalog.round(price * locked_take.provider_duration_seconds, 2);
  IF price IS NULL OR price <= 0
     OR price::TEXT IN ('NaN', 'Infinity', '-Infinity')
     OR take_cost <= 0
     OR take_cost::TEXT IN ('NaN', 'Infinity', '-Infinity')
     OR locked_run.max_submission_count IS NULL
     OR locked_run.max_estimated_cost_usd IS NULL
     OR locked_run.max_estimated_cost_usd::TEXT IN ('NaN', 'Infinity', '-Infinity')
     OR locked_run.reserved_submission_count + 1 > locked_run.max_submission_count
     OR locked_run.reserved_estimated_cost_usd + take_cost > locked_run.max_estimated_cost_usd THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: paid submission budget or quota is exhausted';
  END IF;

  UPDATE public.semantic_video_runs AS run
  SET reserved_submission_count = run.reserved_submission_count + 1,
      reserved_estimated_cost_usd = run.reserved_estimated_cost_usd + take_cost,
      revision = run.revision + 1
  WHERE run.id = p_run_id
    AND run.lease_owner = p_worker_id
    AND run.lease_token = p_lease_token;
  UPDATE public.semantic_video_takes AS take
  SET submission_state = 'reserved',
      quota_state = 'reserved',
      quota_reservation_key = pg_catalog.gen_random_uuid(),
      quota_cost_usd = take_cost
  WHERE take.id = p_take_id
  RETURNING take.* INTO locked_take;
  RETURN pg_catalog.to_jsonb(locked_take);
END;
$$;

CREATE OR REPLACE FUNCTION public.persist_semantic_video_submission_intent(
  p_run_id UUID,
  p_take_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_request_hash TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE updated_take public.semantic_video_takes%ROWTYPE;
BEGIN
  PERFORM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, 'generating'
  );
  UPDATE public.semantic_video_takes AS take
  SET submission_state = 'intent_persisted',
      submission_intent_at = now()
  WHERE take.id = p_take_id
    AND take.run_id = p_run_id
    AND take.submission_state = 'reserved'
    AND take.quota_state = 'reserved'
    AND take.request_hash = p_request_hash
  RETURNING take.* INTO updated_take;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'semantic_video_conflict: submission intent contract changed';
  END IF;
  RETURN pg_catalog.to_jsonb(updated_take);
END;
$$;

CREATE OR REPLACE FUNCTION public.persist_semantic_video_accepted_operation(
  p_run_id UUID,
  p_take_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_operation_id TEXT,
  p_provider_model TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE updated_take public.semantic_video_takes%ROWTYPE;
BEGIN
  PERFORM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, 'generating'
  );
  IF NULLIF(pg_catalog.btrim(p_operation_id), '') IS NULL
     OR NULLIF(pg_catalog.btrim(p_provider_model), '') IS NULL THEN
    RAISE EXCEPTION 'accepted semantic video operation contract is incomplete';
  END IF;
  UPDATE public.semantic_video_takes AS take
  SET submission_state = 'submitted',
      quota_state = 'consumed',
      operation_id = p_operation_id,
      operation_accepted_at = now(),
      provider_model = p_provider_model,
      submission_error = NULL
  WHERE take.id = p_take_id
    AND take.run_id = p_run_id
    AND take.submission_state = 'intent_persisted'
    AND take.quota_state = 'reserved'
    AND take.operation_id IS NULL
  RETURNING take.* INTO updated_take;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'semantic_video_conflict: accepted operation was already persisted or fenced';
  END IF;
  RETURN pg_catalog.to_jsonb(updated_take);
END;
$$;

CREATE OR REPLACE FUNCTION public.persist_semantic_video_submission_unknown(
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
DECLARE updated_take public.semantic_video_takes%ROWTYPE;
BEGIN
  PERFORM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, 'generating'
  );
  IF pg_catalog.jsonb_typeof(p_error) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'submission unknown requires an error envelope';
  END IF;
  UPDATE public.semantic_video_takes AS take
  SET submission_state = 'submission_unknown',
      quota_state = 'consumed',
      submission_error = p_error
  WHERE take.id = p_take_id
    AND take.run_id = p_run_id
    AND take.submission_state = 'intent_persisted'
    AND take.quota_state = 'reserved'
    AND take.operation_id IS NULL
  RETURNING take.* INTO updated_take;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'semantic_video_conflict: unknown submission was already reconciled or fenced';
  END IF;
  RETURN pg_catalog.to_jsonb(updated_take);
END;
$$;

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
      submission_error = p_error
  WHERE take.id = p_take_id
    AND take.run_id = p_run_id
    AND take.submission_state IN ('intent_persisted', 'submitted')
  ;
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

CREATE OR REPLACE FUNCTION public.persist_semantic_video_completed_take(
  p_run_id UUID,
  p_take_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_provider_video_uri TEXT,
  p_raw_artifact_uri TEXT,
  p_raw_artifact_sha256 TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE updated_take public.semantic_video_takes%ROWTYPE;
BEGIN
  PERFORM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, 'generating'
  );
  IF NULLIF(pg_catalog.btrim(p_provider_video_uri), '') IS NULL
     OR NULLIF(pg_catalog.btrim(p_raw_artifact_uri), '') IS NULL
     OR p_raw_artifact_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'completed semantic video take artifact contract is invalid';
  END IF;
  UPDATE public.semantic_video_takes AS take
  SET submission_state = 'completed',
      provider_video_uri = p_provider_video_uri,
      raw_artifact_uri = p_raw_artifact_uri,
      raw_artifact_sha256 = p_raw_artifact_sha256,
      submission_error = NULL
  WHERE take.id = p_take_id
    AND take.run_id = p_run_id
    AND take.submission_state = 'submitted'
    AND take.quota_state = 'consumed'
    AND take.operation_id IS NOT NULL
  RETURNING take.* INTO updated_take;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'semantic_video_conflict: completed take operation is not accepted';
  END IF;
  RETURN pg_catalog.to_jsonb(updated_take);
END;
$$;

CREATE OR REPLACE FUNCTION public.advance_semantic_video_stage(
  p_run_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_expected_stage TEXT,
  p_next_stage TEXT,
  p_artifacts JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE updated_run public.semantic_video_runs%ROWTYPE;
BEGIN
  PERFORM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, p_expected_stage
  );
  IF pg_catalog.jsonb_typeof(p_artifacts) IS DISTINCT FROM 'object'
     OR (p_expected_stage, p_next_stage) NOT IN (
       ('generating', 'transcript_qa'),
       ('transcript_qa', 'identity_qa'),
       ('identity_qa', 'voice_qa'),
       ('voice_qa', 'acoustic_qa'),
       ('acoustic_qa', 'composing'),
       ('composing', 'uploading')
     ) THEN
    RAISE EXCEPTION 'semantic video stage transition contract is invalid';
  END IF;
  IF p_expected_stage = 'generating' AND EXISTS (
    SELECT 1
    FROM (
      SELECT DISTINCT ON (take.take_index) take.submission_state
      FROM public.semantic_video_takes AS take
      WHERE take.run_id = p_run_id
      ORDER BY take.take_index, take.attempt DESC
    ) AS latest
    WHERE latest.submission_state IS DISTINCT FROM 'completed'
  ) THEN
    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'semantic_video_conflict: generation is not complete';
  END IF;
  UPDATE public.semantic_video_runs AS run
  SET stage = p_next_stage,
      artifact_manifest = run.artifact_manifest || p_artifacts,
      failure_envelope = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;
  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

CREATE OR REPLACE FUNCTION public.require_semantic_video_retry_approval(
  p_run_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_expected_stage TEXT,
  p_failed_take_indexes INTEGER[],
  p_evidence JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE updated_run public.semantic_video_runs%ROWTYPE;
BEGIN
  PERFORM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, p_expected_stage
  );
  IF p_expected_stage NOT IN ('transcript_qa', 'identity_qa', 'voice_qa', 'acoustic_qa')
     OR COALESCE(pg_catalog.cardinality(p_failed_take_indexes), 0) = 0
     OR pg_catalog.jsonb_typeof(p_evidence) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'semantic video retry evidence contract is invalid';
  END IF;
  WITH latest AS (
    SELECT DISTINCT ON (take.take_index) take.id, take.take_index
    FROM public.semantic_video_takes AS take
    WHERE take.run_id = p_run_id
    ORDER BY take.take_index, take.attempt DESC
  )
  UPDATE public.semantic_video_takes AS take
  SET submission_state = 'qa_failed',
      retry_guidance = p_evidence
  FROM latest
  WHERE take.id = latest.id
    AND latest.take_index = ANY(p_failed_take_indexes)
    AND take.submission_state = 'completed';
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'semantic_video_conflict: no completed retry targets were found';
  END IF;
  UPDATE public.semantic_video_runs AS run
  SET stage = 'retry_approval_required',
      artifact_manifest = run.artifact_manifest || p_evidence,
      failure_envelope = pg_catalog.jsonb_build_object(
        'code', 'qa_failed',
        'stage', p_expected_stage,
        'failed_take_indexes', p_failed_take_indexes
      ),
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;
  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

CREATE OR REPLACE FUNCTION public.release_semantic_video_lease(
  p_run_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE updated_run public.semantic_video_runs%ROWTYPE;
BEGIN
  PERFORM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, NULL
  );
  UPDATE public.semantic_video_runs AS run
  SET lease_owner = NULL,
      lease_token = NULL,
      lease_expires_at = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
    AND run.lease_owner = p_worker_id
    AND run.lease_token = p_lease_token
  RETURNING run.* INTO updated_run;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'semantic_video_conflict: worker lease release was fenced';
  END IF;
  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

CREATE OR REPLACE FUNCTION public.complete_semantic_video_run(
  p_run_id UUID,
  p_worker_id TEXT,
  p_lease_token UUID,
  p_final_video_uri TEXT,
  p_final_video_sha256 TEXT,
  p_final_caption_uri TEXT,
  p_final_caption_sha256 TEXT,
  p_artifact_manifest JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE locked_run public.semantic_video_runs%ROWTYPE;
DECLARE updated_run public.semantic_video_runs%ROWTYPE;
BEGIN
  SELECT * INTO locked_run
  FROM public.require_semantic_video_worker_lease(
    p_run_id, p_worker_id, p_lease_token, 'uploading'
  );
  IF NULLIF(pg_catalog.btrim(p_final_video_uri), '') IS NULL
     OR p_final_video_sha256 !~ '^[0-9a-f]{64}$'
     OR NULLIF(pg_catalog.btrim(p_final_caption_uri), '') IS NULL
     OR p_final_caption_sha256 !~ '^[0-9a-f]{64}$'
     OR pg_catalog.jsonb_typeof(p_artifact_manifest) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'semantic video completion artifact contract is invalid';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM (
      SELECT DISTINCT ON (take.take_index) take.submission_state
      FROM public.semantic_video_takes AS take
      WHERE take.run_id = p_run_id
      ORDER BY take.take_index, take.attempt DESC
    ) AS latest
    WHERE latest.submission_state IS DISTINCT FROM 'completed'
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: run still has incomplete latest takes';
  END IF;
  UPDATE public.semantic_video_runs AS run
  SET stage = 'completed',
      final_video_uri = p_final_video_uri,
      final_video_sha256 = p_final_video_sha256,
      final_caption_uri = p_final_caption_uri,
      final_caption_sha256 = p_final_caption_sha256,
      artifact_manifest = run.artifact_manifest || p_artifact_manifest,
      failure_envelope = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;
  UPDATE public.posts AS post
  SET video_url = p_final_caption_uri,
      video_status = 'caption_completed',
      video_metadata = COALESCE(post.video_metadata, '{}'::JSONB) || pg_catalog.jsonb_build_object(
        'semantic_video_run_id', p_run_id,
        'raw_video_url', p_final_video_uri,
        'raw_video_sha256', p_final_video_sha256,
        'caption_video_url', p_final_caption_uri,
        'caption_video_sha256', p_final_caption_sha256
      )
  WHERE post.id = locked_run.post_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'semantic video completion post does not exist';
  END IF;
  RETURN pg_catalog.jsonb_build_object(
    'run', pg_catalog.to_jsonb(updated_run),
    'post_id', locked_run.post_id,
    'video_status', 'caption_completed'
  );
END;
$$;

REVOKE INSERT, UPDATE, DELETE ON TABLE public.semantic_video_runs FROM service_role;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.semantic_video_takes FROM service_role;

REVOKE ALL ON FUNCTION public.require_semantic_video_worker_lease(UUID, TEXT, UUID, TEXT) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER, UUID) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER, UUID) TO service_role;

DO $$
DECLARE signature TEXT;
BEGIN
  FOREACH signature IN ARRAY ARRAY[
    'public.reserve_semantic_video_submission(uuid,uuid,text,uuid)',
    'public.persist_semantic_video_submission_intent(uuid,uuid,text,uuid,text)',
    'public.persist_semantic_video_accepted_operation(uuid,uuid,text,uuid,text,text)',
    'public.persist_semantic_video_submission_unknown(uuid,uuid,text,uuid,jsonb)',
    'public.persist_semantic_video_provider_failure(uuid,uuid,text,uuid,jsonb)',
    'public.persist_semantic_video_completed_take(uuid,uuid,text,uuid,text,text,text)',
    'public.advance_semantic_video_stage(uuid,text,uuid,text,text,jsonb)',
    'public.require_semantic_video_retry_approval(uuid,text,uuid,text,integer[],jsonb)',
    'public.release_semantic_video_lease(uuid,text,uuid)',
    'public.complete_semantic_video_run(uuid,text,uuid,text,text,text,text,jsonb)'
  ] LOOP
    EXECUTE 'REVOKE ALL ON FUNCTION ' || signature || ' FROM PUBLIC, anon, authenticated';
    EXECUTE 'GRANT EXECUTE ON FUNCTION ' || signature || ' TO service_role';
  END LOOP;
END;
$$;
