from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260713_semantic_ugc_production.sql"
CONTAINER = os.getenv("SEMANTIC_UGC_POSTGRES_CONTAINER")
DATABASE = "semantic_ugc_plan_rpc_test"
RUN_ID = "00000000-0000-0000-0000-000000000021"
POST_ID = "00000000-0000-0000-0000-000000000011"
BATCH_ID = "00000000-0000-0000-0000-000000000001"


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


def _jsonb(value) -> str:
    return f"$json${json.dumps(value, separators=(',', ':'))}$json$::jsonb"


def _run_update(*, plan_hash: str, request_hashes: list[str]) -> dict:
    return {
        "post_id": POST_ID,
        "batch_id": BATCH_ID,
        "requested_duration_seconds": 50,
        "duration_contract": {"requested_duration_seconds": 50},
        "duration_contract_hash": "duration-hash",
        "script_snapshot": {"text": "approved"},
        "script_hash": "script-hash",
        "actor_identity_id": None,
        "actor_snapshot": {},
        "reference_snapshot": {
            "actor_identity_id": None,
            "actor_references": [],
            "location_reference": {"storage_uri": "semantic/location.png"},
        },
        "reference_hash": "reference-hash",
        "master_snapshot": {
            "storage_uri": "semantic/master.png",
            "sha256": "master-hash",
            "byte_length": 10,
        },
        "master_hash": "master-hash",
        "stage": "awaiting_paid_approval",
        "plan_snapshot": {
            "take_count": 2,
            "billable_provider_seconds": 16,
            "quota_units": 2,
            "price_per_provider_second_usd": "0.40",
            "estimated_cost_usd": "6.40",
            "takes": [
                {
                    "take_index": index,
                    "request_hash": request_hash,
                    "provider_duration_seconds": 8,
                }
                for index, request_hash in enumerate(request_hashes)
            ],
        },
        "plan_hash": plan_hash,
        "provider_model": "veo-3.1-generate-001",
        "resolution": "1080p",
        "estimated_cost_usd": "6.40",
        "artifact_prefix": f"semantic-videos/{BATCH_ID}/{POST_ID}",
    }


def _takes(*, request_hashes: list[str], bad_word_count: bool = False) -> list[dict]:
    return [
        {
            "take_index": index,
            "attempt": 1,
            "beat_text": f"Beat {index}",
            "word_count": -1 if bad_word_count and index == 1 else 2,
            "estimated_speech_seconds": 1.25,
            "provider_duration_seconds": 8,
            "shot_transform": {"index": index},
            "shot_hash": f"shot-{index}",
            "prompt_hash": f"prompt-{index}",
            "negative_prompt_hash": f"negative-{index}",
            "provider_model": "veo-3.1-generate-001",
            "seed": 100 + index,
            "request_contract": {"take_index": index},
            "request_hash": request_hash,
            "submission_state": "planned",
            "retry_guidance": {},
        }
        for index, request_hash in enumerate(request_hashes)
    ]


