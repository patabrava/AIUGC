from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE_MIGRATION = ROOT / "supabase/migrations/001_initial_schema.sql"
QA_MIGRATION = ROOT / "supabase/migrations/20260813190809_atomic_qa_delivery_decision.sql"
CONTAINER = os.getenv("SEMANTIC_UGC_POSTGRES_CONTAINER")
DATABASE = "qa_delivery_rpc_test"
BATCH_ID = "00000000-0000-0000-0000-000000000201"
POST_ID = "00000000-0000-0000-0000-000000000211"
REMOVED_POST_ID = "00000000-0000-0000-0000-000000000212"


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


def test_qa_delivery_migration_exposes_one_locked_service_role_transaction():
    source = QA_MIGRATION.read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION public.apply_post_qa_decision" in source
    assert "FOR UPDATE" in source
    assert "SET statement_timeout = '8s'" in source
    assert "target_batch_state := 'S7_PUBLISH_PLAN'" in source
    assert "FROM PUBLIC, anon, authenticated" in source
    assert "TO service_role" in source
    assert "NOTIFY pgrst, 'reload schema'" in source


@pytest.mark.skipif(
    not CONTAINER,
    reason="Set SEMANTIC_UGC_POSTGRES_CONTAINER to run PostgreSQL migration integration.",
)
def test_qa_delivery_rpc_is_idempotent_and_reconciles_stale_semantic_state():
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
            """,
        )
        _psql(DATABASE, BASE_MIGRATION.read_text(encoding="utf-8"))
        _psql(
            DATABASE,
            """
            ALTER TABLE public.batches
              ADD COLUMN creation_mode TEXT NOT NULL DEFAULT 'automated';
            ALTER TABLE public.posts
              ADD COLUMN identity_gate_result JSONB;
            ALTER TABLE public.posts DROP CONSTRAINT posts_video_status_check;
            """,
        )
        _psql(DATABASE, QA_MIGRATION.read_text(encoding="utf-8"))
        _psql(DATABASE, QA_MIGRATION.read_text(encoding="utf-8"))
        _psql(
            DATABASE,
            f"""
            INSERT INTO public.batches (id, brand, state, creation_mode)
            VALUES ('{BATCH_ID}', 'QA delivery', 'S4_SCRIPTED', 'semantic_ugc');
            INSERT INTO public.posts (
              id, batch_id, post_type, seed_data, video_status, video_metadata,
              identity_gate_result
            ) VALUES (
              '{POST_ID}',
              '{BATCH_ID}',
              'value',
              '{{"script_review_status":"approved"}}'::JSONB,
              'caption_completed',
              '{{"actor_identity_source":"actor_identity_scene_reference_set"}}'::JSONB,
              '{{"status":"manual_required","gate_type":"manual","details":{{}}}}'::JSONB
            );
            INSERT INTO public.posts (
              id, batch_id, post_type, seed_data
            ) VALUES (
              '{REMOVED_POST_ID}',
              '{BATCH_ID}',
              'value',
              '{{"script_review_status":"removed","video_excluded":true}}'::JSONB
            );
            """,
        )

        first = _psql(
            DATABASE,
            "SET ROLE service_role; "
            f"SELECT public.apply_post_qa_decision('{POST_ID}', TRUE, 'looks good');",
        )
        first_result = json.loads(first.stdout.strip())
        assert first_result["ok"] is True
        assert first_result["batch_state"] == "S7_PUBLISH_PLAN"
        assert first_result["batch_advanced"] is True

        persisted = _psql(
            DATABASE,
            f"""
            SELECT batch.state || ':' || post.qa_pass || ':' || post.qa_notes || ':' ||
                   (post.seed_data ->> 'video_review_status') || ':' ||
                   (post.identity_gate_result ->> 'status')
            FROM public.batches AS batch
            JOIN public.posts AS post ON post.batch_id = batch.id
            WHERE post.id = '{POST_ID}';
            """,
        )
        assert persisted.stdout.strip() == "S7_PUBLISH_PLAN:true:looks good:approved:passed"

        repeated = _psql(
            DATABASE,
            "SET ROLE service_role; "
            f"SELECT public.apply_post_qa_decision('{POST_ID}', TRUE, 'looks good');",
        )
        repeated_result = json.loads(repeated.stdout.strip())
        assert repeated_result["ok"] is True
        assert repeated_result["batch_state"] == "S7_PUBLISH_PLAN"

        denied = _psql(
            DATABASE,
            "SET ROLE authenticated; "
            f"SELECT public.apply_post_qa_decision('{POST_ID}', TRUE, NULL);",
            check=False,
        )
        assert denied.returncode != 0
        assert "permission denied" in denied.stderr.lower()
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};", check=False)
