BEGIN;
-- Bound repeat failures at the authoritative claim boundary. All worker versions
-- use this RPC, so obsolete workers cannot keep reclaiming the failed run.
CREATE OR REPLACE FUNCTION public.persist_semantic_video_worker_exception(
 p_run_id UUID, p_worker_id TEXT, p_lease_token UUID, p_stage TEXT, p_error JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE r public.semantic_video_runs%ROWTYPE; n INTEGER;
BEGIN
 IF jsonb_typeof(p_error) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'invalid worker error'; END IF;
 SELECT * INTO r FROM public.require_semantic_video_worker_lease(p_run_id,p_worker_id,p_lease_token,p_stage);
 n := CASE WHEN r.failure_envelope->>'stage'=p_stage THEN COALESCE((r.failure_envelope->>'failure_count')::integer,0)+1 ELSE 1 END;
 UPDATE public.semantic_video_runs SET
  stage=CASE WHEN n>=3 OR p_error->>'code'='submission_intent_acknowledgement_lost' THEN 'failed' ELSE stage END,
  failure_envelope=p_error||jsonb_build_object('stage',p_stage,'recorded_at',now(),'failure_count',n,'runtime_failure',true),
  revision=revision+1 WHERE id=p_run_id RETURNING * INTO r;
 RETURN to_jsonb(r);
END; $$;

CREATE OR REPLACE FUNCTION public.resume_semantic_video_runtime_v1(
 p_run_id UUID,p_expected_revision INTEGER,p_plan_hash TEXT,p_requested_by TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE r public.semantic_video_runs%ROWTYPE; t public.semantic_video_takes%ROWTYPE; bad_count INTEGER; previous_error JSONB; resume_stage TEXT;
BEGIN
 SELECT * INTO STRICT r FROM public.semantic_video_runs WHERE id=p_run_id FOR UPDATE;
 IF r.stage<>'failed' OR r.revision<>p_expected_revision OR r.plan_hash IS DISTINCT FROM p_plan_hash
 OR COALESCE(r.failure_envelope->>'runtime_failure','false')<>'true'
 OR (r.lease_expires_at IS NOT NULL AND r.lease_expires_at>now())
 OR NULLIF(btrim(p_requested_by),'') IS NULL THEN
  RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='semantic_video_conflict: recovery state changed';
 END IF;
 resume_stage:=r.failure_envelope->>'stage';
 IF resume_stage NOT IN ('generating','transcript_qa','identity_qa','voice_qa','acoustic_qa','composing','uploading') THEN RAISE EXCEPTION 'unsupported recovery stage'; END IF;
 PERFORM 1 FROM public.semantic_video_takes WHERE run_id=p_run_id FOR UPDATE;
 SELECT count(*) INTO bad_count FROM (
  SELECT DISTINCT ON(take_index) * FROM public.semantic_video_takes WHERE run_id=p_run_id ORDER BY take_index,attempt DESC
 ) AS latest WHERE submission_state IN ('intent_persisted','submission_unknown');
 IF bad_count>0 THEN
  IF bad_count<>1 OR r.failure_envelope->>'code'<>'submission_intent_acknowledgement_lost'
  OR r.failure_envelope->'provider_call_entered' IS DISTINCT FROM 'false'::jsonb THEN
   RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='semantic_video_conflict: provider submission needs confirmation';
  END IF;
  SELECT * INTO t FROM (SELECT DISTINCT ON(take_index) * FROM public.semantic_video_takes WHERE run_id=p_run_id ORDER BY take_index,attempt DESC) AS latest WHERE submission_state IN ('intent_persisted','submission_unknown');
  IF t.id IS NULL OR t.id::text IS DISTINCT FROM r.failure_envelope->>'take_id' OR t.submission_state<>'intent_persisted' OR t.operation_id IS NOT NULL OR t.quota_state<>'reserved'
  OR t.request_hash IS DISTINCT FROM r.failure_envelope->>'request_hash' THEN
   RAISE EXCEPTION USING ERRCODE='40001',MESSAGE='semantic_video_conflict: pre-provider evidence changed';
  END IF;
  UPDATE public.semantic_video_takes SET submission_state='reserved',submission_intent_at=NULL WHERE id=t.id;
 END IF;
 previous_error:=r.failure_envelope;
 UPDATE public.semantic_video_runs SET stage=resume_stage,failure_envelope=NULL,
  lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,revision=revision+1,
  artifact_manifest=artifact_manifest||jsonb_build_object('runtime_recovery',jsonb_build_object('previous_error',previous_error,'requested_by',p_requested_by,'resumed_at',now()))
 WHERE id=p_run_id RETURNING * INTO r;
 RETURN to_jsonb(r);
END; $$;
REVOKE ALL ON FUNCTION public.persist_semantic_video_worker_exception(UUID,TEXT,UUID,TEXT,JSONB) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.persist_semantic_video_worker_exception(UUID,TEXT,UUID,TEXT,JSONB) TO service_role;
REVOKE ALL ON FUNCTION public.resume_semantic_video_runtime_v1(UUID,INTEGER,TEXT,TEXT) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.resume_semantic_video_runtime_v1(UUID,INTEGER,TEXT,TEXT) TO service_role;
COMMIT;
