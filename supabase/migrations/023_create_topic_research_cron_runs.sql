create table if not exists public.topic_research_cron_runs (
  id uuid primary key default gen_random_uuid(),
  topics_requested integer not null default 0 check (topics_requested >= 0),
  topics_completed integer not null default 0 check (topics_completed >= 0),
  topics_failed integer not null default 0 check (topics_failed >= 0),
  seed_source text not null default '',
  status text not null default 'running',
  topic_ids jsonb not null default '[]'::jsonb,
  error_message text,
  details jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint topic_research_cron_runs_status_check
    check (status in ('running', 'completed', 'failed'))
);

create index if not exists idx_topic_research_cron_runs_status
  on public.topic_research_cron_runs(status, started_at desc);

create index if not exists idx_topic_research_cron_runs_started_at
  on public.topic_research_cron_runs(started_at desc);

drop trigger if exists topic_research_cron_runs_touch_updated_at
  on public.topic_research_cron_runs;
create trigger topic_research_cron_runs_touch_updated_at
before update on public.topic_research_cron_runs
for each row execute function public.touch_updated_at();
