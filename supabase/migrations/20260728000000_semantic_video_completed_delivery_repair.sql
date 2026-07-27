-- Atomically replace a completed Semantic UGC delivery using already-paid artifacts.
-- This path is deliberately separate from provider submission and cannot reserve quota.

CREATE OR REPLACE FUNCTION public.repair_completed_semantic_video_delivery(
  p_run_id UUID,
  p_expected_revision BIGINT,
  p_expected_final_video_sha256 TEXT,
  p_expected_final_caption_sha256 TEXT,
  p_final_video_uri TEXT,
  p_final_video_sha256 TEXT,
  p_final_caption_uri TEXT,
  p_final_caption_sha256 TEXT,
  p_artifact_manifest JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE locked_run public.semantic_video_runs%ROWTYPE;
DECLARE updated_run public.semantic_video_runs%ROWTYPE;
DECLARE delivery JSONB;
DECLARE terminal_qa JSONB;
DECLARE stitch_metadata JSONB;
BEGIN
  SELECT run.* INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND
     OR locked_run.stage IS DISTINCT FROM 'completed'
     OR locked_run.revision IS DISTINCT FROM p_expected_revision
     OR locked_run.final_video_sha256 IS DISTINCT FROM p_expected_final_video_sha256
     OR locked_run.final_caption_sha256 IS DISTINCT FROM p_expected_final_caption_sha256 THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: completed delivery repair was fenced';
  END IF;

  IF NULLIF(pg_catalog.btrim(p_final_video_uri), '') IS NULL
     OR p_final_video_sha256 !~ '^[0-9a-f]{64}$'
     OR NULLIF(pg_catalog.btrim(p_final_caption_uri), '') IS NULL
     OR p_final_caption_sha256 !~ '^[0-9a-f]{64}$'
     OR pg_catalog.jsonb_typeof(p_artifact_manifest) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'semantic video completed delivery repair artifact contract is invalid';
  END IF;

  delivery := p_artifact_manifest -> 'delivery';
  terminal_qa := p_artifact_manifest -> 'pipeline_manifest' -> 'delivery_terminal_qa';
  stitch_metadata := p_artifact_manifest -> 'pipeline_manifest' -> 'stitch' -> 'metadata';
  IF delivery ->> 'passed' IS DISTINCT FROM 'true'
     OR delivery -> 'raw' ->> 'url' IS DISTINCT FROM p_final_video_uri
     OR delivery -> 'raw' ->> 'sha256' IS DISTINCT FROM p_final_video_sha256
     OR delivery -> 'captioned' ->> 'url' IS DISTINCT FROM p_final_caption_uri
     OR delivery -> 'captioned' ->> 'sha256' IS DISTINCT FROM p_final_caption_sha256
     OR terminal_qa ->> 'passed' IS DISTINCT FROM 'true'
     OR terminal_qa ->> 'reset_detected' IS DISTINCT FROM 'false'
     OR terminal_qa ->> 'requires_paid_regeneration' IS DISTINCT FROM 'false'
     OR terminal_qa ->> 'video_sha256' IS DISTINCT FROM p_final_video_sha256
     OR stitch_metadata ->> 'stitch_end_pan_protection_applied' IS DISTINCT FROM 'true'
     OR (stitch_metadata ->> 'stitch_end_pan_tail_exclusion_s')::NUMERIC IS DISTINCT FROM 0.5
     OR (stitch_metadata ->> 'stitch_delivery_target_s')::NUMERIC IS DISTINCT FROM 8.0 THEN
    RAISE EXCEPTION 'semantic video completed delivery repair QA contract is invalid';
  END IF;

  UPDATE public.semantic_video_runs AS run
  SET final_video_uri = p_final_video_uri,
      final_video_sha256 = p_final_video_sha256,
      final_caption_uri = p_final_caption_uri,
      final_caption_sha256 = p_final_caption_sha256,
      artifact_manifest = p_artifact_manifest,
      failure_envelope = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
    AND run.revision = p_expected_revision
  RETURNING run.* INTO updated_run;

  IF NOT FOUND THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: completed delivery repair lost its revision fence';
  END IF;

  UPDATE public.posts AS post
  SET video_url = p_final_caption_uri,
      video_status = 'caption_completed',
      video_metadata = COALESCE(post.video_metadata, '{}'::JSONB)
        || pg_catalog.jsonb_build_object(
          'semantic_video_run_id', p_run_id,
          'raw_video_url', p_final_video_uri,
          'raw_video_sha256', p_final_video_sha256,
          'caption_video_url', p_final_caption_uri,
          'caption_video_sha256', p_final_caption_sha256,
          'terminal_tail_protection', terminal_qa
        )
  WHERE post.id = locked_run.post_id
    AND post.batch_id = locked_run.batch_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'semantic video completed delivery repair post does not exist';
  END IF;

  RETURN pg_catalog.jsonb_build_object(
    'run', pg_catalog.to_jsonb(updated_run),
    'post_id', locked_run.post_id,
    'video_status', 'caption_completed',
    'provider_submission_created', FALSE
  );
END;
$$;

REVOKE ALL ON FUNCTION public.repair_completed_semantic_video_delivery(
  UUID, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.repair_completed_semantic_video_delivery(
  UUID, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB
) TO service_role;
