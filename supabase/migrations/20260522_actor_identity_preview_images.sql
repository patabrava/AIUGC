-- Migration: add stable preview images for ActorIdentity roster cards and active selection.
-- Date: 2026-05-22

ALTER TABLE public.actor_identities
  ADD COLUMN IF NOT EXISTS portrait_image_url TEXT,
  ADD COLUMN IF NOT EXISTS cover_image_url TEXT;

UPDATE public.actor_identities
SET
  portrait_image_url = COALESCE(portrait_image_url, NULLIF(training_images->>0, '')),
  cover_image_url = COALESCE(
    cover_image_url,
    NULLIF(training_images->>0, '')
  )
WHERE portrait_image_url IS NULL OR cover_image_url IS NULL;
