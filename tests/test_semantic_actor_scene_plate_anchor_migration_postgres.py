from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE_MIGRATION = ROOT / "supabase/migrations/20260713000000_semantic_ugc_production.sql"
API_MIGRATION = ROOT / "supabase/migrations/20260713000100_semantic_video_api_transactions.sql"
ANCHOR_MIGRATION = ROOT / "supabase/migrations/20260720000200_semantic_actor_scene_plate_anchor.sql"
CONTAINER = os.getenv("SEMANTIC_UGC_POSTGRES_CONTAINER")
DATABASE = "semantic_actor_anchor_rpc_test"
BATCH_ID = "10000000-0000-0000-0000-000000000001"
ACTOR_ID = "10000000-0000-0000-0000-000000000002"
FINGERPRINT = "a" * 64
VISUAL_HASH = "b" * 64
RUN_IDS = (
    "10000000-0000-0000-0000-000000000011",
    "10000000-0000-0000-0000-000000000012",
    "10000000-0000-0000-0000-000000000013",
)
POST_IDS = (
    "10000000-0000-0000-0000-000000000021",
    "10000000-0000-0000-0000-000000000022",
    "10000000-0000-0000-0000-000000000023",
)


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
            "-At",
        ],
        input=sql,
        text=True,
        capture_output=True,
        check=check,
    )


def _jsonb(value: object) -> str:
    return f"$json${json.dumps(value, separators=(',', ':'))}$json$::jsonb"


def _run_insert(*, run_id: str, post_id: str, master_hash: str, mode: str, anchor=None) -> str:
    candidate = {
        "index": 1,
        "storage_uri": f"https://cdn.example.com/{master_hash[:8]}.png",
        "sha256": master_hash,
        "byte_length": 1234,
        "mime_type": "image/png",
        "provider_model": "gemini-3.1-flash-image",
        "visual_contract_hash": VISUAL_HASH,
        "actor_reference_fingerprint": FINGERPRINT,
        "derivation_mode": mode,
        "canonical_anchor_id": anchor["id"] if anchor else None,
        "canonical_anchor_sha256": anchor["master_sha256"] if anchor else None,
    }
    reference = {
        "actor_reference_fingerprint": FINGERPRINT,
        "visual_contract": {"contract_hash": VISUAL_HASH},
    }
    master = {
        "candidates": [candidate],
        "visual_contract": {"contract_hash": VISUAL_HASH},
        "visual_contract_hash": VISUAL_HASH,
        "actor_reference_fingerprint": FINGERPRINT,
        "derivation_mode": mode,
        "prompt_writer_system_prompt": "system",
        "prompt_writer_system_prompt_sha256": "c" * 64,
        "prompt_writer_output": "output",
        "composition_prompt": "composition",
        "scene_plate_prompts": ["composition"],
    }
    return f"""
      INSERT INTO public.semantic_video_runs (
        id, post_id, batch_id, requested_duration_seconds,
        duration_contract, duration_contract_hash, script_snapshot, script_hash,
        actor_identity_id, actor_snapshot, reference_snapshot, reference_hash,
        master_snapshot, stage, artifact_prefix
      ) VALUES (
        '{run_id}', '{post_id}', '{BATCH_ID}', 16,
        '{{"requested_duration_seconds":16}}'::JSONB, 'duration-hash',
        '{{"text":"approved"}}'::JSONB, 'script-hash',
        '{ACTOR_ID}', '{{}}'::JSONB, {_jsonb(reference)}, 'reference-hash',
        {_jsonb(master)}, 'awaiting_reference_approval', 'semantic/{run_id}'
      );
    """


def test_anchor_migration_is_additive_and_restricts_writes_to_approval_rpc():
    sql = ANCHOR_MIGRATION.read_text()

    assert ANCHOR_MIGRATION.exists()
    assert "CREATE TABLE IF NOT EXISTS public.semantic_actor_scene_plate_anchors" in sql
    assert "UNIQUE (actor_identity_id, actor_reference_fingerprint)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE INSERT, UPDATE, DELETE" in sql
    assert "GRANT SELECT ON TABLE public.semantic_actor_scene_plate_anchors TO service_role" in sql
    assert "CREATE OR REPLACE FUNCTION public.approve_semantic_video_master" in sql
    assert "SET search_path = ''" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "FOR UPDATE" in sql
    assert "regenerate from the canonical anchor" in sql
    assert sql.index("pg_advisory_xact_lock") < sql.index(
        "INSERT INTO public.semantic_video_approvals"
    )


