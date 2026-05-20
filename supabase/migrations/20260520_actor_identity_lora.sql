-- Migration: add Magnific-backed ActorIdentity and scene reference image state.
-- Date: 2026-05-20

CREATE TABLE IF NOT EXISTS public.actor_identities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  provider TEXT NOT NULL DEFAULT 'magnific',
  provider_lora_id TEXT,
  provider_lora_name TEXT,
  provider_training_task_id TEXT,
  training_status TEXT NOT NULL DEFAULT 'not_started',
  training_phase TEXT NOT NULL DEFAULT 'not_started',
  training_progress_percent INTEGER NOT NULL DEFAULT 0 CHECK (training_progress_percent >= 0 AND training_progress_percent <= 100),
  training_error TEXT,
  training_images JSONB NOT NULL DEFAULT '[]'::jsonb,
  consent_source TEXT,
  training_started_at TIMESTAMPTZ,
  training_completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS actor_identities_one_active
  ON public.actor_identities (is_active)
  WHERE is_active IS TRUE;

ALTER TABLE public.actor_identities ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS public.scene_reference_images (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_identity_id UUID NOT NULL REFERENCES public.actor_identities(id) ON DELETE RESTRICT,
  post_id UUID NOT NULL REFERENCES public.posts(id) ON DELETE CASCADE,
  scene_key TEXT NOT NULL,
  wardrobe_key TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT 'magnific',
  provider_task_id TEXT,
  image_url TEXT,
  prompt TEXT NOT NULL,
  provider_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  identity_gate_result JSONB,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS scene_reference_images_post_status_idx
  ON public.scene_reference_images (post_id, status);

ALTER TABLE public.scene_reference_images ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.batches
  ADD COLUMN IF NOT EXISTS actor_identity_id UUID REFERENCES public.actor_identities(id),
  ADD COLUMN IF NOT EXISTS actor_identity_snapshot JSONB;

ALTER TABLE public.posts
  ADD COLUMN IF NOT EXISTS scene_reference_image_id UUID REFERENCES public.scene_reference_images(id),
  ADD COLUMN IF NOT EXISTS identity_gate_result JSONB;
