"""Regression for two independently rendered scene plates sharing one actor."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = os.getenv('SEMANTIC_UGC_POSTGRES_CONTAINER')
DB = 'independent_scene_approval_test'
MIGRATION = ROOT / 'supabase/migrations/20261019000000_independent_scene_master_approval.sql'


def sql(text, *, check=True):
    return subprocess.run(
        ['docker', 'exec', '-i', CONTAINER, 'psql', '-U', 'postgres', '-d', DB,
         '-At', '-v', 'ON_ERROR_STOP=1'], input=text, text=True,
        capture_output=True, check=check,
    )


def seed(number, *, passed=True):
    run = f'10000000-0000-0000-0000-{number:012d}'
    post = f'20000000-0000-0000-0000-{number:012d}'
    digest = f'{number:064x}'
    gate = dict(passed=passed, status='passed', candidate_sha256=digest,
                evaluated_actor_reference_fingerprint='a'*64,
                evaluator_model='gemini-2.5-flash', evaluator_contract_version='semantic-scene-identity-v2',
                confidence=.95, blocking_reasons=[], component_results={k: True for k in (
                    'same_person', 'facial_geometry_consistent', 'apparent_age_consistent',
                    'hairline_and_hair_consistent', 'skin_texture_natural',
                    'not_beautified_or_stylized', 'no_face_artifacts')})
    candidate = dict(index=1, storage_uri=f'https://example.com/{number}.png', sha256=digest,
                     byte_length=1234, mime_type='image/png', provider_model='gemini-3.1-flash-image',
                     actor_reference_fingerprint='a'*64, visual_contract_hash='b'*64,
                     generation_contract_hash='c'*64, derivation_mode='bootstrap', identity_gate_result=gate)
    master = dict(candidates=[candidate], actor_reference_fingerprint='a'*64,
                  visual_contract_hash='b'*64, generation_contract_hash='c'*64, derivation_mode='bootstrap')
    reference = dict(actor_reference_fingerprint='a'*64, visual_contract={'contract_hash':'b'*64},
                     scene_plate_generation_contract={'contract_hash':'c'*64, 'model':'gemini-3.1-flash-image',
                         'identity_evaluator_model':'gemini-2.5-flash', 'minimum_identity_confidence':.9})
    sql(f"""INSERT INTO posts VALUES ('{post}', '30000000-0000-0000-0000-000000000001');
      INSERT INTO semantic_video_runs(id,post_id,batch_id,requested_duration_seconds,duration_contract,
        duration_contract_hash,script_snapshot,script_hash,actor_identity_id,actor_snapshot,
        reference_snapshot,reference_hash,master_snapshot,stage,artifact_prefix)
      VALUES ('{run}','{post}','30000000-0000-0000-0000-000000000001',32,'{{}}','duration',
        '{{}}','script','40000000-0000-0000-0000-000000000001','{{}}',
        $j${json.dumps(reference)}$j$,'reference',$j${json.dumps(master)}$j$,'awaiting_reference_approval','test');""")
    return run


def approve(run, *, attest=True):
    return sql(f"SET ROLE service_role; SELECT public.approve_semantic_video_master('{run}',0,1,'operator@example.com',{str(attest).lower()},'semantic-actor-identity-v1',NULL);", check=False)


@pytest.mark.skipif(not CONTAINER, reason='Set SEMANTIC_UGC_POSTGRES_CONTAINER for real PostgreSQL approval regression.')
def test_independent_siblings_preserve_anchor_and_identity_fences():
    admin = ['docker','exec','-i',CONTAINER,'psql','-U','postgres','-d','postgres','-v','ON_ERROR_STOP=1']
    subprocess.run(admin, input=f'DROP DATABASE IF EXISTS {DB}; CREATE DATABASE {DB};', text=True, capture_output=True, check=True)
    try:
        sql("""DO $$ BEGIN
          IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
          IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
          IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
          END $$;
          ALTER ROLE service_role BYPASSRLS;
          CREATE TABLE batches(id UUID PRIMARY KEY, brand TEXT, state TEXT DEFAULT 'S1_SETUP',
            creation_mode TEXT DEFAULT 'automated', post_type_counts JSONB DEFAULT '{}',
            manual_post_count INTEGER,target_length_tier INTEGER,video_pipeline_route TEXT,archived BOOLEAN DEFAULT FALSE);
          CREATE TABLE actor_identities(id UUID PRIMARY KEY);
          CREATE TABLE posts(id UUID PRIMARY KEY,batch_id UUID REFERENCES batches(id));
          CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.updated_at=now();RETURN NEW; END $$;""")
        for name in ('20260713000000_semantic_ugc_production.sql', '20260713000100_semantic_video_api_transactions.sql',
                     '20260720000200_semantic_actor_scene_plate_anchor.sql', '20260726000000_semantic_scene_identity_contract.sql',
                     '20260825021000_actor_front_passthrough_master.sql'):
            sql((ROOT/'supabase/migrations'/name).read_text())
        sql("INSERT INTO batches(id,brand,creation_mode,target_duration_seconds,video_pipeline_route) VALUES('30000000-0000-0000-0000-000000000001','test','semantic_ugc',32,'semantic_ugc'); INSERT INTO actor_identities VALUES('40000000-0000-0000-0000-000000000001');")
        first, second = seed(1), seed(2)
        assert approve(first).returncode == 0
        anchor = sql('SELECT row_to_json(a) FROM semantic_actor_scene_plate_anchors a;').stdout.strip()
        failure = approve(second)
        assert 'current-contract actor anchor differs' in failure.stderr
        sql(MIGRATION.read_text())
        sql(MIGRATION.read_text())  # Reapplication must not rename the passthrough wrapper.
        assert approve(second).returncode == 0
        assert sql('SELECT row_to_json(a) FROM semantic_actor_scene_plate_anchors a;').stdout.strip() == anchor
        assert sql(f"SELECT master_snapshot->>'claimed_canonical_anchor_id' IS NULL FROM semantic_video_runs WHERE id='{second}';").stdout.strip() == 't'
        assert approve(second).returncode != 0
        invalid = seed(3, passed=False)
        assert approve(invalid).returncode != 0
        no_attestation = seed(4)
        assert approve(no_attestation, attest=False).returncode != 0
        siblings = [seed(i) for i in range(5,12)]
        with ThreadPoolExecutor(max_workers=7) as pool:
            outcomes = list(pool.map(approve, siblings))
        assert all(o.returncode == 0 for o in outcomes), [o.stderr for o in outcomes]
        assert sql('SELECT count(*) FROM semantic_video_approvals;').stdout.strip() == '9'
        assert sql('SELECT row_to_json(a) FROM semantic_actor_scene_plate_anchors a;').stdout.strip() == anchor
        # A forged derived image must still prove the precise persisted anchor lineage.
        forged = seed(12)
        sql(f"UPDATE semantic_video_runs SET master_snapshot=jsonb_set(jsonb_set(master_snapshot,'{{derivation_mode}}','\"canonical_anchor\"'),'{{candidates,0,derivation_mode}}','\"canonical_anchor\"') WHERE id='{forged}';")
        assert 'verified canonical actor anchor changed or was not used' in approve(forged).stderr
        assert sql("SELECT has_function_privilege('authenticated','public.approve_semantic_video_generated_master_v1(uuid,integer,integer,text,boolean,text,text)','EXECUTE');").stdout.strip() == 'f'
    finally:
        subprocess.run(admin, input=f'DROP DATABASE IF EXISTS {DB};', text=True, capture_output=True, check=True)
