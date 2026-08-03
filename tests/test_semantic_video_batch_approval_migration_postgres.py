from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE_MIGRATION = ROOT / "supabase/migrations/20260713000000_semantic_ugc_production.sql"
API_MIGRATION = ROOT / "supabase/migrations/20260713000100_semantic_video_api_transactions.sql"
BATCH_MIGRATION = ROOT / "supabase/migrations/20260804000000_semantic_video_batch_initial_approval.sql"
CONTAINER = os.getenv("SEMANTIC_UGC_POSTGRES_CONTAINER")
DATABASE = "semantic_video_batch_approval_rpc_test"
BATCH_ID = "00000000-0000-0000-0000-000000000101"
POST_IDS = [f"00000000-0000-0000-0000-00000000011{index}" for index in range(1, 4)]
RUN_IDS = [f"00000000-0000-0000-0000-00000000012{index}" for index in range(1, 4)]


def _psql(database: str, sql: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            str(CONTAINER),
            "psql",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
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


def test_batch_approval_uses_a_later_atomic_service_role_rpc():
    source = BATCH_MIGRATION.read_text()

    assert int(BATCH_MIGRATION.name.split("_", 1)[0]) > int(
        API_MIGRATION.name.split("_", 1)[0]
    )
    assert "CREATE OR REPLACE FUNCTION public.approve_semantic_video_batch_initial_plans" in source
    assert "FOR UPDATE OF run" in source
    assert "public.approve_semantic_video_initial_plan(" in source
    assert "approval_count IS DISTINCT FROM eligible_count" in source
    assert "matched_count IS DISTINCT FROM eligible_count" in source
    assert "FROM PUBLIC, anon, authenticated" in source
    assert "TO service_role" in source


@pytest.mark.skipif(
    not CONTAINER,
    reason="Set SEMANTIC_UGC_POSTGRES_CONTAINER to run PostgreSQL migration integration.",
)
def test_batch_approval_is_all_or_nothing_for_three_independent_runs():
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
              seed_data JSONB NOT NULL DEFAULT '{}'::JSONB
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
        _psql(DATABASE, BATCH_MIGRATION.read_text())
        _psql(DATABASE, BATCH_MIGRATION.read_text())

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
        run_rows = []
        take_rows = []
        post_rows = [
            f"('{post_id}', '{BATCH_ID}', '{{\"script_review_status\":\"approved\"}}')"
            for post_id in POST_IDS
        ]
        for index, (post_id, run_id) in enumerate(zip(POST_IDS, RUN_IDS), start=1):
            run_rows.append(
                "(" + ",".join(
                    [
                        f"'{run_id}'",
                        f"'{post_id}'",
                        f"'{BATCH_ID}'",
                        "16",
                        "'{\"requested_duration_seconds\":16}'::JSONB",
                        f"'duration-hash-{index}'",
                        "'{\"text\":\"approved\"}'::JSONB",
                        f"'script-hash-{index}'",
                        "'{}'::JSONB",
                        "'{}'::JSONB",
                        f"'reference-hash-{index}'",
                        f"'{{\"storage_uri\":\"semantic/master-{index}.png\",\"sha256\":\"master-hash-{index}\",\"byte_length\":10}}'::JSONB",
                        f"'master-hash-{index}'",
                        "'awaiting_paid_approval'",
                        _jsonb(plan_snapshot),
                        f"'{'a' * 63}{index}'",
                        "'veo-3.1-generate-001'",
                        "'1080p'",
                        "6.40",
                        f"'semantic/batch-{index}'",
                    ]
                ) + ")"
            )
            for take_index in range(2):
                take_rows.append(
                    f"('{run_id}', {take_index}, 1, 'Beat {take_index}', 2, 1.0, 8, '{{}}', "
                    f"'shot-{take_index}', 'prompt-{take_index}', 'negative-{take_index}', "
                    f"'veo-3.1-generate-001', {100 + take_index}, "
                    f"'{{\"prompt\":\"Speak Beat {take_index} once.\",\"seed\":{100 + take_index}}}', "
                    f"'request-{take_index}', 'planned', '{{}}')"
                )

        _psql(
            DATABASE,
            f"""
            INSERT INTO public.batches (
              id, brand, creation_mode, target_length_tier, target_duration_seconds,
              video_pipeline_route
            ) VALUES ('{BATCH_ID}', 'Batch approval', 'semantic_ugc', NULL, 16, 'semantic_ugc');
            INSERT INTO public.posts (id, batch_id, seed_data) VALUES
              {','.join(post_rows)};
            INSERT INTO public.semantic_video_runs (
              id, post_id, batch_id, requested_duration_seconds, duration_contract,
              duration_contract_hash, script_snapshot, script_hash, actor_snapshot,
              reference_snapshot, reference_hash, master_snapshot, master_hash, stage,
              plan_snapshot, plan_hash, provider_model, resolution, estimated_cost_usd,
              artifact_prefix
            ) VALUES {','.join(run_rows)};
            INSERT INTO public.semantic_video_takes (
              run_id, take_index, attempt, beat_text, word_count, estimated_speech_seconds,
              provider_duration_seconds, shot_transform, shot_hash, prompt_hash,
              negative_prompt_hash, provider_model, seed, request_contract, request_hash,
              submission_state, retry_guidance
            ) VALUES {','.join(take_rows)};
            """,
        )

        approvals = [
            {
                "run_id": run_id,
                "expected_revision": 0,
                "plan_hash": f"{'a' * 63}{index}",
            }
            for index, run_id in enumerate(RUN_IDS, start=1)
        ]
        incomplete = _psql(
            DATABASE,
            "SET ROLE service_role; "
            f"SELECT public.approve_semantic_video_batch_initial_plans('{BATCH_ID}', {_jsonb(approvals[:2])}, 'operator@example.com', NULL);",
            check=False,
        )
        assert incomplete.returncode != 0
        assert "ready batch plans changed" in incomplete.stderr

        stale = [dict(item) for item in approvals]
        stale[-1]["plan_hash"] = "f" * 64
        stale_result = _psql(
            DATABASE,
            "SET ROLE service_role; "
            f"SELECT public.approve_semantic_video_batch_initial_plans('{BATCH_ID}', {_jsonb(stale)}, 'operator@example.com', NULL);",
            check=False,
        )
        assert stale_result.returncode != 0
        assert "ready batch plans changed" in stale_result.stderr
        untouched = _psql(
            DATABASE,
            f"SELECT count(*) FROM public.semantic_video_runs WHERE batch_id = '{BATCH_ID}' AND stage = 'awaiting_paid_approval';",
        )
        assert untouched.stdout.strip() == "3"

        approved = _psql(
            DATABASE,
            "SET ROLE service_role; "
            f"SELECT public.approve_semantic_video_batch_initial_plans('{BATCH_ID}', {_jsonb(approvals)}, 'operator@example.com', 'complete batch');",
        )
        result = json.loads(approved.stdout.strip())
        assert result["batch_id"] == BATCH_ID
        assert result["approval_count"] == 3
        assert result["approved_provider_seconds"] == 48
        assert result["quota_units"] == 6
        assert result["estimated_cost_usd"] == "19.20"
        assert len(result["approvals"]) == 3

        persisted = _psql(
            DATABASE,
            f"SELECT count(*) || ':' || min(revision) || ':' || max(revision) FROM public.semantic_video_runs WHERE batch_id = '{BATCH_ID}' AND stage = 'generating';",
        )
        assert persisted.stdout.strip() == "3:1:1"
        approval_rows = _psql(
            DATABASE,
            "SELECT count(*) FROM public.semantic_video_approvals WHERE approval_type = 'initial_plan';",
        )
        assert approval_rows.stdout.strip() == "3"

        denied = _psql(
            DATABASE,
            "SET ROLE authenticated; "
            f"SELECT public.approve_semantic_video_batch_initial_plans('{BATCH_ID}', {_jsonb(approvals)}, 'operator@example.com', NULL);",
            check=False,
        )
        assert denied.returncode != 0
        assert "permission denied" in denied.stderr.lower()
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};", check=False)
