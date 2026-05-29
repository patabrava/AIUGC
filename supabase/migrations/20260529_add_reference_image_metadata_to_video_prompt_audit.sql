-- Store non-secret metadata for the exact visual references sent with a VEO request.
ALTER TABLE public.video_prompt_audit
  ADD COLUMN IF NOT EXISTS reference_image_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
