-- Make original-actor identity evidence and explicit human attestation part of
-- the transactional Semantic UGC scene-plate approval contract.

ALTER TABLE public.semantic_actor_scene_plate_anchors
  ADD COLUMN IF NOT EXISTS generation_contract_hash TEXT,
  ADD COLUMN IF NOT EXISTS identity_gate_result JSONB,
  ADD COLUMN IF NOT EXISTS approved_by TEXT,
  ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'legacy_unverified';

ALTER TABLE public.semantic_actor_scene_plate_anchors
  DROP CONSTRAINT IF EXISTS semantic_actor_scene_plate_anchors_master_mime_type_check;

ALTER TABLE public.semantic_actor_scene_plate_anchors
  ADD CONSTRAINT semantic_actor_scene_plate_anchors_master_mime_type_check
  CHECK (master_mime_type IN ('image/png', 'image/jpeg'));

UPDATE public.semantic_actor_scene_plate_anchors
SET verification_status = 'legacy_unverified'
WHERE generation_contract_hash IS NULL
   OR identity_gate_result IS NULL
   OR approved_by IS NULL
   OR approved_at IS NULL;

ALTER TABLE public.semantic_actor_scene_plate_anchors
  DROP CONSTRAINT IF EXISTS semantic_actor_scene_plate_anchors_actor_fingerprint_key;

CREATE UNIQUE INDEX IF NOT EXISTS semantic_actor_scene_plate_anchors_verified_contract_key
  ON public.semantic_actor_scene_plate_anchors (
    actor_identity_id,
    actor_reference_fingerprint,
    generation_contract_hash
  )
  WHERE verification_status = 'verified'
    AND generation_contract_hash IS NOT NULL;

DROP FUNCTION IF EXISTS public.approve_semantic_video_master(UUID, INTEGER, INTEGER, TEXT, TEXT);

