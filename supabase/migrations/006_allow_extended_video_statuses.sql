alter table if exists public.posts
drop constraint if exists posts_video_status_check;

alter table public.posts
add constraint posts_video_status_check
check (
  video_status is null
  or video_status in (
    'pending',
    'queued',
    'submitted',
    'processing',
    'extended_submitted',
    'extended_processing',
    'completed',
    'failed'
  )
);
