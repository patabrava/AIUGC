from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE_MIGRATION = ROOT / "supabase/migrations/20260713000000_semantic_ugc_production.sql"
API_MIGRATION = ROOT / "supabase/migrations/20260713000100_semantic_video_api_transactions.sql"
BASE_MIGRATION_SHA256 = "7e938ecf55215f9818c78a7745a85194d1656296657dc4248e9efbd81d6c1baa"
CONTAINER = os.getenv("SEMANTIC_UGC_POSTGRES_CONTAINER")
DATABASE = "semantic_ugc_plan_rpc_test"
RUN_ID = "00000000-0000-0000-0000-000000000021"
POST_ID = "00000000-0000-0000-0000-000000000011"
STATE_RUN_ID = "00000000-0000-0000-0000-000000000022"
STATE_POST_ID = "00000000-0000-0000-0000-000000000012"
COST_RUN_ID = "00000000-0000-0000-0000-000000000023"
COST_POST_ID = "00000000-0000-0000-0000-000000000013"
CANDIDATE_POST_ID = "00000000-0000-0000-0000-000000000014"
BATCH_ID = "00000000-0000-0000-0000-000000000001"
MASTER_RUN_ID = "00000000-0000-0000-0000-000000000031"
MASTER_POST_ID = "00000000-0000-0000-0000-000000000041"
INITIAL_RUN_ID = "00000000-0000-0000-0000-000000000032"
INITIAL_POST_ID = "00000000-0000-0000-0000-000000000042"
RETRY_RUN_ID = "00000000-0000-0000-0000-000000000033"
RETRY_POST_ID = "00000000-0000-0000-0000-000000000043"
CANCEL_RUN_ID = "00000000-0000-0000-0000-000000000034"
CANCEL_POST_ID = "00000000-0000-0000-0000-000000000044"
UNSAFE_CANCEL_RUN_ID = "00000000-0000-0000-0000-000000000035"
UNSAFE_CANCEL_POST_ID = "00000000-0000-0000-0000-000000000045"
RACE_CANCEL_RUN_ID = "00000000-0000-0000-0000-000000000036"
RACE_CANCEL_POST_ID = "00000000-0000-0000-0000-000000000046"


def test_semantic_video_api_changes_use_strictly_later_forward_migration():
    base_bytes = BASE_MIGRATION.read_bytes()

    assert sha256(base_bytes).hexdigest() == BASE_MIGRATION_SHA256
    assert b"persist_semantic_video_plan" not in base_bytes
    assert API_MIGRATION.exists()
    assert int(API_MIGRATION.name.split("_", 1)[0]) > int(
        BASE_MIGRATION.name.split("_", 1)[0]
    )
    forward_sql = API_MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION public.persist_semantic_video_plan" in forward_sql
    assert "CREATE OR REPLACE FUNCTION public.claim_semantic_video_run" in forward_sql
    assert "CREATE OR REPLACE FUNCTION public.reserve_semantic_video_candidates" in forward_sql
    assert "CREATE OR REPLACE FUNCTION public.finalize_semantic_video_candidates" in forward_sql
    assert "CREATE OR REPLACE FUNCTION public.approve_semantic_video_master" in forward_sql
    assert "CREATE OR REPLACE FUNCTION public.approve_semantic_video_initial_plan" in forward_sql
    assert "CREATE OR REPLACE FUNCTION public.approve_semantic_video_retry" in forward_sql
    assert "CREATE OR REPLACE FUNCTION public.cancel_semantic_video_run" in forward_sql
    assert "SET search_path = ''" in forward_sql
    assert "FROM PUBLIC, anon, authenticated" in forward_sql


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


def _with_canonical_request_hash(contract: dict) -> tuple[dict, str]:
    canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**contract, "canonical_request_json": canonical}, sha256(canonical.encode()).hexdigest()


def _retry_contract_hash(
    *,
    plan_hash: str,
    revision: int,
    indexes: list[int],
    request_hashes: list[str],
    provider_seconds: int,
    quota_units: int,
    estimated_cost: str,
) -> str:
    basis = "\n".join(
        (
            "semantic-retry-contract-v1",
            plan_hash,
            str(revision),
            ",".join(str(index) for index in indexes),
            ",".join(request_hashes),
            str(provider_seconds),
            str(quota_units),
            estimated_cost,
        )
    )
    return sha256(basis.encode()).hexdigest()


def _as_service_role(sql: str) -> str:
    return f"SET ROLE service_role;\n{sql}"


def _as_service_role_rollback(sql: str) -> str:
    return f"BEGIN;\nSET LOCAL ROLE service_role;\n{sql}\nROLLBACK;"


