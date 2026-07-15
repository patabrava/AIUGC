CREATE OR REPLACE FUNCTION public.reuse_semantic_video_prior_attempts(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_plan_hash TEXT,
  p_selected_attempts JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  selected public.semantic_video_takes%ROWTYPE;
  selected_index INTEGER;
  selected_attempt INTEGER;
  next_attempt INTEGER;
  selection RECORD;
BEGIN
  SELECT run.* INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND
     OR locked_run.revision IS DISTINCT FROM p_expected_revision
     OR locked_run.stage IS DISTINCT FROM 'retry_approval_required'
     OR locked_run.plan_hash IS DISTINCT FROM p_plan_hash
     OR pg_catalog.coalesce(locked_run.failure_envelope ->> 'stage', '')
        NOT IN ('transcript_qa', 'acoustic_qa') THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: prior-attempt QA reuse contract is stale';
  END IF;

  IF pg_catalog.jsonb_typeof(p_selected_attempts) IS DISTINCT FROM 'object'
     OR p_selected_attempts = '{}'::JSONB THEN
    RAISE EXCEPTION USING
      ERRCODE = '22023',
      MESSAGE = 'semantic_video_invalid: prior-attempt QA reuse selection is empty';
  END IF;

  FOR selection IN
    SELECT entry.key, entry.value
    FROM pg_catalog.jsonb_each(p_selected_attempts) AS entry
  LOOP
    IF selection.key !~ '^[0-9]+$'
       OR pg_catalog.jsonb_typeof(selection.value) IS DISTINCT FROM 'number'
       OR selection.value::TEXT !~ '^[0-9]+$' THEN
      RAISE EXCEPTION USING
        ERRCODE = '22023',
        MESSAGE = 'semantic_video_invalid: prior-attempt QA reuse selection is invalid';
    END IF;

    selected_index := selection.key::INTEGER;
    selected_attempt := selection.value::TEXT::INTEGER;
    IF NOT EXISTS (
      SELECT 1
      FROM pg_catalog.jsonb_array_elements_text(
        pg_catalog.coalesce(locked_run.failure_envelope -> 'failed_take_indexes', '[]'::JSONB)
      ) AS failed(value)
      WHERE failed.value = selected_index::TEXT
    ) THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: QA reuse may target only failed take indexes';
    END IF;

    SELECT take.* INTO selected
    FROM public.semantic_video_takes AS take
    WHERE take.run_id = p_run_id
      AND take.take_index = selected_index
      AND take.attempt = selected_attempt;

    IF NOT FOUND
       OR selected.submission_state NOT IN ('completed', 'qa_failed')
       OR NULLIF(pg_catalog.btrim(selected.raw_artifact_uri), '') IS NULL
       OR selected.raw_artifact_sha256 !~ '^[0-9a-f]{64}$' THEN
      RAISE EXCEPTION USING
        ERRCODE = '40001',
        MESSAGE = 'semantic_video_conflict: selected prior attempt is not a durable paid take';
    END IF;

    SELECT pg_catalog.coalesce(pg_catalog.max(take.attempt), 0) + 1 INTO next_attempt
    FROM public.semantic_video_takes AS take
    WHERE take.run_id = p_run_id
      AND take.take_index = selected_index;

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
      raw_artifact_uri,
      raw_artifact_sha256,
      retry_guidance
    ) VALUES (
      selected.run_id,
      selected.take_index,
      next_attempt,
      selected.beat_text,
      selected.word_count,
      selected.estimated_speech_seconds,
      selected.provider_duration_seconds,
      selected.shot_transform,
      selected.shot_hash,
      selected.prompt_hash,
      selected.negative_prompt_hash,
      selected.provider_model,
      selected.seed,
      selected.request_contract || pg_catalog.jsonb_build_object(
        'qa_reuse',
        pg_catalog.jsonb_build_object(
          'source_take_id', selected.id,
          'source_attempt', selected.attempt,
          'reason', 'reuse durable paid raw without provider submission'
        )
      ),
      pg_catalog.encode(
        extensions.digest(
          selected.request_hash || pg_catalog.chr(10) || 'qa-reuse' || pg_catalog.chr(10) || next_attempt::TEXT,
          'sha256'
        ),
        'hex'
      ),
      'completed',
      selected.raw_artifact_uri,
      selected.raw_artifact_sha256,
      NULL
    );
  END LOOP;

  UPDATE public.semantic_video_runs AS run
  SET stage = 'transcript_qa',
      failure_envelope = NULL,
      artifact_manifest = pg_catalog.coalesce(run.artifact_manifest, '{}'::JSONB)
        - 'pipeline_manifest'
        - 'transcript_qa'
        - 'visual_qa'
        - 'voice_qa'
        - 'acoustic_qa'
        - 'composition'
        - 'delivery',
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;

  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

REVOKE ALL ON FUNCTION public.reuse_semantic_video_prior_attempts(UUID, INTEGER, TEXT, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reuse_semantic_video_prior_attempts(UUID, INTEGER, TEXT, JSONB)
  TO service_role;
