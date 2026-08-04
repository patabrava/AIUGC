-- A candidate request can fail after its reservation expires. In that case the
-- token-fenced progress RPC correctly rejects the late "failed" update, so the
-- reservation release must persist the terminal retry state in the same atomic
-- transition that clears the token. This preserves every partial candidate and
-- prevents an orphaned active phase from projecting as idle forever.
CREATE OR REPLACE FUNCTION public.release_semantic_video_candidate_reservation(
  p_run_id UUID,
  p_expected_revision INTEGER,
  p_reservation_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  locked_run public.semantic_video_runs%ROWTYPE;
  updated_run public.semantic_video_runs%ROWTYPE;
  progress_details JSONB;
  partial_candidates JSONB;
  completed_candidates INTEGER;
BEGIN
  SELECT run.* INTO locked_run
  FROM public.semantic_video_runs AS run
  WHERE run.id = p_run_id
  FOR UPDATE;

  IF NOT FOUND
     OR locked_run.revision IS DISTINCT FROM p_expected_revision
     OR locked_run.stage IS DISTINCT FROM 'awaiting_reference_approval'
     OR locked_run.candidate_reservation_token IS DISTINCT FROM p_reservation_token
     OR (CASE
       WHEN NOT (locked_run.master_snapshot ? 'candidates') THEN FALSE
       WHEN pg_catalog.jsonb_typeof(locked_run.master_snapshot -> 'candidates')
         IS DISTINCT FROM 'array' THEN TRUE
       ELSE pg_catalog.jsonb_array_length(locked_run.master_snapshot -> 'candidates') <> 0
     END) THEN
    RAISE EXCEPTION USING
      ERRCODE = '40001',
      MESSAGE = 'semantic_video_conflict: candidate reservation release contract is stale';
  END IF;

  progress_details := CASE
    WHEN pg_catalog.jsonb_typeof(
      locked_run.candidate_generation_progress -> 'details'
    ) = 'object'
      THEN locked_run.candidate_generation_progress -> 'details'
    ELSE '{}'::JSONB
  END;
  partial_candidates := CASE
    WHEN pg_catalog.jsonb_typeof(progress_details -> 'partial_candidates') = 'array'
      THEN progress_details -> 'partial_candidates'
    ELSE '[]'::JSONB
  END;
  completed_candidates := pg_catalog.jsonb_array_length(partial_candidates);

  UPDATE public.semantic_video_runs AS run
  SET candidate_generation_progress = CASE
        WHEN pg_catalog.jsonb_typeof(run.candidate_generation_progress) = 'object'
          AND run.candidate_generation_progress ->> 'phase' IN (
            'preparing_references',
            'generating_images',
            'checking_diversity',
            'regenerating_duplicates',
            'checking_identity',
            'saving_candidates'
          )
        THEN run.candidate_generation_progress
          || pg_catalog.jsonb_build_object(
            'phase', 'failed',
            'updated_at', pg_catalog.clock_timestamp()::TEXT,
            'details', progress_details || pg_catalog.jsonb_build_object(
              'candidate_count', 3,
              'completed_candidates', completed_candidates,
              'partial_candidates', partial_candidates,
              'retryable', TRUE,
              'failure_code', COALESCE(
                NULLIF(progress_details ->> 'failure_code', ''),
                'reservation_released_after_generation_failure'
              )
            )
          )
        ELSE run.candidate_generation_progress
      END,
      candidate_reservation_owner = NULL,
      candidate_reservation_token = NULL,
      candidate_reservation_expires_at = NULL,
      revision = run.revision + 1
  WHERE run.id = p_run_id
  RETURNING run.* INTO updated_run;

  RETURN pg_catalog.to_jsonb(updated_run);
END;
$$;

REVOKE ALL ON FUNCTION public.release_semantic_video_candidate_reservation(UUID, INTEGER, UUID)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.release_semantic_video_candidate_reservation(UUID, INTEGER, UUID)
  TO service_role;

-- Repair rows released after the previous one-time backfill. The read path also
-- projects these as stalled, but persisting the terminal phase makes recovery
-- explicit to every consumer and retains exact partial-candidate metadata.
UPDATE public.semantic_video_runs AS run
SET candidate_generation_progress =
  run.candidate_generation_progress
  || pg_catalog.jsonb_build_object(
    'phase', 'failed',
    'updated_at', pg_catalog.clock_timestamp()::TEXT,
    'details',
      COALESCE(run.candidate_generation_progress -> 'details', '{}'::JSONB)
      || pg_catalog.jsonb_build_object(
        'candidate_count', 3,
        'completed_candidates', pg_catalog.jsonb_array_length(
          COALESCE(
            run.candidate_generation_progress -> 'details' -> 'partial_candidates',
            '[]'::JSONB
          )
        ),
        'retryable', TRUE,
        'failure_code', 'released_generation_recovered'
      )
  )
WHERE run.stage = 'awaiting_reference_approval'
  AND run.candidate_reservation_token IS NULL
  AND run.candidate_reservation_owner IS NULL
  AND run.candidate_reservation_expires_at IS NULL
  AND pg_catalog.jsonb_typeof(run.candidate_generation_progress) = 'object'
  AND run.candidate_generation_progress ->> 'phase' IN (
    'preparing_references',
    'generating_images',
    'checking_diversity',
    'regenerating_duplicates',
    'checking_identity',
    'saving_candidates'
  )
  AND pg_catalog.jsonb_typeof(
    COALESCE(
      run.candidate_generation_progress -> 'details' -> 'partial_candidates',
      '[]'::JSONB
    )
  ) = 'array'
  AND (
    NOT (run.master_snapshot ? 'candidates')
    OR pg_catalog.jsonb_typeof(run.master_snapshot -> 'candidates') <> 'array'
    OR pg_catalog.jsonb_array_length(run.master_snapshot -> 'candidates') = 0
  );
