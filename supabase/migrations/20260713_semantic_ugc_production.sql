-- Semantic UGC batch contract and resumable production persistence.
-- The application enforces the configurable maximum duration; PostgreSQL keeps
-- only the structural eight-second minimum so that the maximum can rise later.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS target_duration_seconds INTEGER;

UPDATE public.batches
SET target_length_tier = 8
WHERE creation_mode <> 'semantic_ugc'
  AND target_length_tier IS NULL;

ALTER TABLE public.batches
  DROP CONSTRAINT IF EXISTS batches_creation_mode_check,
  DROP CONSTRAINT IF EXISTS batches_target_length_tier_check,
  DROP CONSTRAINT IF EXISTS batches_target_duration_seconds_check,
  DROP CONSTRAINT IF EXISTS batches_duration_authority_check,
  DROP CONSTRAINT IF EXISTS batches_video_pipeline_route_check,
  DROP CONSTRAINT IF EXISTS batches_semantic_pipeline_route_check;

ALTER TABLE public.batches
  ADD CONSTRAINT batches_creation_mode_check CHECK (
    creation_mode IN (
      'automated',
      'manual',
      'manual_character_consistency',
      'character_consistency',
      'character_consistency_light',
      'character_consistency_mid',
      'semantic_ugc'
    )
  ),
  ADD CONSTRAINT batches_target_length_tier_check CHECK (
    target_length_tier IS NULL OR target_length_tier IN (8, 16, 32, 48, 64)
  ),
  ADD CONSTRAINT batches_target_duration_seconds_check CHECK (
    target_duration_seconds IS NULL OR target_duration_seconds >= 8
  ),
  ADD CONSTRAINT batches_duration_authority_check CHECK (
    (
      creation_mode = 'semantic_ugc'
      AND target_length_tier IS NULL
      AND target_duration_seconds IS NOT NULL
    )
    OR
    (
      creation_mode <> 'semantic_ugc'
      AND target_length_tier IS NOT NULL
      AND target_duration_seconds IS NULL
    )
  ),
  ADD CONSTRAINT batches_video_pipeline_route_check CHECK (
    video_pipeline_route IS NULL
    OR video_pipeline_route IN ('short', 'veo_extended', 'semantic_ugc')
  ),
  ADD CONSTRAINT batches_semantic_pipeline_route_check CHECK (
    (
      creation_mode = 'semantic_ugc'
      AND video_pipeline_route = 'semantic_ugc'
    )
    OR
    (
      creation_mode <> 'semantic_ugc'
      AND video_pipeline_route IS DISTINCT FROM 'semantic_ugc'
    )
  );

CREATE UNIQUE INDEX IF NOT EXISTS posts_id_batch_id_unique
  ON public.posts (id, batch_id);

