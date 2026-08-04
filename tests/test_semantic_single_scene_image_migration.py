from pathlib import Path


MIGRATION = Path(
    "supabase/migrations/20260804000400_semantic_single_scene_image_jobs.sql"
)


def test_single_scene_image_migration_has_durable_fenced_queue_contract():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS public.semantic_scene_image_jobs" in sql
    assert "WHERE status IN ('queued', 'processing')" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "job.lease_token = p_lease_token" in sql
    assert "CREATE OR REPLACE FUNCTION public.enqueue_semantic_scene_image" in sql
    assert "CREATE OR REPLACE FUNCTION public.claim_semantic_scene_image" in sql
    assert "CREATE OR REPLACE FUNCTION public.finish_semantic_scene_image" in sql
    assert "candidate_count IS DISTINCT FROM 1" in sql
