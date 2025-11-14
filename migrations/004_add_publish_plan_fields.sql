-- Migration: Add publish plan fields to posts table
-- Phase 6: S7_PUBLISH_PLAN support
-- Date: 2025-11-13

-- Add scheduled_at column for publish time scheduling
ALTER TABLE posts
ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMPTZ;

-- Add social_networks as array of selected platforms
ALTER TABLE posts
ADD COLUMN IF NOT EXISTS social_networks TEXT[] DEFAULT '{}'::TEXT[];

-- Add publish_status to track publishing state
ALTER TABLE posts
ADD COLUMN IF NOT EXISTS publish_status TEXT DEFAULT 'pending';

-- Add platform_ids to store IDs returned from social platforms
ALTER TABLE posts
ADD COLUMN IF NOT EXISTS platform_ids JSONB DEFAULT '{}'::JSONB;

-- Add indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_posts_scheduled_at 
ON posts (scheduled_at) WHERE scheduled_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_posts_publish_status 
ON posts (publish_status);

-- Add check constraint for valid publish statuses
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'check_publish_status'
          AND conrelid = 'posts'::regclass
    ) THEN
        ALTER TABLE posts
        ADD CONSTRAINT check_publish_status 
        CHECK (publish_status IN ('pending', 'scheduled', 'publishing', 'published', 'failed'));
    END IF;
END $$;

-- Add comments for documentation
COMMENT ON COLUMN posts.scheduled_at IS 'Scheduled publish time in UTC (display in Europe/Berlin)';
COMMENT ON COLUMN posts.social_networks IS 'Array of selected social networks: tiktok, instagram, facebook';
COMMENT ON COLUMN posts.publish_status IS 'Publishing state: pending, scheduled, publishing, published, failed';
COMMENT ON COLUMN posts.platform_ids IS 'Platform-specific IDs after successful publish (e.g., {"tiktok": "123", "instagram": "456"})';
