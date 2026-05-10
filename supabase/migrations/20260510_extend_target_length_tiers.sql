-- Migration: extend allowed target_length_tier values to support manual
-- auto-derived long-script videos (tier 48 ≈ 43s, tier 64 ≈ 57s).
-- Topic-based batches still only emit 8/16/32; the wider set is consumed
-- by the manual auto-resolver in app/features/videos/handlers.py.
-- Date: 2026-05-10

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'batches_target_length_tier_check'
      AND conrelid = 'public.batches'::regclass
  ) THEN
    ALTER TABLE public.batches
    DROP CONSTRAINT batches_target_length_tier_check;
  END IF;

  ALTER TABLE public.batches
  ADD CONSTRAINT batches_target_length_tier_check
  CHECK (
    target_length_tier IS NULL
    OR target_length_tier IN (8, 16, 32, 48, 64)
  );
END $$;
