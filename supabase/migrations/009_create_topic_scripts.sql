create table if not exists public.topic_scripts (
    id uuid primary key default gen_random_uuid(),
    topic_registry_id uuid not null references public.topic_registry(id) on delete cascade,
    post_type text,
    title text not null,
    script text not null,
    target_length_tier integer not null check (target_length_tier in (8, 16, 32)),
    bucket text,
    hook_style text,
    framework text,
    tone text,
    estimated_duration_s integer,
    lane_key text,
    lane_family text,
    cluster_id text,
    anchor_topic text,
    disclaimer text,
    source_summary text,
    primary_source_url text,
    primary_source_title text,
    source_urls jsonb not null default '[]'::jsonb,
    seed_payload jsonb not null default '{}'::jsonb,
    quality_score numeric,
    quality_notes text not null default '',
    use_count integer not null default 0,
    last_used_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (topic_registry_id, target_length_tier, script)
);

create index if not exists topic_scripts_topic_registry_id_idx
    on public.topic_scripts (topic_registry_id);

create index if not exists topic_scripts_target_length_tier_idx
    on public.topic_scripts (target_length_tier);

create index if not exists topic_scripts_cluster_lane_idx
    on public.topic_scripts (cluster_id, lane_family, lane_key);
