"""Real PostgreSQL proof of bounded failure, atomic retry and paid-work fences."""
import json
import os
from pathlib import Path
import pytest
from tests.test_semantic_video_worker_migration_postgres import _psql, _scalar, ROOT

DB = 'semantic_runtime_recovery_test'
RUN = '00000000-0000-0000-0000-000000000103'
TAKE = '00000000-0000-0000-0000-000000000105'
MIGRATION = ROOT / 'supabase/migrations/20261105000000_semantic_video_runtime_recovery.sql'
pytestmark = pytest.mark.skipif(not os.getenv('SEMANTIC_UGC_POSTGRES_CONTAINER'), reason='Requires isolated PostgreSQL container')


@pytest.fixture(scope='module', autouse=True)
def database():
    _psql('postgres', f'DROP DATABASE IF EXISTS {DB}; CREATE DATABASE {DB};')
    _psql(DB, '''
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
    END $$;
    ALTER ROLE service_role BYPASSRLS;
    CREATE TABLE public.batches(id UUID PRIMARY KEY,brand TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'S1_SETUP',creation_mode TEXT NOT NULL DEFAULT 'automated',post_type_counts JSONB NOT NULL DEFAULT '{}',manual_post_count INTEGER,target_length_tier INTEGER,video_pipeline_route TEXT,archived BOOLEAN NOT NULL DEFAULT false);
    CREATE TABLE public.actor_identities(id UUID PRIMARY KEY);
    CREATE TABLE public.posts(id UUID PRIMARY KEY,batch_id UUID REFERENCES public.batches(id),video_url TEXT,video_status TEXT,video_metadata JSONB NOT NULL DEFAULT '{}');
    CREATE FUNCTION public.touch_updated_at() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN NEW.updated_at=now(); RETURN NEW; END $$;
    ''')
    for name in ['20260713000000_semantic_ugc_production.sql','20260713000100_semantic_video_api_transactions.sql','20260713000200_semantic_video_worker.sql','20260720000100_semantic_video_lease_renewal.sql']:
        _psql(DB, (ROOT/'supabase/migrations'/name).read_text())
    _psql(DB,MIGRATION.read_text())
    _psql(DB,MIGRATION.read_text())  # reviewed migration is safely repeatable
    yield
    _psql('postgres', f'DROP DATABASE {DB};')


@pytest.fixture(autouse=True)
def seed(database):
    _psql(DB, f'''TRUNCATE public.batches CASCADE;
    INSERT INTO public.batches(id,brand,creation_mode,target_duration_seconds,video_pipeline_route) VALUES ('00000000-0000-0000-0000-000000000101','test','semantic_ugc',16,'semantic_ugc');
    INSERT INTO public.posts(id,batch_id) VALUES ('00000000-0000-0000-0000-000000000102','00000000-0000-0000-0000-000000000101');
    INSERT INTO public.semantic_video_runs(id,post_id,batch_id,requested_duration_seconds,duration_contract,duration_contract_hash,script_snapshot,script_hash,stage,plan_hash,artifact_prefix,max_submission_count,max_estimated_cost_usd,reserved_submission_count,reserved_estimated_cost_usd)
    VALUES ('{RUN}','00000000-0000-0000-0000-000000000102','00000000-0000-0000-0000-000000000101',16,'{{}}','duration','{{}}','script','generating','plan','test',2,6.4,2,6.4);
    INSERT INTO public.semantic_video_takes(id,run_id,take_index,attempt,beat_text,word_count,estimated_speech_seconds,provider_duration_seconds,shot_transform,shot_hash,prompt_hash,provider_model,request_contract,request_hash,submission_state,quota_state,quota_reservation_key,quota_cost_usd,raw_artifact_uri,raw_artifact_sha256)
    VALUES ('00000000-0000-0000-0000-000000000104','{RUN}',0,1,'One',1,1,8,'{{}}','shot','prompt','veo-3.1-generate-001','{{}}','hash0','completed','consumed','00000000-0000-0000-0000-000000000110',3.2,'https://stored/completed.mp4','unchanged'),
    ('{TAKE}','{RUN}',1,1,'Two',1,1,8,'{{}}','shot','prompt','veo-3.1-generate-001','{{}}','hash1','reserved','reserved','00000000-0000-0000-0000-000000000111',3.2,NULL,NULL);
    ''')


