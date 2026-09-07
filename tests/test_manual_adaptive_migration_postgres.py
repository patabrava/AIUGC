"""Rolling cutover: fixed runs stay claimable; adaptive runs require v3."""
import os
from pathlib import Path
import subprocess
import pytest

CONTAINER = os.getenv('SEMANTIC_UGC_POSTGRES_CONTAINER')
MIGRATION = Path(__file__).resolve().parents[1] / 'supabase/migrations/20260907000100_manual_adaptive_video_worker.sql'


def sql(text):
    return subprocess.run(['docker', 'exec', '-i', CONTAINER, 'psql', '-U', 'postgres',
        '-v', 'ON_ERROR_STOP=1', '-At', '-d', 'postgres'], input=text, text=True, capture_output=True, check=True).stdout


@pytest.mark.skipif(not CONTAINER, reason='Requires a disposable PostgreSQL container')
def test_v2_cannot_claim_adaptive_run_but_v3_can():
    # Transaction rollback leaves this test isolated even on an existing test container.
    setup = '''
    BEGIN;
    DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon; END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated; END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role; END IF;
    END $$;
    CREATE TABLE public.semantic_video_runs (
        id uuid PRIMARY KEY, stage text, duration_contract jsonb,
        lease_owner text, lease_token uuid, lease_expires_at timestamptz,
        revision int DEFAULT 1, updated_at timestamptz DEFAULT now(), created_at timestamptz DEFAULT now()
    );
    CREATE TABLE public.semantic_video_takes (
        run_id uuid, take_index int, attempt int, submission_state text
    );
    INSERT INTO public.semantic_video_runs (id,stage,duration_contract) VALUES
        ('00000000-0000-0000-0000-000000000001','composing','{"duration_mode":"manual_script_v1"}'),
        ('00000000-0000-0000-0000-000000000002','composing','{}');
    '''
    checks = '''
    SELECT 'adaptive_v2=' || count(*) FROM public.claim_semantic_video_run('semantic-video-contract-v2-test',120,'00000000-0000-0000-0000-000000000001');
    SELECT 'fixed_v2=' || count(*) FROM public.claim_semantic_video_run('semantic-video-contract-v2-test',120,'00000000-0000-0000-0000-000000000002');
    SELECT 'adaptive_v3=' || count(*) FROM public.claim_semantic_video_run('semantic-video-contract-v3-test',120,'00000000-0000-0000-0000-000000000001');
    SELECT 'double_claim=' || count(*) FROM public.claim_semantic_video_run('semantic-video-contract-v3-other',120,'00000000-0000-0000-0000-000000000001');
    ROLLBACK;
    '''
    output = sql(setup + MIGRATION.read_text() + checks)
    assert 'adaptive_v2=0' in output
    assert 'fixed_v2=1' in output
    assert 'adaptive_v3=1' in output
    assert 'double_claim=0' in output
