# Blog Post Feature — Changelog & Reference

**Branch:** `main` (merged from `feature/blog-post-feature`)
**Spec:** `docs/superpowers/specs/2026-03-27-blog-post-feature-design.md`
**Plan:** `docs/superpowers/plans/2026-03-27-blog-post-feature.md`

---

## What It Does

Enables selecting posts within a batch to generate blog articles from their research dossiers, with inline editing and Webflow CMS publishing. Blog posts are independent of the video/social media pipeline.

---

## Commits (chronological)

| Commit | Description |
|--------|-------------|
| `f982202` | Migration 020: blog columns on posts table |
| `04d925a` | Pydantic schemas (BlogContent, BlogSource, etc.) |
| `214e0e0` | Blog queries (toggle, status update, content edit) |
| `a341be6` | Webflow CMS client adapter + config env vars |
| `23759be` | Blog generation runtime + LLM prompt template |
| `b5e38ab` | FastAPI handlers (5 endpoints) + router registration |
| `8630c60` | Blog toggle + status chips on post card template |
| `9dce0e7` | Blog panel + modal templates |
| `bca75e9` | Integration tests (12 tests) |
| `8a61608` | Test adjustment for test env constraints |
| `9a590eb` | **Bugfix:** Toggle race condition (htmx/Alpine) + panel not rendering (`posts` → `batch_view.visible_posts`) |
| `04227d3` | **Bugfix:** Handlers used `details`/`message` instead of `data` on SuccessResponse |
| `3a21f88` | **Bugfix:** PostDetail schema missing blog fields — toggle reset on page reload |

---

## Files Created

| File | Purpose |
|------|---------|
| `supabase/migrations/020_add_blog_post_fields.sql` | DB migration: 5 blog columns |
| `app/features/blog/__init__.py` | Package init |
| `app/features/blog/schemas.py` | BlogContent, BlogSource, BlogToggleResponse, BlogContentUpdateRequest, BlogPublishResponse |
| `app/features/blog/queries.py` | `_load_post_for_blog`, `toggle_blog_enabled`, `update_blog_status`, `update_blog_content_fields`, `get_blog_enabled_posts` |
| `app/features/blog/blog_runtime.py` | `_build_blog_prompt`, `_parse_blog_response`, `_lookup_dossier`, `generate_blog_draft` |
| `app/features/blog/webflow_client.py` | `WebflowClient` with `create_item`, `update_item`, `publish_site` |
| `app/features/blog/handlers.py` | 5 FastAPI endpoints |
| `app/features/topics/prompt_data/blog_post.txt` | German LLM prompt template (separate from code) |
| `templates/batches/detail/_blog_panel.html` | Collapsible blog panel with inline editing |
| `templates/batches/detail/_blog_modal.html` | Full-screen editor modal (placeholder) |
| `tests/test_blog_feature.py` | 12 tests |

## Files Modified

| File | Change |
|------|--------|
| `app/core/config.py` | Added `webflow_api_token`, `webflow_collection_id`, `webflow_site_id` |
| `app/main.py` | Imported + registered `blog_router` |
| `app/features/batches/schemas.py` | Added blog fields to `PostDetail` |
| `app/features/batches/handlers.py` | Pass blog fields when constructing `PostDetail` |
| `templates/batches/detail/_post_card.html` | Blog toggle in S2_SEEDED action row + status chips |
| `templates/batches/detail/_posts_section.html` | `{% include %}` for blog panel + modal |

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `PUT` | `/blog/posts/{id}/blog-toggle` | Toggle blog_enabled on/off |
| `POST` | `/blog/posts/{id}/blog/generate` | Generate blog draft from research dossier |
| `POST` | `/blog/batches/{id}/blog/generate-all` | Generate drafts for all blog-enabled posts |
| `PUT` | `/blog/posts/{id}/blog/content` | Save edits to blog fields (blur-save) |
| `POST` | `/blog/posts/{id}/blog/publish` | Push to Webflow CMS |

---

## Database Schema (Migration 020)

New columns on `posts` table:

| Column | Type | Default |
|--------|------|---------|
| `blog_enabled` | boolean | false |
| `blog_status` | text | 'disabled' (CHECK: disabled/pending/generating/draft/published/failed) |
| `blog_content` | jsonb | '{}' |
| `blog_webflow_item_id` | text | null |
| `blog_published_at` | timestamptz | null |

Partial indexes: `idx_posts_blog_enabled`, `idx_posts_blog_status`

### `blog_content` JSONB Structure

```json
{
  "title": "Blog-Titel",
  "body": "Vollständiger Artikeltext...",
  "slug": "seo-url-slug",
  "meta_description": "SEO Meta-Description",
  "sources": [{"title": "Source", "url": "https://..."}],
  "word_count": 742,
  "generated_at": "2026-03-27T14:00:00Z",
  "dossier_id": "uuid"
}
```

### `blog_status` Transitions

```
disabled → pending       (toggle ON)
pending → generating     (generation triggered)
generating → draft       (LLM returns content)
generating → failed      (LLM error)
draft → published        (pushed to Webflow)
draft → generating       (regenerate)
disabled ← any           (toggle OFF, preserves blog_content)
```

---

## Environment Variables

Add to `.env` for Webflow publishing:

```
WEBFLOW_API_TOKEN=<your-webflow-api-token>
WEBFLOW_COLLECTION_ID=<your-cms-collection-id>
WEBFLOW_SITE_ID=<your-site-id>
```

---

## Bugs Found & Fixed During Testing

### 1. Toggle race condition (commit `9a590eb`)
**Symptom:** Toggle appeared to work but sent wrong value.
**Root cause:** Alpine's `@click` toggled `blogEnabled` first, then htmx's `hx-vals` negated the already-toggled value, sending `false` when clicking ON.
**Fix:** Replaced htmx with `fetch()` from Alpine's `@click` handler.

### 2. Blog panel never rendered (commit `9a590eb`)
**Symptom:** Blog panel didn't appear even with blog-enabled posts.
**Root cause:** Template referenced `posts` (undefined) instead of `batch_view.visible_posts`.
**Fix:** Changed to `batch_view.visible_posts if batch_view is defined`.

### 3. All endpoints returned 500 (commit `04227d3`)
**Symptom:** Every blog API call returned 500 Internal Server Error.
**Root cause:** Handlers used `SuccessResponse(message=..., details=...)` but model only accepts `SuccessResponse(data=...)`.
**Fix:** Changed all handlers to use `data=` parameter.

### 4. Toggle reset on page reload (commit `3a21f88`)
**Symptom:** Toggle showed ON, but after "Approve Script" reload it showed OFF.
**Root cause:** `PostDetail` schema and batch detail handler didn't include `blog_enabled`/`blog_status` fields, so template always got `false`/undefined.
**Fix:** Added 5 blog fields to `PostDetail` and passed them in the handler.

---

## What's Left

- [ ] Manual UI testing: verify toggle persists through Approve Script
- [ ] End-to-end generation test with real dossier data
- [ ] Webflow integration test with real API token
- [ ] Full-screen editor modal (currently placeholder)
- [ ] Optional: scheduled blog publishing (currently only "Publish Now")
- [ ] Optional: blog status visibility in S7 publish panel
