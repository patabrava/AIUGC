-- Preserve an immutable actor_front object as the canonical standing-presenter
-- master. Generated scene plates continue through the existing approval path.

ALTER FUNCTION public.approve_semantic_video_master(
  UUID, INTEGER, INTEGER, TEXT, BOOLEAN, TEXT, TEXT
) RENAME TO approve_semantic_video_generated_master_v1;

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
  selected_candidate JSONB;
  actor_front JSONB;
  gate JSONB;
  approved_master JSONB;
  derivation_mode TEXT;
  selected_hash TEXT;
  actor_fingerprint TEXT;
  visual_contract_hash TEXT;
  generation_contract_hash TEXT;
  approval_time TIMESTAMPTZ := pg_catalog.clock_timestamp();
BEGIN
  SELECT candidate.value ->> 'derivation_mode'
  INTO derivation_mode
  FROM public.semantic_video_runs AS run
  CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(
    COALESCE(run.master_snapshot -> 'candidates', '[]'::JSONB)
  ) AS candidate(value)
  WHERE run.id = p_run_id
    AND pg_catalog.jsonb_typeof(candidate.value -> 'index') = 'number'
    AND (candidate.value ->> 'index')::INTEGER = p_candidate_index
  LIMIT 1;

  IF derivation_mode IS DISTINCT FROM 'actor_front_passthrough' THEN
    RETURN public.approve_semantic_video_generated_master_v1(
      p_run_id,
      p_expected_revision,
      p_candidate_index,
      p_approved_by,
      p_identity_attestation,
      p_attestation_version,
      p_reason
    );
  END IF;

  IF p_run_id IS NULL
     OR p_expected_revision IS NULL
     OR p_expected_revision < 0
     OR p_candidate_index IS NULL
     OR p_candidate_index < 1
     OR NULLIF(pg_catalog.btrim(p_approved_by), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video master approval identity is invalid';
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

  SELECT candidate.value
  INTO selected_candidate
  FROM pg_catalog.jsonb_array_elements(
    COALESCE(locked_run.master_snapshot -> 'candidates', '[]'::JSONB)
  ) AS candidate(value)
  WHERE pg_catalog.jsonb_typeof(candidate.value -> 'index') = 'number'
    AND (candidate.value ->> 'index')::INTEGER = p_candidate_index;

  SELECT source.value
  INTO actor_front
  FROM pg_catalog.jsonb_array_elements(
    COALESCE(locked_run.reference_snapshot -> 'actor_references', '[]'::JSONB)
  ) AS source(value)
  WHERE source.value ->> 'role' = 'actor_front';

  gate := selected_candidate -> 'identity_gate_result';
  selected_hash := selected_candidate ->> 'sha256';
  actor_fingerprint := selected_candidate ->> 'actor_reference_fingerprint';
  visual_contract_hash := selected_candidate ->> 'visual_contract_hash';
  generation_contract_hash := selected_candidate ->> 'generation_contract_hash';

  IF selected_candidate IS NULL
     OR actor_front IS NULL
     OR selected_candidate ->> 'storage_uri' IS DISTINCT FROM actor_front ->> 'storage_uri'
     OR selected_candidate ->> 'mime_type' IS DISTINCT FROM actor_front ->> 'mime_type'
     OR selected_candidate ->> 'byte_length' IS DISTINCT FROM actor_front ->> 'byte_length'
     OR selected_hash IS DISTINCT FROM actor_front ->> 'sha256'
     OR selected_candidate ->> 'provider_model' IS DISTINCT FROM
        locked_run.reference_snapshot #>> '{scene_plate_generation_contract,model}'
     OR selected_candidate ->> 'provider_model' IS DISTINCT FROM 'actor-front-passthrough-v1'
     OR actor_fingerprint IS DISTINCT FROM
        locked_run.reference_snapshot ->> 'actor_reference_fingerprint'
     OR actor_fingerprint IS DISTINCT FROM
        locked_run.master_snapshot ->> 'actor_reference_fingerprint'
     OR visual_contract_hash IS DISTINCT FROM
        locked_run.reference_snapshot #>> '{visual_contract,contract_hash}'
     OR visual_contract_hash IS DISTINCT FROM
        locked_run.master_snapshot ->> 'visual_contract_hash'
     OR generation_contract_hash IS DISTINCT FROM
        locked_run.reference_snapshot #>> '{scene_plate_generation_contract,contract_hash}'
     OR generation_contract_hash IS DISTINCT FROM
        locked_run.master_snapshot ->> 'generation_contract_hash'
     OR NULLIF(selected_candidate ->> 'canonical_anchor_id', '') IS NOT NULL
     OR NULLIF(selected_candidate ->> 'canonical_anchor_sha256', '') IS NOT NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: actor_front passthrough lineage is invalid';
  END IF;

  IF pg_catalog.jsonb_typeof(gate) IS DISTINCT FROM 'object'
     OR gate ->> 'status' IS DISTINCT FROM 'passed'
     OR gate -> 'passed' IS DISTINCT FROM 'true'::JSONB
     OR gate ->> 'candidate_sha256' IS DISTINCT FROM selected_hash
     OR gate ->> 'evaluated_actor_reference_fingerprint' IS DISTINCT FROM actor_fingerprint
     OR gate ->> 'evaluator_model' IS DISTINCT FROM
        locked_run.reference_snapshot #>> '{scene_plate_generation_contract,identity_evaluator_model}'
     OR gate ->> 'evaluator_contract_version' IS DISTINCT FROM 'semantic-scene-identity-v2'
     OR gate ->> 'evidence_mode' IS DISTINCT FROM 'actor_front_byte_identity'
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
      MESSAGE = 'semantic_video_conflict: actor_front byte-identity evidence is invalid';
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
    'claimed_canonical_anchor_id', NULL,
    'claimed_canonical_anchor_sha256', NULL,
    'claimed_canonical_anchor_source_run_id', NULL
  );

  INSERT INTO public.semantic_video_approvals (
    run_id, approval_type, run_revision, contract_hash,
    approved_take_indexes, approved_provider_seconds, quota_units,
    estimated_cost_usd, approved_by, reason
  ) VALUES (
    p_run_id, 'reference', p_expected_revision, selected_hash,
    '{}'::INTEGER[], 0, 0, 0, p_approved_by, p_reason
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

REVOKE ALL ON FUNCTION public.approve_semantic_video_generated_master_v1(
  UUID, INTEGER, INTEGER, TEXT, BOOLEAN, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_semantic_video_generated_master_v1(
  UUID, INTEGER, INTEGER, TEXT, BOOLEAN, TEXT, TEXT
) TO service_role;
REVOKE ALL ON FUNCTION public.approve_semantic_video_master(
  UUID, INTEGER, INTEGER, TEXT, BOOLEAN, TEXT, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_semantic_video_master(
  UUID, INTEGER, INTEGER, TEXT, BOOLEAN, TEXT, TEXT
) TO service_role;
