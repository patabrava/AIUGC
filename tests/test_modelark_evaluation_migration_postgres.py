"""Real PostgreSQL host isolation, rolling compatibility and actual-spend settlement."""
import pytest
from pathlib import Path
from tests.test_runway_evaluation_migration_postgres import SETUP, QUOTA_MIGRATIONS, MIGRATION, CHECKS, CONTAINER, _sql

DIRECT = Path(__file__).resolve().parents[1] / 'supabase/migrations/20261005000000_modelark_seedance_evaluations.sql'


def setup():
    # Preserve the test's outer transaction: every schema, role and row rolls back.
    migration = DIRECT.read_text().replace('BEGIN;\n', '', 1).removesuffix('COMMIT;\n')
    return SETUP + ''.join(p.read_text() for p in QUOTA_MIGRATIONS) + MIGRATION.read_text() + migration


@pytest.mark.skipif(not CONTAINER, reason='Requires a disposable PostgreSQL container')
def test_legacy_lifecycle_after_direct_migration():
    lines = set(_sql(setup() + CHECKS).splitlines())
    assert {'first_allowed=true','claimed=1','double_claim=0','completed=completed',
            'unknown=submission_unknown','service_insert=false','service_update=false'} <= lines


@pytest.mark.skipif(not CONTAINER, reason='Requires a disposable PostgreSQL container')
def test_direct_host_fences_and_actual_spend():
    script = r'''
CREATE FUNCTION pg_temp.payload(suffix text, host text DEFAULT 'modelark') RETURNS jsonb LANGUAGE sql AS $$
 SELECT jsonb_build_object('post_id','00000000-0000-0000-0000-0000000000a1',
 'semantic_run_id','00000000-0000-0000-0000-0000000000c1','take_id','00000000-0000-0000-0000-0000000000d1',
 'take_index',0,'take_attempt',1,'provider',host,'provider_model',CASE WHEN host='modelark' THEN 'dreamina-seedance-2-5-260628' ELSE 'seedance2_5' END,
 'requested_resolution','480p','requested_ratio','480:854','requested_duration_seconds',8,
 'request_contract','{}'::jsonb,'request_hash',repeat('1',64),'source_provenance','{}'::jsonb,
 'estimated_credits',945763,'estimated_usd',0.945763,'reservation_key',host || '_seedance_2_5:evaluation:' || suffix,
 'requested_by','ops@example.test','poller_environment','production','poller_scope','studio.example.test') $$;
SELECT 'direct=' || (public.create_runway_video_evaluation(pg_temp.payload('one'),10000000,1,0)->>'allowed');
SELECT 'legacy=' || (public.create_runway_video_evaluation(pg_temp.payload('legacy','runway'),10000000,1,0)->>'allowed');
SELECT public.mark_runway_video_evaluation_submitting(id) FROM public.runway_video_evaluations;
SELECT public.acknowledge_runway_video_evaluation(id,'cgt-' || provider,estimated_credits) FROM public.runway_video_evaluations;
UPDATE public.runway_video_evaluations SET next_poll_at=now()-interval '1 second';
SELECT 'old_claim=' || string_agg(provider,',') FROM public.claim_runway_video_evaluations('old',300,10,'production','studio.example.test');
CREATE TEMP TABLE claimed AS SELECT * FROM public.claim_seedance_video_evaluations('modelark','direct',300,10,'production','studio.example.test');
SELECT 'direct_claim=' || string_agg(provider,',') FROM claimed;
SELECT public.complete_runway_video_evaluation(id,lease_token,jsonb_build_object('storage_key','modelark-evaluations/x.mp4',
 'url','https://cdn.example.test/x.mp4','sha256',repeat('a',64),'byte_length',1024,'probe','{}'::jsonb),822402) FROM claimed;
SELECT 'settled=' || reserved_units || ':' || consumed_units || ':' || released_units || ':' || status
 FROM public.video_provider_quota_reservations WHERE provider='modelark_seedance_2_5';
SELECT 'legacy_unchanged=' || consumed_units FROM public.video_provider_quota_reservations WHERE provider='runway_seedance_2_5';
DO $$ BEGIN
 PERFORM public.create_runway_video_evaluation(pg_temp.payload('bad') || '{"provider_model":"seedance2_5"}'::jsonb,10000000,5,0);
 RAISE EXCEPTION 'bad model was admitted';
EXCEPTION WHEN raise_exception THEN IF SQLERRM = 'bad model was admitted' THEN RAISE; END IF; END $$;
SELECT 'bad_reserved=' || count(*) FROM public.video_provider_quota_reservations WHERE reservation_key LIKE '%:bad';
SELECT public.create_runway_video_evaluation(pg_temp.payload('failed'),10000000,1,0);
SELECT public.mark_runway_video_evaluation_submitting(id) FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:failed';
SELECT public.acknowledge_runway_video_evaluation(id,'cgt-failed',945763) FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:failed';
UPDATE public.runway_video_evaluations SET next_poll_at=now()-interval '1 second' WHERE reservation_key LIKE '%:failed';
CREATE TEMP TABLE failed_claim AS SELECT * FROM public.claim_seedance_video_evaluations('modelark','direct',300,1,'production','studio.example.test');
SELECT public.fail_runway_video_evaluation_after_submit(id,lease_token,'failed','provider_failed','terminal','{}'::jsonb,'FAILED',0) FROM failed_claim;
SELECT 'zero=' || consumed_units || ':' || released_units FROM public.video_provider_quota_reservations WHERE reservation_key LIKE '%:failed';
SELECT public.create_runway_video_evaluation(pg_temp.payload('over'),10000000,1,0);
SELECT public.mark_runway_video_evaluation_submitting(id) FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:over';
SELECT public.acknowledge_runway_video_evaluation(id,'cgt-over',945763) FROM public.runway_video_evaluations WHERE reservation_key LIKE '%:over';
UPDATE public.runway_video_evaluations SET next_poll_at=now()-interval '1 second' WHERE reservation_key LIKE '%:over';
CREATE TEMP TABLE over_claim AS SELECT * FROM public.claim_seedance_video_evaluations('modelark','direct',300,1,'production','studio.example.test');
SELECT public.complete_runway_video_evaluation(id,lease_token,jsonb_build_object('storage_key','modelark-evaluations/over.mp4',
 'url','https://cdn.example.test/over.mp4','sha256',repeat('b',64),'byte_length',1024,'probe','{}'::jsonb),1000000) FROM over_claim;
SELECT 'over_settled=' || reserved_units || ':' || consumed_units || ':' || released_units FROM public.video_provider_quota_reservations WHERE reservation_key LIKE '%:over';
SELECT 'over_freeze=' || count(*) FROM public.video_provider_quota_reservations WHERE provider='modelark_seedance_2_5' AND status='frozen';
SELECT 'anon_claim=' || has_function_privilege('anon','public.claim_seedance_video_evaluations(text,text,integer,integer,text,text)','EXECUTE');
SELECT 'service_claim=' || has_function_privilege('service_role','public.claim_seedance_video_evaluations(text,text,integer,integer,text,text)','EXECUTE');
ROLLBACK;
'''
    lines = set(_sql(setup() + script).splitlines())
    expected={'direct=true','legacy=true','old_claim=runway','direct_claim=modelark',
              'settled=945763:822402:123361:completed','legacy_unchanged=945763','bad_reserved=0',
              'zero=0:945763','over_settled=1000000:1000000:0','over_freeze=1','anon_claim=false','service_claim=true'}
    assert expected <= lines, expected-lines
