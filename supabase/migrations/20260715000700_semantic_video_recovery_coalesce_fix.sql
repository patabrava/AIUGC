DO $$
DECLARE
  function_definition TEXT;
BEGIN
  function_definition := pg_catalog.pg_get_functiondef(
    'public.reuse_semantic_video_prior_attempts(uuid,integer,text,jsonb)'::pg_catalog.regprocedure
  );
  IF pg_catalog.strpos(function_definition, 'pg_catalog.coalesce') = 0 THEN
    RAISE EXCEPTION 'semantic video prior-attempt recovery function has no qualified COALESCE defect';
  END IF;
  EXECUTE pg_catalog.replace(
    function_definition,
    'pg_catalog.coalesce',
    'COALESCE'
  );

  function_definition := pg_catalog.pg_get_functiondef(
    'public.reclaim_semantic_video_candidate_reservation(uuid,integer)'::pg_catalog.regprocedure
  );
  IF pg_catalog.strpos(function_definition, 'pg_catalog.coalesce') = 0 THEN
    RAISE EXCEPTION 'semantic video candidate recovery function has no qualified COALESCE defect';
  END IF;
  EXECUTE pg_catalog.replace(
    function_definition,
    'pg_catalog.coalesce',
    'COALESCE'
  );
END;
$$;
