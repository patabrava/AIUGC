-- 021_add_blog_scheduling.sql
-- Add scheduling support for blog publishing and extend blog status values.

ALTER TABLE posts
    ADD COLUMN blog_scheduled_at TIMESTAMPTZ;

ALTER TABLE posts
    DROP CONSTRAINT IF EXISTS posts_blog_status_check;

ALTER TABLE posts
    ADD CONSTRAINT posts_blog_status_check
    CHECK (blog_status IN ('disabled', 'pending', 'generating', 'draft', 'scheduled', 'publishing', 'published', 'failed'));

CREATE INDEX idx_posts_blog_scheduled_at
    ON posts (blog_scheduled_at)
    WHERE blog_scheduled_at IS NOT NULL;
