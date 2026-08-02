CREATE OR REPLACE FUNCTION public.claim_semantic_video_run(
  worker_id TEXT,
  lease_seconds INTEGER,
  requested_run_id UUID DEFAULT NULL
)
RETURNS SETOF public.semantic_video_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  IF NULLIF(pg_catalog.btrim(worker_id), '') IS NULL THEN
    RAISE EXCEPTION 'worker_id is required';
  END IF;
  IF worker_id NOT LIKE 'semantic-video-contract-v2-%' THEN
    RAISE EXCEPTION USING
      ERRCODE = '42501',
      MESSAGE = 'semantic_video_conflict: worker contract is stale';
  END IF;
  IF lease_seconds IS NULL OR lease_seconds <= 0 OR lease_seconds > 3600 THEN
    RAISE EXCEPTION 'lease_seconds must be between 1 and 3600';
  END IF;

  RETURN QUERY
  WITH candidate AS (
    SELECT run.id
    FROM public.semantic_video_runs AS run
    WHERE run.stage IN (
      'generating', 'transcript_qa', 'identity_qa', 'voice_qa',
      'acoustic_qa', 'composing', 'uploading'
    )
      AND (requested_run_id IS NULL OR run.id = requested_run_id)
      AND (run.lease_expires_at IS NULL OR run.lease_expires_at <= now())
      AND (
        run.stage <> 'generating'
        OR (
          EXISTS (
            SELECT 1
            FROM (
              SELECT DISTINCT ON (take.take_index) take.submission_state
              FROM public.semantic_video_takes AS take
              WHERE take.run_id = run.id
              ORDER BY take.take_index, take.attempt DESC
            ) AS latest
          )
          AND NOT EXISTS (
            SELECT 1
            FROM (
              SELECT DISTINCT ON (take.take_index) take.submission_state
              FROM public.semantic_video_takes AS take
              WHERE take.run_id = run.id
              ORDER BY take.take_index, take.attempt DESC
            ) AS latest
            WHERE latest.submission_state IN ('intent_persisted', 'submission_unknown')
          )
          AND (
            EXISTS (
              SELECT 1
              FROM (
                SELECT DISTINCT ON (take.take_index) take.submission_state
                FROM public.semantic_video_takes AS take
                WHERE take.run_id = run.id
                ORDER BY take.take_index, take.attempt DESC
              ) AS latest
              WHERE latest.submission_state IN ('planned', 'reserved', 'submitted')
            )
            OR NOT EXISTS (
              SELECT 1
              FROM (
                SELECT DISTINCT ON (take.take_index) take.submission_state
                FROM public.semantic_video_takes AS take
                WHERE take.run_id = run.id
                ORDER BY take.take_index, take.attempt DESC
              ) AS latest
              WHERE latest.submission_state IS DISTINCT FROM 'completed'
            )
          )
        )
      )
    ORDER BY run.updated_at, run.created_at, run.id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
  ), claimed AS (
    UPDATE public.semantic_video_runs AS run
    SET lease_owner = worker_id,
        lease_token = pg_catalog.gen_random_uuid(),
        lease_expires_at = now() + pg_catalog.make_interval(secs => lease_seconds),
        revision = run.revision + 1
    FROM candidate
    WHERE run.id = candidate.id
    RETURNING run.*
  )
  SELECT * FROM claimed;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER, UUID)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_semantic_video_run(TEXT, INTEGER, UUID)
  TO service_role;
