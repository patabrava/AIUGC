create table if not exists public.video_prompt_audit (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references public.posts(id),
  batch_id uuid,
  operation_id text not null,
  provider text not null,
  prompt_text text not null,
  negative_prompt text,
  prompt_path text not null,
  aspect_ratio text not null,
  resolution text not null,
  requested_seconds integer not null,
  correlation_id text not null,
  created_at timestamptz not null default now()
);

create index idx_video_prompt_audit_post_id on public.video_prompt_audit(post_id);
create index idx_video_prompt_audit_created_at on public.video_prompt_audit(created_at);
