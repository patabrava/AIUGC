create table if not exists public.video_provider_quota_reservations (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  reservation_key text not null unique,
  reservation_kind text not null default 'video_chain',
  post_id uuid references public.posts(id) on delete set null,
  batch_id uuid references public.batches(id) on delete set null,
  quota_day_pt date not null,
  reserved_units integer not null check (reserved_units >= 0),
  consumed_units integer not null default 0 check (consumed_units >= 0),
  released_units integer not null default 0 check (released_units >= 0),
  status text not null default 'reserved',
  freeze_until timestamptz,
  freeze_reason text,
  provider_last_error_code text,
  provider_last_error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint video_provider_quota_reservations_kind_check
    check (reservation_kind in ('video_chain', 'freeze', 'retry_submit')),
  constraint video_provider_quota_reservations_status_check
    check (status in ('reserved', 'active', 'completed', 'released', 'failed', 'frozen')),
  constraint video_provider_quota_reservations_totals_check
    check (consumed_units + released_units <= reserved_units)
);

create index if not exists idx_video_provider_quota_reservations_provider_day
  on public.video_provider_quota_reservations(provider, quota_day_pt, created_at desc);

create index if not exists idx_video_provider_quota_reservations_provider_status
  on public.video_provider_quota_reservations(provider, status, created_at desc);

drop trigger if exists video_provider_quota_reservations_touch_updated_at
  on public.video_provider_quota_reservations;
create trigger video_provider_quota_reservations_touch_updated_at
before update on public.video_provider_quota_reservations
for each row execute function public.touch_updated_at();

create table if not exists public.video_provider_quota_events (
  id uuid primary key default gen_random_uuid(),
  reservation_id uuid not null references public.video_provider_quota_reservations(id) on delete cascade,
  provider text not null,
  reservation_key text not null,
  event_type text not null,
  units integer not null default 0 check (units >= 0),
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint video_provider_quota_events_type_check
    check (event_type in ('reserved', 'consumed', 'released', 'completed', 'failed', 'frozen'))
);

create index if not exists idx_video_provider_quota_events_provider_created_at
  on public.video_provider_quota_events(provider, created_at desc);

create index if not exists idx_video_provider_quota_events_reservation_key
  on public.video_provider_quota_events(reservation_key, created_at desc);

create or replace function public.get_video_provider_quota_snapshot(
  p_provider text,
  p_daily_limit integer,
  p_minute_limit integer,
  p_soft_buffer integer default 0
)
returns jsonb
language plpgsql
as $$
declare
  v_now timestamptz := now();
  v_quota_day_pt date := timezone('America/Los_Angeles', v_now)::date;
  v_minute_start timestamptz := date_trunc('minute', v_now);
  v_daily_committed integer := 0;
  v_minute_used integer := 0;
  v_effective_daily_limit integer := greatest(coalesce(p_daily_limit, 0) - greatest(coalesce(p_soft_buffer, 0), 0), 0);
  v_freeze_until timestamptz;
  v_freeze_reason text;
begin
  select freeze_until, freeze_reason
  into v_freeze_until, v_freeze_reason
  from public.video_provider_quota_reservations
  where provider = p_provider
    and reservation_kind = 'freeze'
    and status = 'frozen'
    and freeze_until > v_now
  order by freeze_until desc
  limit 1;

  select coalesce(sum(consumed_units + greatest(reserved_units - consumed_units - released_units, 0)), 0)
  into v_daily_committed
  from public.video_provider_quota_reservations
  where provider = p_provider
    and reservation_kind in ('video_chain', 'retry_submit')
    and quota_day_pt = v_quota_day_pt;

  select coalesce(sum(units), 0)
  into v_minute_used
  from public.video_provider_quota_events
  where provider = p_provider
    and event_type = 'consumed'
    and created_at >= v_minute_start
    and created_at < (v_minute_start + interval '1 minute');

  return jsonb_build_object(
    'provider', p_provider,
    'quota_day_pt', v_quota_day_pt,
    'daily_limit', coalesce(p_daily_limit, 0),
    'daily_effective_limit', v_effective_daily_limit,
    'daily_committed_units', v_daily_committed,
    'daily_remaining_units', greatest(v_effective_daily_limit - v_daily_committed, 0),
    'minute_limit', coalesce(p_minute_limit, 0),
    'minute_used_units', v_minute_used,
    'minute_remaining_units', greatest(coalesce(p_minute_limit, 0) - v_minute_used, 0),
    'frozen', v_freeze_until is not null,
    'freeze_until', v_freeze_until,
    'freeze_reason', v_freeze_reason
  );
