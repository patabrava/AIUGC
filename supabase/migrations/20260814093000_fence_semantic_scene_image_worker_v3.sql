-- Fence orphaned v2 scene-image workers before the credential-aware v3 rollout.

CREATE OR REPLACE FUNCTION public.claim_semantic_scene_image_v3(
  p_worker_id TEXT,
  p_lease_seconds INTEGER
)
RETURNS SETOF public.semantic_scene_image_jobs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  IF p_worker_id NOT LIKE 'semantic-scene-image-v3-%' THEN
    RAISE EXCEPTION 'semantic scene image worker contract is obsolete';
  END IF;

  RETURN QUERY
  SELECT *
  FROM public.claim_semantic_scene_image(p_worker_id, p_lease_seconds);
END;
$$;

-- A displaced v2 handler is fenced by the cleared durable lease. The next v3
-- claim reclaims its linked run through the existing attempt-count path.
UPDATE public.semantic_scene_image_jobs
SET status = 'queued',
    worker_id = NULL,
    lease_token = NULL,
    lease_expires_at = NULL,
    heartbeat_at = NULL,
    updated_at = pg_catalog.clock_timestamp()
WHERE status = 'processing'
  AND COALESCE(worker_id, '') NOT LIKE 'semantic-scene-image-v3-%';

REVOKE ALL ON FUNCTION public.claim_semantic_scene_image(TEXT, INTEGER)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.claim_semantic_scene_image_v3(TEXT, INTEGER)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_semantic_scene_image_v3(TEXT, INTEGER)
  TO service_role;

NOTIFY pgrst, 'reload schema';
