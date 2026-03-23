-- Formalize the dual publishing architecture:
-- TikTok: connected_accounts -> media_assets -> publish_jobs (dedicated tables)
-- Meta (Facebook/Instagram): batches.meta_connection -> posts.publish_results (inline)

COMMENT ON TABLE public.connected_accounts IS
  'TikTok-only OAuth credentials. Meta uses batches.meta_connection instead.';

COMMENT ON TABLE public.publish_jobs IS
  'TikTok-only publish job tracking. Meta publishing is tracked inline on posts.publish_results.';

COMMENT ON TABLE public.media_assets IS
  'TikTok-only media asset storage for video uploads.';

COMMENT ON COLUMN public.batches.meta_connection IS
  'Meta (Facebook/Instagram) OAuth connection, reachable pages, and selected targets. This is the Meta equivalent of connected_accounts — scoped per batch.';

COMMENT ON COLUMN public.posts.publish_results IS
  'Per-network publish results keyed by network name (e.g. {"tiktok": {...}, "instagram": {...}}). Meta results stored here; TikTok results also mirrored here from publish_jobs.';

COMMENT ON COLUMN public.posts.social_networks IS
  'Selected target networks for this post: tiktok, instagram, facebook. Unified across both publishing paths.';
