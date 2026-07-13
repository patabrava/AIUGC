-- Transaction-safe Semantic UGC API boundary applied after the production schema migration.

ALTER TABLE public.semantic_video_runs
  ADD COLUMN IF NOT EXISTS candidate_reservation_owner TEXT,
  ADD COLUMN IF NOT EXISTS candidate_reservation_token UUID,
  ADD COLUMN IF NOT EXISTS candidate_reservation_expires_at TIMESTAMPTZ;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_constraint
    WHERE conname = 'semantic_video_candidate_reservation_complete_check'
      AND conrelid = 'public.semantic_video_runs'::pg_catalog.regclass
  ) THEN
    ALTER TABLE public.semantic_video_runs
      ADD CONSTRAINT semantic_video_candidate_reservation_complete_check CHECK (
        (
          candidate_reservation_owner IS NULL
          AND candidate_reservation_token IS NULL
          AND candidate_reservation_expires_at IS NULL
        )
        OR
        (
          candidate_reservation_owner IS NOT NULL
          AND candidate_reservation_token IS NOT NULL
          AND candidate_reservation_expires_at IS NOT NULL
        )
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_constraint
    WHERE conname = 'semantic_video_runs_estimated_cost_finite_check'
      AND conrelid = 'public.semantic_video_runs'::pg_catalog.regclass
  ) THEN
    ALTER TABLE public.semantic_video_runs
      ADD CONSTRAINT semantic_video_runs_estimated_cost_finite_check CHECK (
        estimated_cost_usd IS NULL
        OR estimated_cost_usd::TEXT NOT IN ('NaN', 'Infinity', '-Infinity')
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_constraint
    WHERE conname = 'semantic_video_approvals_estimated_cost_finite_check'
      AND conrelid = 'public.semantic_video_approvals'::pg_catalog.regclass
  ) THEN
    ALTER TABLE public.semantic_video_approvals
      ADD CONSTRAINT semantic_video_approvals_estimated_cost_finite_check CHECK (
        estimated_cost_usd::TEXT NOT IN ('NaN', 'Infinity', '-Infinity')
      );
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.claim_semantic_video_run(
  worker_id TEXT,
  lease_seconds INTEGER
)
RETURNS SETOF public.semantic_video_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  IF NULLIF(btrim(worker_id), '') IS NULL THEN
    RAISE EXCEPTION 'worker_id is required';
  END IF;
  IF lease_seconds IS NULL OR lease_seconds <= 0 THEN
    RAISE EXCEPTION 'lease_seconds must be positive';
  END IF;

  RETURN QUERY
  WITH candidate AS (
    SELECT run.id
    FROM public.semantic_video_runs AS run
    WHERE run.stage NOT IN ('completed', 'failed')
      AND (
        run.lease_expires_at IS NULL
        OR run.lease_expires_at <= now()
        OR run.lease_owner = worker_id
      )
    ORDER BY run.updated_at, run.created_at, run.id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
  ), claimed AS (
    UPDATE public.semantic_video_runs AS run
    SET lease_owner = worker_id,
        lease_expires_at = now() + make_interval(secs => lease_seconds),
        revision = run.revision + 1,
        updated_at = now()
    FROM candidate
    WHERE run.id = candidate.id
    RETURNING run.*
  )
  SELECT * FROM claimed;
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
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  reserved_run public.semantic_video_runs%ROWTYPE;
BEGIN
  IF p_post_id IS NULL THEN
    RAISE EXCEPTION 'semantic video candidate post id is required';
  END IF;
  IF NULLIF(pg_catalog.btrim(p_reservation_owner), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video candidate reservation owner is required';
  END IF;
  IF p_reservation_token IS NULL THEN
    RAISE EXCEPTION 'semantic video candidate reservation token is required';
  END IF;
  IF p_reservation_seconds IS NULL OR p_reservation_seconds <= 0 OR p_reservation_seconds > 3600 THEN
    RAISE EXCEPTION 'semantic video candidate reservation seconds must be between 1 and 3600';
  END IF;
  IF pg_catalog.jsonb_typeof(p_run_create) IS DISTINCT FROM 'object'
     OR p_run_create ? 'revision' THEN
    RAISE EXCEPTION 'semantic video candidate run create must be an object without revision';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM pg_catalog.jsonb_object_keys(p_run_create) AS field_name
    WHERE NOT (
      field_name = ANY (ARRAY[
        'post_id',
        'batch_id',
        'requested_duration_seconds',
        'duration_contract',
        'duration_contract_hash',
        'script_snapshot',
        'script_hash',
        'actor_identity_id',
        'actor_snapshot',
        'reference_snapshot',
        'reference_hash',
        'master_snapshot',
        'master_hash',
        'stage',
        'plan_snapshot',
        'plan_hash',
        'provider_model',
        'resolution',
        'estimated_cost_usd',
        'artifact_prefix',
        'failure_envelope'
      ]::TEXT[])
    )
  ) THEN
    RAISE EXCEPTION 'semantic video candidate run create contains unsupported fields';
  END IF;
  IF NOT p_run_create ?& ARRAY[
    'post_id',
    'batch_id',
    'requested_duration_seconds',
    'duration_contract',
    'duration_contract_hash',
    'script_snapshot',
    'script_hash',
    'actor_identity_id',
    'actor_snapshot',
    'reference_snapshot',
    'reference_hash',
    'master_snapshot',
    'master_hash',
    'stage',
    'plan_snapshot',
    'plan_hash',
    'provider_model',
    'resolution',
    'estimated_cost_usd',
    'artifact_prefix',
    'failure_envelope'
  ] THEN
    RAISE EXCEPTION 'semantic video candidate run create is incomplete';
  END IF;
  IF p_run_create ->> 'post_id' IS DISTINCT FROM p_post_id::TEXT
     OR pg_catalog.jsonb_typeof(p_run_create -> 'batch_id') IS DISTINCT FROM 'string'
     OR pg_catalog.jsonb_typeof(p_run_create -> 'requested_duration_seconds') IS DISTINCT FROM 'number'
     OR (p_run_create ->> 'requested_duration_seconds')::INTEGER < 8
     OR pg_catalog.jsonb_typeof(p_run_create -> 'duration_contract') IS DISTINCT FROM 'object'
     OR NULLIF(pg_catalog.btrim(p_run_create ->> 'duration_contract_hash'), '') IS NULL
     OR pg_catalog.jsonb_typeof(p_run_create -> 'script_snapshot') IS DISTINCT FROM 'object'
     OR NULLIF(pg_catalog.btrim(p_run_create ->> 'script_hash'), '') IS NULL
     OR pg_catalog.jsonb_typeof(p_run_create -> 'actor_snapshot') IS DISTINCT FROM 'object'
     OR pg_catalog.jsonb_typeof(p_run_create -> 'reference_snapshot') IS DISTINCT FROM 'object'
     OR NULLIF(pg_catalog.btrim(p_run_create ->> 'reference_hash'), '') IS NULL
     OR p_run_create -> 'master_snapshot' IS DISTINCT FROM '{}'::JSONB
     OR p_run_create -> 'master_hash' IS DISTINCT FROM 'null'::JSONB
     OR p_run_create ->> 'stage' IS DISTINCT FROM 'awaiting_reference_approval'
     OR p_run_create -> 'plan_snapshot' IS DISTINCT FROM 'null'::JSONB
     OR p_run_create -> 'plan_hash' IS DISTINCT FROM 'null'::JSONB
     OR p_run_create -> 'provider_model' IS DISTINCT FROM 'null'::JSONB
     OR p_run_create -> 'resolution' IS DISTINCT FROM 'null'::JSONB
     OR p_run_create -> 'estimated_cost_usd' IS DISTINCT FROM 'null'::JSONB
     OR NULLIF(pg_catalog.btrim(p_run_create ->> 'artifact_prefix'), '') IS NULL
     OR p_run_create -> 'failure_envelope' IS DISTINCT FROM 'null'::JSONB THEN
    RAISE EXCEPTION 'semantic video candidate run create is invalid';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(p_post_id::TEXT, 0)
  );

  SELECT run.*
  INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.post_id = p_post_id
    AND run.stage NOT IN ('completed', 'failed')
  ORDER BY run.created_at DESC, run.id DESC
  LIMIT 1
  FOR UPDATE;

  IF NOT FOUND THEN
    IF p_expected_revision IS NOT NULL THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: candidate run does not exist at the expected revision';
    END IF;

    INSERT INTO public.semantic_video_runs AS run (
      post_id,
      batch_id,
      requested_duration_seconds,
      duration_contract,
      duration_contract_hash,
      script_snapshot,
      script_hash,
      actor_identity_id,
      actor_snapshot,
      reference_snapshot,
      reference_hash,
      master_snapshot,
      master_hash,
      stage,
      plan_snapshot,
      plan_hash,
      provider_model,
      resolution,
      estimated_cost_usd,
      artifact_prefix,
      failure_envelope,
      candidate_reservation_owner,
      candidate_reservation_token,
      candidate_reservation_expires_at
    ) VALUES (
      p_post_id,
      (p_run_create ->> 'batch_id')::UUID,
      (p_run_create ->> 'requested_duration_seconds')::INTEGER,
      p_run_create -> 'duration_contract',
      p_run_create ->> 'duration_contract_hash',
      p_run_create -> 'script_snapshot',
      p_run_create ->> 'script_hash',
      NULLIF(p_run_create ->> 'actor_identity_id', '')::UUID,
      p_run_create -> 'actor_snapshot',
      p_run_create -> 'reference_snapshot',
      p_run_create ->> 'reference_hash',
      '{}'::JSONB,
      NULL,
      'awaiting_reference_approval',
      NULL,
      NULL,
      NULL,
      NULL,
      NULL,
      p_run_create ->> 'artifact_prefix',
      NULL,
      p_reservation_owner,
      p_reservation_token,
      pg_catalog.clock_timestamp() + pg_catalog.make_interval(secs => p_reservation_seconds)
    )
    RETURNING run.* INTO reserved_run;

    RETURN NEXT reserved_run;
    RETURN;
  END IF;

  IF locked_run.candidate_reservation_token IS NOT NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate generation attempt requires manual reconciliation';
  END IF;
  IF p_expected_revision IS NULL
     OR locked_run.revision IS DISTINCT FROM p_expected_revision THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = pg_catalog.format(
        'semantic_video_conflict: candidate revision mismatch, expected %s, actual %s',
        p_expected_revision,
        locked_run.revision
      );
  END IF;
  IF locked_run.stage IS DISTINCT FROM 'awaiting_reference_approval' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate run is not awaiting reference approval';
  END IF;
  IF p_run_create ->> 'batch_id' IS DISTINCT FROM locked_run.batch_id::TEXT
     OR (p_run_create ->> 'requested_duration_seconds')::INTEGER IS DISTINCT FROM locked_run.requested_duration_seconds
     OR p_run_create -> 'duration_contract' IS DISTINCT FROM locked_run.duration_contract
     OR p_run_create ->> 'duration_contract_hash' IS DISTINCT FROM locked_run.duration_contract_hash
     OR p_run_create -> 'script_snapshot' IS DISTINCT FROM locked_run.script_snapshot
     OR p_run_create ->> 'script_hash' IS DISTINCT FROM locked_run.script_hash
     OR NULLIF(p_run_create ->> 'actor_identity_id', '')::UUID IS DISTINCT FROM locked_run.actor_identity_id
     OR p_run_create -> 'actor_snapshot' IS DISTINCT FROM locked_run.actor_snapshot
     OR p_run_create -> 'reference_snapshot' IS DISTINCT FROM locked_run.reference_snapshot
     OR p_run_create ->> 'reference_hash' IS DISTINCT FROM locked_run.reference_hash
     OR p_run_create ->> 'artifact_prefix' IS DISTINCT FROM locked_run.artifact_prefix THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate source snapshot changed';
  END IF;

  UPDATE public.semantic_video_runs AS run
  SET candidate_reservation_owner = p_reservation_owner,
      candidate_reservation_token = p_reservation_token,
      candidate_reservation_expires_at =
        pg_catalog.clock_timestamp() + pg_catalog.make_interval(secs => p_reservation_seconds),
      revision = run.revision + 1
  WHERE run.id = locked_run.id
    AND run.revision = p_expected_revision
  RETURNING run.* INTO reserved_run;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate reservation lost its revision';
  END IF;

  RETURN NEXT reserved_run;
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
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  finalized_run public.semantic_video_runs%ROWTYPE;
BEGIN
  IF p_run_id IS NULL
     OR p_reserved_revision IS NULL
     OR p_reserved_revision < 0
     OR p_reservation_token IS NULL THEN
    RAISE EXCEPTION 'semantic video candidate finalization identity is invalid';
  END IF;
  IF pg_catalog.jsonb_typeof(p_run_update) IS DISTINCT FROM 'object'
     OR p_run_update ? 'revision' THEN
    RAISE EXCEPTION 'semantic video candidate run update must be an object without revision';
  END IF;
  IF NOT p_run_update ?& ARRAY[
    'post_id',
    'batch_id',
    'requested_duration_seconds',
    'duration_contract',
    'duration_contract_hash',
    'script_snapshot',
    'script_hash',
    'actor_identity_id',
    'actor_snapshot',
    'reference_snapshot',
    'reference_hash',
    'master_snapshot',
    'master_hash',
    'stage',
    'plan_snapshot',
    'plan_hash',
    'provider_model',
    'resolution',
    'estimated_cost_usd',
    'artifact_prefix',
    'failure_envelope'
  ] THEN
    RAISE EXCEPTION 'semantic video candidate run update is incomplete';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM pg_catalog.jsonb_object_keys(p_run_update) AS field_name
    WHERE NOT (
      field_name = ANY (ARRAY[
        'post_id',
        'batch_id',
        'requested_duration_seconds',
        'duration_contract',
        'duration_contract_hash',
        'script_snapshot',
        'script_hash',
        'actor_identity_id',
        'actor_snapshot',
        'reference_snapshot',
        'reference_hash',
        'master_snapshot',
        'master_hash',
        'stage',
        'plan_snapshot',
        'plan_hash',
        'provider_model',
        'resolution',
        'estimated_cost_usd',
        'artifact_prefix',
        'failure_envelope'
      ]::TEXT[])
    )
  ) THEN
    RAISE EXCEPTION 'semantic video candidate run update contains unsupported fields';
  END IF;
  IF p_run_update ->> 'stage' IS DISTINCT FROM 'awaiting_reference_approval'
     OR pg_catalog.jsonb_typeof(p_run_update -> 'master_snapshot') IS DISTINCT FROM 'object'
     OR pg_catalog.jsonb_typeof(p_run_update #> '{master_snapshot,candidates}') IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_array_length(p_run_update #> '{master_snapshot,candidates}') <> 3
     OR pg_catalog.jsonb_typeof(p_run_update #> '{master_snapshot,prompt_writer_system_prompt}') IS DISTINCT FROM 'string'
     OR NULLIF(pg_catalog.btrim(p_run_update #>> '{master_snapshot,prompt_writer_system_prompt}'), '') IS NULL
     OR pg_catalog.jsonb_typeof(p_run_update #> '{master_snapshot,prompt_writer_system_prompt_sha256}') IS DISTINCT FROM 'string'
     OR p_run_update #>> '{master_snapshot,prompt_writer_system_prompt_sha256}' IS DISTINCT FROM pg_catalog.encode(
       pg_catalog.sha256(
         pg_catalog.convert_to(
           p_run_update #>> '{master_snapshot,prompt_writer_system_prompt}',
           'UTF8'
         )
       ),
       'hex'
     )
     OR pg_catalog.jsonb_typeof(p_run_update #> '{master_snapshot,prompt_writer_output}') IS DISTINCT FROM 'string'
     OR NULLIF(pg_catalog.btrim(p_run_update #>> '{master_snapshot,prompt_writer_output}'), '') IS NULL
     OR pg_catalog.jsonb_typeof(p_run_update #> '{master_snapshot,composition_prompt}') IS DISTINCT FROM 'string'
     OR NULLIF(pg_catalog.btrim(p_run_update #>> '{master_snapshot,composition_prompt}'), '') IS NULL
     OR p_run_update -> 'master_hash' IS DISTINCT FROM 'null'::JSONB
     OR p_run_update -> 'plan_snapshot' IS DISTINCT FROM 'null'::JSONB
     OR p_run_update -> 'plan_hash' IS DISTINCT FROM 'null'::JSONB
     OR p_run_update -> 'provider_model' IS DISTINCT FROM 'null'::JSONB
     OR p_run_update -> 'resolution' IS DISTINCT FROM 'null'::JSONB
     OR p_run_update -> 'estimated_cost_usd' IS DISTINCT FROM 'null'::JSONB
     OR p_run_update -> 'failure_envelope' IS DISTINCT FROM 'null'::JSONB THEN
    RAISE EXCEPTION 'semantic video candidate run update is invalid';
  END IF;

  SELECT run.*
  INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate run does not exist';
  END IF;
  IF locked_run.revision IS DISTINCT FROM p_reserved_revision
     OR locked_run.stage IS DISTINCT FROM 'awaiting_reference_approval'
     OR locked_run.candidate_reservation_token IS DISTINCT FROM p_reservation_token
     OR locked_run.candidate_reservation_expires_at IS NULL
     OR locked_run.candidate_reservation_expires_at <= pg_catalog.clock_timestamp() THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate reservation is stale or no longer owned';
  END IF;
  IF p_run_update ->> 'post_id' IS DISTINCT FROM locked_run.post_id::TEXT
     OR p_run_update ->> 'batch_id' IS DISTINCT FROM locked_run.batch_id::TEXT
     OR (p_run_update ->> 'requested_duration_seconds')::INTEGER IS DISTINCT FROM locked_run.requested_duration_seconds
     OR p_run_update -> 'duration_contract' IS DISTINCT FROM locked_run.duration_contract
     OR p_run_update ->> 'duration_contract_hash' IS DISTINCT FROM locked_run.duration_contract_hash
     OR p_run_update -> 'script_snapshot' IS DISTINCT FROM locked_run.script_snapshot
     OR p_run_update ->> 'script_hash' IS DISTINCT FROM locked_run.script_hash
     OR NULLIF(p_run_update ->> 'actor_identity_id', '')::UUID IS DISTINCT FROM locked_run.actor_identity_id
     OR p_run_update -> 'actor_snapshot' IS DISTINCT FROM locked_run.actor_snapshot
     OR p_run_update -> 'reference_snapshot' IS DISTINCT FROM locked_run.reference_snapshot
     OR p_run_update ->> 'reference_hash' IS DISTINCT FROM locked_run.reference_hash
     OR p_run_update ->> 'artifact_prefix' IS DISTINCT FROM locked_run.artifact_prefix THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate source snapshot changed before finalization';
  END IF;

  UPDATE public.semantic_video_runs AS run
  SET master_snapshot = p_run_update -> 'master_snapshot',
      master_hash = NULL
  WHERE run.id = p_run_id
    AND run.revision = p_reserved_revision
    AND run.candidate_reservation_token = p_reservation_token
  RETURNING run.* INTO finalized_run;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate finalization lost its reservation';
  END IF;

  RETURN NEXT finalized_run;
END;
$$;

CREATE OR REPLACE FUNCTION public.persist_semantic_video_plan(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_run_update JSONB,
  p_initial_takes JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  take_payload JSONB;
  plan_take JSONB;
  take_position INTEGER := 0;
  input_take_count INTEGER;
  input_provider_seconds INTEGER;
  plan_price_per_second NUMERIC;
  supplied_plan_cost NUMERIC;
  supplied_run_cost NUMERIC;
  computed_plan_cost NUMERIC;
  existing_take_count INTEGER;
  affected_count INTEGER;
  returned_takes JSONB;
BEGIN
  IF p_run_id IS NULL THEN
    RAISE EXCEPTION 'semantic video plan run id is required';
  END IF;
  IF p_expected_revision IS NULL OR p_expected_revision < 0 THEN
    RAISE EXCEPTION 'semantic video plan expected revision must be non-negative';
  END IF;
  IF jsonb_typeof(p_run_update) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'semantic video plan run update must be an object';
  END IF;
  IF p_run_update ? 'revision' THEN
    RAISE EXCEPTION 'semantic video plan run update cannot set revision';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM jsonb_object_keys(p_run_update) AS field_name
    WHERE NOT (
      field_name = ANY (ARRAY[
        'post_id',
        'batch_id',
        'requested_duration_seconds',
        'duration_contract',
        'duration_contract_hash',
        'script_snapshot',
        'script_hash',
        'actor_identity_id',
        'actor_snapshot',
        'reference_snapshot',
        'reference_hash',
        'master_snapshot',
        'master_hash',
        'stage',
        'plan_snapshot',
        'plan_hash',
        'provider_model',
        'resolution',
        'estimated_cost_usd',
        'artifact_prefix'
      ]::TEXT[])
    )
  ) THEN
    RAISE EXCEPTION 'semantic video plan run update contains unsupported fields';
  END IF;
  IF NOT p_run_update ?& ARRAY[
    'post_id',
    'batch_id',
    'requested_duration_seconds',
    'duration_contract',
    'duration_contract_hash',
    'script_snapshot',
    'script_hash',
    'actor_identity_id',
    'actor_snapshot',
    'reference_snapshot',
    'reference_hash',
    'master_snapshot',
    'master_hash',
    'stage',
    'plan_snapshot',
    'plan_hash',
    'provider_model',
    'resolution',
    'estimated_cost_usd',
    'artifact_prefix'
  ] THEN
    RAISE EXCEPTION 'semantic video plan run update is incomplete';
  END IF;

  SELECT run.*
  INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: plan run does not exist';
  END IF;
  IF locked_run.revision IS DISTINCT FROM p_expected_revision THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = pg_catalog.format(
        'semantic_video_conflict: plan revision mismatch, expected %s, actual %s',
        p_expected_revision,
        locked_run.revision
      );
  END IF;
  IF locked_run.stage IS DISTINCT FROM 'awaiting_paid_approval' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = pg_catalog.format(
        'semantic_video_conflict: plan stage mismatch, expected awaiting_paid_approval, actual %s',
        locked_run.stage
      );
  END IF;
  IF p_run_update ->> 'stage' IS DISTINCT FROM 'awaiting_paid_approval' THEN
    RAISE EXCEPTION 'semantic video plan update must preserve awaiting_paid_approval stage';
  END IF;
  IF p_run_update ->> 'post_id' IS DISTINCT FROM locked_run.post_id::TEXT
     OR p_run_update ->> 'batch_id' IS DISTINCT FROM locked_run.batch_id::TEXT THEN
    RAISE EXCEPTION 'semantic video plan update cannot change run ownership';
  END IF;
  IF (p_run_update ->> 'requested_duration_seconds')::INTEGER IS DISTINCT FROM locked_run.requested_duration_seconds
     OR p_run_update ->> 'duration_contract_hash' IS DISTINCT FROM locked_run.duration_contract_hash
     OR p_run_update ->> 'script_hash' IS DISTINCT FROM locked_run.script_hash
     OR p_run_update ->> 'actor_identity_id' IS DISTINCT FROM locked_run.actor_identity_id::TEXT
     OR p_run_update ->> 'reference_hash' IS DISTINCT FROM locked_run.reference_hash
     OR p_run_update ->> 'master_hash' IS DISTINCT FROM locked_run.master_hash
     OR p_run_update -> 'duration_contract' IS DISTINCT FROM locked_run.duration_contract
     OR p_run_update -> 'script_snapshot' IS DISTINCT FROM locked_run.script_snapshot
     OR p_run_update -> 'actor_snapshot' IS DISTINCT FROM locked_run.actor_snapshot
     OR p_run_update -> 'reference_snapshot' IS DISTINCT FROM locked_run.reference_snapshot
     OR p_run_update -> 'master_snapshot' IS DISTINCT FROM locked_run.master_snapshot THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: plan source snapshot changed';
  END IF;
  IF jsonb_typeof(p_run_update -> 'duration_contract') IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_run_update -> 'script_snapshot') IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_run_update -> 'actor_snapshot') IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_run_update -> 'reference_snapshot') IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_run_update -> 'master_snapshot') IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_run_update -> 'plan_snapshot') IS DISTINCT FROM 'object'
     OR NULLIF(btrim(p_run_update ->> 'plan_hash'), '') IS NULL
     OR NULLIF(btrim(p_run_update ->> 'provider_model'), '') IS NULL
     OR NULLIF(btrim(p_run_update ->> 'resolution'), '') IS NULL
     OR NULLIF(btrim(p_run_update ->> 'artifact_prefix'), '') IS NULL
     OR (p_run_update ->> 'estimated_cost_usd')::NUMERIC < 0 THEN
    RAISE EXCEPTION 'semantic video plan run update is invalid';
  END IF;
  IF jsonb_typeof(p_initial_takes) IS DISTINCT FROM 'array'
     OR jsonb_array_length(p_initial_takes) = 0 THEN
    RAISE EXCEPTION 'semantic video plan requires initial takes';
  END IF;
  IF jsonb_typeof(p_run_update #> '{plan_snapshot,takes}') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'semantic video plan snapshot requires ordered takes';
  END IF;
  IF jsonb_typeof(p_run_update #> '{plan_snapshot,take_count}') IS DISTINCT FROM 'number'
     OR jsonb_typeof(p_run_update #> '{plan_snapshot,quota_units}') IS DISTINCT FROM 'number'
     OR jsonb_typeof(p_run_update #> '{plan_snapshot,billable_provider_seconds}') IS DISTINCT FROM 'number'
     OR jsonb_typeof(p_run_update #> '{plan_snapshot,price_per_provider_second_usd}') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_run_update #> '{plan_snapshot,estimated_cost_usd}') IS DISTINCT FROM 'string'
     OR jsonb_typeof(p_run_update -> 'estimated_cost_usd') IS DISTINCT FROM 'string' THEN
    RAISE EXCEPTION 'semantic video plan count and billing contract is invalid';
  END IF;

  input_take_count := jsonb_array_length(p_initial_takes);
  IF (p_run_update #>> '{plan_snapshot,take_count}')::INTEGER IS DISTINCT FROM input_take_count
     OR (p_run_update #>> '{plan_snapshot,quota_units}')::INTEGER IS DISTINCT FROM input_take_count
     OR jsonb_array_length(p_run_update #> '{plan_snapshot,takes}') IS DISTINCT FROM input_take_count THEN
    RAISE EXCEPTION 'semantic video plan take counts do not match';
  END IF;

  FOR take_payload IN
    SELECT entry.value
    FROM jsonb_array_elements(p_initial_takes) WITH ORDINALITY AS entry(value, position)
    ORDER BY entry.position
  LOOP
    IF jsonb_typeof(take_payload) IS DISTINCT FROM 'object'
       OR (take_payload ->> 'take_index')::INTEGER IS DISTINCT FROM take_position
       OR (take_payload ->> 'attempt')::INTEGER IS DISTINCT FROM 1
       OR NULLIF(btrim(take_payload ->> 'request_hash'), '') IS NULL THEN
      RAISE EXCEPTION 'semantic video plan initial takes are not in exact attempt-one order';
    END IF;
    IF take_payload ->> 'submission_state' IS DISTINCT FROM 'planned' THEN
      RAISE EXCEPTION 'semantic video plan initial submission state must be planned';
    END IF;
    plan_take := (p_run_update #> '{plan_snapshot,takes}') -> take_position;
    IF jsonb_typeof(plan_take) IS DISTINCT FROM 'object'
       OR (plan_take ->> 'take_index')::INTEGER IS DISTINCT FROM take_position
       OR plan_take ->> 'request_hash' IS DISTINCT FROM take_payload ->> 'request_hash'
       OR (plan_take ->> 'provider_duration_seconds')::INTEGER IS DISTINCT FROM
          (take_payload ->> 'provider_duration_seconds')::INTEGER THEN
      RAISE EXCEPTION 'semantic video plan snapshot does not match exact initial takes';
    END IF;
    take_position := take_position + 1;
  END LOOP;

  SELECT COALESCE(sum((entry.value ->> 'provider_duration_seconds')::INTEGER), 0)
  INTO input_provider_seconds
  FROM jsonb_array_elements(p_initial_takes) AS entry(value);
  IF (p_run_update #>> '{plan_snapshot,billable_provider_seconds}')::INTEGER IS DISTINCT FROM
     input_provider_seconds THEN
    RAISE EXCEPTION 'semantic video plan provider seconds do not match initial takes';
  END IF;
  plan_price_per_second := NULLIF(
    btrim(p_run_update #>> '{plan_snapshot,price_per_provider_second_usd}'),
    ''
  )::NUMERIC;
  supplied_plan_cost := NULLIF(
    btrim(p_run_update #>> '{plan_snapshot,estimated_cost_usd}'),
    ''
  )::NUMERIC;
  supplied_run_cost := NULLIF(btrim(p_run_update ->> 'estimated_cost_usd'), '')::NUMERIC;
  IF plan_price_per_second IS NULL
     OR supplied_plan_cost IS NULL
     OR supplied_run_cost IS NULL
     OR plan_price_per_second::TEXT IN ('NaN', 'Infinity', '-Infinity')
     OR supplied_plan_cost::TEXT IN ('NaN', 'Infinity', '-Infinity')
     OR supplied_run_cost::TEXT IN ('NaN', 'Infinity', '-Infinity')
     OR plan_price_per_second <= 0 THEN
    RAISE EXCEPTION 'semantic video plan cost contract is invalid';
  END IF;
  computed_plan_cost := round(plan_price_per_second * input_provider_seconds, 2);
  IF supplied_plan_cost IS DISTINCT FROM computed_plan_cost
     OR supplied_run_cost IS DISTINCT FROM computed_plan_cost THEN
    RAISE EXCEPTION 'semantic video plan cost mismatch: expected %, plan %, run %',
      computed_plan_cost,
      supplied_plan_cost,
      supplied_run_cost;
  END IF;
  IF (
    SELECT count(DISTINCT entry.value ->> 'request_hash')
    FROM jsonb_array_elements(p_initial_takes) AS entry(value)
  ) <> input_take_count THEN
    RAISE EXCEPTION 'semantic video plan initial request hashes must be unique';
  END IF;

  SELECT count(*)
  INTO existing_take_count
  FROM public.semantic_video_takes AS take
  WHERE take.run_id = p_run_id
    AND take.attempt = 1;

  UPDATE public.semantic_video_runs AS run
  SET requested_duration_seconds = (p_run_update ->> 'requested_duration_seconds')::INTEGER,
      duration_contract = p_run_update -> 'duration_contract',
      duration_contract_hash = p_run_update ->> 'duration_contract_hash',
      script_snapshot = p_run_update -> 'script_snapshot',
      script_hash = p_run_update ->> 'script_hash',
      actor_identity_id = NULLIF(p_run_update ->> 'actor_identity_id', '')::UUID,
      actor_snapshot = p_run_update -> 'actor_snapshot',
      reference_snapshot = p_run_update -> 'reference_snapshot',
      reference_hash = p_run_update ->> 'reference_hash',
      master_snapshot = p_run_update -> 'master_snapshot',
      master_hash = p_run_update ->> 'master_hash',
      stage = 'awaiting_paid_approval',
      plan_snapshot = p_run_update -> 'plan_snapshot',
      plan_hash = p_run_update ->> 'plan_hash',
      provider_model = p_run_update ->> 'provider_model',
      resolution = p_run_update ->> 'resolution',
      estimated_cost_usd = (p_run_update ->> 'estimated_cost_usd')::NUMERIC,
      artifact_prefix = p_run_update ->> 'artifact_prefix',
      failure_envelope = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
    AND run.revision = p_expected_revision
    AND run.stage = 'awaiting_paid_approval'
  RETURNING run.* INTO updated_run;
  GET DIAGNOSTICS affected_count = ROW_COUNT;
  IF affected_count <> 1 THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: plan update lost its revision';
  END IF;

  DELETE FROM public.semantic_video_takes AS take
  WHERE take.run_id = p_run_id
    AND take.attempt = 1;
  GET DIAGNOSTICS affected_count = ROW_COUNT;
  IF affected_count <> existing_take_count THEN
    RAISE EXCEPTION 'semantic video plan take deletion affected %, expected % rows',
      affected_count,
      existing_take_count;
  END IF;

  INSERT INTO public.semantic_video_takes (
    run_id,
    take_index,
    attempt,
    beat_text,
    word_count,
    estimated_speech_seconds,
    provider_duration_seconds,
    shot_transform,
    shot_hash,
    prompt_hash,
    negative_prompt_hash,
    provider_model,
    seed,
    request_contract,
    request_hash,
    submission_state,
    retry_guidance
  )
  SELECT
    p_run_id,
    initial_take.take_index,
    1,
    initial_take.beat_text,
    initial_take.word_count,
    initial_take.estimated_speech_seconds,
    initial_take.provider_duration_seconds,
    initial_take.shot_transform,
    initial_take.shot_hash,
    initial_take.prompt_hash,
    initial_take.negative_prompt_hash,
    initial_take.provider_model,
    initial_take.seed,
    initial_take.request_contract,
    initial_take.request_hash,
    'planned',
    initial_take.retry_guidance
  FROM jsonb_to_recordset(p_initial_takes) AS initial_take (
    take_index INTEGER,
    attempt INTEGER,
    beat_text TEXT,
    word_count INTEGER,
    estimated_speech_seconds NUMERIC,
    provider_duration_seconds INTEGER,
    shot_transform JSONB,
    shot_hash TEXT,
    prompt_hash TEXT,
    negative_prompt_hash TEXT,
    provider_model TEXT,
    seed BIGINT,
    request_contract JSONB,
    request_hash TEXT,
    submission_state TEXT,
    retry_guidance JSONB
  );
  GET DIAGNOSTICS affected_count = ROW_COUNT;
  IF affected_count <> input_take_count THEN
    RAISE EXCEPTION 'semantic video plan take insertion affected %, expected % rows',
      affected_count,
      input_take_count;
  END IF;

  SELECT COALESCE(jsonb_agg(to_jsonb(take) ORDER BY take.take_index), '[]'::JSONB)
  INTO returned_takes
  FROM public.semantic_video_takes AS take
  WHERE take.run_id = p_run_id
    AND take.attempt = 1;
  IF jsonb_array_length(returned_takes) <> input_take_count THEN
    RAISE EXCEPTION 'semantic video plan return contract has an unexpected take count';
  END IF;

  RETURN jsonb_build_object(
    'run', to_jsonb(updated_run),
    'takes', returned_takes
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.approve_semantic_video_master(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_candidate_index INTEGER,
  p_approved_by TEXT,
  p_reason TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  approval_row public.semantic_video_approvals%ROWTYPE;
  selected_candidate JSONB;
  approved_master JSONB;
  candidate_count INTEGER;
  selected_hash TEXT;
BEGIN
  IF p_run_id IS NULL
     OR p_expected_revision IS NULL
     OR p_expected_revision < 0
     OR p_candidate_index IS NULL
     OR p_candidate_index < 1 THEN
    RAISE EXCEPTION 'semantic video master approval identity is invalid';
  END IF;
  IF NULLIF(pg_catalog.btrim(p_approved_by), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video master approver is required';
  END IF;

  SELECT run.*
  INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: master approval run does not exist';
  END IF;
  IF locked_run.revision IS DISTINCT FROM p_expected_revision THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: master approval revision mismatch';
  END IF;
  IF locked_run.stage IS DISTINCT FROM 'awaiting_reference_approval' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: run is not awaiting master approval';
  END IF;
  IF pg_catalog.jsonb_typeof(locked_run.master_snapshot -> 'candidates') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: master candidates are unavailable';
  END IF;

  SELECT pg_catalog.count(*), (
    SELECT candidate.value
    FROM pg_catalog.jsonb_array_elements(locked_run.master_snapshot -> 'candidates') AS candidate(value)
    WHERE pg_catalog.jsonb_typeof(candidate.value) = 'object'
      AND pg_catalog.jsonb_typeof(candidate.value -> 'index') = 'number'
      AND (candidate.value ->> 'index')::INTEGER = p_candidate_index
    LIMIT 1
  )
  INTO candidate_count, selected_candidate
  FROM pg_catalog.jsonb_array_elements(locked_run.master_snapshot -> 'candidates') AS candidate(value)
  WHERE pg_catalog.jsonb_typeof(candidate.value) = 'object'
    AND pg_catalog.jsonb_typeof(candidate.value -> 'index') = 'number'
    AND (candidate.value ->> 'index')::INTEGER = p_candidate_index;

  IF candidate_count IS DISTINCT FROM 1 OR selected_candidate IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: selected master candidate is unavailable';
  END IF;
  IF pg_catalog.jsonb_typeof(selected_candidate -> 'storage_uri') IS DISTINCT FROM 'string'
     OR NULLIF(pg_catalog.btrim(selected_candidate ->> 'storage_uri'), '') IS NULL
     OR pg_catalog.jsonb_typeof(selected_candidate -> 'sha256') IS DISTINCT FROM 'string'
     OR NULLIF(pg_catalog.btrim(selected_candidate ->> 'sha256'), '') IS NULL
     OR pg_catalog.jsonb_typeof(selected_candidate -> 'byte_length') IS DISTINCT FROM 'number'
     OR (selected_candidate ->> 'byte_length')::INTEGER <= 0
     OR pg_catalog.jsonb_typeof(selected_candidate -> 'mime_type') IS DISTINCT FROM 'string'
     OR NULLIF(pg_catalog.btrim(selected_candidate ->> 'mime_type'), '') IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: selected master candidate contract is invalid';
  END IF;

  selected_hash := selected_candidate ->> 'sha256';
  approved_master := selected_candidate || pg_catalog.jsonb_build_object(
    'candidates', locked_run.master_snapshot -> 'candidates',
    'prompt_writer_system_prompt', locked_run.master_snapshot -> 'prompt_writer_system_prompt',
    'prompt_writer_system_prompt_sha256', locked_run.master_snapshot -> 'prompt_writer_system_prompt_sha256',
    'prompt_writer_output', locked_run.master_snapshot -> 'prompt_writer_output',
    'composition_prompt', locked_run.master_snapshot -> 'composition_prompt',
    'approved_candidate_index', p_candidate_index,
    'approved_by', p_approved_by
  );

  INSERT INTO public.semantic_video_approvals (
    run_id,
    approval_type,
    run_revision,
    contract_hash,
    approved_take_indexes,
    approved_provider_seconds,
    quota_units,
    estimated_cost_usd,
    approved_by,
    reason
  ) VALUES (
    p_run_id,
    'reference',
    p_expected_revision,
    selected_hash,
    '{}'::INTEGER[],
    0,
    0,
    0,
    p_approved_by,
    p_reason
  )
  RETURNING * INTO approval_row;

  UPDATE public.semantic_video_runs AS run
  SET master_snapshot = approved_master,
      master_hash = selected_hash,
      stage = 'awaiting_paid_approval',
      plan_snapshot = NULL,
      plan_hash = NULL,
      provider_model = NULL,
      resolution = NULL,
      estimated_cost_usd = NULL,
      failure_envelope = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
    AND run.revision = p_expected_revision
    AND run.stage = 'awaiting_reference_approval'
  RETURNING run.* INTO updated_run;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: master approval lost its revision';
  END IF;

  RETURN pg_catalog.jsonb_build_object(
    'run', pg_catalog.to_jsonb(updated_run),
    'approval', pg_catalog.to_jsonb(approval_row)
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.approve_semantic_video_initial_plan(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_plan_hash TEXT,
  p_approved_by TEXT,
  p_reason TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  approval_row public.semantic_video_approvals%ROWTYPE;
  initial_take_count INTEGER;
  approved_indexes INTEGER[];
  provider_seconds INTEGER;
  plan_price NUMERIC;
  plan_cost NUMERIC;
  computed_cost NUMERIC;
BEGIN
  IF p_run_id IS NULL
     OR p_expected_revision IS NULL
     OR p_expected_revision < 0
     OR NULLIF(pg_catalog.btrim(p_plan_hash), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video initial approval identity is invalid';
  END IF;
  IF NULLIF(pg_catalog.btrim(p_approved_by), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video initial approver is required';
  END IF;

  SELECT run.*
  INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: initial approval run does not exist';
  END IF;
  IF locked_run.revision IS DISTINCT FROM p_expected_revision THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: initial approval revision mismatch';
  END IF;
  IF locked_run.stage IS DISTINCT FROM 'awaiting_paid_approval' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: run is not awaiting initial paid approval';
  END IF;
  IF locked_run.plan_hash IS DISTINCT FROM p_plan_hash THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: initial approval plan hash is stale';
  END IF;
  IF pg_catalog.jsonb_typeof(locked_run.plan_snapshot) IS DISTINCT FROM 'object'
     OR pg_catalog.jsonb_typeof(locked_run.plan_snapshot -> 'takes') IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_typeof(locked_run.plan_snapshot -> 'take_count') IS DISTINCT FROM 'number'
     OR pg_catalog.jsonb_typeof(locked_run.plan_snapshot -> 'quota_units') IS DISTINCT FROM 'number'
     OR pg_catalog.jsonb_typeof(locked_run.plan_snapshot -> 'billable_provider_seconds') IS DISTINCT FROM 'number'
     OR pg_catalog.jsonb_typeof(locked_run.plan_snapshot -> 'price_per_provider_second_usd') IS DISTINCT FROM 'string'
     OR pg_catalog.jsonb_typeof(locked_run.plan_snapshot -> 'estimated_cost_usd') IS DISTINCT FROM 'string'
     OR locked_run.estimated_cost_usd IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: persisted initial plan contract is invalid';
  END IF;

  SELECT
    pg_catalog.count(*)::INTEGER,
    COALESCE(pg_catalog.array_agg(take.take_index ORDER BY take.take_index), '{}'::INTEGER[]),
    COALESCE(pg_catalog.sum(take.provider_duration_seconds), 0)::INTEGER
  INTO initial_take_count, approved_indexes, provider_seconds
  FROM public.semantic_video_takes AS take
  WHERE take.run_id = p_run_id
    AND take.attempt = 1;

  IF initial_take_count <= 0
     OR initial_take_count IS DISTINCT FROM pg_catalog.jsonb_array_length(locked_run.plan_snapshot -> 'takes')
     OR initial_take_count IS DISTINCT FROM (locked_run.plan_snapshot ->> 'take_count')::INTEGER
     OR initial_take_count IS DISTINCT FROM (locked_run.plan_snapshot ->> 'quota_units')::INTEGER
     OR provider_seconds IS DISTINCT FROM (locked_run.plan_snapshot ->> 'billable_provider_seconds')::INTEGER
     OR approved_indexes IS DISTINCT FROM ARRAY(
       SELECT pg_catalog.generate_series(0, initial_take_count - 1)
     ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: persisted initial take counts do not match the plan';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.semantic_video_takes AS take
    WHERE take.run_id = p_run_id
      AND take.attempt = 1
      AND take.submission_state IS DISTINCT FROM 'planned'
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: initial takes are no longer unsubmitted';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM pg_catalog.jsonb_array_elements(locked_run.plan_snapshot -> 'takes')
      WITH ORDINALITY AS planned(value, position)
    LEFT JOIN public.semantic_video_takes AS take
      ON take.run_id = p_run_id
     AND take.attempt = 1
     AND take.take_index = (planned.position - 1)::INTEGER
    WHERE take.id IS NULL
       OR pg_catalog.jsonb_typeof(planned.value) IS DISTINCT FROM 'object'
       OR (planned.value ->> 'take_index')::INTEGER IS DISTINCT FROM take.take_index
       OR planned.value ->> 'request_hash' IS DISTINCT FROM take.request_hash
       OR (planned.value ->> 'provider_duration_seconds')::INTEGER IS DISTINCT FROM take.provider_duration_seconds
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: persisted initial requests do not match the plan';
  END IF;

  plan_price := NULLIF(
    pg_catalog.btrim(locked_run.plan_snapshot ->> 'price_per_provider_second_usd'),
    ''
  )::NUMERIC;
  plan_cost := NULLIF(
    pg_catalog.btrim(locked_run.plan_snapshot ->> 'estimated_cost_usd'),
    ''
  )::NUMERIC;
  computed_cost := pg_catalog.round(plan_price * provider_seconds, 2);
  IF plan_price IS NULL
     OR plan_price <= 0
     OR plan_cost IS NULL
     OR plan_price::TEXT IN ('NaN', 'Infinity', '-Infinity')
     OR plan_cost::TEXT IN ('NaN', 'Infinity', '-Infinity')
     OR locked_run.estimated_cost_usd::TEXT IN ('NaN', 'Infinity', '-Infinity')
     OR plan_cost IS DISTINCT FROM computed_cost
     OR locked_run.estimated_cost_usd IS DISTINCT FROM computed_cost THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: persisted initial cost contract is invalid';
  END IF;

  INSERT INTO public.semantic_video_approvals (
    run_id,
    approval_type,
    run_revision,
    contract_hash,
    approved_take_indexes,
    approved_provider_seconds,
    quota_units,
    estimated_cost_usd,
    approved_by,
    reason
  ) VALUES (
    p_run_id,
    'initial_plan',
    p_expected_revision,
    p_plan_hash,
    approved_indexes,
    provider_seconds,
    initial_take_count,
    computed_cost,
    p_approved_by,
    p_reason
  )
  RETURNING * INTO approval_row;

  UPDATE public.semantic_video_runs AS run
  SET stage = 'generating',
      failure_envelope = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
    AND run.revision = p_expected_revision
    AND run.stage = 'awaiting_paid_approval'
    AND run.plan_hash = p_plan_hash
  RETURNING run.* INTO updated_run;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: initial approval lost its revision';
  END IF;

  RETURN pg_catalog.jsonb_build_object(
    'run', pg_catalog.to_jsonb(updated_run),
    'approval', pg_catalog.to_jsonb(approval_row)
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.approve_semantic_video_retry(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_plan_hash TEXT,
  p_retry_takes JSONB,
  p_contract_hash TEXT,
  p_approved_by TEXT,
  p_reason TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  approval_row public.semantic_video_approvals%ROWTYPE;
  previous_take public.semantic_video_takes%ROWTYPE;
  retry_payload JSONB;
  retry_count INTEGER;
  affected_count INTEGER;
  provider_seconds INTEGER := 0;
  approved_indexes INTEGER[] := '{}'::INTEGER[];
  plan_price NUMERIC;
  computed_cost NUMERIC;
  retry_prompt TEXT;
  retry_guidance_text TEXT;
  canonical_request_json TEXT;
  computed_prompt_hash TEXT;
  computed_request_hash TEXT;
  retry_request_hash_csv TEXT;
  expected_contract_basis TEXT;
  expected_contract_hash TEXT;
  returned_takes JSONB;
BEGIN
  IF p_run_id IS NULL
     OR p_expected_revision IS NULL
     OR p_expected_revision < 0
     OR NULLIF(pg_catalog.btrim(p_plan_hash), '') IS NULL
     OR NULLIF(pg_catalog.btrim(p_contract_hash), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video retry approval identity is invalid';
  END IF;
  IF NULLIF(pg_catalog.btrim(p_approved_by), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video retry approver is required';
  END IF;
  IF pg_catalog.jsonb_typeof(p_retry_takes) IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_array_length(p_retry_takes) = 0 THEN
    RAISE EXCEPTION 'semantic video retry approval requires retry takes';
  END IF;

  SELECT run.*
  INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: retry approval run does not exist';
  END IF;
  IF locked_run.revision IS DISTINCT FROM p_expected_revision THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: retry approval revision mismatch';
  END IF;
  IF locked_run.stage IS DISTINCT FROM 'retry_approval_required' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: run is not awaiting retry approval';
  END IF;
  IF locked_run.plan_hash IS DISTINCT FROM p_plan_hash THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: retry approval plan hash is stale';
  END IF;
  IF pg_catalog.jsonb_typeof(locked_run.plan_snapshot) IS DISTINCT FROM 'object'
     OR pg_catalog.jsonb_typeof(locked_run.plan_snapshot -> 'price_per_provider_second_usd') IS DISTINCT FROM 'string' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: persisted retry price contract is invalid';
  END IF;

  retry_count := pg_catalog.jsonb_array_length(p_retry_takes);
  FOR retry_payload IN
    SELECT entry.value
    FROM pg_catalog.jsonb_array_elements(p_retry_takes) WITH ORDINALITY AS entry(value, position)
    ORDER BY entry.position
  LOOP
    IF pg_catalog.jsonb_typeof(retry_payload) IS DISTINCT FROM 'object'
       OR NOT retry_payload ?& ARRAY[
         'take_index',
         'attempt',
         'beat_text',
         'word_count',
         'estimated_speech_seconds',
         'provider_duration_seconds',
         'shot_transform',
         'shot_hash',
         'prompt_hash',
         'negative_prompt_hash',
         'provider_model',
         'seed',
         'request_contract',
         'request_hash',
         'submission_state',
         'retry_guidance'
       ]
       OR pg_catalog.jsonb_typeof(retry_payload -> 'take_index') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'attempt') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'beat_text') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'word_count') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'estimated_speech_seconds') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'provider_duration_seconds') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'shot_transform') IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'shot_hash') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'prompt_hash') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'provider_model') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'seed') IS DISTINCT FROM 'number'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'request_contract') IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'request_hash') IS DISTINCT FROM 'string'
       OR retry_payload ->> 'submission_state' IS DISTINCT FROM 'planned'
       OR pg_catalog.jsonb_typeof(retry_payload -> 'retry_guidance') IS DISTINCT FROM 'object' THEN
      RAISE EXCEPTION 'semantic video retry take contract is invalid';
    END IF;
    IF (retry_payload ->> 'take_index')::INTEGER < 0
       OR (retry_payload ->> 'attempt')::INTEGER < 2
       OR (retry_payload ->> 'provider_duration_seconds')::INTEGER <= 0
       OR NULLIF(pg_catalog.btrim(retry_payload ->> 'beat_text'), '') IS NULL
       OR NULLIF(pg_catalog.btrim(retry_payload ->> 'request_hash'), '') IS NULL
       OR NULLIF(pg_catalog.btrim(retry_payload ->> 'prompt_hash'), '') IS NULL THEN
      RAISE EXCEPTION 'semantic video retry take values are invalid';
    END IF;

    SELECT take.*
    INTO previous_take
    FROM public.semantic_video_takes AS take
    WHERE take.run_id = p_run_id
      AND take.take_index = (retry_payload ->> 'take_index')::INTEGER
    ORDER BY take.attempt DESC
    LIMIT 1
    FOR UPDATE;

    IF NOT FOUND
       OR previous_take.submission_state NOT IN ('qa_failed', 'failed') THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: retry target is not currently failed';
    END IF;
    IF (retry_payload ->> 'attempt')::INTEGER IS DISTINCT FROM previous_take.attempt + 1
       OR retry_payload ->> 'beat_text' IS DISTINCT FROM previous_take.beat_text
       OR (retry_payload ->> 'word_count')::INTEGER IS DISTINCT FROM previous_take.word_count
       OR (retry_payload ->> 'estimated_speech_seconds')::NUMERIC IS DISTINCT FROM previous_take.estimated_speech_seconds
       OR (retry_payload ->> 'provider_duration_seconds')::INTEGER IS DISTINCT FROM previous_take.provider_duration_seconds
       OR retry_payload -> 'shot_transform' IS DISTINCT FROM previous_take.shot_transform
       OR retry_payload ->> 'shot_hash' IS DISTINCT FROM previous_take.shot_hash
       OR retry_payload ->> 'negative_prompt_hash' IS DISTINCT FROM previous_take.negative_prompt_hash
       OR retry_payload ->> 'provider_model' IS DISTINCT FROM previous_take.provider_model
       OR previous_take.seed IS NULL
       OR (retry_payload ->> 'seed')::BIGINT IS DISTINCT FROM previous_take.seed + 1000
       OR retry_payload -> 'retry_guidance' IS DISTINCT FROM previous_take.retry_guidance
       OR retry_payload ->> 'request_hash' IS NOT DISTINCT FROM previous_take.request_hash
       OR retry_payload ->> 'prompt_hash' IS NOT DISTINCT FROM previous_take.prompt_hash THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: retry take does not preserve the failed take contract';
    END IF;

    retry_prompt := retry_payload #>> '{request_contract,prompt}';
    retry_guidance_text := COALESCE(
      NULLIF(pg_catalog.btrim(pg_catalog.regexp_replace(previous_take.retry_guidance ->> 'guidance', '\s+', ' ', 'g')), ''),
      NULLIF(pg_catalog.btrim(pg_catalog.regexp_replace(previous_take.retry_guidance ->> 'prompt_suffix', '\s+', ' ', 'g')), ''),
      NULLIF(pg_catalog.btrim(pg_catalog.regexp_replace(previous_take.retry_guidance ->> 'instruction', '\s+', ' ', 'g')), ''),
      NULLIF(pg_catalog.btrim(pg_catalog.regexp_replace(previous_take.retry_guidance ->> 'message', '\s+', ' ', 'g')), '')
    );
    canonical_request_json := retry_payload #>> '{request_contract,canonical_request_json}';
    computed_prompt_hash := pg_catalog.encode(
      pg_catalog.sha256(pg_catalog.convert_to(retry_prompt, 'UTF8')),
      'hex'
    );
    computed_request_hash := pg_catalog.encode(
      pg_catalog.sha256(pg_catalog.convert_to(canonical_request_json, 'UTF8')),
      'hex'
    );
    IF pg_catalog.jsonb_typeof(retry_payload #> '{request_contract,attempt}') IS DISTINCT FROM 'number'
       OR (retry_payload #>> '{request_contract,attempt}')::INTEGER IS DISTINCT FROM previous_take.attempt + 1
       OR pg_catalog.jsonb_typeof(retry_payload #> '{request_contract,seed}') IS DISTINCT FROM 'number'
       OR (retry_payload #>> '{request_contract,seed}')::BIGINT IS DISTINCT FROM previous_take.seed + 1000
       OR retry_payload #>> '{request_contract,retry_of_request_hash}' IS DISTINCT FROM previous_take.request_hash
       OR retry_payload #> '{request_contract,retry_guidance}' IS DISTINCT FROM previous_take.retry_guidance
       OR pg_catalog.jsonb_typeof(retry_payload #> '{request_contract,canonical_request_json}') IS DISTINCT FROM 'string'
       OR NULLIF(pg_catalog.btrim(retry_prompt), '') IS NULL
       OR NULLIF(pg_catalog.btrim(retry_guidance_text), '') IS NULL
       OR retry_prompt IS NOT DISTINCT FROM previous_take.request_contract ->> 'prompt'
       OR pg_catalog.strpos(retry_prompt, 'Retry delivery correction:') = 0
       OR pg_catalog.strpos(retry_prompt, previous_take.beat_text) = 0
       OR (
         (pg_catalog.length(retry_prompt) - pg_catalog.length(pg_catalog.replace(retry_prompt, retry_guidance_text, '')))
         / pg_catalog.length(retry_guidance_text)
       ) IS DISTINCT FROM 1
       OR (
         (pg_catalog.length(retry_prompt) - pg_catalog.length(pg_catalog.replace(retry_prompt, previous_take.beat_text, '')))
         / pg_catalog.length(previous_take.beat_text)
       ) IS DISTINCT FROM 1
       OR canonical_request_json::JSONB IS DISTINCT FROM
          (retry_payload -> 'request_contract') - 'canonical_request_json'
       OR retry_payload ->> 'prompt_hash' IS DISTINCT FROM computed_prompt_hash
       OR retry_payload ->> 'request_hash' IS DISTINCT FROM computed_request_hash THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: retry request contract is not a deterministic correction';
    END IF;

    approved_indexes := pg_catalog.array_append(
      approved_indexes,
      (retry_payload ->> 'take_index')::INTEGER
    );
    provider_seconds := provider_seconds + (retry_payload ->> 'provider_duration_seconds')::INTEGER;
  END LOOP;

  IF (
    SELECT pg_catalog.count(DISTINCT entry.value ->> 'take_index')
    FROM pg_catalog.jsonb_array_elements(p_retry_takes) AS entry(value)
  ) IS DISTINCT FROM retry_count THEN
    RAISE EXCEPTION 'semantic video retry take indexes must be unique';
  END IF;
  SELECT pg_catalog.array_agg(retry_index ORDER BY retry_index)
  INTO approved_indexes
  FROM pg_catalog.unnest(approved_indexes) AS retry_index;

  plan_price := NULLIF(
    pg_catalog.btrim(locked_run.plan_snapshot ->> 'price_per_provider_second_usd'),
    ''
  )::NUMERIC;
  IF plan_price IS NULL
     OR plan_price <= 0
     OR plan_price::TEXT IN ('NaN', 'Infinity', '-Infinity') THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: persisted retry price is invalid';
  END IF;
  computed_cost := pg_catalog.round(plan_price * provider_seconds, 2);
  SELECT pg_catalog.string_agg(
    entry.value ->> 'request_hash',
    ','
    ORDER BY (entry.value ->> 'take_index')::INTEGER
  )
  INTO retry_request_hash_csv
  FROM pg_catalog.jsonb_array_elements(p_retry_takes) AS entry(value);
  expected_contract_basis := 'semantic-retry-contract-v1'
    || pg_catalog.chr(10) || p_plan_hash
    || pg_catalog.chr(10) || p_expected_revision::TEXT
    || pg_catalog.chr(10) || pg_catalog.array_to_string(approved_indexes, ',')
    || pg_catalog.chr(10) || retry_request_hash_csv
    || pg_catalog.chr(10) || provider_seconds::TEXT
    || pg_catalog.chr(10) || retry_count::TEXT
    || pg_catalog.chr(10) || computed_cost::TEXT;
  expected_contract_hash := pg_catalog.encode(
    pg_catalog.sha256(pg_catalog.convert_to(expected_contract_basis, 'UTF8')),
    'hex'
  );
  IF p_contract_hash IS DISTINCT FROM expected_contract_hash THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: retry approval contract hash is invalid';
  END IF;

  INSERT INTO public.semantic_video_takes (
    run_id,
    take_index,
    attempt,
    beat_text,
    word_count,
    estimated_speech_seconds,
    provider_duration_seconds,
    shot_transform,
    shot_hash,
    prompt_hash,
    negative_prompt_hash,
    provider_model,
    seed,
    request_contract,
    request_hash,
    submission_state,
    retry_guidance
  )
  SELECT
    p_run_id,
    retry_take.take_index,
    retry_take.attempt,
    retry_take.beat_text,
    retry_take.word_count,
    retry_take.estimated_speech_seconds,
    retry_take.provider_duration_seconds,
    retry_take.shot_transform,
    retry_take.shot_hash,
    retry_take.prompt_hash,
    retry_take.negative_prompt_hash,
    retry_take.provider_model,
    retry_take.seed,
    retry_take.request_contract,
    retry_take.request_hash,
    'planned',
    retry_take.retry_guidance
  FROM pg_catalog.jsonb_to_recordset(p_retry_takes) AS retry_take (
    take_index INTEGER,
    attempt INTEGER,
    beat_text TEXT,
    word_count INTEGER,
    estimated_speech_seconds NUMERIC,
    provider_duration_seconds INTEGER,
    shot_transform JSONB,
    shot_hash TEXT,
    prompt_hash TEXT,
    negative_prompt_hash TEXT,
    provider_model TEXT,
    seed BIGINT,
    request_contract JSONB,
    request_hash TEXT,
    submission_state TEXT,
    retry_guidance JSONB
  );
  GET DIAGNOSTICS affected_count = ROW_COUNT;
  IF affected_count IS DISTINCT FROM retry_count THEN
    RAISE EXCEPTION 'semantic video retry insertion affected an unexpected row count';
  END IF;

  INSERT INTO public.semantic_video_approvals (
    run_id,
    approval_type,
    run_revision,
    contract_hash,
    approved_take_indexes,
    approved_provider_seconds,
    quota_units,
    estimated_cost_usd,
    approved_by,
    reason
  ) VALUES (
    p_run_id,
    'retry',
    p_expected_revision,
    p_contract_hash,
    approved_indexes,
    provider_seconds,
    retry_count,
    computed_cost,
    p_approved_by,
    p_reason
  )
  RETURNING * INTO approval_row;

  UPDATE public.semantic_video_runs AS run
  SET stage = 'generating',
      failure_envelope = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
    AND run.revision = p_expected_revision
    AND run.stage = 'retry_approval_required'
    AND run.plan_hash = p_plan_hash
  RETURNING run.* INTO updated_run;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: retry approval lost its revision';
  END IF;

  SELECT COALESCE(
    pg_catalog.jsonb_agg(pg_catalog.to_jsonb(take) ORDER BY take.take_index),
    '[]'::JSONB
  )
  INTO returned_takes
  FROM public.semantic_video_takes AS take
  WHERE take.run_id = p_run_id
    AND take.request_hash IN (
      SELECT entry.value ->> 'request_hash'
      FROM pg_catalog.jsonb_array_elements(p_retry_takes) AS entry(value)
    );
  IF pg_catalog.jsonb_array_length(returned_takes) IS DISTINCT FROM retry_count THEN
    RAISE EXCEPTION 'semantic video retry return contract has an unexpected row count';
  END IF;

  RETURN pg_catalog.jsonb_build_object(
    'run', pg_catalog.to_jsonb(updated_run),
    'approval', pg_catalog.to_jsonb(approval_row),
    'takes', returned_takes
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.cancel_semantic_video_run(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_cancelled_by TEXT,
  p_reason TEXT,
  p_correlation_id TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  cancelled_take_count INTEGER;
BEGIN
  IF p_run_id IS NULL
     OR p_expected_revision IS NULL
     OR p_expected_revision < 0 THEN
    RAISE EXCEPTION 'semantic video cancellation identity is invalid';
  END IF;
  IF NULLIF(pg_catalog.btrim(p_cancelled_by), '') IS NULL
     OR NULLIF(pg_catalog.btrim(p_reason), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video cancellation actor and reason are required';
  END IF;

  SELECT run.*
  INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: cancellation run does not exist';
  END IF;
  IF locked_run.revision IS DISTINCT FROM p_expected_revision THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: cancellation revision mismatch';
  END IF;
  IF locked_run.stage IN ('completed', 'failed') THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: terminal run cannot be cancelled';
  END IF;

  PERFORM take.id
  FROM public.semantic_video_takes AS take
  WHERE take.run_id = p_run_id
  ORDER BY take.id
  FOR UPDATE;

  IF EXISTS (
    SELECT 1
    FROM public.semantic_video_takes AS take
    WHERE take.run_id = p_run_id
      AND (
        take.submission_state IN ('intent_persisted', 'submitted', 'submission_unknown')
        OR (
          take.operation_id IS NOT NULL
          AND take.submission_state NOT IN ('completed', 'qa_failed', 'failed', 'cancelled')
        )
      )
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: paid submission is in flight or ambiguous';
  END IF;

  UPDATE public.semantic_video_takes AS take
  SET submission_state = 'cancelled'
  WHERE take.run_id = p_run_id
    AND take.submission_state IN ('planned', 'reserved');
  GET DIAGNOSTICS cancelled_take_count = ROW_COUNT;

  UPDATE public.semantic_video_runs AS run
  SET stage = 'failed',
      failure_envelope = pg_catalog.jsonb_build_object(
        'code', 'cancelled',
        'message', p_reason,
        'cancelled_by', p_cancelled_by,
        'correlation_id', COALESCE(p_correlation_id, '')
      ),
      lease_owner = NULL,
      lease_expires_at = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
    AND run.revision = p_expected_revision
    AND run.stage NOT IN ('completed', 'failed')
  RETURNING run.* INTO updated_run;
  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: cancellation lost its revision';
  END IF;

  RETURN pg_catalog.jsonb_build_object(
    'run', pg_catalog.to_jsonb(updated_run),
    'cancelled_take_count', cancelled_take_count
  );
END;
$$;

REVOKE ALL ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER) TO service_role;
REVOKE ALL ON FUNCTION public.reserve_semantic_video_candidates(UUID, INTEGER, JSONB, TEXT, UUID, INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reserve_semantic_video_candidates(UUID, INTEGER, JSONB, TEXT, UUID, INTEGER) TO service_role;
REVOKE ALL ON FUNCTION public.finalize_semantic_video_candidates(UUID, INTEGER, UUID, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.finalize_semantic_video_candidates(UUID, INTEGER, UUID, JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.persist_semantic_video_plan(UUID, INTEGER, JSONB, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.persist_semantic_video_plan(UUID, INTEGER, JSONB, JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.approve_semantic_video_master(UUID, INTEGER, INTEGER, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_semantic_video_master(UUID, INTEGER, INTEGER, TEXT, TEXT) TO service_role;
REVOKE ALL ON FUNCTION public.approve_semantic_video_initial_plan(UUID, INTEGER, TEXT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_semantic_video_initial_plan(UUID, INTEGER, TEXT, TEXT, TEXT) TO service_role;
REVOKE ALL ON FUNCTION public.approve_semantic_video_retry(UUID, INTEGER, TEXT, JSONB, TEXT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_semantic_video_retry(UUID, INTEGER, TEXT, JSONB, TEXT, TEXT, TEXT) TO service_role;
REVOKE ALL ON FUNCTION public.cancel_semantic_video_run(UUID, INTEGER, TEXT, TEXT, TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.cancel_semantic_video_run(UUID, INTEGER, TEXT, TEXT, TEXT) TO service_role;
REVOKE INSERT ON TABLE public.semantic_video_approvals FROM service_role;
REVOKE INSERT ON TABLE public.semantic_video_takes FROM service_role;
