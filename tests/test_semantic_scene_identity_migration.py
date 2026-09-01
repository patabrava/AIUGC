from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations/20260726000000_semantic_scene_identity_contract.sql"
)
PASSTHROUGH_MIGRATION = (
    ROOT
    / "supabase/migrations/20260825021000_actor_front_passthrough_master.sql"
)


def test_scene_identity_migration_invalidates_legacy_anchors_and_keys_current_contract():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "generation_contract_hash text" in sql
    assert "identity_gate_result jsonb" in sql
    assert "verification_status text not null default 'legacy_unverified'" in sql
    assert "set verification_status = 'legacy_unverified'" in sql
    assert "semantic_actor_scene_plate_anchors_verified_contract_key" in sql
    assert (
        "drop constraint if exists "
        "semantic_actor_scene_plate_anchors_master_mime_type_check"
    ) in sql
    assert "check (master_mime_type in ('image/png', 'image/jpeg'))" in sql
    assert "generation_contract_hash" in sql
    assert "where verification_status = 'verified'" in sql


def test_scene_identity_migration_requires_machine_gate_and_human_attestation():
    from app.features.semantic_videos.visual_contract import (
        SCENE_IDENTITY_EVALUATOR_CONTRACT_VERSION,
    )

    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "p_identity_attestation boolean" in sql
    assert "p_attestation_version text" in sql
    assert "semantic-actor-identity-v1" in sql
    assert SCENE_IDENTITY_EVALUATOR_CONTRACT_VERSION.lower() in sql
    for field in (
        "same_person",
        "facial_geometry_consistent",
        "apparent_age_consistent",
        "hairline_and_hair_consistent",
        "skin_texture_natural",
        "not_beautified_or_stylized",
        "no_face_artifacts",
    ):
        assert f'"{field}": true' in sql
    assert "'identity_attestation', true" in sql
    assert "'approved_at', approval_time" in sql
    assert "selected_mime_type is null" in sql
    assert "selected_mime_type not in ('image/png', 'image/jpeg')" in sql
    assert (
        "uuid, integer, integer, text, boolean, text, text"
        in " ".join(sql.split())
    )


def test_actor_front_passthrough_migration_preserves_exact_source_and_legacy_path():
    sql = PASSTHROUGH_MIGRATION.read_text(encoding="utf-8").lower()

    assert "approve_semantic_video_generated_master_v1" in sql
    assert "derivation_mode is distinct from 'actor_front_passthrough'" in sql
    assert "actor-front-passthrough-v1" in sql
    assert "actor_front_byte_identity" in sql
    for field in ("storage_uri", "mime_type", "byte_length"):
        assert f"selected_candidate ->> '{field}' is distinct from actor_front ->> '{field}'" in sql
    assert "selected_hash is distinct from actor_front ->> 'sha256'" in sql
    assert "'claimed_canonical_anchor_id', null" in sql
