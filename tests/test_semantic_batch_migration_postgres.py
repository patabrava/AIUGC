from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260713000000_semantic_ugc_production.sql"
MANUAL_MODE_MIGRATION = ROOT / "supabase/migrations/20260714000000_manual_semantic_ugc_mode.sql"
CONTAINER = os.getenv("SEMANTIC_UGC_POSTGRES_CONTAINER")
DATABASE = "semantic_ugc_migration_test"


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
def test_migration_reapplies_and_rejects_cross_batch_semantic_run():
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
        manual_mode_migration = MANUAL_MODE_MIGRATION.read_text()
        _psql(DATABASE, manual_mode_migration)
        _psql(DATABASE, manual_mode_migration)

        _psql(
            DATABASE,
            """
            INSERT INTO public.batches (
              id, brand, creation_mode, target_length_tier, target_duration_seconds,
              video_pipeline_route
            ) VALUES
              ('00000000-0000-0000-0000-000000000001', 'A', 'semantic_ugc', NULL, 50, 'semantic_ugc'),
              ('00000000-0000-0000-0000-000000000002', 'B', 'semantic_ugc', NULL, 50, 'semantic_ugc'),
              ('00000000-0000-0000-0000-000000000003', 'C', 'manual_semantic_ugc', NULL, 50, 'semantic_ugc');
            INSERT INTO public.posts (id, batch_id) VALUES (
              '00000000-0000-0000-0000-000000000011',
              '00000000-0000-0000-0000-000000000001'
            );
            """,
        )

        mismatch = _psql(
            DATABASE,
            """
            INSERT INTO public.semantic_video_runs (
              post_id, batch_id, requested_duration_seconds, duration_contract,
              duration_contract_hash, script_snapshot, script_hash
            ) VALUES (
              '00000000-0000-0000-0000-000000000011',
              '00000000-0000-0000-0000-000000000002',
              50, '{}'::JSONB, 'duration-hash', '{}'::JSONB, 'script-hash'
            );
            """,
            check=False,
        )
        assert mismatch.returncode != 0
        assert "semantic_video_runs_post_batch_fk" in mismatch.stderr

        _psql(
            DATABASE,
            """
            INSERT INTO public.semantic_video_runs (
              post_id, batch_id, requested_duration_seconds, duration_contract,
              duration_contract_hash, script_snapshot, script_hash
            ) VALUES (
              '00000000-0000-0000-0000-000000000011',
              '00000000-0000-0000-0000-000000000001',
              50, '{}'::JSONB, 'duration-hash', '{}'::JSONB, 'script-hash'
            );
            """,
        )
    finally:
        _psql("postgres", f"DROP DATABASE IF EXISTS {DATABASE};", check=False)
