-- Migration: add character consistency mode infrastructure.
-- Date: 2026-05-08

CREATE TABLE IF NOT EXISTS public.characters (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL DEFAULT 'Default Character',
  front_image_url TEXT NOT NULL,
  three_quarter_image_url TEXT NOT NULL,
  profile_image_url TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS characters_one_active
  ON public.characters (is_active)
  WHERE is_active IS TRUE;

ALTER TABLE public.characters ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS character_snapshot JSONB,
  ADD COLUMN IF NOT EXISTS scene_plan JSONB;

COMMENT ON COLUMN public.batches.character_snapshot IS
  'Immutable copy of the active character at batch creation. Populated only when creation_mode = ''character_consistency''.';

COMMENT ON COLUMN public.batches.scene_plan IS
  'Per-batch scene plan keyed by post_type (value/lifestyle/product). Populated by the prompt-build step in character_consistency mode.';