end;
$$;

create or replace function public.reserve_video_provider_quota(
  p_provider text,
  p_reservation_key text,
  p_requested_units integer,
  p_post_id uuid default null,
  p_batch_id uuid default null,
  p_daily_limit integer default 0,
  p_minute_limit integer default 0,
  p_soft_buffer integer default 0,
  p_reservation_kind text default 'video_chain',
  p_require_immediate_slot boolean default true
)
returns jsonb
language plpgsql
as $$
declare
  v_now timestamptz := now();
  v_quota_day_pt date := timezone('America/Los_Angeles', v_now)::date;
  v_minute_start timestamptz := date_trunc('minute', v_now);
  v_effective_daily_limit integer := greatest(coalesce(p_daily_limit, 0) - greatest(coalesce(p_soft_buffer, 0), 0), 0);
  v_daily_committed integer := 0;
  v_minute_used integer := 0;
  v_freeze_until timestamptz;
  v_freeze_reason text;
  v_row public.video_provider_quota_reservations%rowtype;
begin
  if coalesce(p_requested_units, 0) <= 0 then
    return jsonb_build_object(
      'allowed', false,
      'reason', 'invalid_requested_units',
      'requested_units', coalesce(p_requested_units, 0)
    );
  end if;

  select freeze_until, freeze_reason
  into v_freeze_until, v_freeze_reason
  from public.video_provider_quota_reservations
  where provider = p_provider
    and reservation_kind = 'freeze'
    and status = 'frozen'
    and freeze_until > v_now
  order by freeze_until desc
  limit 1;

  if v_freeze_until is not null then
    return jsonb_build_object(
      'allowed', false,
      'reason', 'provider_frozen',
      'freeze_until', v_freeze_until,
      'freeze_reason', v_freeze_reason,
      'quota_day_pt', v_quota_day_pt
    );
  end if;

  select coalesce(sum(consumed_units + greatest(reserved_units - consumed_units - released_units, 0)), 0)
  into v_daily_committed
  from public.video_provider_quota_reservations
  where provider = p_provider
    and reservation_kind in ('video_chain', 'retry_submit')
    and quota_day_pt = v_quota_day_pt;

  if (v_daily_committed + p_requested_units) > v_effective_daily_limit then
    return jsonb_build_object(
      'allowed', false,
      'reason', 'daily_quota_exhausted',
      'quota_day_pt', v_quota_day_pt,
      'daily_limit', coalesce(p_daily_limit, 0),
      'daily_effective_limit', v_effective_daily_limit,
      'daily_committed_units', v_daily_committed,
      'daily_remaining_units', greatest(v_effective_daily_limit - v_daily_committed, 0),
      'requested_units', p_requested_units
    );
  end if;

  if p_require_immediate_slot and coalesce(p_minute_limit, 0) > 0 then
    select coalesce(sum(units), 0)
    into v_minute_used
    from public.video_provider_quota_events
    where provider = p_provider
      and event_type = 'consumed'
      and created_at >= v_minute_start
      and created_at < (v_minute_start + interval '1 minute');

    if (v_minute_used + 1) > p_minute_limit then
      return jsonb_build_object(
        'allowed', false,
        'reason', 'minute_quota_exhausted',
        'quota_day_pt', v_quota_day_pt,
        'minute_limit', p_minute_limit,
        'minute_used_units', v_minute_used,
        'minute_remaining_units', greatest(p_minute_limit - v_minute_used, 0),
        'requested_units', p_requested_units
      );
    end if;
  end if;

  insert into public.video_provider_quota_reservations (
    provider,
    reservation_key,
    reservation_kind,
    post_id,
    batch_id,
    quota_day_pt,
    reserved_units,
    consumed_units,
    released_units,
    status
  )
  values (
    p_provider,
    p_reservation_key,
    p_reservation_kind,
    p_post_id,
    p_batch_id,
    v_quota_day_pt,
    p_requested_units,
    0,
    0,
    'reserved'
  )
  returning * into v_row;

  insert into public.video_provider_quota_events (
    reservation_id,
    provider,
    reservation_key,
    event_type,
    units,
    details
  )
  values (
    v_row.id,
    p_provider,
    p_reservation_key,
    'reserved',
    p_requested_units,
    jsonb_build_object(
      'quota_day_pt', v_quota_day_pt,
      'require_immediate_slot', p_require_immediate_slot
    )
  );

  return jsonb_build_object(
    'allowed', true,
    'reservation_id', v_row.id,
    'reservation_key', p_reservation_key,
    'quota_day_pt', v_quota_day_pt,
    'reserved_units', p_requested_units,
    'daily_limit', coalesce(p_daily_limit, 0),
    'daily_effective_limit', v_effective_daily_limit,
    'daily_committed_units', v_daily_committed + p_requested_units,
    'daily_remaining_units', greatest(v_effective_daily_limit - (v_daily_committed + p_requested_units), 0)
  );
