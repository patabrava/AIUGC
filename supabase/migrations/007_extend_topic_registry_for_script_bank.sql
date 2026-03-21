ALTER TABLE public.topic_registry
ADD COLUMN IF NOT EXISTS post_type TEXT,
ADD COLUMN IF NOT EXISTS script_bank JSONB NOT NULL DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS seed_payloads JSONB NOT NULL DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS source_bank JSONB NOT NULL DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS research_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS target_length_tiers INTEGER[] NOT NULL DEFAULT '{}'::INTEGER[],
ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'de',
ADD COLUMN IF NOT EXISTS last_harvested_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_topic_registry_post_type
ON public.topic_registry (post_type);

CREATE INDEX IF NOT EXISTS idx_topic_registry_last_harvested_at
ON public.topic_registry (last_harvested_at DESC);

CREATE TABLE IF NOT EXISTS public.topic_research_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trigger_source TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'running',
  requested_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
  target_length_tier INTEGER,
  result_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_message TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'topic_research_runs_status_check'
      AND conrelid = 'public.topic_research_runs'::regclass
  ) THEN
    ALTER TABLE public.topic_research_runs
    ADD CONSTRAINT topic_research_runs_status_check
    CHECK (status = ANY (ARRAY['running'::text, 'completed'::text, 'failed'::text]));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_topic_research_runs_created_at
ON public.topic_research_runs (created_at DESC);

DROP TRIGGER IF EXISTS topic_research_runs_touch_updated_at ON public.topic_research_runs;
CREATE TRIGGER topic_research_runs_touch_updated_at
BEFORE UPDATE ON public.topic_research_runs
FOR EACH ROW
EXECUTE FUNCTION public.touch_updated_at();

ALTER TABLE public.topic_research_runs ENABLE ROW LEVEL SECURITY;