def _run_update(
    *,
    plan_hash: str,
    request_hashes: list[str],
    post_id: str = POST_ID,
    price: str = "0.40",
    estimated_cost: str = "6.40",
) -> dict:
    return {
        "post_id": post_id,
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
            "price_per_provider_second_usd": price,
            "estimated_cost_usd": estimated_cost,
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
        "estimated_cost_usd": estimated_cost,
        "artifact_prefix": f"semantic-videos/{BATCH_ID}/{post_id}",
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


def _candidate_run_payload(*, master_label: str | None = None) -> dict:
    system_prompt = "Exact Raw Camera system prompt."
    return {
        "post_id": CANDIDATE_POST_ID,
        "batch_id": BATCH_ID,
        "requested_duration_seconds": 50,
        "duration_contract": {"requested_duration_seconds": 50},
        "duration_contract_hash": "candidate-duration-hash",
        "script_snapshot": {"text": "approved candidate script"},
        "script_hash": "candidate-script-hash",
        "actor_identity_id": None,
        "actor_snapshot": {"name": "Actor"},
        "reference_snapshot": {
            "actor_identity_id": None,
            "actor_references": [
                {"role": "actor_front", "storage_uri": "semantic/front.png"},
                {
                    "role": "actor_three_quarter",
                    "storage_uri": "semantic/three-quarter.png",
                },
            ],
            "location_reference": {
                "role": "location",
                "storage_uri": "semantic/location.png",
            },
        },
        "reference_hash": "candidate-reference-hash",
        "master_snapshot": (
            {
                "candidates": [
                    {"index": index, "label": master_label}
                    for index in range(1, 4)
                ],
                "prompt_writer_system_prompt": system_prompt,
                "prompt_writer_system_prompt_sha256": sha256(
                    system_prompt.encode("utf-8")
                ).hexdigest(),
                "prompt_writer_output": "Exact prompt writer output.",
                "composition_prompt": "Exact composition prompt.",
            }
            if master_label
            else {}
        ),
        "master_hash": None,
        "stage": "awaiting_reference_approval",
        "plan_snapshot": None,
        "plan_hash": None,
        "provider_model": None,
        "resolution": None,
        "estimated_cost_usd": None,
        "artifact_prefix": f"semantic-videos/{BATCH_ID}/{CANDIDATE_POST_ID}",
        "failure_envelope": None,
    }


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
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
            END;
            $$;
            ALTER ROLE service_role BYPASSRLS;
            ALTER ROLE anon NOBYPASSRLS;
            ALTER ROLE authenticated NOBYPASSRLS;

            CREATE SCHEMA IF NOT EXISTS supabase_migrations;
            CREATE TABLE supabase_migrations.schema_migrations (
              version TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              statements TEXT[] NOT NULL DEFAULT '{}'::TEXT[]
            );

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

        _psql(DATABASE, BASE_MIGRATION.read_text())
        _psql(
            DATABASE,
            """
            INSERT INTO supabase_migrations.schema_migrations (version, name)
            VALUES ('20260713000000', 'semantic_ugc_production');
            """,
        )
        _psql(DATABASE, API_MIGRATION.read_text())
        _psql(
            DATABASE,
            """
            INSERT INTO supabase_migrations.schema_migrations (version, name)
            VALUES ('20260713000100', 'semantic_video_api_transactions');
            """,
        )
        _psql(DATABASE, API_MIGRATION.read_text())

        _psql(
            DATABASE,
            f"""
            INSERT INTO public.batches (
              id, brand, creation_mode, target_length_tier, target_duration_seconds,
              video_pipeline_route
            ) VALUES ('{BATCH_ID}', 'A', 'semantic_ugc', NULL, 50, 'semantic_ugc');
            INSERT INTO public.posts (id, batch_id) VALUES
              ('{POST_ID}', '{BATCH_ID}'),
              ('{STATE_POST_ID}', '{BATCH_ID}'),
              ('{COST_POST_ID}', '{BATCH_ID}'),
              ('{CANDIDATE_POST_ID}', '{BATCH_ID}');
            INSERT INTO public.semantic_video_runs (
              id, post_id, batch_id, requested_duration_seconds, duration_contract,
              duration_contract_hash, script_snapshot, script_hash, reference_snapshot,
              reference_hash, master_snapshot, master_hash, stage
            ) VALUES
              (
                '{RUN_ID}', '{POST_ID}', '{BATCH_ID}', 50,
                '{{"requested_duration_seconds":50}}'::JSONB, 'duration-hash',
                '{{"text":"approved"}}'::JSONB, 'script-hash',
                '{{"actor_identity_id":null,"actor_references":[],"location_reference":{{"storage_uri":"semantic/location.png"}}}}'::JSONB,
                'reference-hash',
                '{{"storage_uri":"semantic/master.png","sha256":"master-hash","byte_length":10}}'::JSONB,
                'master-hash', 'awaiting_paid_approval'
              ),
              (
                '{STATE_RUN_ID}', '{STATE_POST_ID}', '{BATCH_ID}', 50,
                '{{"requested_duration_seconds":50}}'::JSONB, 'duration-hash',
                '{{"text":"approved"}}'::JSONB, 'script-hash',
                '{{"actor_identity_id":null,"actor_references":[],"location_reference":{{"storage_uri":"semantic/location.png"}}}}'::JSONB,
                'reference-hash',
                '{{"storage_uri":"semantic/master.png","sha256":"master-hash","byte_length":10}}'::JSONB,
                'master-hash', 'awaiting_paid_approval'
              ),
              (
                '{COST_RUN_ID}', '{COST_POST_ID}', '{BATCH_ID}', 50,
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
              IF has_function_privilege(
                'anon',
                'public.persist_semantic_video_plan(uuid,integer,jsonb,jsonb)',
                'EXECUTE'
              ) OR has_function_privilege(
                'authenticated',
                'public.persist_semantic_video_plan(uuid,integer,jsonb,jsonb)',
                'EXECUTE'
              ) THEN
                RAISE EXCEPTION 'untrusted API roles retain execute privilege';
              END IF;
              IF NOT (
                SELECT prosecdef
                FROM pg_proc
                WHERE oid = 'public.persist_semantic_video_plan(uuid,integer,jsonb,jsonb)'::regprocedure
              ) THEN
                RAISE EXCEPTION 'plan RPC is not SECURITY DEFINER';
              END IF;
              IF NOT EXISTS (
                SELECT 1
                FROM pg_proc
                WHERE oid = 'public.persist_semantic_video_plan(uuid,integer,jsonb,jsonb)'::regprocedure
                  AND array_to_string(proconfig, ',') = 'search_path=""'
              ) THEN
                RAISE EXCEPTION 'plan RPC does not use an empty search_path';
              END IF;
              IF (
                SELECT array_agg(version ORDER BY version)
                FROM supabase_migrations.schema_migrations
              ) <> ARRAY['20260713000000', '20260713000100'] THEN
                RAISE EXCEPTION 'migration ledger ordering is incorrect';
              END IF;
            END;
            $$;
            """,
        )

        denied_anon = _psql(
            DATABASE,
            f"SET ROLE anon; SELECT public.persist_semantic_video_plan('{RUN_ID}', 0, '{{}}'::JSONB, '[]'::JSONB);",
            check=False,
        )
        denied_authenticated = _psql(
            DATABASE,
            f"SET ROLE authenticated; SELECT public.persist_semantic_video_plan('{RUN_ID}', 0, '{{}}'::JSONB, '[]'::JSONB);",
            check=False,
        )
        assert denied_anon.returncode != 0
        assert denied_authenticated.returncode != 0
        assert "permission denied" in denied_anon.stderr.lower()
        assert "permission denied" in denied_authenticated.stderr.lower()

        _psql(
            DATABASE,
            """
            DO $$
            DECLARE
              function_signature TEXT;
            BEGIN
              FOREACH function_signature IN ARRAY ARRAY[
                'public.reserve_semantic_video_candidates(uuid,integer,jsonb,text,uuid,integer)',
                'public.finalize_semantic_video_candidates(uuid,integer,uuid,jsonb)'
              ] LOOP
                IF has_function_privilege('anon', function_signature, 'EXECUTE')
                   OR has_function_privilege('authenticated', function_signature, 'EXECUTE') THEN
                  RAISE EXCEPTION 'untrusted API role can execute %', function_signature;
                END IF;
                IF NOT has_function_privilege('service_role', function_signature, 'EXECUTE') THEN
                  RAISE EXCEPTION 'service_role cannot execute %', function_signature;
                END IF;
                IF NOT (
                  SELECT proc.prosecdef
                    AND array_to_string(proc.proconfig, ',') = 'search_path=""'
                  FROM pg_proc AS proc
                  WHERE proc.oid = function_signature::regprocedure
                ) THEN
                  RAISE EXCEPTION '% is not hardened', function_signature;
                END IF;
              END LOOP;
            END;
            $$;
            """,
        )

        initial_tokens = [
            "00000000-0000-0000-0000-000000000101",
            "00000000-0000-0000-0000-000000000102",
        ]
        initial_sql = [
            _as_service_role(
                "SELECT id FROM public.reserve_semantic_video_candidates("
                f"'{CANDIDATE_POST_ID}', NULL, {_jsonb(_candidate_run_payload())}, "
                f"'operator-{index}', '{token}', 300);"
            )
            for index, token in enumerate(initial_tokens, start=1)
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            initial_results = list(
                pool.map(lambda sql: _psql(DATABASE, sql, check=False), initial_sql)
            )
        assert sum(result.returncode == 0 for result in initial_results) == 1, [
            (result.stdout, result.stderr) for result in initial_results
        ]
        initial_winner_index = next(
            index for index, result in enumerate(initial_results) if result.returncode == 0
        )
        initial_winner = initial_tokens[initial_winner_index]
        initial_loser = initial_tokens[1 - initial_winner_index]
        assert "semantic_video_conflict:" in initial_results[1 - initial_winner_index].stderr

        losing_finalize = _psql(
            DATABASE,
            _as_service_role(
                "SELECT id FROM public.finalize_semantic_video_candidates("
                f"(SELECT id FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}'), "
                f"0, '{initial_loser}', {_jsonb(_candidate_run_payload(master_label='loser'))});"
            ),
            check=False,
        )
        assert losing_finalize.returncode != 0
        assert "semantic_video_conflict:" in losing_finalize.stderr

        invalid_count_payload = _candidate_run_payload(master_label="invalid-count")
        invalid_count_payload["master_snapshot"]["candidates"] = invalid_count_payload[
            "master_snapshot"
        ]["candidates"][:2]
        invalid_count_finalize = _psql(
            DATABASE,
            _as_service_role(
                "SELECT id FROM public.finalize_semantic_video_candidates("
                f"(SELECT id FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}'), "
                f"0, '{initial_winner}', {_jsonb(invalid_count_payload)});"
            ),
            check=False,
        )
        assert invalid_count_finalize.returncode != 0
        assert "candidate run update is invalid" in invalid_count_finalize.stderr

        _psql(
            DATABASE,
            _as_service_role(
                "SELECT id FROM public.finalize_semantic_video_candidates("
                f"(SELECT id FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}'), "
                f"0, '{initial_winner}', {_jsonb(_candidate_run_payload(master_label='initial'))});"
            ),
        )
        _psql(
            DATABASE,
            f"""
            DO $$
            BEGIN
              IF (SELECT count(*) FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}') <> 1
                 OR (SELECT revision FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}') <> 0
                 OR (SELECT candidate_reservation_token FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}')
                    IS DISTINCT FROM '{initial_winner}'::UUID
                 OR (SELECT master_snapshot #>> '{{candidates,0,label}}' FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}') <> 'initial' THEN
                RAISE EXCEPTION 'initial candidate reservation/finalization contract failed';
              END IF;
            END;
            $$;
            """,
        )

        refresh_token = "00000000-0000-0000-0000-000000000103"
        blocked_refresh = _psql(
            DATABASE,
            _as_service_role(
                "SELECT id FROM public.reserve_semantic_video_candidates("
                f"'{CANDIDATE_POST_ID}', 0, {_jsonb(_candidate_run_payload())}, "
                f"'refresh', '{refresh_token}', 300);"
            ),
            check=False,
        )
        assert blocked_refresh.returncode != 0
        assert "manual reconciliation" in blocked_refresh.stderr

        recovery_token = "00000000-0000-0000-0000-000000000105"
        _psql(
            DATABASE,
            f"UPDATE public.semantic_video_runs SET candidate_reservation_expires_at = now() - interval '1 second' WHERE post_id = '{CANDIDATE_POST_ID}';",
        )
        blocked_recovery = _psql(
            DATABASE,
            _as_service_role(
                "SELECT id FROM public.reserve_semantic_video_candidates("
                f"'{CANDIDATE_POST_ID}', 0, {_jsonb(_candidate_run_payload())}, "
                f"'recovery', '{recovery_token}', 300);"
            ),
            check=False,
        )
        assert blocked_recovery.returncode != 0
        assert "manual reconciliation" in blocked_recovery.stderr
        _psql(
            DATABASE,
            f"""
            DO $$
            BEGIN
              IF (SELECT revision FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}') <> 0
                 OR (SELECT candidate_reservation_token FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}')
                    IS DISTINCT FROM '{initial_winner}'::UUID
                 OR (SELECT master_snapshot #>> '{{candidates,0,label}}' FROM public.semantic_video_runs WHERE post_id = '{CANDIDATE_POST_ID}') <> 'initial' THEN
                RAISE EXCEPTION 'expired paid candidate attempt was reclaimed or mutated';
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
            _as_service_role(f"""
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
            """),
        )

        stale = _psql(
            DATABASE,
            "\\set VERBOSITY verbose\n"
            + _as_service_role(f"SELECT public.persist_semantic_video_plan('{RUN_ID}', 0, {_jsonb(happy_update)}, {_jsonb(happy_takes)});"),
            check=False,
        )
        assert stale.returncode != 0
        assert "40001" in stale.stderr
        assert "revision" in stale.stderr.lower()

        _psql(DATABASE, f"UPDATE public.semantic_video_runs SET stage = 'generating' WHERE id = '{RUN_ID}';")
        wrong_stage = _psql(
            DATABASE,
            "\\set VERBOSITY verbose\n"
            + _as_service_role(f"SELECT public.persist_semantic_video_plan('{RUN_ID}', 1, {_jsonb(happy_update)}, {_jsonb(happy_takes)});"),
            check=False,
        )
        assert wrong_stage.returncode != 0
        assert "40001" in wrong_stage.stderr
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
            _as_service_role(f"SELECT public.persist_semantic_video_plan('{RUN_ID}', 1, {_jsonb(bad_update)}, {_jsonb(bad_takes)});"),
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

        state_baseline_hashes = ["state-baseline-0", "state-baseline-1"]
        cost_baseline_hashes = ["cost-baseline-0", "cost-baseline-1"]
        state_baseline_update = _run_update(
            plan_hash="state-baseline-plan",
            request_hashes=state_baseline_hashes,
            post_id=STATE_POST_ID,
        )
        cost_baseline_update = _run_update(
            plan_hash="cost-baseline-plan",
            request_hashes=cost_baseline_hashes,
            post_id=COST_POST_ID,
        )
        _psql(
            DATABASE,
            _as_service_role(f"SELECT public.persist_semantic_video_plan('{STATE_RUN_ID}', 0, {_jsonb(state_baseline_update)}, {_jsonb(_takes(request_hashes=state_baseline_hashes))});"),
        )
        _psql(
            DATABASE,
            _as_service_role(f"SELECT public.persist_semantic_video_plan('{COST_RUN_ID}', 0, {_jsonb(cost_baseline_update)}, {_jsonb(_takes(request_hashes=cost_baseline_hashes))});"),
        )

        completed_hashes = ["completed-request-0", "completed-request-1"]
        completed_update = _run_update(
            plan_hash="completed-plan",
            request_hashes=completed_hashes,
            post_id=STATE_POST_ID,
        )
        completed_takes = _takes(request_hashes=completed_hashes)
        completed_takes[0]["submission_state"] = "completed"
        completed_state = _psql(
            DATABASE,
            _as_service_role_rollback(f"SELECT public.persist_semantic_video_plan('{STATE_RUN_ID}', 1, {_jsonb(completed_update)}, {_jsonb(completed_takes)});"),
            check=False,
        )

        underpriced_hashes = ["underpriced-request-0", "underpriced-request-1"]
        underpriced_update = _run_update(
            plan_hash="underpriced-plan",
            request_hashes=underpriced_hashes,
            post_id=COST_POST_ID,
            price="0.40",
            estimated_cost="0.01",
        )
        underpriced_cost = _psql(
            DATABASE,
            _as_service_role_rollback(f"SELECT public.persist_semantic_video_plan('{COST_RUN_ID}', 1, {_jsonb(underpriced_update)}, {_jsonb(_takes(request_hashes=underpriced_hashes))});"),
            check=False,
        )

        missing_counter_hashes = ["missing-counter-0", "missing-counter-1"]
        missing_counter_update = _run_update(
            plan_hash="missing-counter-plan",
            request_hashes=missing_counter_hashes,
            post_id=COST_POST_ID,
        )
        del missing_counter_update["plan_snapshot"]["take_count"]
        missing_counter = _psql(
            DATABASE,
            _as_service_role_rollback(
                f"SELECT public.persist_semantic_video_plan('{COST_RUN_ID}', 1, {_jsonb(missing_counter_update)}, {_jsonb(_takes(request_hashes=missing_counter_hashes))});"
            ),
            check=False,
        )
        null_counter_hashes = ["null-counter-0", "null-counter-1"]
        null_counter_update = _run_update(
            plan_hash="null-counter-plan",
            request_hashes=null_counter_hashes,
            post_id=COST_POST_ID,
        )
        null_counter_update["plan_snapshot"]["quota_units"] = None
        null_counter = _psql(
            DATABASE,
            _as_service_role_rollback(
                f"SELECT public.persist_semantic_video_plan('{COST_RUN_ID}', 1, {_jsonb(null_counter_update)}, {_jsonb(_takes(request_hashes=null_counter_hashes))});"
            ),
            check=False,
        )
        zero_price_update = _run_update(
            plan_hash="zero-price-plan",
            request_hashes=["zero-price-0", "zero-price-1"],
            post_id=COST_POST_ID,
            price="0.00",
            estimated_cost="0.00",
        )
        zero_price = _psql(
            DATABASE,
            _as_service_role_rollback(
                f"SELECT public.persist_semantic_video_plan('{COST_RUN_ID}', 1, {_jsonb(zero_price_update)}, {_jsonb(_takes(request_hashes=['zero-price-0', 'zero-price-1']))});"
            ),
            check=False,
        )
        nonfinite_cost_results = []
        for label, special_value in (("nan", "NaN"), ("infinity", "Infinity")):
            request_hashes = [f"{label}-price-0", f"{label}-price-1"]
            nonfinite_update = _run_update(
                plan_hash=f"{label}-price-plan",
                request_hashes=request_hashes,
                post_id=COST_POST_ID,
                price=special_value,
                estimated_cost=special_value,
            )
            nonfinite_cost_results.append(
                _psql(
                    DATABASE,
                    _as_service_role_rollback(
                        f"SELECT public.persist_semantic_video_plan('{COST_RUN_ID}', 1, "
                        f"{_jsonb(nonfinite_update)}, {_jsonb(_takes(request_hashes=request_hashes))});"
                    ),
                    check=False,
                )
            )

        malformed_billing_results = []
        billing_mutations = {
            "missing-billable-seconds": lambda update: update["plan_snapshot"].pop(
                "billable_provider_seconds"
            ),
            "missing-price": lambda update: update["plan_snapshot"].pop(
                "price_per_provider_second_usd"
            ),
            "missing-nested-cost": lambda update: update["plan_snapshot"].pop(
                "estimated_cost_usd"
            ),
            "missing-top-level-cost": lambda update: update.pop("estimated_cost_usd"),
            "null-top-level-cost": lambda update: update.__setitem__(
                "estimated_cost_usd", None
            ),
            "wrong-billable-type": lambda update: update["plan_snapshot"].__setitem__(
                "billable_provider_seconds", {"seconds": 16}
            ),
            "wrong-price-type": lambda update: update["plan_snapshot"].__setitem__(
                "price_per_provider_second_usd", ["0.40"]
            ),
        }
        for label, mutate in billing_mutations.items():
            request_hashes = [f"{label}-0", f"{label}-1"]
            malformed_update = _run_update(
                plan_hash=f"{label}-plan",
                request_hashes=request_hashes,
                post_id=COST_POST_ID,
            )
            mutate(malformed_update)
            malformed_billing_results.append(
                _psql(
                    DATABASE,
                    _as_service_role_rollback(
                        f"SELECT public.persist_semantic_video_plan('{COST_RUN_ID}', 1, "
                        f"{_jsonb(malformed_update)}, {_jsonb(_takes(request_hashes=request_hashes))});"
                    ),
                    check=False,
                )
            )

        assert (
            completed_state.returncode != 0,
            underpriced_cost.returncode != 0,
            missing_counter.returncode != 0,
            null_counter.returncode != 0,
            zero_price.returncode != 0,
        ) == (True, True, True, True, True), (
            completed_state.stdout,
            completed_state.stderr,
            underpriced_cost.stdout,
            underpriced_cost.stderr,
            missing_counter.stdout,
            missing_counter.stderr,
            null_counter.stdout,
            null_counter.stderr,
            zero_price.stdout,
            zero_price.stderr,
        )
        assert all(result.returncode != 0 for result in nonfinite_cost_results), [
            (result.stdout, result.stderr) for result in nonfinite_cost_results
        ]
        assert all(result.returncode != 0 for result in malformed_billing_results), [
            (result.stdout, result.stderr) for result in malformed_billing_results
        ]
        assert "submission state" in completed_state.stderr.lower()
        assert "cost" in underpriced_cost.stderr.lower()
        assert "count" in missing_counter.stderr.lower()
        assert "count" in null_counter.stderr.lower()
        assert "cost" in zero_price.stderr.lower()
        assert all("cost" in result.stderr.lower() for result in nonfinite_cost_results)

        _psql(
            DATABASE,
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM public.semantic_video_runs
                WHERE (id = '{STATE_RUN_ID}' AND (
                  revision <> 1 OR plan_hash <> 'state-baseline-plan' OR estimated_cost_usd <> 6.40
                )) OR (id = '{COST_RUN_ID}' AND (
                  revision <> 1 OR plan_hash <> 'cost-baseline-plan' OR estimated_cost_usd <> 6.40
                ))
              ) THEN
                RAISE EXCEPTION 'adversarial run update escaped failed transaction';
              END IF;
              IF (
                SELECT array_agg(request_hash ORDER BY take_index)
                FROM public.semantic_video_takes
                WHERE run_id = '{STATE_RUN_ID}' AND attempt = 1
              ) <> ARRAY['state-baseline-0', 'state-baseline-1']
                 OR (
                   SELECT array_agg(request_hash ORDER BY take_index)
                   FROM public.semantic_video_takes
                   WHERE run_id = '{COST_RUN_ID}' AND attempt = 1
                 ) <> ARRAY['cost-baseline-0', 'cost-baseline-1'] THEN
                RAISE EXCEPTION 'adversarial takes escaped failed transaction';
              END IF;
            END;
            $$;
            """,
        )
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};", check=False)


@pytest.mark.skipif(
    not CONTAINER,
    reason="Set SEMANTIC_UGC_POSTGRES_CONTAINER to run PostgreSQL migration integration.",
)
def test_transactional_approval_retry_and_cancel_rpcs_are_atomic_and_concurrent_safe():
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
        _psql(DATABASE, BASE_MIGRATION.read_text())
        _psql(DATABASE, API_MIGRATION.read_text())
        _psql(DATABASE, API_MIGRATION.read_text())

        plan_snapshot = {
            "take_count": 2,
            "billable_provider_seconds": 16,
            "quota_units": 2,
            "price_per_provider_second_usd": "0.40",
            "estimated_cost_usd": "6.40",
            "takes": [
                {"take_index": 0, "request_hash": "request-0", "provider_duration_seconds": 8},
                {"take_index": 1, "request_hash": "request-1", "provider_duration_seconds": 8},
            ],
        }
        master_system_prompt = "Exact master system prompt."
        master_candidates = {
            "prompt_writer_system_prompt": master_system_prompt,
            "prompt_writer_system_prompt_sha256": sha256(
                master_system_prompt.encode("utf-8")
            ).hexdigest(),
            "prompt_writer_output": "Exact master writer output.",
            "composition_prompt": "Exact master composition prompt.",
            "candidates": [
                {
                    "index": index,
                    "storage_uri": f"semantic/master-candidate-{index}.png",
                    "mime_type": "image/png",
                    "byte_length": 10,
                    "sha256": f"master-candidate-hash-{index}",
                    "provider_model": "gemini-3.1-flash-image",
                }
                for index in range(1, 4)
            ]
        }
        _psql(
            DATABASE,
            f"""
            INSERT INTO public.batches (
              id, brand, creation_mode, target_length_tier, target_duration_seconds,
              video_pipeline_route
            ) VALUES ('{BATCH_ID}', 'A', 'semantic_ugc', NULL, 50, 'semantic_ugc');
            INSERT INTO public.posts (id, batch_id) VALUES
              ('{MASTER_POST_ID}', '{BATCH_ID}'),
              ('{INITIAL_POST_ID}', '{BATCH_ID}'),
              ('{RETRY_POST_ID}', '{BATCH_ID}'),
              ('{CANCEL_POST_ID}', '{BATCH_ID}'),
              ('{UNSAFE_CANCEL_POST_ID}', '{BATCH_ID}'),
              ('{RACE_CANCEL_POST_ID}', '{BATCH_ID}');

            INSERT INTO public.semantic_video_runs (
              id, post_id, batch_id, requested_duration_seconds, duration_contract,
              duration_contract_hash, script_snapshot, script_hash, actor_snapshot,
              reference_snapshot, reference_hash, master_snapshot, master_hash, stage,
              plan_snapshot, plan_hash, provider_model, resolution, estimated_cost_usd,
              artifact_prefix
            ) VALUES
              (
                '{MASTER_RUN_ID}', '{MASTER_POST_ID}', '{BATCH_ID}', 50,
                '{{"requested_duration_seconds":50}}'::JSONB, 'duration-hash',
                '{{"text":"approved"}}'::JSONB, 'script-hash', '{{}}'::JSONB,
                '{{}}'::JSONB, 'reference-hash', {_jsonb(master_candidates)}, NULL,
                'awaiting_reference_approval', NULL, NULL, NULL, NULL, NULL,
                'semantic/master'
              ),
              (
                '{INITIAL_RUN_ID}', '{INITIAL_POST_ID}', '{BATCH_ID}', 50,
                '{{"requested_duration_seconds":50}}'::JSONB, 'duration-hash',
                '{{"text":"approved"}}'::JSONB, 'script-hash', '{{}}'::JSONB,
                '{{}}'::JSONB, 'reference-hash',
                '{{"storage_uri":"semantic/master.png","sha256":"master-hash","byte_length":10}}'::JSONB,
                'master-hash', 'awaiting_paid_approval', {_jsonb(plan_snapshot)},
                'plan-hash', 'veo-3.1-generate-001', '1080p', 6.40, 'semantic/initial'
              ),
              (
                '{RETRY_RUN_ID}', '{RETRY_POST_ID}', '{BATCH_ID}', 50,
                '{{"requested_duration_seconds":50}}'::JSONB, 'duration-hash',
                '{{"text":"approved"}}'::JSONB, 'script-hash', '{{}}'::JSONB,
                '{{}}'::JSONB, 'reference-hash',
                '{{"storage_uri":"semantic/master.png","sha256":"master-hash","byte_length":10}}'::JSONB,
                'master-hash', 'retry_approval_required', {_jsonb(plan_snapshot)},
                'plan-hash', 'veo-3.1-generate-001', '1080p', 6.40, 'semantic/retry'
              ),
              (
                '{CANCEL_RUN_ID}', '{CANCEL_POST_ID}', '{BATCH_ID}', 50,
                '{{"requested_duration_seconds":50}}'::JSONB, 'duration-hash',
                '{{"text":"approved"}}'::JSONB, 'script-hash', '{{}}'::JSONB,
                '{{}}'::JSONB, 'reference-hash', '{{}}'::JSONB, NULL,
                'generating', NULL, NULL, NULL, NULL, NULL, 'semantic/cancel'
              ),
              (
                '{UNSAFE_CANCEL_RUN_ID}', '{UNSAFE_CANCEL_POST_ID}', '{BATCH_ID}', 50,
                '{{"requested_duration_seconds":50}}'::JSONB, 'duration-hash',
                '{{"text":"approved"}}'::JSONB, 'script-hash', '{{}}'::JSONB,
                '{{}}'::JSONB, 'reference-hash', '{{}}'::JSONB, NULL,
                'generating', NULL, NULL, NULL, NULL, NULL, 'semantic/unsafe-cancel'
              ),
              (
                '{RACE_CANCEL_RUN_ID}', '{RACE_CANCEL_POST_ID}', '{BATCH_ID}', 50,
                '{{"requested_duration_seconds":50}}'::JSONB, 'duration-hash',
                '{{"text":"approved"}}'::JSONB, 'script-hash', '{{}}'::JSONB,
                '{{}}'::JSONB, 'reference-hash', '{{}}'::JSONB, NULL,
                'generating', NULL, NULL, NULL, NULL, NULL, 'semantic/race-cancel'
              );

            INSERT INTO public.semantic_video_takes (
              run_id, take_index, attempt, beat_text, word_count, estimated_speech_seconds,
              provider_duration_seconds, shot_transform, shot_hash, prompt_hash,
              negative_prompt_hash, provider_model, seed, request_contract, request_hash,
              submission_state, retry_guidance
            ) VALUES
              ('{INITIAL_RUN_ID}', 0, 1, 'Beat 0', 2, 1.0, 8, '{{}}', 'shot-0', 'prompt-0', 'negative-0', 'veo-3.1-generate-001', 100, '{{"prompt":"Speak Beat 0 once.","seed":100}}', 'request-0', 'planned', '{{}}'),
              ('{INITIAL_RUN_ID}', 1, 1, 'Beat 1', 2, 1.0, 8, '{{}}', 'shot-1', 'prompt-1', 'negative-1', 'veo-3.1-generate-001', 101, '{{"prompt":"Speak Beat 1 once.","seed":101}}', 'request-1', 'planned', '{{}}'),
              ('{RETRY_RUN_ID}', 0, 1, 'Beat 0', 2, 1.0, 8, '{{}}', 'shot-0', 'prompt-0', 'negative-0', 'veo-3.1-generate-001', 100, '{{"prompt":"Speak Beat 0 once.","seed":100}}', 'retry-request-0', 'completed', '{{}}'),
              ('{RETRY_RUN_ID}', 1, 1, 'Beat 1', 2, 1.0, 8, '{{}}', 'shot-1', 'prompt-1', 'negative-1', 'veo-3.1-generate-001', 101, '{{"prompt":"Speak Beat 1 once.","seed":101}}', 'retry-request-1', 'qa_failed', '{{"guidance":"Hold eye contact."}}'),
              ('{CANCEL_RUN_ID}', 0, 1, 'Beat 0', 2, 1.0, 8, '{{}}', 'shot-0', 'prompt-0', NULL, 'veo-3.1-generate-001', 100, '{{}}', 'cancel-request-0', 'planned', '{{}}'),
              ('{CANCEL_RUN_ID}', 1, 1, 'Beat 1', 2, 1.0, 8, '{{}}', 'shot-1', 'prompt-1', NULL, 'veo-3.1-generate-001', 101, '{{}}', 'cancel-request-1', 'reserved', '{{}}'),
              ('{UNSAFE_CANCEL_RUN_ID}', 0, 1, 'Beat 0', 2, 1.0, 8, '{{}}', 'shot-0', 'prompt-0', NULL, 'veo-3.1-generate-001', 100, '{{}}', 'unsafe-request-0', 'planned', '{{}}'),
              ('{UNSAFE_CANCEL_RUN_ID}', 1, 1, 'Beat 1', 2, 1.0, 8, '{{}}', 'shot-1', 'prompt-1', NULL, 'veo-3.1-generate-001', 101, '{{}}', 'unsafe-request-1', 'intent_persisted', '{{}}'),
              ('{RACE_CANCEL_RUN_ID}', 0, 1, 'Beat 0', 2, 1.0, 8, '{{}}', 'shot-0', 'prompt-0', NULL, 'veo-3.1-generate-001', 100, '{{}}', 'race-request-0', 'planned', '{{}}');

            CREATE OR REPLACE FUNCTION public.fail_semantic_transition_update()
            RETURNS TRIGGER LANGUAGE plpgsql AS $$
            BEGIN
              RAISE EXCEPTION 'injected transition failure';
            END;
            $$;
            """,
        )

        signatures = [
            "public.approve_semantic_video_master(uuid,integer,integer,text,text)",
            "public.approve_semantic_video_initial_plan(uuid,integer,text,text,text)",
            "public.approve_semantic_video_retry(uuid,integer,text,jsonb,text,text,text)",
            "public.cancel_semantic_video_run(uuid,integer,text,text,text)",
        ]
        _psql(
            DATABASE,
            "DO $$ DECLARE signature TEXT; BEGIN FOREACH signature IN ARRAY ARRAY["
            + ",".join(f"'{signature}'" for signature in signatures)
            + "] LOOP "
            "IF NOT has_function_privilege('service_role', signature, 'EXECUTE') THEN "
            "RAISE EXCEPTION 'service_role cannot execute %', signature; END IF; "
            "IF has_function_privilege('anon', signature, 'EXECUTE') OR "
            "has_function_privilege('authenticated', signature, 'EXECUTE') THEN "
            "RAISE EXCEPTION 'untrusted role can execute %', signature; END IF; "
            "END LOOP; END; $$;",
        )
        _psql(
            DATABASE,
            "DO $$ BEGIN "
            "IF has_table_privilege('service_role', 'public.semantic_video_approvals', 'INSERT') THEN "
            "RAISE EXCEPTION 'service_role can bypass approval RPCs with direct insert'; END IF; "
            "IF NOT has_table_privilege('service_role', 'public.semantic_video_approvals', 'SELECT') THEN "
            "RAISE EXCEPTION 'service_role cannot read persisted approvals'; END IF; "
            "IF has_table_privilege('service_role', 'public.semantic_video_takes', 'INSERT') THEN "
            "RAISE EXCEPTION 'service_role can bypass plan and retry RPCs with direct take insert'; END IF; "
            "IF NOT has_table_privilege('service_role', 'public.semantic_video_takes', 'SELECT') "
            "OR NOT has_table_privilege('service_role', 'public.semantic_video_takes', 'UPDATE') THEN "
            "RAISE EXCEPTION 'service_role cannot execute worker take transitions'; END IF; "
            "END; $$;",
        )
        for role in ("anon", "authenticated"):
            denied = _psql(
                DATABASE,
                f"SET ROLE {role}; SELECT public.approve_semantic_video_master('{MASTER_RUN_ID}', 0, 1, 'operator@example.com', NULL);",
                check=False,
            )
            assert denied.returncode != 0
            assert "permission denied" in denied.stderr.lower()

        nonfinite_initial = _psql(
            DATABASE,
            "\\set VERBOSITY verbose\n"
            "BEGIN; "
            f"UPDATE public.semantic_video_runs SET plan_snapshot = jsonb_set(jsonb_set(plan_snapshot, '{{price_per_provider_second_usd}}', to_jsonb('NaN'::text)), '{{estimated_cost_usd}}', to_jsonb('NaN'::text)), estimated_cost_usd = 'NaN'::numeric WHERE id = '{INITIAL_RUN_ID}'; "
            "SET LOCAL ROLE service_role; "
            f"SELECT public.approve_semantic_video_initial_plan('{INITIAL_RUN_ID}', 0, 'plan-hash', 'operator@example.com', NULL); "
            "ROLLBACK;",
            check=False,
        )
        assert nonfinite_initial.returncode != 0, (
            nonfinite_initial.stdout,
            nonfinite_initial.stderr,
        )
        assert "cost" in nonfinite_initial.stderr.lower()

        def assert_one_winner(sql: str) -> None:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda _index: _psql(
                            DATABASE,
                            "\\set VERBOSITY verbose\n" + _as_service_role(sql),
                            check=False,
                        ),
                        range(2),
                    )
                )
            assert sum(result.returncode == 0 for result in results) == 1, [
                (result.stdout, result.stderr) for result in results
            ]
            loser = next(result for result in results if result.returncode != 0)
            assert "40001" in loser.stderr
            assert "semantic_video_conflict:" in loser.stderr

        transition_cases = [
            (
                "master",
                MASTER_RUN_ID,
                f"SELECT public.approve_semantic_video_master('{MASTER_RUN_ID}', 0, 1, 'operator@example.com', 'best match');",
            ),
            (
                "initial",
                INITIAL_RUN_ID,
                f"SELECT public.approve_semantic_video_initial_plan('{INITIAL_RUN_ID}', 0, 'plan-hash', 'operator@example.com', NULL);",
            ),
        ]
        retry_prompt = "Speak Beat 1 once. Retry delivery correction: Hold eye contact."
        retry_request_contract, retry_request_hash = _with_canonical_request_hash(
            {
                "prompt": retry_prompt,
                "seed": 1101,
                "attempt": 2,
                "retry_of_request_hash": "retry-request-1",
                "retry_guidance": {"guidance": "Hold eye contact."},
            }
        )
        retry_take = {
            "take_index": 1,
            "attempt": 2,
            "beat_text": "Beat 1",
            "word_count": 2,
            "estimated_speech_seconds": 1.0,
            "provider_duration_seconds": 8,
            "shot_transform": {},
            "shot_hash": "shot-1",
            "prompt_hash": sha256(retry_prompt.encode()).hexdigest(),
            "negative_prompt_hash": "negative-1",
            "provider_model": "veo-3.1-generate-001",
            "seed": 1101,
            "request_contract": retry_request_contract,
            "request_hash": retry_request_hash,
            "submission_state": "planned",
            "retry_guidance": {"guidance": "Hold eye contact."},
        }
        retry_contract_hash = _retry_contract_hash(
            plan_hash="plan-hash",
            revision=0,
            indexes=[1],
            request_hashes=[retry_request_hash],
            provider_seconds=8,
            quota_units=1,
            estimated_cost="3.20",
        )
        nonfinite_retry = _psql(
            DATABASE,
            "\\set VERBOSITY verbose\n"
            "BEGIN; "
            f"UPDATE public.semantic_video_runs SET plan_snapshot = jsonb_set(plan_snapshot, '{{price_per_provider_second_usd}}', to_jsonb('NaN'::text)) WHERE id = '{RETRY_RUN_ID}'; "
            "SET LOCAL ROLE service_role; "
            "SELECT public.approve_semantic_video_retry("
            f"'{RETRY_RUN_ID}', 0, 'plan-hash', {_jsonb([retry_take])}, "
            f"'{retry_contract_hash}', 'operator@example.com', 'qa correction'); "
            "ROLLBACK;",
            check=False,
        )
        assert nonfinite_retry.returncode != 0, (
            nonfinite_retry.stdout,
            nonfinite_retry.stderr,
        )
        assert "price" in nonfinite_retry.stderr.lower()

        tampered_retry_cases = []
        bad_prompt_hash_take = deepcopy(retry_take)
        bad_prompt_hash_take["prompt_hash"] = "0" * 64
        tampered_retry_cases.append((bad_prompt_hash_take, retry_contract_hash))

        bad_request_hash_take = deepcopy(retry_take)
        bad_request_hash_take["request_hash"] = "f" * 64
        tampered_retry_cases.append(
            (
                bad_request_hash_take,
                _retry_contract_hash(
                    plan_hash="plan-hash",
                    revision=0,
                    indexes=[1],
                    request_hashes=["f" * 64],
                    provider_seconds=8,
                    quota_units=1,
                    estimated_cost="3.20",
                ),
            )
        )
        tampered_retry_cases.append((retry_take, "0" * 64))

        duplicate_guidance_take = deepcopy(retry_take)
        duplicate_prompt = retry_prompt + " Hold eye contact."
        duplicate_contract = {
            key: value
            for key, value in duplicate_guidance_take["request_contract"].items()
            if key != "canonical_request_json"
        }
        duplicate_contract["prompt"] = duplicate_prompt
        duplicate_contract, duplicate_hash = _with_canonical_request_hash(duplicate_contract)
        duplicate_guidance_take["request_contract"] = duplicate_contract
        duplicate_guidance_take["prompt_hash"] = sha256(duplicate_prompt.encode()).hexdigest()
        duplicate_guidance_take["request_hash"] = duplicate_hash
        tampered_retry_cases.append(
            (
                duplicate_guidance_take,
                _retry_contract_hash(
                    plan_hash="plan-hash",
                    revision=0,
                    indexes=[1],
                    request_hashes=[duplicate_hash],
                    provider_seconds=8,
                    quota_units=1,
                    estimated_cost="3.20",
                ),
            )
        )
        tampered_results = [
            _psql(
                DATABASE,
                "\\set VERBOSITY verbose\n"
                + _as_service_role_rollback(
                    "SELECT public.approve_semantic_video_retry("
                    f"'{RETRY_RUN_ID}', 0, 'plan-hash', {_jsonb([take])}, "
                    f"'{contract_hash}', 'operator@example.com', 'qa correction');"
                ),
                check=False,
            )
            for take, contract_hash in tampered_retry_cases
        ]
        assert all(result.returncode != 0 for result in tampered_results), [
            (result.stdout, result.stderr) for result in tampered_results
        ]
        transition_cases.append(
            (
                "retry",
                RETRY_RUN_ID,
                "SELECT public.approve_semantic_video_retry("
                f"'{RETRY_RUN_ID}', 0, 'plan-hash', {_jsonb([retry_take])}, "
                f"'{retry_contract_hash}', 'operator@example.com', 'qa correction');",
            )
        )

        for label, run_id, sql in transition_cases:
            _psql(
                DATABASE,
                f"CREATE TRIGGER fail_{label}_transition BEFORE UPDATE ON public.semantic_video_runs "
                f"FOR EACH ROW WHEN (OLD.id = '{run_id}') EXECUTE FUNCTION public.fail_semantic_transition_update();",
            )
            failed = _psql(DATABASE, _as_service_role(sql), check=False)
            assert failed.returncode != 0
            assert "injected transition failure" in failed.stderr
            assert "semantic_video_conflict:" not in failed.stderr
            _psql(
                DATABASE,
                f"DROP TRIGGER fail_{label}_transition ON public.semantic_video_runs;",
            )
            _psql(
                DATABASE,
                f"DO $$ BEGIN IF (SELECT count(*) FROM public.semantic_video_approvals WHERE run_id = '{run_id}') <> 0 THEN "
                f"RAISE EXCEPTION '{label} approval escaped rollback'; END IF; END; $$;",
            )
            if label == "retry":
                _psql(
                    DATABASE,
                    f"DO $$ BEGIN IF (SELECT count(*) FROM public.semantic_video_takes WHERE run_id = '{run_id}' AND attempt = 2) <> 0 THEN "
                    "RAISE EXCEPTION 'retry attempt escaped rollback'; END IF; END; $$;",
                )
            assert_one_winner(sql)
            _psql(
                DATABASE,
                f"DO $$ BEGIN IF (SELECT count(*) FROM public.semantic_video_approvals WHERE run_id = '{run_id}') <> 1 THEN "
                f"RAISE EXCEPTION '{label} concurrent approval count mismatch'; END IF; END; $$;",
            )

        _psql(
            DATABASE,
            f"""
            DO $$
            BEGIN
              IF (SELECT master_snapshot ->> 'prompt_writer_system_prompt' FROM public.semantic_video_runs WHERE id = '{MASTER_RUN_ID}')
                   IS DISTINCT FROM 'Exact master system prompt.'
                 OR (SELECT master_snapshot ->> 'prompt_writer_system_prompt_sha256' FROM public.semantic_video_runs WHERE id = '{MASTER_RUN_ID}')
                   IS DISTINCT FROM '{sha256(master_system_prompt.encode('utf-8')).hexdigest()}'
                 OR (SELECT master_snapshot ->> 'prompt_writer_output' FROM public.semantic_video_runs WHERE id = '{MASTER_RUN_ID}')
                   IS DISTINCT FROM 'Exact master writer output.'
                 OR (SELECT master_snapshot ->> 'composition_prompt' FROM public.semantic_video_runs WHERE id = '{MASTER_RUN_ID}')
                   IS DISTINCT FROM 'Exact master composition prompt.' THEN
                RAISE EXCEPTION 'master approval dropped prompt provenance';
              END IF;
            END;
            $$;
            """,
        )

        unsafe = _psql(
            DATABASE,
            _as_service_role(
                f"SELECT public.cancel_semantic_video_run('{UNSAFE_CANCEL_RUN_ID}', 0, 'operator@example.com', 'stop', 'corr-unsafe');"
            ),
            check=False,
        )
        assert unsafe.returncode != 0
        assert "semantic_video_conflict:" in unsafe.stderr
        _psql(
            DATABASE,
            f"DO $$ BEGIN IF (SELECT stage FROM public.semantic_video_runs WHERE id = '{UNSAFE_CANCEL_RUN_ID}') <> 'generating' "
            f"OR (SELECT array_agg(submission_state ORDER BY take_index) FROM public.semantic_video_takes WHERE run_id = '{UNSAFE_CANCEL_RUN_ID}') "
            "<> ARRAY['planned','intent_persisted'] THEN RAISE EXCEPTION 'unsafe cancellation partially mutated state'; END IF; END; $$;",
        )

        _psql(
            DATABASE,
            """
            CREATE OR REPLACE FUNCTION public.pause_racing_cancellation()
            RETURNS TRIGGER LANGUAGE plpgsql AS $$
            BEGIN
              IF current_setting('application_name') = 'semantic-cancel-race' THEN
                PERFORM pg_sleep(2);
              END IF;
              RETURN NULL;
            END;
            $$;
            CREATE TRIGGER pause_racing_cancellation
            BEFORE UPDATE ON public.semantic_video_takes
            FOR EACH STATEMENT EXECUTE FUNCTION public.pause_racing_cancellation();
            """,
        )
        racing_cancel_sql = (
            "SET application_name = 'semantic-cancel-race';\n"
            + _as_service_role(
                f"SELECT public.cancel_semantic_video_run('{RACE_CANCEL_RUN_ID}', 0, "
                "'operator@example.com', 'stop', 'corr-race');"
            )
        )
        racing_intent_sql = _as_service_role(
            "DO $$ DECLARE changed_count INTEGER; BEGIN "
            "UPDATE public.semantic_video_takes SET submission_state = 'intent_persisted', "
            "submission_intent_at = now() "
            f"WHERE run_id = '{RACE_CANCEL_RUN_ID}' AND take_index = 0 "
            "AND submission_state = 'planned' AND request_hash = 'race-request-0'; "
            "GET DIAGNOSTICS changed_count = ROW_COUNT; "
            "IF changed_count <> 1 THEN RAISE EXCEPTION 'intent transition lost race'; END IF; "
            "END; $$;"
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            cancel_future = pool.submit(
                _psql,
                DATABASE,
                "\\set VERBOSITY verbose\n" + racing_cancel_sql,
                check=False,
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                sleeping = _psql(
                    DATABASE,
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name = 'semantic-cancel-race' "
                    "AND wait_event = 'PgSleep';",
                )
                if any(line.strip() == "1" for line in sleeping.stdout.splitlines()):
                    break
                time.sleep(0.05)
            else:
                pytest.fail("cancellation did not reach the post-check race boundary")
            intent_future = pool.submit(
                _psql,
                DATABASE,
                racing_intent_sql,
                check=False,
            )
            race_results = [cancel_future.result(), intent_future.result()]

        assert sum(result.returncode == 0 for result in race_results) == 1, [
            (result.stdout, result.stderr) for result in race_results
        ]
        race_state = _psql(
            DATABASE,
            f"SELECT run.stage || ':' || take.submission_state "
            "FROM public.semantic_video_runs AS run "
            "JOIN public.semantic_video_takes AS take ON take.run_id = run.id "
            f"WHERE run.id = '{RACE_CANCEL_RUN_ID}';",
        ).stdout
        assert "failed:cancelled" in race_state or "generating:intent_persisted" in race_state
        _psql(
            DATABASE,
            "DROP TRIGGER pause_racing_cancellation ON public.semantic_video_takes; "
            "DROP FUNCTION public.pause_racing_cancellation();",
        )

        cancel_sql = (
            f"SELECT public.cancel_semantic_video_run('{CANCEL_RUN_ID}', 0, "
            "'operator@example.com', 'stop', 'corr-cancel');"
        )
        _psql(
            DATABASE,
            f"CREATE TRIGGER fail_cancel_transition BEFORE UPDATE ON public.semantic_video_runs "
            f"FOR EACH ROW WHEN (OLD.id = '{CANCEL_RUN_ID}') EXECUTE FUNCTION public.fail_semantic_transition_update();",
        )
        failed_cancel = _psql(DATABASE, _as_service_role(cancel_sql), check=False)
        assert failed_cancel.returncode != 0
        assert "injected transition failure" in failed_cancel.stderr
        assert "semantic_video_conflict:" not in failed_cancel.stderr
        _psql(DATABASE, "DROP TRIGGER fail_cancel_transition ON public.semantic_video_runs;")
        _psql(
            DATABASE,
            f"DO $$ BEGIN IF (SELECT stage FROM public.semantic_video_runs WHERE id = '{CANCEL_RUN_ID}') <> 'generating' "
            f"OR (SELECT array_agg(submission_state ORDER BY take_index) FROM public.semantic_video_takes WHERE run_id = '{CANCEL_RUN_ID}') "
            "<> ARRAY['planned','reserved'] THEN RAISE EXCEPTION 'cancel rollback left partial state'; END IF; END; $$;",
        )
        assert_one_winner(cancel_sql)
        _psql(
            DATABASE,
            f"DO $$ BEGIN IF (SELECT stage FROM public.semantic_video_runs WHERE id = '{CANCEL_RUN_ID}') <> 'failed' "
            f"OR (SELECT array_agg(submission_state ORDER BY take_index) FROM public.semantic_video_takes WHERE run_id = '{CANCEL_RUN_ID}') "
            "<> ARRAY['cancelled','cancelled'] THEN RAISE EXCEPTION 'safe cancellation contract failed'; END IF; END; $$;",
        )
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};", check=False)