def claim():
    return json.loads(_scalar(DB, f"SELECT to_jsonb(x) FROM public.claim_semantic_video_run('worker-1',120,'{RUN}') x"))


def fail(token, error=None):
    error=error or {'code':'ReadError','message':'transport failed'}
    return json.loads(_scalar(DB,f"SELECT public.persist_semantic_video_worker_exception('{RUN}','worker-1','{token}','generating','{json.dumps(error)}'::jsonb)"))


def release(token):
    _psql(DB,f"SELECT public.release_semantic_video_lease('{RUN}','worker-1','{token}');")


def resume(revision, plan='plan', check=True):
    return _psql(DB,f"SET ROLE service_role; SELECT public.resume_semantic_video_runtime_v1('{RUN}',{revision},'{plan}','operator@example.com');",check=check)


def test_three_failures_stop_claims_and_explicit_retry_preserves_paid_artifacts():
    for n in range(1,4):
        token=claim()['lease_token']; r=fail(token); release(token)
        assert r['failure_envelope']['failure_count']==n
        assert r['stage']==('failed' if n==3 else 'generating')
    assert _scalar(DB,f"SELECT count(*) FROM public.claim_semantic_video_run('worker-2',120,'{RUN}')")=='0'
    rev=int(_scalar(DB,f"SELECT revision FROM public.semantic_video_runs WHERE id='{RUN}'"))
    assert resume(rev-1,check=False).returncode != 0
    assert resume(rev,plan='wrong',check=False).returncode != 0
    resume(rev)
    assert resume(rev,check=False).returncode != 0
    assert _scalar(DB,f"SELECT stage||':'||reserved_submission_count||':'||reserved_estimated_cost_usd FROM public.semantic_video_runs WHERE id='{RUN}'").startswith('generating:2:6.4')
    assert _scalar(DB,"SELECT raw_artifact_sha256||':'||submission_state FROM public.semantic_video_takes WHERE take_index=0")=='unchanged:completed'
    assert claim()['stage']=='generating'


def test_proven_pre_provider_ack_loss_can_reset_only_its_exact_reserved_intent():
    token=claim()['lease_token']
    _psql(DB,f"UPDATE public.semantic_video_takes SET submission_state='intent_persisted' WHERE id='{TAKE}';")
    error={'code':'submission_intent_acknowledgement_lost','provider_call_entered':False,'take_id':TAKE,'request_hash':'hash1'}
    r=fail(token,error); assert r['stage']=='failed'
    assert resume(r['revision'],check=False).returncode != 0  # live lease
    release(token)
    rev=int(_scalar(DB,f"SELECT revision FROM public.semantic_video_runs WHERE id='{RUN}'"));resume(rev)
    assert _scalar(DB,f"SELECT submission_state||':'||quota_state||':'||quota_cost_usd FROM public.semantic_video_takes WHERE id='{TAKE}'").startswith('reserved:reserved:3.2')
    assert _scalar(DB,f"SELECT artifact_manifest->'runtime_recovery'->'previous_error'->>'take_id' FROM public.semantic_video_runs WHERE id='{RUN}'")==TAKE


@pytest.mark.parametrize('mutation', ["'submission_unknown'", "'intent_persisted'"])
def test_ambiguous_submission_without_nonentry_proof_is_never_reset(mutation):
    token=claim()['lease_token']
    _psql(DB,f"UPDATE public.semantic_video_takes SET submission_state={mutation} WHERE id='{TAKE}';")
    fail(token);fail(token);fail(token);release(token)
    rev=int(_scalar(DB,f"SELECT revision FROM public.semantic_video_runs WHERE id='{RUN}'"))
    assert resume(rev,check=False).returncode != 0
    assert _scalar(DB,f"SELECT stage FROM public.semantic_video_runs WHERE id='{RUN}'")=='failed'


def test_recovery_permissions_are_service_only():
    assert _scalar(DB,"SELECT has_function_privilege('service_role','public.resume_semantic_video_runtime_v1(uuid,integer,text,text)','execute')")=='t'
    for role in ['anon','authenticated']:
        assert _scalar(DB,f"SELECT has_function_privilege('{role}','public.resume_semantic_video_runtime_v1(uuid,integer,text,text)','execute')")=='f'
