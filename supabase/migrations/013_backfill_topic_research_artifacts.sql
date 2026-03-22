insert into public.topic_research_dossiers (
    topic_research_run_id,
    topic_registry_id,
    seed_topic,
    post_type,
    target_length_tier,
    cluster_id,
    topic,
    anchor_topic,
    normalized_payload,
    raw_prompt,
    raw_response,
    prompt_name,
    prompt_version,
    created_at,
    updated_at
)
select
    null,
    tr.id,
    coalesce(nullif(tr.research_payload->>'seed_topic', ''), tr.title),
    coalesce(nullif(tr.post_type, ''), 'value'),
    coalesce(
        nullif((tr.target_length_tiers[1])::text, '')::int,
        8
    ),
    coalesce(nullif(tr.research_payload->>'cluster_id', ''), tr.id::text),
    coalesce(nullif(tr.research_payload->>'topic', ''), tr.title),
    coalesce(nullif(tr.research_payload->>'anchor_topic', ''), tr.title),
    coalesce(tr.research_payload, '{}'::jsonb),
    '',
    '',
    'prompt1_research',
    '1',
    coalesce(tr.last_harvested_at, tr.first_seen_at, now()),
    now()
from public.topic_registry tr
where coalesce(tr.research_payload, '{}'::jsonb) <> '{}'::jsonb
  and not exists (
      select 1
      from public.topic_research_dossiers d
      where d.topic_registry_id = tr.id
        and d.prompt_name = 'prompt1_research'
        and d.prompt_version = '1'
  );

alter table public.topic_registry
drop column if exists source_bank,
drop column if exists research_payload,
drop column if exists script_bank,
drop column if exists seed_payloads,
drop column if exists target_length_tiers,
drop column if exists language;
