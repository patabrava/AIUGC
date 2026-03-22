create table if not exists public.topic_research_dossiers (
    id uuid primary key default gen_random_uuid(),
    topic_research_run_id uuid references public.topic_research_runs(id) on delete set null,
    topic_registry_id uuid references public.topic_registry(id) on delete set null,
    seed_topic text not null,
    post_type text not null,
    target_length_tier integer not null check (target_length_tier in (8, 16, 32)),
    cluster_id text not null,
    topic text not null,
    anchor_topic text not null,
    normalized_payload jsonb not null default '{}'::jsonb,
    raw_prompt text not null default '',
    raw_response text not null default '',
    prompt_name text not null default 'prompt1_research',
    prompt_version text not null default '1',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists topic_research_dossiers_topic_registry_id_idx
    on public.topic_research_dossiers (topic_registry_id);

create index if not exists topic_research_dossiers_topic_research_run_id_idx
    on public.topic_research_dossiers (topic_research_run_id);

create index if not exists topic_research_dossiers_target_length_tier_idx
    on public.topic_research_dossiers (target_length_tier);

drop trigger if exists topic_research_dossiers_touch_updated_at on public.topic_research_dossiers;
create trigger topic_research_dossiers_touch_updated_at
before update on public.topic_research_dossiers
for each row
execute function public.touch_updated_at();

alter table public.topic_research_dossiers enable row level security;

alter table public.topic_research_runs
add column if not exists topic_registry_id uuid,
add column if not exists seed_topic text,
add column if not exists post_type text,
add column if not exists raw_prompt text not null default '',
add column if not exists raw_response text not null default '',
add column if not exists provider_interaction_id text,
add column if not exists normalized_payload jsonb not null default '{}'::jsonb,
add column if not exists dossier_id uuid;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'topic_research_runs_topic_registry_id_fkey'
      and conrelid = 'public.topic_research_runs'::regclass
  ) then
    alter table public.topic_research_runs
    add constraint topic_research_runs_topic_registry_id_fkey
    foreign key (topic_registry_id) references public.topic_registry(id) on delete set null;
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'topic_research_runs_dossier_id_fkey'
      and conrelid = 'public.topic_research_runs'::regclass
  ) then
    alter table public.topic_research_runs
    add constraint topic_research_runs_dossier_id_fkey
    foreign key (dossier_id) references public.topic_research_dossiers(id) on delete set null;
  end if;
end $$;
