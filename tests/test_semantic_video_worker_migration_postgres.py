from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE_MIGRATION = ROOT / "supabase/migrations/20260713000000_semantic_ugc_production.sql"
API_MIGRATION = ROOT / "supabase/migrations/20260713000100_semantic_video_api_transactions.sql"
WORKER_MIGRATION = ROOT / "supabase/migrations/20260713000200_semantic_video_worker.sql"
RECOVERY_MIGRATION = ROOT / "supabase/migrations/20260714000100_semantic_video_provider_recovery.sql"
CONTAINER = os.getenv("SEMANTIC_UGC_POSTGRES_CONTAINER")
DATABASE = "semantic_ugc_worker_rpc_test"
BATCH_ID = "00000000-0000-0000-0000-000000000101"
POST_ID = "00000000-0000-0000-0000-000000000102"
RUN_ID = "00000000-0000-0000-0000-000000000103"
TAKE_1 = "00000000-0000-0000-0000-000000000104"
TAKE_2 = "00000000-0000-0000-0000-000000000105"
APPROVAL_ID = "00000000-0000-0000-0000-000000000106"


def test_provider_recovery_migration_persists_retry_guidance_and_backfills_stuck_failures():
    sql = RECOVERY_MIGRATION.read_text()

    assert "CREATE OR REPLACE FUNCTION public.persist_semantic_video_provider_failure" in sql
    assert "retry_guidance = pg_catalog.jsonb_build_object" in sql
    assert "submission_state = 'failed'" in sql
    assert "retry_guidance IS NULL" in sql
    assert "provider_internal_failure" in sql
    assert "GRANT EXECUTE ON FUNCTION public.persist_semantic_video_provider_failure" in sql


def _psql(database: str, sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            str(CONTAINER),
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            database,
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=check,
    )


