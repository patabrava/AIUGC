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

ALTER TABLE public.semantic_video_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.semantic_video_takes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.semantic_video_approvals ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.semantic_video_runs TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.semantic_video_takes TO service_role;
REVOKE UPDATE, DELETE ON public.semantic_video_approvals FROM service_role;
GRANT SELECT, INSERT ON public.semantic_video_approvals TO service_role;
REVOKE ALL ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER) TO service_role;
