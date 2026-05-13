-- Migration: store TikTok Content Posting API required fields per post and per batch.
-- Required for the Content Sharing Guidelines reapply (Required UX Implementation §1–§5).
-- Date: 2026-05-13

ALTER TABLE public.posts
  ADD COLUMN IF NOT EXISTS tiktok_settings JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS tiktok_defaults JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN public.posts.tiktok_settings IS
  'TikTok Content Posting API per-post fields: title, privacy_level, allow_comment, allow_duet, allow_stitch, commercial_disclosure, your_brand, branded_content.';

COMMENT ON COLUMN public.batches.tiktok_defaults IS
  'Batch-level TikTok defaults; copied into each post.tiktok_settings on first edit and overridable per post.';
