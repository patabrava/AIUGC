with ranked_matches as (
    select
        ts.id as topic_script_id,
        ts.created_at as topic_script_created_at,
        ts.updated_at as topic_script_updated_at,
        ts.target_length_tier,
        ts.bucket,
        ts.lane_key,
        d.id as topic_research_dossier_id,
        row_number() over (
            partition by ts.id
            order by
                case
                    when coalesce(ts.cluster_id, '') <> '' and ts.cluster_id = d.cluster_id then 0
                    else 1
                end,
                d.created_at desc,
                d.id desc
        ) as rn
    from public.topic_scripts ts
    join public.topic_research_dossiers d
      on d.topic_registry_id = ts.topic_registry_id
     and d.post_type = coalesce(ts.post_type, d.post_type)
     and d.target_length_tier = ts.target_length_tier
    where ts.topic_research_dossier_id is null
),
best_matches as (
    select
        topic_script_id,
        topic_research_dossier_id
    from ranked_matches
    where rn = 1
),
deduped_matches as (
    select
        topic_script_id,
        topic_research_dossier_id,
        row_number() over (
            partition by topic_research_dossier_id, target_length_tier, bucket, lane_key
            order by
                coalesce(topic_script_updated_at, topic_script_created_at) desc,
                topic_script_created_at desc,
                topic_script_id desc
        ) as key_rank
    from ranked_matches
    where rn = 1
)
update public.topic_scripts ts
set topic_research_dossier_id = dm.topic_research_dossier_id
from deduped_matches dm
where dm.topic_script_id = ts.id
  and dm.key_rank = 1
  and ts.topic_research_dossier_id is null;
