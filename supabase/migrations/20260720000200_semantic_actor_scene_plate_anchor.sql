-- One approved canonical wheelchair scene plate per exact ordered actor-reference set.
-- Approval owns the only write path so two bootstrap runs cannot silently establish
-- different actors/wheelchairs for the same immutable reference bytes.

CREATE TABLE IF NOT EXISTS public.semantic_actor_scene_plate_anchors (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_identity_id UUID NOT NULL REFERENCES public.actor_identities(id) ON DELETE RESTRICT,
  actor_reference_fingerprint TEXT NOT NULL CHECK (
    actor_reference_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  source_run_id UUID REFERENCES public.semantic_video_runs(id) ON DELETE SET NULL,
  master_storage_uri TEXT NOT NULL CHECK (NULLIF(btrim(master_storage_uri), '') IS NOT NULL),
  master_sha256 TEXT NOT NULL CHECK (master_sha256 ~ '^[0-9a-f]{64}$'),
  master_byte_length INTEGER NOT NULL CHECK (master_byte_length > 0),
  master_mime_type TEXT NOT NULL CHECK (master_mime_type = 'image/png'),
  provider_model TEXT NOT NULL CHECK (NULLIF(btrim(provider_model), '') IS NOT NULL),
  visual_contract_hash TEXT NOT NULL CHECK (visual_contract_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT semantic_actor_scene_plate_anchors_actor_fingerprint_key
    UNIQUE (actor_identity_id, actor_reference_fingerprint)
);

CREATE INDEX IF NOT EXISTS semantic_actor_scene_plate_anchors_source_run_idx
  ON public.semantic_actor_scene_plate_anchors (source_run_id)
  WHERE source_run_id IS NOT NULL;

ALTER TABLE public.semantic_actor_scene_plate_anchors ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.semantic_actor_scene_plate_anchors FROM PUBLIC, anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.semantic_actor_scene_plate_anchors FROM service_role;
GRANT SELECT ON TABLE public.semantic_actor_scene_plate_anchors TO service_role;

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
  anchor_row public.semantic_actor_scene_plate_anchors%ROWTYPE;
  selected_candidate JSONB;
  approved_master JSONB;
  candidate_count INTEGER;
  selected_hash TEXT;
  selected_storage_uri TEXT;
  selected_mime_type TEXT;
  selected_provider_model TEXT;
  selected_byte_length INTEGER;
  actor_fingerprint TEXT;
  derivation_mode TEXT;
  visual_contract_hash TEXT;
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
  IF locked_run.actor_identity_id IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: master approval actor identity is unavailable';
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
     OR (selected_candidate ->> 'sha256') !~ '^[0-9a-f]{64}$'
     OR pg_catalog.jsonb_typeof(selected_candidate -> 'byte_length') IS DISTINCT FROM 'number'
     OR (selected_candidate ->> 'byte_length')::INTEGER <= 0
     OR pg_catalog.jsonb_typeof(selected_candidate -> 'mime_type') IS DISTINCT FROM 'string'
     OR selected_candidate ->> 'mime_type' IS DISTINCT FROM 'image/png'
     OR pg_catalog.jsonb_typeof(selected_candidate -> 'provider_model') IS DISTINCT FROM 'string'
     OR NULLIF(pg_catalog.btrim(selected_candidate ->> 'provider_model'), '') IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: selected master candidate contract is invalid';
  END IF;

  selected_hash := selected_candidate ->> 'sha256';
  selected_storage_uri := selected_candidate ->> 'storage_uri';
  selected_mime_type := selected_candidate ->> 'mime_type';
  selected_provider_model := selected_candidate ->> 'provider_model';
  selected_byte_length := (selected_candidate ->> 'byte_length')::INTEGER;
  actor_fingerprint := selected_candidate ->> 'actor_reference_fingerprint';
  derivation_mode := selected_candidate ->> 'derivation_mode';
  visual_contract_hash := selected_candidate ->> 'visual_contract_hash';

  IF actor_fingerprint IS NULL
     OR actor_fingerprint !~ '^[0-9a-f]{64}$'
     OR actor_fingerprint IS DISTINCT FROM locked_run.reference_snapshot ->> 'actor_reference_fingerprint'
     OR actor_fingerprint IS DISTINCT FROM locked_run.master_snapshot ->> 'actor_reference_fingerprint'
     OR derivation_mode IS NULL
     OR derivation_mode NOT IN ('bootstrap', 'canonical_anchor')
     OR derivation_mode IS DISTINCT FROM locked_run.master_snapshot ->> 'derivation_mode'
     OR visual_contract_hash IS NULL
     OR visual_contract_hash !~ '^[0-9a-f]{64}$'
     OR visual_contract_hash IS DISTINCT FROM locked_run.master_snapshot ->> 'visual_contract_hash'
     OR visual_contract_hash IS DISTINCT FROM locked_run.reference_snapshot #>> '{visual_contract,contract_hash}' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: selected master candidate lineage is invalid';
  END IF;
  IF EXISTS (
       SELECT 1
       FROM pg_catalog.jsonb_array_elements(
         COALESCE(locked_run.reference_snapshot -> 'actor_references', '[]'::JSONB)
       ) AS source(value)
       WHERE source.value ->> 'sha256' = selected_hash
     )
     OR locked_run.reference_snapshot #>> '{location_reference,sha256}' = selected_hash THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: selected master is an unchanged source reference';
  END IF;

  -- Different runs for the same exact actor bytes serialize here before the
  -- unique-row claim/recheck. The table constraint remains the final guard.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      locked_run.actor_identity_id::TEXT || ':' || actor_fingerprint,
      0
    )
  );

  SELECT anchor.*
  INTO anchor_row
  FROM public.semantic_actor_scene_plate_anchors AS anchor
  WHERE anchor.actor_identity_id = locked_run.actor_identity_id
    AND anchor.actor_reference_fingerprint = actor_fingerprint
  FOR UPDATE;

  IF derivation_mode = 'bootstrap' THEN
    IF NULLIF(selected_candidate ->> 'canonical_anchor_id', '') IS NOT NULL
       OR NULLIF(selected_candidate ->> 'canonical_anchor_sha256', '') IS NOT NULL THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: bootstrap candidate claims a pre-existing anchor';
    END IF;
    IF anchor_row.id IS NULL THEN
      INSERT INTO public.semantic_actor_scene_plate_anchors (
        actor_identity_id,
        actor_reference_fingerprint,
        source_run_id,
        master_storage_uri,
        master_sha256,
        master_byte_length,
        master_mime_type,
        provider_model,
        visual_contract_hash
      ) VALUES (
        locked_run.actor_identity_id,
        actor_fingerprint,
        p_run_id,
        selected_storage_uri,
        selected_hash,
        selected_byte_length,
        selected_mime_type,
        selected_provider_model,
        visual_contract_hash
      )
      RETURNING * INTO anchor_row;
    ELSIF anchor_row.master_sha256 IS DISTINCT FROM selected_hash THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: actor anchor was established by another approved master; regenerate from the canonical anchor';
    END IF;
  ELSE
    IF anchor_row.id IS NULL
       OR anchor_row.id::TEXT IS DISTINCT FROM selected_candidate ->> 'canonical_anchor_id'
       OR anchor_row.master_sha256 IS DISTINCT FROM selected_candidate ->> 'canonical_anchor_sha256'
       OR selected_hash IS NOT DISTINCT FROM anchor_row.master_sha256 THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: canonical actor anchor changed or was not used';
    END IF;
  END IF;

  approved_master := selected_candidate || pg_catalog.jsonb_build_object(
    'candidates', locked_run.master_snapshot -> 'candidates',
    'visual_contract', locked_run.master_snapshot -> 'visual_contract',
    'prompt_writer_system_prompt', locked_run.master_snapshot -> 'prompt_writer_system_prompt',
    'prompt_writer_system_prompt_sha256', locked_run.master_snapshot -> 'prompt_writer_system_prompt_sha256',
    'prompt_writer_output', locked_run.master_snapshot -> 'prompt_writer_output',
    'composition_prompt', locked_run.master_snapshot -> 'composition_prompt',
    'scene_plate_prompts', locked_run.master_snapshot -> 'scene_plate_prompts',
    'approved_candidate_index', p_candidate_index,
    'approved_by', p_approved_by,
    'claimed_canonical_anchor_id', anchor_row.id,
    'claimed_canonical_anchor_sha256', anchor_row.master_sha256,
    'claimed_canonical_anchor_source_run_id', anchor_row.source_run_id
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

REVOKE ALL ON FUNCTION public.approve_semantic_video_master(UUID, INTEGER, INTEGER, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_semantic_video_master(UUID, INTEGER, INTEGER, TEXT, TEXT)
  TO service_role;
