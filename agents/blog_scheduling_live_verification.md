# Blog Scheduling Live Verification

Date: 2026-06-04
Commit: f6548c6
Production URL: https://lippelift.xyz

## Supabase Schema

- `blog_scheduled_at`: present and writable through production PostgREST.
- `posts_blog_status_check`: runtime-verified to allow `scheduled` and `publishing` through schedule save and due dispatch.
- `idx_posts_blog_scheduled_at`: not directly inspected from this environment. Supabase Management SQL access returned Cloudflare 1010, so index verification should be completed in the Supabase SQL editor if required.

## UI Schedule Save

- Batch ID: d0518dcc-1610-4f9d-9cbe-912665ee93f8
- Post ID: 2cd14967-76d4-4711-831b-4c88f26522bb
- Selected local time: 2026-06-05 10:00 Europe/Berlin
- Persisted UTC time: 2026-06-05T08:00:00+00:00
- UI status observed: authenticated batch page loaded with Blog panel and schedule controls; schedule endpoint returned `blog_status=scheduled`.

## Due Dispatch

- Trigger: `POST https://lippelift.xyz/blog/cron/dispatch` with cron bearer auth
- Response: `{"processed":1,"published":1,"failed":0,"trigger":"cron"}`
- Final `blog_status`: published
- Webflow item ID: 6a21f5e670870b63c4de931f
- Published at: 2026-06-04T22:02:16+00:00
- Public URL: https://www.lippelift.de/blog/einkaufen-rollstuhl-supermarkt-unsichtbare-huerden-bfsg

## Caveats

- Direct Supabase `information_schema` and index queries could not be run from this environment because `api.supabase.com` returned Cloudflare 1010. Runtime schedule persistence and dispatch were verified against the production row.
