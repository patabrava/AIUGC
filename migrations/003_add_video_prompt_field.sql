-- Migration: Add video_prompt_json field to posts table
-- Phase 3: S4_SCRIPTED → S5_PROMPTS_BUILT
-- Date: 2025-11-06

-- Add video_prompt_json column to store assembled video generation prompts
ALTER TABLE posts
ADD COLUMN IF NOT EXISTS video_prompt_json JSONB;

-- Add index for faster queries on posts with prompts built
CREATE INDEX IF NOT EXISTS idx_posts_video_prompt_exists 
ON posts ((video_prompt_json IS NOT NULL));

-- Add comment for documentation
COMMENT ON COLUMN posts.video_prompt_json IS 'Complete video generation prompt with dialogue from Phase 2 inserted into template';