CREATE OR REPLACE FUNCTION public.approve_semantic_video_master(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_candidate_index INTEGER,
  p_approved_by TEXT,
  p_identity_attestation BOOLEAN,
  p_attestation_version TEXT,
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
  gate JSONB;
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
  v_generation_contract_hash TEXT;
  approval_time TIMESTAMPTZ := pg_catalog.clock_timestamp();
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
  IF p_identity_attestation IS DISTINCT FROM TRUE
     OR p_attestation_version IS DISTINCT FROM 'semantic-actor-identity-v1' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: explicit actor identity attestation is required';
  END IF;

  SELECT run.*
  INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND
     OR locked_run.revision IS DISTINCT FROM p_expected_revision
     OR locked_run.stage IS DISTINCT FROM 'awaiting_reference_approval' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: master approval state changed';
  END IF;
  IF locked_run.actor_identity_id IS NULL
     OR pg_catalog.jsonb_typeof(locked_run.master_snapshot -> 'candidates')
        IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: master candidate state is incomplete';
  END IF;

  SELECT pg_catalog.count(*), (
    SELECT candidate.value
    FROM pg_catalog.jsonb_array_elements(
      locked_run.master_snapshot -> 'candidates'
    ) AS candidate(value)
    WHERE pg_catalog.jsonb_typeof(candidate.value) = 'object'
      AND pg_catalog.jsonb_typeof(candidate.value -> 'index') = 'number'
      AND (candidate.value ->> 'index')::INTEGER = p_candidate_index
    LIMIT 1
  )
  INTO candidate_count, selected_candidate
  FROM pg_catalog.jsonb_array_elements(
    locked_run.master_snapshot -> 'candidates'
  ) AS candidate(value)
  WHERE pg_catalog.jsonb_typeof(candidate.value) = 'object'
    AND pg_catalog.jsonb_typeof(candidate.value -> 'index') = 'number'
    AND (candidate.value ->> 'index')::INTEGER = p_candidate_index;

  IF candidate_count IS DISTINCT FROM 1 OR selected_candidate IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: selected master candidate is unavailable';
  END IF;

  selected_hash := selected_candidate ->> 'sha256';
  selected_storage_uri := selected_candidate ->> 'storage_uri';
  selected_mime_type := selected_candidate ->> 'mime_type';
  selected_provider_model := selected_candidate ->> 'provider_model';
  selected_byte_length := CASE
    WHEN pg_catalog.jsonb_typeof(selected_candidate -> 'byte_length') = 'number'
    THEN (selected_candidate ->> 'byte_length')::INTEGER
    ELSE 0
  END;
  actor_fingerprint := selected_candidate ->> 'actor_reference_fingerprint';
  derivation_mode := selected_candidate ->> 'derivation_mode';
  visual_contract_hash := selected_candidate ->> 'visual_contract_hash';
  v_generation_contract_hash := selected_candidate ->> 'generation_contract_hash';
  gate := selected_candidate -> 'identity_gate_result';

  IF NULLIF(pg_catalog.btrim(selected_storage_uri), '') IS NULL
     OR selected_hash !~ '^[0-9a-f]{64}$'
     OR selected_byte_length <= 0
     OR selected_mime_type IS NULL
     OR selected_mime_type NOT IN ('image/png', 'image/jpeg')
     OR NULLIF(pg_catalog.btrim(selected_provider_model), '') IS NULL
     OR actor_fingerprint !~ '^[0-9a-f]{64}$'
     OR actor_fingerprint IS DISTINCT FROM
        locked_run.reference_snapshot ->> 'actor_reference_fingerprint'
     OR actor_fingerprint IS DISTINCT FROM
        locked_run.master_snapshot ->> 'actor_reference_fingerprint'
     OR derivation_mode NOT IN ('bootstrap', 'canonical_anchor')
     OR derivation_mode IS DISTINCT FROM
        locked_run.master_snapshot ->> 'derivation_mode'
     OR visual_contract_hash !~ '^[0-9a-f]{64}$'
     OR visual_contract_hash IS DISTINCT FROM
        locked_run.master_snapshot ->> 'visual_contract_hash'
     OR visual_contract_hash IS DISTINCT FROM
        locked_run.reference_snapshot #>> '{visual_contract,contract_hash}'
     OR v_generation_contract_hash !~ '^[0-9a-f]{64}$'
     OR v_generation_contract_hash IS DISTINCT FROM
        locked_run.master_snapshot ->> 'generation_contract_hash'
     OR v_generation_contract_hash IS DISTINCT FROM
        locked_run.reference_snapshot #>> '{scene_plate_generation_contract,contract_hash}'
     OR selected_provider_model IS DISTINCT FROM
        locked_run.reference_snapshot #>> '{scene_plate_generation_contract,model}' THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: selected master candidate lineage is invalid';
  END IF;

  IF pg_catalog.jsonb_typeof(gate) IS DISTINCT FROM 'object'
     OR gate ->> 'status' IS DISTINCT FROM 'passed'
     OR gate -> 'passed' IS DISTINCT FROM 'true'::JSONB
     OR gate ->> 'candidate_sha256' IS DISTINCT FROM selected_hash
     OR gate ->> 'evaluated_actor_reference_fingerprint'
        IS DISTINCT FROM actor_fingerprint
     OR gate ->> 'evaluator_model' IS DISTINCT FROM
        locked_run.reference_snapshot
          #>> '{scene_plate_generation_contract,identity_evaluator_model}'
     OR gate ->> 'evaluator_contract_version'
        IS DISTINCT FROM 'semantic-scene-identity-v2'
     OR pg_catalog.jsonb_typeof(gate -> 'confidence') IS DISTINCT FROM 'number'
     OR (gate ->> 'confidence')::NUMERIC <
        (
          locked_run.reference_snapshot
            #>> '{scene_plate_generation_contract,minimum_identity_confidence}'
        )::NUMERIC
     OR gate -> 'blocking_reasons' IS DISTINCT FROM '[]'::JSONB
     OR NOT COALESCE(
       gate -> 'component_results' @> '{
         "same_person": true,
         "facial_geometry_consistent": true,
         "apparent_age_consistent": true,
         "hairline_and_hair_consistent": true,
         "skin_texture_natural": true,
         "not_beautified_or_stylized": true,
         "no_face_artifacts": true
       }'::JSONB,
       FALSE
     ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: selected master failed the current actor identity gate';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_catalog.jsonb_array_elements(
      COALESCE(locked_run.reference_snapshot -> 'actor_references', '[]'::JSONB)
    ) AS source(value)
    WHERE source.value ->> 'sha256' = selected_hash
  ) OR locked_run.reference_snapshot #>> '{location_reference,sha256}' = selected_hash THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: selected master is an unchanged source reference';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      locked_run.actor_identity_id::TEXT || ':' || actor_fingerprint
        || ':' || v_generation_contract_hash,
      0
    )
  );

  SELECT anchor.*
  INTO anchor_row
  FROM public.semantic_actor_scene_plate_anchors AS anchor
  WHERE anchor.actor_identity_id = locked_run.actor_identity_id
    AND anchor.actor_reference_fingerprint = actor_fingerprint
    AND anchor.generation_contract_hash = v_generation_contract_hash
    AND anchor.verification_status = 'verified'
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
        generation_contract_hash,
        source_run_id,
        master_storage_uri,
        master_sha256,
        master_byte_length,
        master_mime_type,
        provider_model,
        visual_contract_hash,
        identity_gate_result,
        approved_by,
        approved_at,
        verification_status
      ) VALUES (
        locked_run.actor_identity_id,
        actor_fingerprint,
        v_generation_contract_hash,
        p_run_id,
        selected_storage_uri,
        selected_hash,
        selected_byte_length,
        selected_mime_type,
        selected_provider_model,
        visual_contract_hash,
        gate,
        p_approved_by,
        approval_time,
        'verified'
      )
      RETURNING * INTO anchor_row;
    ELSIF anchor_row.master_sha256 IS DISTINCT FROM selected_hash THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: current-contract actor anchor differs; regenerate from it';
    END IF;
  ELSE
    IF anchor_row.id IS NULL
       OR anchor_row.id::TEXT IS DISTINCT FROM
          selected_candidate ->> 'canonical_anchor_id'
       OR anchor_row.master_sha256 IS DISTINCT FROM
          selected_candidate ->> 'canonical_anchor_sha256'
       OR selected_hash IS NOT DISTINCT FROM anchor_row.master_sha256 THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: verified canonical actor anchor changed or was not used';
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
    'approved_at', approval_time,
    'identity_attestation', TRUE,
    'attestation_version', p_attestation_version,
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

REVOKE ALL ON FUNCTION public.approve_semantic_video_master(
  UUID, INTEGER, INTEGER, TEXT, BOOLEAN, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_semantic_video_master(
  UUID, INTEGER, INTEGER, TEXT, BOOLEAN, TEXT, TEXT
) TO service_role;
