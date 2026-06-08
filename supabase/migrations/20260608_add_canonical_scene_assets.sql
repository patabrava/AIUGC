create table if not exists canonical_scene_assets (
    id uuid primary key,
    scene_key text not null,
    scene_bible_version integer not null,
    status text not null,
    provider text not null,
    provider_model text,
    system_prompt_name text not null,
    prompt_text text not null,
    aspect_ratio text not null,
    image_size text not null,
    image_url text,
    storage_key text,
    provider_metadata jsonb not null default '{}'::jsonb,
    generated_at timestamptz,
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now())
);

create unique index if not exists canonical_scene_assets_scene_version_format_idx
    on canonical_scene_assets (scene_key, scene_bible_version, aspect_ratio, image_size);

create index if not exists canonical_scene_assets_scene_key_idx
    on canonical_scene_assets (scene_key, status, created_at desc);
