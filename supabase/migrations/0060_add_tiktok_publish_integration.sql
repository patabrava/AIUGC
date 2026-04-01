-- Migration: Add TikTok sandbox account, media asset, and publish job support
-- Date: 2026-03-17

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.connected_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID,
  platform TEXT NOT NULL DEFAULT 'tiktok',
  open_id TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  avatar_url TEXT NOT NULL DEFAULT '',
  access_token TEXT NOT NULL DEFAULT '',
  refresh_token TEXT NOT NULL DEFAULT '',
  access_token_expires_at TIMESTAMPTZ,
  refresh_token_expires_at TIMESTAMPTZ,
  scope TEXT NOT NULL DEFAULT '',
  environment TEXT NOT NULL DEFAULT 'sandbox',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.media_assets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID,
  source_url TEXT NOT NULL,
  storage_key TEXT NOT NULL DEFAULT '',
  mime_type TEXT NOT NULL DEFAULT '',
  file_size BIGINT NOT NULL DEFAULT 0,
  duration_seconds DOUBLE PRECISION,
  status TEXT NOT NULL DEFAULT 'ready',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.publish_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID,
  connected_account_id UUID NOT NULL REFERENCES public.connected_accounts(id) ON DELETE CASCADE,
  platform TEXT NOT NULL DEFAULT 'tiktok',
  media_asset_id UUID NOT NULL REFERENCES public.media_assets(id) ON DELETE CASCADE,
  caption TEXT NOT NULL DEFAULT '',
  post_mode TEXT NOT NULL DEFAULT 'draft',
  tiktok_publish_id TEXT,
  status TEXT NOT NULL DEFAULT 'created',
  request_payload_json JSONB NOT NULL DEFAULT '{}'::JSONB,
  response_payload_json JSONB NOT NULL DEFAULT '{}'::JSONB,
  error_message TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'connected_accounts_platform_check'
      AND conrelid = 'public.connected_accounts'::regclass
  ) THEN
    ALTER TABLE public.connected_accounts
    ADD CONSTRAINT connected_accounts_platform_check
    CHECK (platform IN ('tiktok'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'connected_accounts_environment_check'
      AND conrelid = 'public.connected_accounts'::regclass
  ) THEN
    ALTER TABLE public.connected_accounts
    ADD CONSTRAINT connected_accounts_environment_check
    CHECK (environment IN ('sandbox', 'production'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'media_assets_status_check'
      AND conrelid = 'public.media_assets'::regclass
  ) THEN
    ALTER TABLE public.media_assets
    ADD CONSTRAINT media_assets_status_check
    CHECK (status IN ('ready', 'failed'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'publish_jobs_platform_check'
      AND conrelid = 'public.publish_jobs'::regclass
  ) THEN
    ALTER TABLE public.publish_jobs
    ADD CONSTRAINT publish_jobs_platform_check
    CHECK (platform IN ('tiktok'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'publish_jobs_post_mode_check'
      AND conrelid = 'public.publish_jobs'::regclass
  ) THEN
    ALTER TABLE public.publish_jobs
    ADD CONSTRAINT publish_jobs_post_mode_check
    CHECK (post_mode IN ('draft', 'direct'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'publish_jobs_status_check'
      AND conrelid = 'public.publish_jobs'::regclass
  ) THEN
    ALTER TABLE public.publish_jobs
    ADD CONSTRAINT publish_jobs_status_check
    CHECK (status IN ('created', 'uploading', 'submitted', 'failed'));
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_connected_accounts_platform_environment_open_id
ON public.connected_accounts (platform, environment, open_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_media_assets_source_url
ON public.media_assets (source_url);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_platform_status
ON public.publish_jobs (platform, status, created_at DESC);

DROP TRIGGER IF EXISTS connected_accounts_touch_updated_at ON public.connected_accounts;
CREATE TRIGGER connected_accounts_touch_updated_at
BEFORE UPDATE ON public.connected_accounts
FOR EACH ROW
EXECUTE FUNCTION public.touch_updated_at();

DROP TRIGGER IF EXISTS publish_jobs_touch_updated_at ON public.publish_jobs;
CREATE TRIGGER publish_jobs_touch_updated_at
BEFORE UPDATE ON public.publish_jobs
FOR EACH ROW
EXECUTE FUNCTION public.touch_updated_at();

CREATE OR REPLACE FUNCTION public.upsert_tiktok_connected_account(
  p_user_id UUID,
  p_open_id TEXT,
  p_display_name TEXT,
  p_avatar_url TEXT,
  p_access_token_plain TEXT,
  p_refresh_token_plain TEXT,
  p_access_token_expires_at TIMESTAMPTZ,
  p_refresh_token_expires_at TIMESTAMPTZ,
  p_scope TEXT,
  p_environment TEXT,
  p_encryption_key TEXT
) RETURNS public.connected_accounts
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_row public.connected_accounts;
BEGIN
  INSERT INTO public.connected_accounts (
    user_id,
    platform,
    open_id,
    display_name,
    avatar_url,
    access_token,
    refresh_token,
    access_token_expires_at,
    refresh_token_expires_at,
    scope,
    environment
  ) VALUES (
    p_user_id,
    'tiktok',
    p_open_id,
    COALESCE(p_display_name, ''),
    COALESCE(p_avatar_url, ''),
    CASE WHEN COALESCE(p_access_token_plain, '') = '' THEN '' ELSE encode(extensions.pgp_sym_encrypt(p_access_token_plain, p_encryption_key), 'base64') END,
    CASE WHEN COALESCE(p_refresh_token_plain, '') = '' THEN '' ELSE encode(extensions.pgp_sym_encrypt(p_refresh_token_plain, p_encryption_key), 'base64') END,
    p_access_token_expires_at,
    p_refresh_token_expires_at,
    COALESCE(p_scope, ''),
    COALESCE(p_environment, 'sandbox')
  )
  ON CONFLICT (platform, environment, open_id)
  DO UPDATE SET
    user_id = EXCLUDED.user_id,
    display_name = EXCLUDED.display_name,
    avatar_url = EXCLUDED.avatar_url,
    access_token = EXCLUDED.access_token,
    refresh_token = EXCLUDED.refresh_token,
    access_token_expires_at = EXCLUDED.access_token_expires_at,
    refresh_token_expires_at = EXCLUDED.refresh_token_expires_at,
    scope = EXCLUDED.scope,
    updated_at = now()
  RETURNING * INTO v_row;

  RETURN v_row;
END;
$$;

CREATE OR REPLACE FUNCTION public.get_tiktok_connected_account_secret(
  p_environment TEXT,
  p_encryption_key TEXT
) RETURNS TABLE (
  id UUID,
  user_id UUID,
  platform TEXT,
  open_id TEXT,
  display_name TEXT,
  avatar_url TEXT,
  access_token_plain TEXT,
  refresh_token_plain TEXT,
  access_token_expires_at TIMESTAMPTZ,
  refresh_token_expires_at TIMESTAMPTZ,
  scope TEXT,
  environment TEXT,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
)
LANGUAGE sql
SECURITY DEFINER
AS $$
  SELECT
    connected_accounts.id,
    connected_accounts.user_id,
    connected_accounts.platform,
    connected_accounts.open_id,
    connected_accounts.display_name,
    connected_accounts.avatar_url,
    CASE WHEN connected_accounts.access_token = '' THEN '' ELSE extensions.pgp_sym_decrypt(decode(connected_accounts.access_token, 'base64'), p_encryption_key) END AS access_token_plain,
    CASE WHEN connected_accounts.refresh_token = '' THEN '' ELSE extensions.pgp_sym_decrypt(decode(connected_accounts.refresh_token, 'base64'), p_encryption_key) END AS refresh_token_plain,
    connected_accounts.access_token_expires_at,
    connected_accounts.refresh_token_expires_at,
    connected_accounts.scope,
    connected_accounts.environment,
    connected_accounts.created_at,
    connected_accounts.updated_at
  FROM public.connected_accounts
  WHERE connected_accounts.platform = 'tiktok'
    AND connected_accounts.environment = COALESCE(p_environment, 'sandbox')
  ORDER BY connected_accounts.updated_at DESC
  LIMIT 1;
$$;

ALTER TABLE public.connected_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.media_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.publish_jobs ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.connected_accounts TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.media_assets TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.publish_jobs TO service_role;
GRANT EXECUTE ON FUNCTION public.upsert_tiktok_connected_account(UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ, TIMESTAMPTZ, TEXT, TEXT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_tiktok_connected_account_secret(TEXT, TEXT) TO service_role;