@pytest.mark.skipif(
    not CONTAINER,
    reason="Set SEMANTIC_UGC_POSTGRES_CONTAINER to run PostgreSQL migration integration.",
)
def test_worker_migration_fences_paid_state_and_completes_post_atomically():
    assert WORKER_MIGRATION.exists()
    _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};")
    _psql("postgres", f"CREATE DATABASE {DATABASE};")
    try:
        _psql(
            DATABASE,
            """
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
            END;
            $$;
            ALTER ROLE service_role BYPASSRLS;
            ALTER ROLE anon NOBYPASSRLS;
            ALTER ROLE authenticated NOBYPASSRLS;
            CREATE TABLE public.batches (
              id UUID PRIMARY KEY,
              brand TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'S1_SETUP',
              creation_mode TEXT NOT NULL DEFAULT 'automated',
              post_type_counts JSONB NOT NULL DEFAULT '{}'::JSONB,
              manual_post_count INTEGER,
              target_length_tier INTEGER,
              video_pipeline_route TEXT,
              archived BOOLEAN NOT NULL DEFAULT FALSE
            );
            CREATE TABLE public.actor_identities (id UUID PRIMARY KEY);
            CREATE TABLE public.posts (
              id UUID PRIMARY KEY,
              batch_id UUID NOT NULL REFERENCES public.batches(id) ON DELETE CASCADE,
              video_url TEXT,
              video_status TEXT,
              video_metadata JSONB NOT NULL DEFAULT '{}'::JSONB
            );
            CREATE OR REPLACE FUNCTION public.touch_updated_at()
            RETURNS TRIGGER LANGUAGE plpgsql AS $$
            BEGIN
              NEW.updated_at = now();
              RETURN NEW;
            END;
            $$;
            """,
        )
        _psql(DATABASE, BASE_MIGRATION.read_text())
        _psql(DATABASE, API_MIGRATION.read_text())

        _psql(
            DATABASE,
            f"""
            INSERT INTO public.batches (
              id, brand, creation_mode, target_length_tier, target_duration_seconds,
              video_pipeline_route
            ) VALUES ('{BATCH_ID}', 'A', 'semantic_ugc', NULL, 16, 'semantic_ugc');
            INSERT INTO public.posts (id, batch_id) VALUES ('{POST_ID}', '{BATCH_ID}');
            INSERT INTO public.semantic_video_runs (
              id, post_id, batch_id, requested_duration_seconds, duration_contract,
              duration_contract_hash, script_snapshot, script_hash, reference_snapshot,
              reference_hash, master_snapshot, master_hash, stage, plan_snapshot,
              plan_hash, provider_model, resolution, estimated_cost_usd, artifact_prefix
            ) VALUES (
              '{RUN_ID}', '{POST_ID}', '{BATCH_ID}', 16, '{{"requested_duration_seconds":16}}',
              'duration-hash', '{{"text":"approved"}}', 'script-hash', '{{}}', 'reference-hash',
              '{{"storage_uri":"semantic/master.png","sha256":"master-hash","byte_length":10}}',
              'master-hash', 'generating',
              '{{"take_count":2,"billable_provider_seconds":16,"quota_units":2,"price_per_provider_second_usd":"0.40","estimated_cost_usd":"6.40","takes":[{{"take_index":0,"request_hash":"request-0","provider_duration_seconds":8}},{{"take_index":1,"request_hash":"request-1","provider_duration_seconds":8}}]}}',
              'plan-hash', 'veo-3.1-generate-001', '1080p', 6.40, 'semantic/run'
            );
            INSERT INTO public.semantic_video_takes (
              id, run_id, take_index, attempt, beat_text, word_count, estimated_speech_seconds,
              provider_duration_seconds, shot_transform, shot_hash, prompt_hash,
              negative_prompt_hash, provider_model, seed, request_contract, request_hash,
              submission_state
            ) VALUES
              ('{TAKE_1}', '{RUN_ID}', 0, 1, 'Beat 0', 2, 1.0, 8, '{{"output_sha256":"shot-0"}}', 'shot-hash-0', 'prompt-0', 'negative-0', 'veo-3.1-generate-001', 100, '{{"prompt":"Beat 0"}}', 'request-0', 'planned'),
              ('{TAKE_2}', '{RUN_ID}', 1, 1, 'Beat 1', 2, 1.0, 8, '{{"output_sha256":"shot-1"}}', 'shot-hash-1', 'prompt-1', 'negative-1', 'veo-3.1-generate-001', 101, '{{"prompt":"Beat 1"}}', 'request-1', 'planned');
            INSERT INTO public.semantic_video_approvals (
              id, run_id, approval_type, run_revision, contract_hash, approved_take_indexes,
              approved_provider_seconds, quota_units, estimated_cost_usd, approved_by
            ) VALUES (
              '{APPROVAL_ID}', '{RUN_ID}', 'initial_plan', 0, 'plan-hash', ARRAY[0, 1],
              16, 2, 6.40, 'operator@example.com'
            );
            """,
        )
        _psql(DATABASE, WORKER_MIGRATION.read_text())
        _psql(DATABASE, WORKER_MIGRATION.read_text())

        claim = _psql(
            DATABASE,
            f"SET ROLE service_role; SELECT id, lease_token FROM public.claim_semantic_video_run('worker-1', 60, '{RUN_ID}');",
        )
        assert RUN_ID in claim.stdout
        token = _psql(
            DATABASE,
            f"SELECT lease_token::text FROM public.semantic_video_runs WHERE id = '{RUN_ID}';",
        ).stdout.splitlines()[2].strip()

        reserve_sql = (
            "SET ROLE service_role; "
            f"SELECT public.reserve_semantic_video_submission('{RUN_ID}', '{{take_id}}', 'worker-1', '{token}');"
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda take_id: _psql(
                        DATABASE,
                        reserve_sql.format(take_id=take_id),
                        check=False,
                    ),
                    (TAKE_1, TAKE_2),
                )
            )
        assert sum(result.returncode == 0 for result in results) == 2, [
            (result.stdout, result.stderr) for result in results
        ]

        denied = _psql(
            DATABASE,
            f"SET ROLE anon; SELECT public.persist_semantic_video_submission_intent('{RUN_ID}', '{TAKE_1}', 'worker-1', '{token}', 'request-0');",
            check=False,
        )
        assert denied.returncode != 0
        assert "permission denied" in denied.stderr.lower()

        _psql(
            DATABASE,
            "SET ROLE service_role; "
            f"SELECT public.persist_semantic_video_submission_intent('{RUN_ID}', '{TAKE_1}', 'worker-1', '{token}', 'request-0'); "
            f"SELECT public.persist_semantic_video_accepted_operation('{RUN_ID}', '{TAKE_1}', 'worker-1', '{token}', 'operation-1', 'veo-3.1-generate-001'); "
            f"SELECT public.persist_semantic_video_submission_intent('{RUN_ID}', '{TAKE_2}', 'worker-1', '{token}', 'request-1'); "
            f"SELECT public.persist_semantic_video_accepted_operation('{RUN_ID}', '{TAKE_2}', 'worker-1', '{token}', 'operation-2', 'veo-3.1-generate-001');",
        )

        fenced_release = _psql(
            DATABASE,
            "SET ROLE service_role; "
            f"SELECT public.release_semantic_video_lease('{RUN_ID}', 'worker-1', '00000000-0000-0000-0000-000000000999');",
            check=False,
        )
        assert fenced_release.returncode != 0
        assert "lease" in fenced_release.stderr.lower()

        _psql(
            DATABASE,
            "SET ROLE service_role; "
            f"SELECT public.persist_semantic_video_completed_take('{RUN_ID}', '{TAKE_1}', 'worker-1', '{token}', 'gs://bucket/raw-1.mp4', 'https://cdn/raw-1.mp4', '{'a' * 64}'); "
            f"SELECT public.persist_semantic_video_completed_take('{RUN_ID}', '{TAKE_2}', 'worker-1', '{token}', 'gs://bucket/raw-2.mp4', 'https://cdn/raw-2.mp4', '{'d' * 64}'); "
            f"SELECT public.advance_semantic_video_stage('{RUN_ID}', 'worker-1', '{token}', 'generating', 'transcript_qa', '{{}}'::JSONB); "
            f"SELECT public.advance_semantic_video_stage('{RUN_ID}', 'worker-1', '{token}', 'transcript_qa', 'identity_qa', '{{}}'::JSONB); "
            f"SELECT public.advance_semantic_video_stage('{RUN_ID}', 'worker-1', '{token}', 'identity_qa', 'voice_qa', '{{}}'::JSONB); "
            f"SELECT public.advance_semantic_video_stage('{RUN_ID}', 'worker-1', '{token}', 'voice_qa', 'acoustic_qa', '{{}}'::JSONB); "
            f"SELECT public.advance_semantic_video_stage('{RUN_ID}', 'worker-1', '{token}', 'acoustic_qa', 'composing', '{{}}'::JSONB); "
            f"SELECT public.advance_semantic_video_stage('{RUN_ID}', 'worker-1', '{token}', 'composing', 'uploading', '{{}}'::JSONB); "
            f"SELECT public.complete_semantic_video_run('{RUN_ID}', 'worker-1', '{token}', 'https://cdn/final.mp4', '{'b' * 64}', 'https://cdn/final-captioned.mp4', '{'c' * 64}', $json${{\"delivery\":{{\"passed\":true}}}}$json$::JSONB);",
        )

        _psql(
            DATABASE,
            f"""
            DO $$
            BEGIN
              IF (SELECT stage FROM public.semantic_video_runs WHERE id = '{RUN_ID}') IS DISTINCT FROM 'completed' THEN
                RAISE EXCEPTION 'run was not completed';
              END IF;
              IF (SELECT video_status FROM public.posts WHERE id = '{POST_ID}') IS DISTINCT FROM 'caption_completed' THEN
                RAISE EXCEPTION 'post did not bypass caption queue';
              END IF;
              IF (SELECT video_url FROM public.posts WHERE id = '{POST_ID}') IS DISTINCT FROM 'https://cdn/final-captioned.mp4' THEN
                RAISE EXCEPTION 'post does not point at captioned output';
              END IF;
              IF (SELECT video_metadata ->> 'caption_video_url' FROM public.posts WHERE id = '{POST_ID}') IS DISTINCT FROM 'https://cdn/final-captioned.mp4' THEN
                RAISE EXCEPTION 'captioned URL metadata missing';
              END IF;
              IF EXISTS (
                SELECT 1 FROM public.semantic_video_takes
                WHERE run_id = '{RUN_ID}' AND approval_id IS DISTINCT FROM '{APPROVAL_ID}'::UUID
              ) THEN
                RAISE EXCEPTION 'approved takes were not linked';
              END IF;
              IF has_table_privilege('service_role', 'public.semantic_video_takes', 'UPDATE')
                 OR has_table_privilege('service_role', 'public.semantic_video_runs', 'UPDATE') THEN
                RAISE EXCEPTION 'service role can bypass worker RPCs';
              END IF;
            END;
            $$;
            """,
        )
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};", check=False)
