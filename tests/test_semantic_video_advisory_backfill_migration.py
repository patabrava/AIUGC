from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "supabase/migrations/20260804000100_semantic_video_superseded_acoustic_advisory_backfill.sql"
)


def test_backfill_clears_only_superseded_completed_acoustic_advisories() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "run.stage = 'completed'" in sql
    assert "delivery_qa_advisory' ->> 'stage' = 'acoustic_qa'" in sql
    assert "'seam_qa' ->> 'passed' = 'true'" in sql
    assert "'acoustic_seam_qa' ->> 'passed' = 'true'" in sql
    assert "'delivery_visual_qa' ->> 'passed' = 'true'" in sql
    assert "run.artifact_manifest - 'qa_advisory'" in sql
    assert "- 'delivery_qa_advisory'" in sql
