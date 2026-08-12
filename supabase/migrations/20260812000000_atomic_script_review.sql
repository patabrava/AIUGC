-- Persist one script-review decision and advance the batch in one transaction.
-- The HTTP approval path can now replace three sequential PostgREST writes/reads
-- with one RPC after it has validated the submitted script.

CREATE OR REPLACE FUNCTION public.apply_post_script_review(
  p_post_id UUID,
  p_seed_data JSONB,
  p_video_prompt_json JSONB,
  p_video_status TEXT DEFAULT NULL,
  p_post_type TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  target_post public.posts%ROWTYPE;
  current_batch_state TEXT;
  review_status TEXT;
  approved_count INTEGER;
  pending_count INTEGER;
BEGIN
  IF p_post_id IS NULL
     OR pg_catalog.jsonb_typeof(p_seed_data) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'script review contract is invalid';
  END IF;

  review_status := COALESCE(p_seed_data ->> 'script_review_status', '');
  IF review_status NOT IN ('approved', 'removed', 'pending') THEN
    RAISE EXCEPTION 'script review status is invalid';
  END IF;

  SELECT post.*
  INTO target_post
  FROM public.posts AS post
  WHERE post.id = p_post_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = 'P0002',
      MESSAGE = 'script review post was not found';
  END IF;

  SELECT batch.state
  INTO current_batch_state
  FROM public.batches AS batch
  WHERE batch.id = target_post.batch_id
  FOR UPDATE;

  BEGIN
    UPDATE public.posts AS post
    SET seed_data = p_seed_data,
        video_prompt_json = p_video_prompt_json,
        video_status = COALESCE(p_video_status, post.video_status),
        post_type = COALESCE(NULLIF(pg_catalog.btrim(p_post_type), ''), post.post_type)
    WHERE post.id = p_post_id;
  EXCEPTION WHEN check_violation THEN
    -- Older deployments constrain posts.post_type more narrowly. The canonical
    -- free-form value remains in seed_data.manual_post_type in that case.
    UPDATE public.posts AS post
    SET seed_data = p_seed_data,
        video_prompt_json = p_video_prompt_json,
        video_status = COALESCE(p_video_status, post.video_status)
    WHERE post.id = p_post_id;
  END;

  IF review_status IN ('approved', 'removed')
     AND current_batch_state = 'S2_SEEDED' THEN
    SELECT
      pg_catalog.count(*) FILTER (
        WHERE COALESCE(post.seed_data ->> 'script_review_status', 'pending') = 'approved'
      )::INTEGER,
      pg_catalog.count(*) FILTER (
        WHERE COALESCE(post.seed_data ->> 'script_review_status', 'pending')
          NOT IN ('approved', 'removed')
      )::INTEGER
    INTO approved_count, pending_count
    FROM public.posts AS post
    WHERE post.batch_id = target_post.batch_id;

    IF approved_count > 0 AND pending_count = 0 THEN
      UPDATE public.batches AS batch
      SET state = 'S4_SCRIPTED'
      WHERE batch.id = target_post.batch_id
        AND batch.state = 'S2_SEEDED';
      current_batch_state := 'S4_SCRIPTED';
    END IF;
  END IF;

  RETURN pg_catalog.jsonb_build_object(
    'post_id', p_post_id,
    'batch_id', target_post.batch_id,
    'script_review_status', review_status,
    'batch_state', current_batch_state
  );
END;
$$;

REVOKE ALL ON FUNCTION public.apply_post_script_review(UUID, JSONB, JSONB, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_post_script_review(UUID, JSONB, JSONB, TEXT, TEXT)
  TO service_role;
