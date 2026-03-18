-- Migration: Add Meta connection + publish result fields
-- Phase 7: Facebook Login for Business and Meta publishing
-- Date: 2026-03-17

ALTER TABLE batches
ADD COLUMN IF NOT EXISTS meta_connection JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE posts
ADD COLUMN IF NOT EXISTS publish_caption TEXT NOT NULL DEFAULT '';

ALTER TABLE posts
ADD COLUMN IF NOT EXISTS publish_results JSONB NOT NULL DEFAULT '{}'::JSONB;

CREATE INDEX IF NOT EXISTS idx_posts_publish_results_gin
ON posts
USING GIN (publish_results);

COMMENT ON COLUMN batches.meta_connection IS 'Batch-scoped dev-limited Meta workflow connection, reachable assets, selected targets, and token metadata';
COMMENT ON COLUMN posts.publish_caption IS 'Editable shared caption used for Meta publishing';
COMMENT ON COLUMN posts.publish_results IS 'Per-network publish result objects keyed by network name';