CREATE TABLE IF NOT EXISTS public.semantic_video_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  post_id UUID NOT NULL,
  batch_id UUID NOT NULL REFERENCES public.batches(id) ON DELETE CASCADE,
  requested_duration_seconds INTEGER NOT NULL CHECK (requested_duration_seconds >= 8),
  duration_contract JSONB NOT NULL,
  duration_contract_hash TEXT NOT NULL,
  script_snapshot JSONB NOT NULL,
  script_hash TEXT NOT NULL,
  actor_identity_id UUID REFERENCES public.actor_identities(id) ON DELETE RESTRICT,
  actor_snapshot JSONB NOT NULL DEFAULT '{}'::JSONB,
  reference_snapshot JSONB NOT NULL DEFAULT '{}'::JSONB,
  reference_hash TEXT,
  master_snapshot JSONB NOT NULL DEFAULT '{}'::JSONB,
  master_hash TEXT,
  stage TEXT NOT NULL DEFAULT 'awaiting_script_approval' CHECK (
    stage IN (
      'awaiting_script_approval',
      'awaiting_reference_approval',
      'awaiting_paid_approval',
      'generating',
      'transcript_qa',
      'identity_qa',
      'voice_qa',
      'retry_approval_required',
      'acoustic_qa',
      'composing',
      'uploading',
      'completed',
      'failed'
    )
  ),
  plan_snapshot JSONB,
  plan_hash TEXT,
  provider_model TEXT,
  resolution TEXT,
  estimated_cost_usd NUMERIC(12, 4) CHECK (
    estimated_cost_usd IS NULL OR estimated_cost_usd >= 0
  ),
  artifact_prefix TEXT,
  lease_owner TEXT,
  lease_expires_at TIMESTAMPTZ,
  failure_envelope JSONB,
  final_video_uri TEXT,
  final_video_sha256 TEXT,
  final_caption_uri TEXT,
  final_caption_sha256 TEXT,
  revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT semantic_video_runs_post_batch_fk
    FOREIGN KEY (post_id, batch_id)
    REFERENCES public.posts(id, batch_id)
    ON DELETE CASCADE
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'semantic_video_runs_post_batch_fk'
      AND conrelid = 'public.semantic_video_runs'::regclass
  ) THEN
    ALTER TABLE public.semantic_video_runs
      ADD CONSTRAINT semantic_video_runs_post_batch_fk
      FOREIGN KEY (post_id, batch_id)
      REFERENCES public.posts(id, batch_id)
      ON DELETE CASCADE;
  END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS public.semantic_video_takes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES public.semantic_video_runs(id) ON DELETE CASCADE,
  take_index INTEGER NOT NULL CHECK (take_index >= 0),
  attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
  beat_text TEXT NOT NULL,
  word_count INTEGER NOT NULL CHECK (word_count >= 0),
  estimated_speech_seconds NUMERIC(8, 3) NOT NULL CHECK (estimated_speech_seconds >= 0),
  provider_duration_seconds INTEGER NOT NULL CHECK (provider_duration_seconds > 0),
  shot_transform JSONB NOT NULL,
  shot_hash TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  negative_prompt_hash TEXT,
  provider_model TEXT NOT NULL,
  seed BIGINT,
  request_contract JSONB NOT NULL,
  request_hash TEXT NOT NULL,
  submission_state TEXT NOT NULL DEFAULT 'planned' CHECK (
    submission_state IN (
      'planned',
      'reserved',
      'intent_persisted',
      'submitted',
      'submission_unknown',
      'completed',
      'qa_failed',
      'failed',
      'cancelled'
    )
  ),
  submission_intent_at TIMESTAMPTZ,
  operation_id TEXT,
  raw_artifact_uri TEXT,
  raw_artifact_sha256 TEXT,
  transcript_result JSONB,
  identity_qa_result JSONB,
  voice_qa_contribution JSONB,
  retry_guidance JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (run_id, take_index, attempt),
  UNIQUE (run_id, request_hash)
);

CREATE TABLE IF NOT EXISTS public.semantic_video_approvals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id UUID NOT NULL REFERENCES public.semantic_video_runs(id) ON DELETE CASCADE,
  approval_type TEXT NOT NULL CHECK (
    approval_type IN ('reference', 'initial_plan', 'retry')
  ),
  run_revision INTEGER NOT NULL CHECK (run_revision >= 0),
  contract_hash TEXT NOT NULL,
  approved_take_indexes INTEGER[] NOT NULL DEFAULT '{}'::INTEGER[],
  approved_provider_seconds INTEGER NOT NULL DEFAULT 0 CHECK (approved_provider_seconds >= 0),
  quota_units INTEGER NOT NULL DEFAULT 0 CHECK (quota_units >= 0),
  estimated_cost_usd NUMERIC(12, 4) NOT NULL DEFAULT 0 CHECK (estimated_cost_usd >= 0),
  approved_by TEXT NOT NULL,
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS semantic_video_runs_one_active_per_post
  ON public.semantic_video_runs (post_id)
  WHERE stage NOT IN ('completed', 'failed');

CREATE INDEX IF NOT EXISTS semantic_video_runs_batch_stage_idx
  ON public.semantic_video_runs (batch_id, stage, updated_at);

CREATE INDEX IF NOT EXISTS semantic_video_runs_lease_idx
  ON public.semantic_video_runs (lease_expires_at, updated_at)
  WHERE stage NOT IN ('completed', 'failed');

CREATE INDEX IF NOT EXISTS semantic_video_takes_run_order_idx
  ON public.semantic_video_takes (run_id, take_index, attempt);