end;
$$;

create or replace function public.consume_video_provider_quota(
  p_reservation_key text,
  p_units integer default 1,
  p_operation_id text default null
)
returns jsonb
language plpgsql
as $$
declare
  v_row public.video_provider_quota_reservations%rowtype;
  v_remaining integer := 0;
begin
  if coalesce(p_units, 0) <= 0 then
    return jsonb_build_object('allowed', false, 'reason', 'invalid_units');
  end if;

  select *
  into v_row
  from public.video_provider_quota_reservations
  where reservation_key = p_reservation_key
  for update;

  if not found then
    return jsonb_build_object('allowed', false, 'reason', 'reservation_not_found');
  end if;

  v_remaining := greatest(v_row.reserved_units - v_row.consumed_units - v_row.released_units, 0);
  if p_units > v_remaining then
    return jsonb_build_object(
      'allowed', false,
      'reason', 'insufficient_reserved_units',
      'remaining_units', v_remaining
    );
  end if;

  update public.video_provider_quota_reservations
  set consumed_units = consumed_units + p_units,
      status = case
        when (consumed_units + p_units + released_units) >= reserved_units then 'completed'
        else 'active'
      end,
      provider_last_error_code = null,
      provider_last_error_message = null
  where id = v_row.id;

  insert into public.video_provider_quota_events (
    reservation_id,
    provider,
    reservation_key,
    event_type,
    units,
    details
  )
  values (
    v_row.id,
    v_row.provider,
    p_reservation_key,
    'consumed',
    p_units,
    case
      when p_operation_id is null then '{}'::jsonb
      else jsonb_build_object('operation_id', p_operation_id)
    end
  );

  if (v_row.consumed_units + p_units + v_row.released_units) >= v_row.reserved_units then
    insert into public.video_provider_quota_events (
      reservation_id,
      provider,
      reservation_key,
      event_type,
      units,
      details
    )
    values (
      v_row.id,
      v_row.provider,
      p_reservation_key,
      'completed',
      0,
      '{}'::jsonb
    );
  end if;

  return jsonb_build_object(
    'allowed', true,
    'reservation_key', p_reservation_key,
    'consumed_units', v_row.consumed_units + p_units,
    'remaining_units', greatest(v_row.reserved_units - (v_row.consumed_units + p_units) - v_row.released_units, 0)
  );
end;
$$;

create or replace function public.release_video_provider_quota(
  p_reservation_key text,
  p_reason text default null,
  p_final_status text default 'released',
  p_error_code text default null
)
returns jsonb
language plpgsql
as $$
declare
  v_row public.video_provider_quota_reservations%rowtype;
  v_release_units integer := 0;
