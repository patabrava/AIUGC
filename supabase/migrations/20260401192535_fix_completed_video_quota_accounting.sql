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
  v_finalize_units integer := 0;
begin
  select *
  into v_row
  from public.video_provider_quota_reservations
  where reservation_key = p_reservation_key
  for update;

  if not found then
    return jsonb_build_object('allowed', false, 'reason', 'reservation_not_found');
  end if;

  v_finalize_units := greatest(v_row.reserved_units - v_row.consumed_units - v_row.released_units, 0);

  if p_final_status = 'completed' then
    update public.video_provider_quota_reservations
    set consumed_units = consumed_units + v_finalize_units,
        status = p_final_status,
        provider_last_error_code = coalesce(p_error_code, provider_last_error_code),
        provider_last_error_message = coalesce(p_reason, provider_last_error_message)
    where id = v_row.id;

    if v_finalize_units > 0 then
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
        v_finalize_units,
        jsonb_build_object('reason', coalesce(p_reason, 'completed'))
      );
    end if;

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

    return jsonb_build_object(
      'allowed', true,
      'reservation_key', p_reservation_key,
      'consumed_units', v_row.consumed_units + v_finalize_units,
      'released_units', 0,
      'final_status', p_final_status
    );
  end if;

  update public.video_provider_quota_reservations
  set released_units = released_units + v_finalize_units,
      status = p_final_status,
      provider_last_error_code = coalesce(p_error_code, provider_last_error_code),
      provider_last_error_message = coalesce(p_reason, provider_last_error_message)
  where id = v_row.id;

  if v_finalize_units > 0 then
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
      v_finalize_units,
      jsonb_build_object('reason', coalesce(p_reason, 'released'))
    );
  end if;

  if p_final_status = 'failed' then
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
    'released_units', v_finalize_units,
    'final_status', p_final_status
  );
end;
$$;

with repaired_rows as (
  update public.video_provider_quota_reservations
  set consumed_units = consumed_units + released_units,
      released_units = 0
  where status = 'completed'
    and reservation_kind in ('video_chain', 'retry_submit')
    and released_units > 0
  returning id
)
update public.video_provider_quota_events as e
set event_type = 'consumed',
    details = jsonb_set(e.details, '{repaired_by_migration}', 'true'::jsonb, true)
from repaired_rows
where e.reservation_id = repaired_rows.id
  and e.event_type = 'released';
