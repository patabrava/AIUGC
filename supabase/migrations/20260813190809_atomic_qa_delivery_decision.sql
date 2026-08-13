-- Persist one delivery QA decision and reconcile the batch in one transaction.
-- The HTTP route calls this idempotent boundary once instead of issuing a chain
-- of PostgREST reads and writes before it can redirect the operator.

CREATE OR REPLACE FUNCTION public.apply_post_qa_decision(
  p_post_id UUID,
  p_approved BOOLEAN,
  p_notes TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
SET statement_timeout = '8s'
AS $$
DECLARE
  target_post public.posts%ROWTYPE;
  current_batch_state TEXT;
  target_batch_state TEXT;
  creation_mode TEXT;
  normalized_seed_data JSONB;
  identity_source TEXT;
  identity_status TEXT;
  identity_gate_update JSONB;
  active_count INTEGER;
  prompts_ready BOOLEAN;
  videos_ready BOOLEAN;
  qa_ready BOOLEAN;
BEGIN
  IF p_post_id IS NULL OR p_approved IS NULL THEN
    RAISE EXCEPTION USING
      ERRCODE = '22023',
      MESSAGE = 'QA delivery decision contract is invalid';
  END IF;

  SELECT post.*
  INTO target_post
  FROM public.posts AS post
  WHERE post.id = p_post_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN pg_catalog.jsonb_build_object(
      'ok', FALSE,
      'error_code', 'not_found',
      'message', 'Delivery QA post was not found',
      'post_id', p_post_id
    );
  END IF;

  SELECT batch.state, batch.creation_mode
  INTO current_batch_state, creation_mode
  FROM public.batches AS batch
  WHERE batch.id = target_post.batch_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN pg_catalog.jsonb_build_object(
      'ok', FALSE,
      'error_code', 'not_found',
      'message', 'Delivery QA batch was not found',
      'post_id', p_post_id,
      'batch_id', target_post.batch_id
    );
  END IF;

  normalized_seed_data := CASE
    WHEN pg_catalog.jsonb_typeof(target_post.seed_data) = 'object'
      THEN target_post.seed_data
    ELSE '{}'::JSONB
  END;

  identity_source := COALESCE(target_post.video_metadata ->> 'actor_identity_source', '');
  identity_status := COALESCE(target_post.identity_gate_result ->> 'status', '');
  identity_gate_update := target_post.identity_gate_result;

  IF p_approved AND identity_source IN (
    'actor_identity_anchor_images',
    'actor_identity_scene_reference',
    'actor_identity_scene_reference_set'
  ) THEN
    IF identity_status = 'manual_required' THEN
      identity_gate_update := pg_catalog.jsonb_build_object(
        'status', 'passed',
        'reason', 'Operator approved video identity match during QA review',
        'gate_type', 'manual',
        'details', '{}'::JSONB
      );
    ELSIF identity_status <> 'passed' THEN
      RETURN pg_catalog.jsonb_build_object(
        'ok', FALSE,
        'error_code', 'validation_error',
        'message', 'ActorIdentity video identity gate must pass before QA approval.',
        'post_id', p_post_id,
        'batch_id', target_post.batch_id
      );
    END IF;
  END IF;

  IF p_approved THEN
    normalized_seed_data := pg_catalog.jsonb_set(
      normalized_seed_data - 'video_excluded',
      '{video_review_status}',
      '"approved"'::JSONB,
      TRUE
    );
  ELSE
    normalized_seed_data := pg_catalog.jsonb_set(
      pg_catalog.jsonb_set(
        normalized_seed_data,
        '{video_review_status}',
        '"rejected"'::JSONB,
        TRUE
      ),
      '{video_excluded}',
      'true'::JSONB,
      TRUE
    );
  END IF;

  UPDATE public.posts AS post
  SET qa_pass = p_approved,
      qa_notes = COALESCE(p_notes, ''),
      seed_data = normalized_seed_data,
      identity_gate_result = identity_gate_update
  WHERE post.id = p_post_id;

  SELECT
    pg_catalog.count(*)::INTEGER,
    COALESCE(pg_catalog.bool_and(post.video_prompt_json IS NOT NULL), FALSE),
    COALESCE(pg_catalog.bool_and(post.video_status = 'caption_completed'), FALSE),
    COALESCE(pg_catalog.bool_and(post.qa_pass IS TRUE), FALSE)
  INTO active_count, prompts_ready, videos_ready, qa_ready
  FROM public.posts AS post
  WHERE post.batch_id = target_post.batch_id
    AND COALESCE(post.seed_data ->> 'script_review_status', '') <> 'removed'
    AND NOT (post.seed_data @> '{"video_excluded": true}'::JSONB);

  target_batch_state := current_batch_state;
  IF active_count > 0 THEN
    IF target_batch_state = 'S4_SCRIPTED'
       AND (
         prompts_ready
         OR (
           creation_mode IN ('semantic_ugc', 'manual_semantic_ugc')
           AND videos_ready
         )
       ) THEN
      target_batch_state := 'S5_PROMPTS_BUILT';
    END IF;

    IF target_batch_state = 'S5_PROMPTS_BUILT' AND videos_ready THEN
      target_batch_state := 'S6_QA';
    END IF;

    IF target_batch_state = 'S6_QA' AND qa_ready THEN
      target_batch_state := 'S7_PUBLISH_PLAN';
    END IF;
  END IF;

  IF target_batch_state IS DISTINCT FROM current_batch_state THEN
    UPDATE public.batches AS batch
    SET state = target_batch_state
    WHERE batch.id = target_post.batch_id;
  END IF;

  RETURN pg_catalog.jsonb_build_object(
    'ok', TRUE,
    'post_id', p_post_id,
    'batch_id', target_post.batch_id,
    'qa_pass', p_approved,
    'qa_notes', COALESCE(p_notes, ''),
    'qa_auto_checks', target_post.qa_auto_checks,
    'batch_state', target_batch_state,
    'batch_advanced', target_batch_state = 'S7_PUBLISH_PLAN'
  );
END;
$$;

REVOKE ALL ON FUNCTION public.apply_post_qa_decision(UUID, BOOLEAN, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_post_qa_decision(UUID, BOOLEAN, TEXT)
  TO service_role;

NOTIFY pgrst, 'reload schema';
