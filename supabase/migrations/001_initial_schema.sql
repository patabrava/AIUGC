-- Migration: Baseline Lippe Lift Studio schema for blank Supabase projects
-- Date: 2026-03-16

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS public.batches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  brand TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'S1_SETUP',
  post_type_counts JSONB NOT NULL DEFAULT '{"value": 0, "lifestyle": 0, "product": 0}'::jsonb,
  archived BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.topic_registry (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL,
  rotation TEXT NOT NULL,
  cta TEXT NOT NULL,
  use_count INTEGER NOT NULL DEFAULT 1,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.posts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  batch_id UUID NOT NULL REFERENCES public.batches(id) ON DELETE CASCADE,
  post_type TEXT NOT NULL,
  topic_title TEXT NOT NULL DEFAULT '',
  topic_rotation TEXT NOT NULL DEFAULT '',
  topic_cta TEXT NOT NULL DEFAULT '',
  spoken_duration DOUBLE PRECISION NOT NULL DEFAULT 0,
  seed_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  video_prompt_json JSONB,
  video_status TEXT NOT NULL DEFAULT 'pending',
  video_url TEXT,
  video_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  video_operation_id TEXT,
  video_provider TEXT,
  video_format TEXT,
  qa_pass BOOLEAN,
  qa_notes TEXT NOT NULL DEFAULT '',
  qa_auto_checks JSONB,
  scheduled_at TIMESTAMPTZ,
  social_networks TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  publish_status TEXT NOT NULL DEFAULT 'pending',
  platform_ids JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'batches_state_check'
      AND conrelid = 'public.batches'::regclass
  ) THEN
    ALTER TABLE public.batches
    ADD CONSTRAINT batches_state_check
    CHECK (state IN (
      'S1_SETUP',
      'S2_SEEDED',
      'S4_SCRIPTED',
      'S5_PROMPTS_BUILT',
      'S6_QA',
      'S7_PUBLISH_PLAN',
      'S8_COMPLETE'
    ));
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'posts_post_type_check'
      AND conrelid = 'public.posts'::regclass
  ) THEN
    ALTER TABLE public.posts
    ADD CONSTRAINT posts_post_type_check
    CHECK (post_type IN ('value', 'lifestyle', 'product'));
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'posts_video_status_check'
      AND conrelid = 'public.posts'::regclass
  ) THEN
    ALTER TABLE public.posts
    ADD CONSTRAINT posts_video_status_check
    CHECK (video_status IN ('pending', 'queued', 'submitted', 'processing', 'completed', 'failed'));
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'posts_publish_status_check'
      AND conrelid = 'public.posts'::regclass
  ) THEN
    ALTER TABLE public.posts
    ADD CONSTRAINT posts_publish_status_check
    CHECK (publish_status IN ('pending', 'scheduled', 'publishing', 'published', 'failed'));
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_topic_registry_title_rotation_cta
ON public.topic_registry (title, rotation, cta);

CREATE INDEX IF NOT EXISTS idx_batches_archived_created_at
ON public.batches (archived, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_posts_batch_id
ON public.posts (batch_id);

CREATE INDEX IF NOT EXISTS idx_posts_batch_id_post_type
ON public.posts (batch_id, post_type);

CREATE INDEX IF NOT EXISTS idx_posts_video_status
ON public.posts (video_status);

CREATE INDEX IF NOT EXISTS idx_posts_qa_pass
ON public.posts (qa_pass);

DROP TRIGGER IF EXISTS batches_touch_updated_at ON public.batches;
CREATE TRIGGER batches_touch_updated_at
BEFORE UPDATE ON public.batches
FOR EACH ROW
EXECUTE FUNCTION public.touch_updated_at();

DROP TRIGGER IF EXISTS posts_touch_updated_at ON public.posts;
CREATE TRIGGER posts_touch_updated_at
BEFORE UPDATE ON public.posts
FOR EACH ROW
EXECUTE FUNCTION public.touch_updated_at();

ALTER TABLE public.batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.topic_registry ENABLE ROW LEVEL SECURITY;

GRANT USAGE ON SCHEMA public TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT USAGE, SELECT ON SEQUENCES TO service_role;