@pytest.mark.skipif(
    not CONTAINER,
    reason="Set SEMANTIC_UGC_POSTGRES_CONTAINER to run PostgreSQL migration integration.",
)
def test_atomic_plan_rpc_reapplies_guards_and_rolls_back_bad_take():
    _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};")
    _psql("postgres", f"CREATE DATABASE {DATABASE};")
    try:
        _psql(
            DATABASE,
            """
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
                CREATE ROLE service_role NOLOGIN;
              END IF;
            END;
            $$;

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
              batch_id UUID NOT NULL REFERENCES public.batches(id) ON DELETE CASCADE
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

        migration = MIGRATION.read_text()
        _psql(DATABASE, migration)
        _psql(DATABASE, migration)

        _psql(
            DATABASE,
            f"""
            INSERT INTO public.batches (
              id, brand, creation_mode, target_length_tier, target_duration_seconds,
              video_pipeline_route
            ) VALUES ('{BATCH_ID}', 'A', 'semantic_ugc', NULL, 50, 'semantic_ugc');
            INSERT INTO public.posts (id, batch_id) VALUES ('{POST_ID}', '{BATCH_ID}');
            INSERT INTO public.semantic_video_runs (
              id, post_id, batch_id, requested_duration_seconds, duration_contract,
              duration_contract_hash, script_snapshot, script_hash, reference_snapshot,
              reference_hash, master_snapshot, master_hash, stage
            ) VALUES (
              '{RUN_ID}', '{POST_ID}', '{BATCH_ID}', 50,
              '{{"requested_duration_seconds":50}}'::JSONB, 'duration-hash',
              '{{"text":"approved"}}'::JSONB, 'script-hash',
              '{{"actor_identity_id":null,"actor_references":[],"location_reference":{{"storage_uri":"semantic/location.png"}}}}'::JSONB,
              'reference-hash',
              '{{"storage_uri":"semantic/master.png","sha256":"master-hash","byte_length":10}}'::JSONB,
              'master-hash', 'awaiting_paid_approval'
            );
            INSERT INTO public.semantic_video_takes (
              run_id, take_index, attempt, beat_text, word_count, estimated_speech_seconds,
              provider_duration_seconds, shot_transform, shot_hash, prompt_hash,
              provider_model, request_contract, request_hash
            ) VALUES (
              '{RUN_ID}', 0, 1, 'Old take', 2, 1.0, 8, '{{}}'::JSONB,
              'old-shot', 'old-prompt', 'veo-3.1-generate-001', '{{}}'::JSONB, 'old-request'
            );
            INSERT INTO public.semantic_video_approvals (
              run_id, approval_type, run_revision, contract_hash, approved_by
            ) VALUES ('{RUN_ID}', 'reference', 0, 'master-hash', 'operator@example.com');
            """,
        )

        _psql(
            DATABASE,
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM pg_proc AS proc
                CROSS JOIN LATERAL aclexplode(proc.proacl) AS acl
                WHERE proc.oid = 'public.persist_semantic_video_plan(uuid,integer,jsonb,jsonb)'::regprocedure
                  AND acl.grantee = 0
                  AND acl.privilege_type = 'EXECUTE'
              ) THEN
                RAISE EXCEPTION 'PUBLIC still has execute privilege';
              END IF;
              IF NOT has_function_privilege(
                'service_role',
                'public.persist_semantic_video_plan(uuid,integer,jsonb,jsonb)',
                'EXECUTE'
              ) THEN
                RAISE EXCEPTION 'service_role lacks execute privilege';
              END IF;
              IF NOT (
                SELECT prosecdef
                FROM pg_proc
                WHERE oid = 'public.persist_semantic_video_plan(uuid,integer,jsonb,jsonb)'::regprocedure
              ) THEN
                RAISE EXCEPTION 'plan RPC is not SECURITY DEFINER';
              END IF;
            END;
            $$;
            """,
        )

        happy_hashes = ["happy-request-0", "happy-request-1"]
        happy_update = _run_update(plan_hash="happy-plan", request_hashes=happy_hashes)
        happy_takes = _takes(request_hashes=happy_hashes)
        _psql(
            DATABASE,
            f"""
            DO $$
            DECLARE
              result JSONB;
            BEGIN
              result := public.persist_semantic_video_plan(
                '{RUN_ID}', 0, {_jsonb(happy_update)}, {_jsonb(happy_takes)}
              );
              IF result #>> '{{run,revision}}' <> '1'
                 OR result #>> '{{run,plan_hash}}' <> 'happy-plan'
                 OR jsonb_array_length(result -> 'takes') <> 2 THEN
                RAISE EXCEPTION 'unexpected happy-path result: %', result;
              END IF;
              IF (SELECT count(*) FROM public.semantic_video_approvals WHERE run_id = '{RUN_ID}') <> 1 THEN
                RAISE EXCEPTION 'approval history was mutated';
              END IF;
            END;
            $$;
            """,
        )

        stale = _psql(
            DATABASE,
            f"SELECT public.persist_semantic_video_plan('{RUN_ID}', 0, {_jsonb(happy_update)}, {_jsonb(happy_takes)});",
            check=False,
        )
        assert stale.returncode != 0
        assert "revision" in stale.stderr.lower()

        _psql(DATABASE, f"UPDATE public.semantic_video_runs SET stage = 'generating' WHERE id = '{RUN_ID}';")
        wrong_stage = _psql(
            DATABASE,
            f"SELECT public.persist_semantic_video_plan('{RUN_ID}', 1, {_jsonb(happy_update)}, {_jsonb(happy_takes)});",
            check=False,
        )
        assert wrong_stage.returncode != 0
        assert "stage" in wrong_stage.stderr.lower()
        _psql(
            DATABASE,
            f"UPDATE public.semantic_video_runs SET stage = 'awaiting_paid_approval' WHERE id = '{RUN_ID}';",
        )

        bad_hashes = ["bad-request-0", "bad-request-1"]
        bad_update = _run_update(plan_hash="bad-plan", request_hashes=bad_hashes)
        bad_takes = _takes(request_hashes=bad_hashes, bad_word_count=True)
        bad_take = _psql(
            DATABASE,
            f"SELECT public.persist_semantic_video_plan('{RUN_ID}', 1, {_jsonb(bad_update)}, {_jsonb(bad_takes)});",
            check=False,
        )
        assert bad_take.returncode != 0
        assert "word_count" in bad_take.stderr.lower()

        _psql(
            DATABASE,
            f"""
            DO $$
            BEGIN
              IF (SELECT revision FROM public.semantic_video_runs WHERE id = '{RUN_ID}') <> 1
                 OR (SELECT plan_hash FROM public.semantic_video_runs WHERE id = '{RUN_ID}') <> 'happy-plan' THEN
                RAISE EXCEPTION 'run update escaped failed take transaction';
              END IF;
              IF (
                SELECT array_agg(request_hash ORDER BY take_index)
                FROM public.semantic_video_takes
                WHERE run_id = '{RUN_ID}' AND attempt = 1
              ) <> ARRAY['happy-request-0', 'happy-request-1'] THEN
                RAISE EXCEPTION 'take replacement escaped failed transaction';
              END IF;
            END;
            $$;
            """,
        )
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};", check=False)
