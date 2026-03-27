-- 020_add_blog_post_fields.sql
-- Add blog post columns to posts table for Webflow blog generation feature.

ALTER TABLE posts ADD COLUMN blog_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE posts ADD COLUMN blog_status TEXT NOT NULL DEFAULT 'disabled'
    CHECK (blog_status IN ('disabled', 'pending', 'generating', 'draft', 'published', 'failed'));
ALTER TABLE posts ADD COLUMN blog_content JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE posts ADD COLUMN blog_webflow_item_id TEXT;
ALTER TABLE posts ADD COLUMN blog_published_at TIMESTAMPTZ;

CREATE INDEX idx_posts_blog_enabled ON posts (blog_enabled) WHERE blog_enabled = true;
CREATE INDEX idx_posts_blog_status ON posts (blog_status) WHERE blog_status != 'disabled';
