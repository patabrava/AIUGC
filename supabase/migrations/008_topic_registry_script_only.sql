ALTER TABLE public.topic_registry
ADD COLUMN IF NOT EXISTS script TEXT NOT NULL DEFAULT '';

UPDATE public.topic_registry
SET script = COALESCE(NULLIF(script, ''), concat_ws(' ', rotation, cta));

ALTER TABLE public.topic_registry
ALTER COLUMN script SET DEFAULT '';

DROP INDEX IF EXISTS uq_topic_registry_title_rotation_cta;

CREATE UNIQUE INDEX IF NOT EXISTS uq_topic_registry_title_script
ON public.topic_registry (title, script);

ALTER TABLE public.topic_registry
DROP COLUMN IF EXISTS rotation,
DROP COLUMN IF EXISTS cta;