begin
  select *
  into v_row
  from public.video_provider_quota_reservations
  where reservation_key = p_reservation_key
  for update;

  if not found then
    return jsonb_build_object('allowed', false, 'reason', 'reservation_not_found');
  end if;

  v_release_units := greatest(v_row.reserved_units - v_row.consumed_units - v_row.released_units, 0);

  update public.video_provider_quota_reservations
  set released_units = released_units + v_release_units,
      status = p_final_status,
      provider_last_error_code = coalesce(p_error_code, provider_last_error_code),
      provider_last_error_message = coalesce(p_reason, provider_last_error_message)
  where id = v_row.id;

  if v_release_units > 0 then
    insert into public.video_provider_quota_events (
      reservation_id,
      provider,
      reservation_key,
      event_type,
      units,
      details
    )
    values (
      v_row.id,
      v_row.provider,
      p_reservation_key,
      'released',
      v_release_units,
      jsonb_build_object('reason', coalesce(p_reason, 'released'))
    );
  end if;

  if p_final_status = 'completed' then
    insert into public.video_provider_quota_events (
      reservation_id,
      provider,
      reservation_key,
      event_type,
      units,
      details
    )
    values (
      v_row.id,
      v_row.provider,
      p_reservation_key,
      'completed',
      0,
      jsonb_build_object('reason', coalesce(p_reason, 'completed'))
    );
  elsif p_final_status = 'failed' then
    insert into public.video_provider_quota_events (
      reservation_id,
      provider,
      reservation_key,
      event_type,
      units,
      details
    )
    values (
      v_row.id,
      v_row.provider,
      p_reservation_key,
      'failed',
      0,
      jsonb_build_object('reason', coalesce(p_reason, 'failed'))
    );
  end if;

  return jsonb_build_object(
    'allowed', true,
    'reservation_key', p_reservation_key,
    'released_units', v_release_units,
    'final_status', p_final_status
  );
end;
$$;

create or replace function public.freeze_video_provider_quota(
  p_provider text,
  p_freeze_until timestamptz,
  p_reason text
)
returns jsonb
language plpgsql
as $$
declare
  v_quota_day_pt date := timezone('America/Los_Angeles', now())::date;
  v_reservation_key text := format('freeze:%s:%s', p_provider, v_quota_day_pt::text);
  v_row public.video_provider_quota_reservations%rowtype;
begin
  insert into public.video_provider_quota_reservations (
    provider,
    reservation_key,
    reservation_kind,
    quota_day_pt,
    reserved_units,
    consumed_units,
    released_units,
    status,
    freeze_until,
    freeze_reason
  )
  values (
    p_provider,
    v_reservation_key,
    'freeze',
    v_quota_day_pt,
    0,
    0,
    0,
    'frozen',
    p_freeze_until,
    p_reason
  )
  on conflict (reservation_key)
  do update set
    status = 'frozen',
    freeze_until = excluded.freeze_until,
    freeze_reason = excluded.freeze_reason
  returning * into v_row;

  insert into public.video_provider_quota_events (
    reservation_id,
    provider,
    reservation_key,
    event_type,
    units,
    details
  )
  values (
    v_row.id,
    p_provider,
    v_reservation_key,
    'frozen',
    0,
    jsonb_build_object('freeze_until', p_freeze_until, 'reason', p_reason)
  );

  return jsonb_build_object(
    'allowed', true,
    'reservation_key', v_reservation_key,
    'freeze_until', p_freeze_until,
    'reason', p_reason
  );
end;
$$;

alter table public.video_provider_quota_reservations enable row level security;
alter table public.video_provider_quota_events enable row level security;

grant select, insert, update, delete on public.video_provider_quota_reservations to service_role;
grant select, insert, update, delete on public.video_provider_quota_events to service_role;
grant execute on function public.get_video_provider_quota_snapshot(text, integer, integer, integer) to service_role;
grant execute on function public.reserve_video_provider_quota(text, text, integer, uuid, uuid, integer, integer, integer, text, boolean) to service_role;
grant execute on function public.consume_video_provider_quota(text, integer, text) to service_role;
grant execute on function public.release_video_provider_quota(text, text, text, text) to service_role;
grant execute on function public.freeze_video_provider_quota(text, timestamptz, text) to service_role;