CREATE INDEX IF NOT EXISTS semantic_video_takes_operation_idx
  ON public.semantic_video_takes (operation_id)
  WHERE operation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS semantic_video_takes_submission_state_idx
  ON public.semantic_video_takes (submission_state, updated_at);

CREATE INDEX IF NOT EXISTS semantic_video_approvals_run_created_idx
  ON public.semantic_video_approvals (run_id, created_at DESC);

DROP TRIGGER IF EXISTS semantic_video_runs_touch_updated_at
  ON public.semantic_video_runs;
CREATE TRIGGER semantic_video_runs_touch_updated_at
BEFORE UPDATE ON public.semantic_video_runs
FOR EACH ROW
EXECUTE FUNCTION public.touch_updated_at();

DROP TRIGGER IF EXISTS semantic_video_takes_touch_updated_at
  ON public.semantic_video_takes;
CREATE TRIGGER semantic_video_takes_touch_updated_at
BEFORE UPDATE ON public.semantic_video_takes
FOR EACH ROW
EXECUTE FUNCTION public.touch_updated_at();

CREATE OR REPLACE FUNCTION public.claim_semantic_video_run(
  worker_id TEXT,
  lease_seconds INTEGER
)
RETURNS SETOF public.semantic_video_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
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

CREATE OR REPLACE FUNCTION public.persist_semantic_video_plan(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_run_update JSONB,
  p_initial_takes JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  take_payload JSONB;
  plan_take JSONB;
  take_position INTEGER := 0;
  input_take_count INTEGER;
  input_provider_seconds INTEGER;
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
    RAISE EXCEPTION 'semantic video plan run does not exist';
  END IF;
  IF locked_run.revision <> p_expected_revision THEN
    RAISE EXCEPTION 'semantic video plan revision mismatch: expected %, actual %',
      p_expected_revision,
      locked_run.revision;
  END IF;
  IF locked_run.stage <> 'awaiting_paid_approval' THEN
    RAISE EXCEPTION 'semantic video plan stage mismatch: expected awaiting_paid_approval, actual %',
      locked_run.stage;
  END IF;
  IF p_run_update ->> 'stage' <> 'awaiting_paid_approval' THEN
    RAISE EXCEPTION 'semantic video plan update must preserve awaiting_paid_approval stage';
  END IF;
  IF p_run_update ->> 'post_id' IS DISTINCT FROM locked_run.post_id::TEXT
     OR p_run_update ->> 'batch_id' IS DISTINCT FROM locked_run.batch_id::TEXT THEN
    RAISE EXCEPTION 'semantic video plan update cannot change run ownership';
  END IF;
  IF (p_run_update ->> 'requested_duration_seconds')::INTEGER <> locked_run.requested_duration_seconds
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
    RAISE EXCEPTION 'semantic video plan update source snapshot does not match locked run';
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

  input_take_count := jsonb_array_length(p_initial_takes);
  IF (p_run_update #>> '{plan_snapshot,take_count}')::INTEGER <> input_take_count
     OR (p_run_update #>> '{plan_snapshot,quota_units}')::INTEGER <> input_take_count
     OR jsonb_array_length(p_run_update #> '{plan_snapshot,takes}') <> input_take_count THEN
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
  IF (p_run_update #>> '{plan_snapshot,billable_provider_seconds}')::INTEGER <>
     input_provider_seconds THEN
    RAISE EXCEPTION 'semantic video plan provider seconds do not match initial takes';
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
    RAISE EXCEPTION 'semantic video plan run update affected % rows', affected_count;
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
    COALESCE(initial_take.submission_state, 'planned'),
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

ALTER TABLE public.semantic_video_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.semantic_video_takes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.semantic_video_approvals ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.semantic_video_runs TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.semantic_video_takes TO service_role;
REVOKE UPDATE, DELETE ON public.semantic_video_approvals FROM service_role;
GRANT SELECT, INSERT ON public.semantic_video_approvals TO service_role;
REVOKE ALL ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER) TO service_role;
REVOKE ALL ON FUNCTION public.persist_semantic_video_plan(UUID, INTEGER, JSONB, JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.persist_semantic_video_plan(UUID, INTEGER, JSONB, JSONB) TO service_role;