@pytest.mark.skipif(
    not CONTAINER,
    reason="Set SEMANTIC_UGC_POSTGRES_CONTAINER to run PostgreSQL anchor integration.",
)
def test_atomic_anchor_claim_has_one_bootstrap_winner_and_rechecks_derived_lineage():
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
        _psql(DATABASE, ANCHOR_MIGRATION.read_text())
        _psql(DATABASE, ANCHOR_MIGRATION.read_text())
        _psql(
            DATABASE,
            f"""
            INSERT INTO public.actor_identities (id) VALUES ('{ACTOR_ID}');
            INSERT INTO public.batches (
              id, brand, creation_mode, target_duration_seconds, video_pipeline_route
            ) VALUES ('{BATCH_ID}', 'Anchor Test', 'semantic_ugc', 16, 'semantic_ugc');
            INSERT INTO public.posts (id, batch_id) VALUES
              ('{POST_IDS[0]}', '{BATCH_ID}'),
              ('{POST_IDS[1]}', '{BATCH_ID}'),
              ('{POST_IDS[2]}', '{BATCH_ID}');
            {_run_insert(run_id=RUN_IDS[0], post_id=POST_IDS[0], master_hash='1' * 64, mode='bootstrap')}
            {_run_insert(run_id=RUN_IDS[1], post_id=POST_IDS[1], master_hash='2' * 64, mode='bootstrap')}
            """,
        )

        privilege_row = _psql(
            DATABASE,
            """
            SELECT
              has_table_privilege('service_role', 'public.semantic_actor_scene_plate_anchors', 'SELECT'),
              has_table_privilege('service_role', 'public.semantic_actor_scene_plate_anchors', 'INSERT'),
              has_table_privilege('service_role', 'public.semantic_actor_scene_plate_anchors', 'UPDATE'),
              has_table_privilege('service_role', 'public.semantic_actor_scene_plate_anchors', 'DELETE'),
              has_table_privilege('anon', 'public.semantic_actor_scene_plate_anchors', 'SELECT'),
              has_function_privilege('service_role', 'public.approve_semantic_video_master(uuid,integer,integer,text,text)', 'EXECUTE'),
              has_function_privilege('authenticated', 'public.approve_semantic_video_master(uuid,integer,integer,text,text)', 'EXECUTE');
            """,
        ).stdout.strip()
        assert privilege_row == "t|f|f|f|f|t|f"

        def approve(run_id: str) -> subprocess.CompletedProcess[str]:
            return _psql(
                DATABASE,
                "\\set VERBOSITY verbose\n"
                "SET ROLE service_role; "
                f"SELECT public.approve_semantic_video_master('{run_id}', 0, 1, 'operator@example.com', NULL);",
                check=False,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(approve, RUN_IDS[:2]))

        assert sum(result.returncode == 0 for result in results) == 1, [
            (result.stdout, result.stderr) for result in results
        ]
        loser = next(result for result in results if result.returncode != 0)
        assert "40001" in loser.stderr
        assert "regenerate from the canonical anchor" in loser.stderr

        anchor_parts = _psql(
            DATABASE,
            """
            SELECT id, master_sha256, source_run_id
            FROM public.semantic_actor_scene_plate_anchors;
            """,
        ).stdout.strip().split("|")
        assert len(anchor_parts) == 3
        anchor = {
            "id": anchor_parts[0],
            "master_sha256": anchor_parts[1],
            "source_run_id": anchor_parts[2],
        }
        assert anchor["master_sha256"] in {"1" * 64, "2" * 64}
        assert anchor["source_run_id"] in RUN_IDS[:2]
        loser_run_id = RUN_IDS[1] if anchor["source_run_id"] == RUN_IDS[0] else RUN_IDS[0]
        loser_state = _psql(
            DATABASE,
            f"""
            SELECT stage, revision,
              (SELECT count(*) FROM public.semantic_video_approvals WHERE run_id = '{loser_run_id}')
            FROM public.semantic_video_runs WHERE id = '{loser_run_id}';
            """,
        ).stdout.strip()
        assert loser_state == "awaiting_reference_approval|0|0"

        derived_hash = "3" * 64
        _psql(
            DATABASE,
            _run_insert(
                run_id=RUN_IDS[2],
                post_id=POST_IDS[2],
                master_hash=derived_hash,
                mode="canonical_anchor",
                anchor=anchor,
            ),
        )
        derived = approve(RUN_IDS[2])
        assert derived.returncode == 0, (derived.stdout, derived.stderr)
        approved = _psql(
            DATABASE,
            f"SELECT stage, master_hash FROM public.semantic_video_runs WHERE id = '{RUN_IDS[2]}';",
        ).stdout.strip()
        assert approved == f"awaiting_paid_approval|{derived_hash}"
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};", check=False)
