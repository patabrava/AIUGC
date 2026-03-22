alter table public.topic_scripts
add column if not exists topic_research_dossier_id uuid;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'topic_scripts_topic_research_dossier_id_fkey'
      and conrelid = 'public.topic_scripts'::regclass
  ) then
    alter table public.topic_scripts
    add constraint topic_scripts_topic_research_dossier_id_fkey
    foreign key (topic_research_dossier_id) references public.topic_research_dossiers(id) on delete set null;
  end if;
end $$;

create index if not exists topic_scripts_topic_research_dossier_id_idx
    on public.topic_scripts (topic_research_dossier_id);

create unique index if not exists topic_scripts_dossier_tier_bucket_lane_key_key
    on public.topic_scripts (topic_research_dossier_id, target_length_tier, bucket, lane_key);

create or replace view public.v_topic_scripts_resolved as
select
    ts.*,
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
left join public.topic_research_dossiers d
  on d.id = ts.topic_research_dossier_id
left join public.topic_research_runs r
  on r.id = d.topic_research_run_id;
