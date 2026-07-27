-- Keep production identity references explicit and derived from one canonical face.

ALTER TABLE public.actor_identities
  ADD COLUMN IF NOT EXISTS reference_front_image_url TEXT,
  ADD COLUMN IF NOT EXISTS reference_three_quarter_image_url TEXT,
  ADD COLUMN IF NOT EXISTS reference_generation_metadata JSONB NOT NULL DEFAULT '{}'::JSONB;

