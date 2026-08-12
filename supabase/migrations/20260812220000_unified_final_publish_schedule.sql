-- Arm social dispatch and enabled blog publication together from the final Publish step.

CREATE OR REPLACE FUNCTION public.arm_batch_content_schedule(
  p_batch_id UUID,
  p_schedules JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  expected_count INTEGER;
  matched_count INTEGER;
  current_batch_state TEXT;
BEGIN
  IF p_batch_id IS NULL
     OR pg_catalog.jsonb_typeof(p_schedules) IS DISTINCT FROM 'array'
     OR pg_catalog.jsonb_array_length(p_schedules) = 0 THEN
    RAISE EXCEPTION 'publish arm contract is invalid';
  END IF;

  SELECT batch.state
  INTO current_batch_state
  FROM public.batches AS batch
  WHERE batch.id = p_batch_id
  FOR UPDATE;

  IF current_batch_state IS DISTINCT FROM 'S7_PUBLISH_PLAN' THEN
    RAISE EXCEPTION 'batch must be in S7_PUBLISH_PLAN';
  END IF;

  expected_count := pg_catalog.jsonb_array_length(p_schedules);

  WITH schedules AS (
    SELECT *
    FROM pg_catalog.jsonb_to_recordset(p_schedules) AS value(
      post_id UUID,
      scheduled_at TIMESTAMPTZ,
      networks JSONB,
      publish_caption TEXT,
      blog_scheduled_at TIMESTAMPTZ
    )
  )
  SELECT pg_catalog.count(*)::INTEGER
  INTO matched_count
  FROM schedules
  JOIN public.posts AS post
    ON post.id = schedules.post_id
   AND post.batch_id = p_batch_id
  WHERE post.video_url IS NOT NULL
    AND schedules.scheduled_at > pg_catalog.now()
    AND pg_catalog.btrim(COALESCE(schedules.publish_caption, '')) <> ''
    AND pg_catalog.jsonb_typeof(schedules.networks) = 'array'
    AND pg_catalog.jsonb_array_length(schedules.networks) > 0
    AND (
      NOT COALESCE(post.blog_enabled, FALSE)
      OR (
        schedules.blog_scheduled_at > pg_catalog.now()
        AND post.blog_status IN ('draft', 'scheduled')
        AND pg_catalog.btrim(COALESCE(post.blog_content ->> 'body_html', '')) <> ''
        AND pg_catalog.btrim(COALESCE(post.blog_content ->> 'preview_image_url', '')) <> ''
      )
    );

  IF matched_count <> expected_count THEN
    RAISE EXCEPTION 'one or more publish schedules are invalid';
  END IF;

  WITH schedules AS (
    SELECT
      value.post_id,
      value.scheduled_at,
      value.publish_caption,
      value.blog_scheduled_at,
      ARRAY(
        SELECT pg_catalog.jsonb_array_elements_text(value.networks)
      )::TEXT[] AS networks
    FROM pg_catalog.jsonb_to_recordset(p_schedules) AS value(
      post_id UUID,
      scheduled_at TIMESTAMPTZ,
      networks JSONB,
      publish_caption TEXT,
      blog_scheduled_at TIMESTAMPTZ
    )
  )
  UPDATE public.posts AS post
  SET scheduled_at = schedules.scheduled_at,
      publish_caption = schedules.publish_caption,
      social_networks = schedules.networks,
      publish_status = 'scheduled',
      blog_scheduled_at = CASE
        WHEN post.blog_enabled THEN schedules.blog_scheduled_at
        ELSE post.blog_scheduled_at
      END,
      blog_status = CASE
        WHEN post.blog_enabled THEN 'scheduled'
        ELSE post.blog_status
      END,
      blog_content = CASE
        WHEN post.blog_enabled THEN pg_catalog.jsonb_set(
          COALESCE(post.blog_content, '{}'::JSONB),
          '{publication_date}',
          pg_catalog.to_jsonb(schedules.blog_scheduled_at::TEXT),
          TRUE
        )
        ELSE post.blog_content
      END
  FROM schedules
  WHERE post.id = schedules.post_id
    AND post.batch_id = p_batch_id;

  RETURN pg_catalog.jsonb_build_object(
    'batch_id', p_batch_id,
    'armed_count', expected_count,
    'scheduled_blog_count', (
      SELECT pg_catalog.count(*)
      FROM pg_catalog.jsonb_to_recordset(p_schedules) AS value(
        blog_scheduled_at TIMESTAMPTZ
      )
      WHERE value.blog_scheduled_at IS NOT NULL
    )
  );
END;
$$;

REVOKE ALL ON FUNCTION public.arm_batch_content_schedule(UUID, JSONB)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.arm_batch_content_schedule(UUID, JSONB)
  TO service_role;

NOTIFY pgrst, 'reload schema';
