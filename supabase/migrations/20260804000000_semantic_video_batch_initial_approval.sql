-- Approve every currently-ready Semantic UGC plan in one database transaction.
-- This restores the server-owned batch boundary used by the legacy generate-all flow
-- while preserving the independent-run Semantic worker model.

CREATE OR REPLACE FUNCTION public.approve_semantic_video_batch_initial_plans(
  p_batch_id UUID,
  p_approvals JSONB,
  p_approved_by TEXT,
  p_reason TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  approval_count INTEGER;
  distinct_run_count INTEGER;
  eligible_count INTEGER;
  matched_count INTEGER;
  approval_input RECORD;
  approval_result JSONB;
  approval_results JSONB := '[]'::JSONB;
  total_provider_seconds INTEGER := 0;
  total_quota_units INTEGER := 0;
  total_cost NUMERIC := 0;
BEGIN
  IF p_batch_id IS NULL
     OR pg_catalog.jsonb_typeof(p_approvals) IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_array_length(p_approvals) < 2
     OR pg_catalog.jsonb_array_length(p_approvals) > 100 THEN
    RAISE EXCEPTION 'semantic video batch approval contract is invalid';
  END IF;
  IF NULLIF(pg_catalog.btrim(p_approved_by), '') IS NULL THEN
    RAISE EXCEPTION 'semantic video batch approver is required';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.batches AS batch
    WHERE batch.id = p_batch_id
      AND batch.creation_mode IN ('semantic_ugc', 'manual_semantic_ugc')
  ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: batch is not an active Semantic UGC batch';
  END IF;

  SELECT pg_catalog.count(*)::INTEGER, pg_catalog.count(DISTINCT item.run_id)::INTEGER
  INTO approval_count, distinct_run_count
  FROM pg_catalog.jsonb_to_recordset(p_approvals)
    AS item(run_id UUID, expected_revision INTEGER, plan_hash TEXT);

  IF approval_count IS DISTINCT FROM pg_catalog.jsonb_array_length(p_approvals)
     OR distinct_run_count IS DISTINCT FROM approval_count
     OR EXISTS (
       SELECT 1
       FROM pg_catalog.jsonb_to_recordset(p_approvals)
         AS item(run_id UUID, expected_revision INTEGER, plan_hash TEXT)
       WHERE item.run_id IS NULL
          OR item.expected_revision IS NULL
          OR item.expected_revision < 0
          OR NULLIF(pg_catalog.btrim(item.plan_hash), '') IS NULL
     ) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: batch approval items are invalid or duplicated';
  END IF;

  -- Lock the full ready set in deterministic order before comparing the caller's
  -- snapshot. The nested single-run RPCs reuse these row locks in this transaction.
  PERFORM run.id
  FROM public.semantic_video_runs AS run
  JOIN public.posts AS post
    ON post.id = run.post_id
   AND post.batch_id = run.batch_id
  WHERE run.batch_id = p_batch_id
    AND run.stage = 'awaiting_paid_approval'
    AND run.plan_hash IS NOT NULL
    AND COALESCE(post.seed_data ->> 'script_review_status', 'pending') <> 'removed'
  ORDER BY run.id
  FOR UPDATE OF run;

  SELECT pg_catalog.count(*)::INTEGER
  INTO eligible_count
  FROM public.semantic_video_runs AS run
  JOIN public.posts AS post
    ON post.id = run.post_id
   AND post.batch_id = run.batch_id
  WHERE run.batch_id = p_batch_id
    AND run.stage = 'awaiting_paid_approval'
    AND run.plan_hash IS NOT NULL
    AND COALESCE(post.seed_data ->> 'script_review_status', 'pending') <> 'removed';

  SELECT pg_catalog.count(*)::INTEGER
  INTO matched_count
  FROM pg_catalog.jsonb_to_recordset(p_approvals)
    AS item(run_id UUID, expected_revision INTEGER, plan_hash TEXT)
  JOIN public.semantic_video_runs AS run
    ON run.id = item.run_id
   AND run.batch_id = p_batch_id
   AND run.stage = 'awaiting_paid_approval'
   AND run.revision = item.expected_revision
   AND run.plan_hash = item.plan_hash
  JOIN public.posts AS post
    ON post.id = run.post_id
   AND post.batch_id = run.batch_id
  WHERE COALESCE(post.seed_data ->> 'script_review_status', 'pending') <> 'removed';

  IF eligible_count < 2
     OR approval_count IS DISTINCT FROM eligible_count
     OR matched_count IS DISTINCT FROM eligible_count THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: ready batch plans changed before approval';
  END IF;

  FOR approval_input IN
    SELECT item.run_id, item.expected_revision, item.plan_hash
    FROM pg_catalog.jsonb_to_recordset(p_approvals)
      AS item(run_id UUID, expected_revision INTEGER, plan_hash TEXT)
    ORDER BY item.run_id
  LOOP
    approval_result := public.approve_semantic_video_initial_plan(
      approval_input.run_id,
      approval_input.expected_revision,
      approval_input.plan_hash,
      p_approved_by,
      p_reason
    );
    approval_results := approval_results || pg_catalog.jsonb_build_array(approval_result);
    total_provider_seconds := total_provider_seconds
      + (approval_result -> 'approval' ->> 'approved_provider_seconds')::INTEGER;
    total_quota_units := total_quota_units
      + (approval_result -> 'approval' ->> 'quota_units')::INTEGER;
    total_cost := total_cost
      + (approval_result -> 'approval' ->> 'estimated_cost_usd')::NUMERIC;
  END LOOP;

  RETURN pg_catalog.jsonb_build_object(
    'batch_id', p_batch_id,
    'approval_count', approval_count,
    'approved_provider_seconds', total_provider_seconds,
    'quota_units', total_quota_units,
    'estimated_cost_usd', pg_catalog.to_char(total_cost, 'FM999999999990.00'),
    'approvals', approval_results
  );
END;
$$;

REVOKE ALL ON FUNCTION public.approve_semantic_video_batch_initial_plans(UUID, JSONB, TEXT, TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.approve_semantic_video_batch_initial_plans(UUID, JSONB, TEXT, TEXT)
  TO service_role;
