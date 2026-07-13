from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.datastructures import FormData

from app.features.batches import queries as batch_queries
from app.features.batches.schemas import BatchDetailResponse, BatchResponse, CreateBatchRequest
from app.features.characters.actor_identity import (
    CHARACTER_CONSISTENCY_MODES,
    is_character_consistency_mode,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260713_semantic_ugc_production.sql"


def _post_counts() -> dict[str, int]:
    return {"value": 1, "lifestyle": 0, "product": 0}


def _enable_test_environment() -> None:
    values = {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_KEY": "test-key",
        "SUPABASE_SERVICE_KEY": "test-service-key",
        "GEMINI_API_KEY": "test-google-key",
        "CLOUDFLARE_R2_ACCOUNT_ID": "test-account",
        "CLOUDFLARE_R2_ACCESS_KEY_ID": "test-access",
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY": "test-secret",
        "CLOUDFLARE_R2_BUCKET_NAME": "test-bucket",
        "CLOUDFLARE_R2_PUBLIC_BASE_URL": "https://example.r2.dev",
        "CRON_SECRET": "test-cron-secret",
    }
    for key, value in values.items():
        os.environ.setdefault(key, value)


def _batch_row(**overrides):
    row = {
        "id": "batch-semantic",
        "brand": "AYRA",
        "state": "S1_SETUP",
        "creation_mode": "semantic_ugc",
        "post_type_counts": _post_counts(),
        "manual_post_count": None,
        "target_length_tier": None,
        "target_duration_seconds": 50,
        "video_pipeline_route": "semantic_ugc",
        "created_at": "2026-07-13T00:00:00Z",
        "updated_at": "2026-07-13T00:00:00Z",
        "archived": False,
    }
    row.update(overrides)
    return row


def test_semantic_batch_uses_numeric_duration_only():
    payload = CreateBatchRequest(
        brand="AYRA",
        creation_mode="semantic_ugc",
        post_type_counts=_post_counts(),
        target_length_tier=16,
        target_duration_seconds=50,
    )

    assert payload.target_duration_seconds == 50
    assert payload.target_length_tier is None


@pytest.mark.parametrize("target_duration_seconds", [7, 61, 8.5, True])
def test_semantic_batch_rejects_invalid_duration(target_duration_seconds):
    with pytest.raises(ValidationError):
        CreateBatchRequest.model_validate(
            {
                "brand": "AYRA",
                "creation_mode": "semantic_ugc",
                "post_type_counts": _post_counts(),
                "target_duration_seconds": target_duration_seconds,
            }
        )


def test_semantic_batch_requires_duration_and_post_counts():
    with pytest.raises(ValidationError):
        CreateBatchRequest.model_validate(
            {
                "brand": "AYRA",
                "creation_mode": "semantic_ugc",
                "post_type_counts": _post_counts(),
            }
        )

    with pytest.raises(ValidationError):
        CreateBatchRequest.model_validate(
            {
                "brand": "AYRA",
                "creation_mode": "semantic_ugc",
                "target_duration_seconds": 50,
            }
        )


def test_legacy_batch_cannot_use_semantic_duration_authority():
    with pytest.raises(ValidationError):
        CreateBatchRequest.model_validate(
            {
                "brand": "AYRA",
                "creation_mode": "automated",
                "post_type_counts": _post_counts(),
                "target_length_tier": 16,
                "target_duration_seconds": 50,
            }
        )


def test_semantic_mode_stays_outside_character_consistency_modes():
    assert "semantic_ugc" not in CHARACTER_CONSISTENCY_MODES
    assert is_character_consistency_mode("semantic_ugc") is False


def test_batch_response_models_expose_both_duration_authorities():
    batch = BatchResponse(**_batch_row())
    detail = BatchDetailResponse(
        **_batch_row(),
        posts_count=0,
        posts_by_state={},
        posts=[],
    )

    assert batch.target_length_tier is None
    assert batch.target_duration_seconds == 50
    assert batch.video_pipeline_route == "semantic_ugc"
    assert detail.target_duration_seconds == 50


def test_create_batch_persists_semantic_duration_and_route(monkeypatch):
    captured = {}

    def fake_insert(payload, legacy_payload=None):
        captured["payload"] = payload
        captured["legacy_payload"] = legacy_payload
        return {"id": "batch-semantic", **payload}

    monkeypatch.setattr(batch_queries, "_insert_batch_row", fake_insert)

    created = batch_queries.create_batch(
        brand="AYRA",
        post_type_counts=_post_counts(),
        target_length_tier=None,
        target_duration_seconds=50,
        creation_mode="semantic_ugc",
    )

    assert captured["payload"]["target_length_tier"] is None
    assert captured["payload"]["target_duration_seconds"] == 50
    assert captured["payload"]["video_pipeline_route"] == "semantic_ugc"
    assert captured["legacy_payload"] is None
    assert created["target_duration_seconds"] == 50


def test_duplicate_batch_copies_semantic_duration_and_route(monkeypatch):
    calls = []
    monkeypatch.setattr(batch_queries, "get_batch_by_id", lambda _batch_id: _batch_row())
    monkeypatch.setattr(
        batch_queries,
        "create_batch",
        lambda *args, **kwargs: calls.append((args, kwargs)) or _batch_row(id="batch-copy"),
    )

    duplicated = batch_queries.duplicate_batch("batch-semantic", "AYRA Copy")

    assert duplicated["id"] == "batch-copy"
    assert calls[0][1]["creation_mode"] == "semantic_ugc"
    assert calls[0][1]["target_duration_seconds"] == 50
    assert calls[0][0][2] is None


@pytest.mark.anyio
async def test_semantic_form_parsing_passes_seconds_to_create_query(monkeypatch):
    _enable_test_environment()
    from app.features.batches import handlers as batch_handlers

    captured = {}

    class FakeRequest:
        headers = {"content-type": "application/x-www-form-urlencoded"}

        async def form(self):
            return FormData(
                [
                    ("brand", "AYRA"),
                    ("creation_mode", "semantic_ugc"),
                    ("post_type_counts.value", "1"),
                    ("post_type_counts.lifestyle", "0"),
                    ("post_type_counts.product", "0"),
                    ("target_duration_seconds", "50"),
                ]
            )

    def fake_create_batch(**kwargs):
        captured.update(kwargs)
        return _batch_row(**kwargs)

    monkeypatch.setattr(batch_handlers, "create_batch", fake_create_batch)
    monkeypatch.setattr(batch_handlers, "start_seeding_interaction", lambda **_kwargs: None)
    monkeypatch.setattr(batch_handlers, "schedule_batch_discovery", lambda *_args, **_kwargs: None)

    response = await batch_handlers.create_batch_endpoint(FakeRequest())

    assert captured["target_length_tier"] is None
    assert captured["target_duration_seconds"] == 50
    assert response.data.target_duration_seconds == 50


@pytest.mark.anyio
async def test_batch_status_exposes_semantic_duration_progress(monkeypatch):
    _enable_test_environment()
    from app.features.batches import handlers as batch_handlers

    monkeypatch.setattr(
        batch_handlers,
        "get_batch_by_id",
        lambda _batch_id: _batch_row(state="S2_SEEDED"),
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_posts_summary",
        lambda _batch_id: {"posts_count": 1, "posts_by_state": {"value": 1}},
    )
    monkeypatch.setattr(batch_handlers, "get_seeding_progress", lambda _batch_id: {"stage": "writing_posts"})
    monkeypatch.setattr(batch_handlers, "_batch_has_manual_drafts", lambda _batch: False)

    response = await batch_handlers.get_batch_status("batch-semantic")

    assert response.data["creation_mode"] == "semantic_ugc"
    assert response.data["target_length_tier"] is None
    assert response.data["target_duration_seconds"] == 50
    assert response.data["video_pipeline_route"] == "semantic_ugc"


def test_semantic_batch_form_has_accessible_conditional_duration_controls():
    source = (ROOT / "templates/batches/list.html").read_text()

    assert '<option value="semantic_ugc">Semantic UGC - Veo 3.1</option>' in source
    assert 'name="target_duration_seconds"' in source
    assert 'min="8"' in source
    assert 'max="{{ semantic_ugc_max_duration_seconds | default(60) }}"' in source
    assert 'aria-describedby="semantic-duration-help"' in source
    for seconds in (8, 16, 32, 50):
        assert f'data-duration-preset="{seconds}"' in source
    assert "creationMode === 'semantic_ugc'" in source
    assert "creationMode !== 'semantic_ugc'" in source
    assert "shot plan" in source.lower()
    assert "approval" in source.lower()


def test_semantic_migration_defines_batch_and_run_persistence_contract():
    assert MIGRATION.exists(), "Semantic UGC production migration is missing"
    sql = MIGRATION.read_text().lower()

    assert "target_duration_seconds integer" in sql
    assert "target_duration_seconds is null or target_duration_seconds >= 8" in sql
    assert "semantic_ugc" in sql
    assert "create table if not exists public.semantic_video_runs" in sql
    assert "create table if not exists public.semantic_video_takes" in sql
    assert "create table if not exists public.semantic_video_approvals" in sql
    assert "create unique index if not exists semantic_video_runs_one_active_per_post" in sql
    assert "where stage not in ('completed', 'failed')" in sql
    assert "create or replace function public.claim_semantic_video_run" in sql
    assert "for update skip locked" in sql
    assert "lease_owner" in sql
    assert "lease_expires_at" in sql
