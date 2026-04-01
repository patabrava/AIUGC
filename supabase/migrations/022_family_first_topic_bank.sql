alter table public.topic_registry
add column if not exists canonical_topic text,
add column if not exists family_fingerprint text,
add column if not exists status text not null default 'provisional',
add column if not exists merged_into_id uuid,
add column if not exists merge_reason text not null default '';

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'topic_registry_merged_into_id_fkey'
      and conrelid = 'public.topic_registry'::regclass
  ) then
    alter table public.topic_registry
    add constraint topic_registry_merged_into_id_fkey
    foreign key (merged_into_id) references public.topic_registry(id) on delete set null;
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'topic_registry_status_check'
      and conrelid = 'public.topic_registry'::regclass
  ) then
    alter table public.topic_registry
    add constraint topic_registry_status_check
    check (status = any (array['provisional'::text, 'active'::text, 'quarantined'::text, 'merged'::text]));
  end if;
end $$;

update public.topic_registry
set canonical_topic = coalesce(nullif(canonical_topic, ''), nullif(title, ''), nullif(script, ''), 'Thema')
where canonical_topic is null or canonical_topic = '';

update public.topic_registry
set family_fingerprint = trim(
  regexp_replace(
    regexp_replace(lower(coalesce(canonical_topic, '')), '[^[:alnum:][:space:]]+', ' ', 'g'),
    '\s+',
    ' ',
    'g'
  )
)
where family_fingerprint is null or family_fingerprint = '';

update public.topic_registry tr
set status = case
  when coalesce(nullif(tr.status, ''), 'provisional') = 'merged' then 'merged'
  when exists (
    select 1
    from public.topic_scripts ts
    where ts.topic_registry_id = tr.id
  ) then 'active'
  else coalesce(nullif(tr.status, ''), 'provisional')
end
where tr.status is null or tr.status = '' or tr.status = 'provisional';

alter table public.topic_registry
alter column canonical_topic set not null;

alter table public.topic_registry
alter column family_fingerprint set not null;

create index if not exists topic_registry_status_idx
on public.topic_registry (status);

create index if not exists topic_registry_family_fingerprint_idx
on public.topic_registry (family_fingerprint);

create unique index if not exists topic_registry_active_family_fingerprint_key
on public.topic_registry (post_type, family_fingerprint)
where status = 'active';

alter table public.topic_scripts
add column if not exists script_fingerprint text,
add column if not exists audit_status text not null default 'pending',
add column if not exists audit_attempts integer not null default 0,
add column if not exists audited_at timestamptz,
add column if not exists origin_kind text not null default 'provider';

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'topic_scripts_audit_status_check'
      and conrelid = 'public.topic_scripts'::regclass
  ) then
    alter table public.topic_scripts
    add constraint topic_scripts_audit_status_check
    check (audit_status = any (array['pending'::text, 'pass'::text, 'needs_repair'::text, 'reject'::text]));
  end if;

  if not exists (
    select 1
    from pg_constraint
    where conname = 'topic_scripts_origin_kind_check'
      and conrelid = 'public.topic_scripts'::regclass
  ) then
    alter table public.topic_scripts
    add constraint topic_scripts_origin_kind_check
    check (origin_kind = any (array['provider'::text, 'synthetic_fallback'::text, 'migrated'::text]));
  end if;
end $$;

update public.topic_scripts
set script_fingerprint = trim(regexp_replace(lower(coalesce(script, '')), '\s+', ' ', 'g'))
where script_fingerprint is null or script_fingerprint = '';

update public.topic_scripts
set audit_status = case
  when quality_score is null then 'pending'
  when quality_score >= 70 then 'pass'
  when quality_score >= 40 then 'needs_repair'
  else 'reject'
end
where audit_status is null or audit_status = '' or audit_status = 'pending';

update public.topic_scripts
set audit_attempts = case
  when quality_score is null then coalesce(audit_attempts, 0)
  when coalesce(audit_attempts, 0) > 0 then audit_attempts
  else 1
end,
audited_at = case
  when quality_score is null then audited_at
  else coalesce(audited_at, updated_at, created_at)
end,
origin_kind = coalesce(nullif(origin_kind, ''), 'provider')
where true;

alter table public.topic_scripts
alter column script_fingerprint set not null;

with ranked as (
  select
    id,
    row_number() over (
      partition by target_length_tier, script_fingerprint
      order by
        case audit_status
          when 'pass' then 0
          when 'needs_repair' then 1
          when 'pending' then 2
          else 3
        end,
        use_count desc,
        updated_at desc,
        created_at desc,
        id desc
    ) as rn
  from public.topic_scripts
  where coalesce(script_fingerprint, '') <> ''
)
delete from public.topic_scripts
where id in (select id from ranked where rn > 1);

create index if not exists topic_scripts_audit_status_idx
on public.topic_scripts (audit_status, target_length_tier, post_type);

create unique index if not exists topic_scripts_tier_fingerprint_key
on public.topic_scripts (target_length_tier, script_fingerprint)
where script_fingerprint <> '';

drop view if exists public.v_topic_scripts_resolved;

create or replace view public.v_topic_scripts_resolved as
select
    ts.*,
    tr.canonical_topic,
    tr.family_fingerprint,
    tr.status as family_status,
    tr.merge_reason as family_merge_reason,
    tr.merged_into_id as family_merged_into_id,
    tr.use_count as family_use_count,
    tr.last_used_at as family_last_used_at,
    tr.last_harvested_at as family_last_harvested_at,
    d.topic_research_run_id,
    d.seed_topic,
    d.cluster_id as dossier_cluster_id,
    d.topic as dossier_topic,
    d.anchor_topic as dossier_anchor_topic,
    d.normalized_payload as dossier_normalized_payload,
    d.raw_prompt as dossier_raw_prompt,
    d.raw_response as dossier_raw_response,
    d.prompt_name as dossier_prompt_name,
    d.prompt_version as dossier_prompt_version,
    d.created_at as dossier_created_at,
    d.updated_at as dossier_updated_at,
    r.trigger_source as run_trigger_source,
    r.status as run_status,
    r.requested_counts as run_requested_counts,
    r.provider_interaction_id as run_provider_interaction_id,
    r.normalized_payload as run_normalized_payload,
    r.dossier_id as run_dossier_id,
    r.result_summary as run_result_summary,
    r.error_message as run_error_message
from public.topic_scripts ts
left join public.topic_registry tr
  on tr.id = ts.topic_registry_id
left join public.topic_research_dossiers d
  on d.id = ts.topic_research_dossier_id
left join public.topic_research_runs r
  on r.id = d.topic_research_run_id;
