update public.topic_scripts ts
set
    source_urls = case
        when coalesce(ts.source_urls, '[]'::jsonb) <> '[]'::jsonb then ts.source_urls
        else coalesce(
            (
                select jsonb_agg(jsonb_build_object('title', sb->>'title', 'url', sb->>'url'))
                from jsonb_array_elements(coalesce(tr.source_bank, '[]'::jsonb)) sb
                where coalesce(sb->>'url', '') <> ''
            ),
            '[]'::jsonb
        )
    end,
    primary_source_url = case
        when coalesce(ts.primary_source_url, '') <> '' then ts.primary_source_url
        else nullif((select sb->>'url' from jsonb_array_elements(coalesce(tr.source_bank, '[]'::jsonb)) sb where coalesce(sb->>'url', '') <> '' limit 1), '')
    end,
    primary_source_title = case
        when coalesce(ts.primary_source_title, '') <> '' then ts.primary_source_title
        else nullif((select sb->>'title' from jsonb_array_elements(coalesce(tr.source_bank, '[]'::jsonb)) sb where coalesce(sb->>'url', '') <> '' limit 1), '')
    end,
    updated_at = now()
from public.topic_registry tr
where tr.id = ts.topic_registry_id
  and coalesce(ts.source_urls, '[]'::jsonb) = '[]'::jsonb
  and coalesce(tr.source_bank, '[]'::jsonb) <> '[]'::jsonb;
