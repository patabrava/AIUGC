-- Migration: add duration-tier routing fields for duration-driven Veo batches
-- Date: 2026-03-18

ALTER TABLE public.batches
ADD COLUMN IF NOT EXISTS target_length_tier INTEGER;

ALTER TABLE public.batches
ADD COLUMN IF NOT EXISTS video_pipeline_route TEXT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'batches_target_length_tier_check'
      AND conrelid = 'public.batches'::regclass
  ) THEN
    ALTER TABLE public.batches
    ADD CONSTRAINT batches_target_length_tier_check
    CHECK (
      target_length_tier IS NULL
      OR target_length_tier IN (8, 16, 32)
    );
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'batches_video_pipeline_route_check'
      AND conrelid = 'public.batches'::regclass
  ) THEN
    ALTER TABLE public.batches
    ADD CONSTRAINT batches_video_pipeline_route_check
    CHECK (
      video_pipeline_route IS NULL
      OR video_pipeline_route IN ('short', 'veo_extended')
    );
  END IF;
END $$;
