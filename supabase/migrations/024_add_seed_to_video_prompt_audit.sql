-- Add seed column to video_prompt_audit for VEO reproducibility
ALTER TABLE video_prompt_audit ADD COLUMN IF NOT EXISTS seed bigint;
