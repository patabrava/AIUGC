-- Migration: add manual batch creation mode
-- Date: 2026-04-12

ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS creation_mode TEXT NOT NULL DEFAULT 'automated',
  ADD COLUMN IF NOT EXISTS manual_post_count INTEGER;

ALTER TABLE public.posts
  DROP CONSTRAINT IF EXISTS posts_post_type_check;
