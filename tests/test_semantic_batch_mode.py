from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from starlette.datastructures import FormData

from app.features.batches import queries as batch_queries
from app.features.batches.schemas import BatchDetailResponse, BatchResponse, CreateBatchRequest
from app.core.errors import ValidationError as FlowForgeValidationError
from app.features.characters.actor_identity import (
    CHARACTER_CONSISTENCY_MODES,
    is_manual_creation_mode,
    is_character_consistency_mode,
    is_semantic_ugc_mode,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260713000000_semantic_ugc_production.sql"
MANUAL_MODE_MIGRATION = ROOT / "supabase/migrations/20260714000000_manual_semantic_ugc_mode.sql"
QA_RESUME_MIGRATION = ROOT / "supabase/migrations/20260715000200_semantic_video_qa_resume.sql"
VISUAL_REMEDIATION_MIGRATION = ROOT / "supabase/migrations/20260715000300_semantic_video_visual_remediation.sql"
QA_STAGE_RESUME_MIGRATION = ROOT / "supabase/migrations/20260715000400_semantic_video_qa_stage_resume.sql"
QA_PRIOR_ATTEMPT_REUSE_MIGRATION = (
    ROOT / "supabase/migrations/20260715000500_semantic_video_prior_attempt_reuse.sql"
)
CANDIDATE_RECLAIM_MIGRATION = (
    ROOT / "supabase/migrations/20260715000600_semantic_video_candidate_reclaim.sql"
)
RECOVERY_COALESCE_FIX_MIGRATION = (
    ROOT / "supabase/migrations/20260715000700_semantic_video_recovery_coalesce_fix.sql"
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
        target_length_tier=50,
        target_duration_seconds=50,
    )

    assert payload.target_duration_seconds == 50
    assert payload.target_length_tier is None


def test_manual_semantic_batch_uses_manual_drafts_and_numeric_duration():
    payload = CreateBatchRequest.model_validate(
        {
            "brand": "AYRA",
            "creation_mode": "manual_semantic_ugc",
            "manual_post_count": 2,
            "target_length_tier": 32,
            "target_duration_seconds": 50,
        }
    )

    assert payload.creation_mode == "manual_semantic_ugc"
    assert payload.manual_post_count == 2
    assert payload.post_type_counts is None
    assert payload.target_duration_seconds == 50
    assert payload.target_length_tier is None


@pytest.mark.parametrize(
    "payload",
    [
        {"target_duration_seconds": 50},
        {"manual_post_count": 2},
        {"manual_post_count": 2, "target_duration_seconds": 7},
        {"manual_post_count": 2, "target_duration_seconds": 61},
    ],
)
def test_manual_semantic_batch_requires_manual_count_and_valid_dynamic_duration(payload):
    with pytest.raises(ValidationError):
        CreateBatchRequest.model_validate(
            {
                "brand": "AYRA",
                "creation_mode": "manual_semantic_ugc",
                **payload,
            }
        )


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
    assert "manual_semantic_ugc" not in CHARACTER_CONSISTENCY_MODES
    assert is_character_consistency_mode("semantic_ugc") is False
    assert is_character_consistency_mode("manual_semantic_ugc") is False
    assert is_semantic_ugc_mode("semantic_ugc") is True
    assert is_semantic_ugc_mode("manual_semantic_ugc") is True
    assert is_manual_creation_mode("semantic_ugc") is False
    assert is_manual_creation_mode("manual_semantic_ugc") is True


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
    monkeypatch.setattr(
        batch_queries,
        "get_active_actor_identity",
        lambda: SimpleNamespace(
            id="actor-semantic",
            name="Semantic Actor",
            character_description="Immutable actor description.",
            is_active=True,
            training_images=[
                "https://cdn.example.com/actor-a.png",
                "https://cdn.example.com/actor-b.png",
            ],
        ),
    )

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


def test_create_batch_persists_manual_semantic_duration_and_route(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        batch_queries,
        "get_active_actor_identity",
        lambda: SimpleNamespace(
            id="actor-semantic",
            name="Semantic Actor",
            character_description="Immutable actor description.",
            is_active=True,
            training_images=[
                "https://cdn.example.com/actor-a.png",
                "https://cdn.example.com/actor-b.png",
            ],
        ),
    )

    def fake_insert(payload, legacy_payload=None):
        captured["payload"] = payload
        captured["legacy_payload"] = legacy_payload
        return {"id": "batch-manual-semantic", **payload}

    monkeypatch.setattr(batch_queries, "_insert_batch_row", fake_insert)

    created = batch_queries.create_batch(
        brand="AYRA",
        post_type_counts={},
        target_length_tier=None,
        target_duration_seconds=50,
        creation_mode="manual_semantic_ugc",
        manual_post_count=2,
    )

    assert captured["payload"]["target_length_tier"] is None
    assert captured["payload"]["target_duration_seconds"] == 50
    assert captured["payload"]["video_pipeline_route"] == "semantic_ugc"
    assert captured["payload"]["manual_post_count"] == 2
    assert captured["legacy_payload"] is None
    assert created["creation_mode"] == "manual_semantic_ugc"


@pytest.mark.parametrize(
    "training_images",
    [None, [], ["https://cdn.example.com/actor-a.png"]],
)
def test_semantic_batch_requires_active_actor_with_two_usable_reference_images(monkeypatch, training_images):
    actor = None
    if training_images is not None:
        actor = SimpleNamespace(
            id="actor-1",
            name="Actor One",
            is_active=True,
            training_images=training_images,
        )
    monkeypatch.setattr(batch_queries, "get_active_actor_identity", lambda: actor)
    monkeypatch.setattr(
        batch_queries,
        "_insert_batch_row",
        lambda payload, legacy_payload=None: {"id": "unexpected", **payload},
    )

    with pytest.raises(FlowForgeValidationError) as exc_info:
        batch_queries.create_batch(
            brand="AYRA",
            post_type_counts=_post_counts(),
            target_length_tier=None,
            target_duration_seconds=50,
            creation_mode="semantic_ugc",
        )

    assert exc_info.value.status_code == 422


def test_semantic_batch_persists_active_actor_with_exactly_two_ordered_images_without_description_or_lora(monkeypatch):
    captured = {}
    actor = SimpleNamespace(
        id="actor-semantic",
        name="Semantic Actor",
        character_description=None,
        is_active=True,
        training_images=[
            " https://cdn.example.com/actor-a.png ",
            "https://cdn.example.com/actor-b.png",
            "https://cdn.example.com/actor-c.png",
        ],
    )
    monkeypatch.setattr(batch_queries, "get_active_actor_identity", lambda: actor)

    def fake_insert(payload, legacy_payload=None):
        captured.update(payload)
        return {"id": "batch-semantic", **payload}

    monkeypatch.setattr(batch_queries, "_insert_batch_row", fake_insert)

    batch_queries.create_batch(
        brand="AYRA",
        post_type_counts=_post_counts(),
        target_length_tier=None,
        target_duration_seconds=50,
        creation_mode="semantic_ugc",
    )

    assert captured["actor_identity_id"] == "actor-semantic"
    assert captured["actor_identity_snapshot"] == {
        "actor_identity_id": "actor-semantic",
        "name": "Semantic Actor",
        "reference_image_urls": [
            "https://cdn.example.com/actor-a.png",
            "https://cdn.example.com/actor-b.png",
        ],
    }


@pytest.mark.parametrize("creation_mode", ["semantic_ugc", "manual_semantic_ugc"])
def test_duplicate_batch_copies_semantic_duration_and_route(monkeypatch, creation_mode):
    calls = []
    actor_snapshot = {
        "actor_identity_id": "actor-original",
        "name": "Original Actor",
        "reference_image_urls": [
            "https://cdn.example.com/original-a.png",
            "https://cdn.example.com/original-b.png",
        ],
    }
    monkeypatch.setattr(
        batch_queries,
        "get_batch_by_id",
        lambda _batch_id: _batch_row(
            creation_mode=creation_mode,
            manual_post_count=2 if creation_mode == "manual_semantic_ugc" else None,
            actor_identity_id="actor-original",
            actor_identity_snapshot=actor_snapshot,
        ),
    )
    monkeypatch.setattr(
        batch_queries,
        "create_batch",
        lambda *args, **kwargs: calls.append((args, kwargs)) or _batch_row(id="batch-copy"),
    )

    duplicated = batch_queries.duplicate_batch("batch-semantic", "AYRA Copy")

    assert duplicated["id"] == "batch-copy"
    assert calls[0][1]["creation_mode"] == creation_mode
    assert calls[0][1]["target_duration_seconds"] == 50
    assert calls[0][1]["semantic_actor_identity_id"] == "actor-original"
    assert calls[0][1]["semantic_actor_identity_snapshot"] == actor_snapshot
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
    discovery_calls = []
    monkeypatch.setattr(
        batch_handlers,
        "start_seeding_interaction",
        lambda **kwargs: discovery_calls.append(("start", kwargs)),
    )
    monkeypatch.setattr(
        batch_handlers,
        "schedule_batch_discovery",
        lambda *args, **kwargs: discovery_calls.append(("schedule", args, kwargs)),
    )

    response = await batch_handlers.create_batch_endpoint(FakeRequest())

    assert captured["target_length_tier"] is None
    assert captured["target_duration_seconds"] == 50
    assert response.data.target_duration_seconds == 50
    assert [call[0] for call in discovery_calls] == ["start", "schedule"]
    assert discovery_calls[1][1:] == (("batch-semantic",), {"reason": "batch_create"})


@pytest.mark.anyio
async def test_manual_semantic_form_creates_drafts_and_skips_discovery(monkeypatch):
    _enable_test_environment()
    from app.features.batches import handlers as batch_handlers

    captured = {}
    draft_calls = []
    discovery_calls = []

    class FakeRequest:
        headers = {"content-type": "application/json"}

        async def json(self):
            return {
                "brand": "AYRA",
                "creation_mode": "manual_semantic_ugc",
                "manual_post_count": 2,
                "target_duration_seconds": 50,
            }

    def fake_create_batch(**kwargs):
        captured.update(kwargs)
        return _batch_row(**{
            **kwargs,
            "id": "batch-manual-semantic",
            "state": "S1_SETUP",
        })

    monkeypatch.setattr(batch_handlers, "create_batch", fake_create_batch)
    monkeypatch.setattr(
        batch_handlers,
        "create_manual_draft_posts",
        lambda batch_id, manual_post_count, target_length_tier: draft_calls.append(
            (batch_id, manual_post_count, target_length_tier)
        ) or [],
    )
    monkeypatch.setattr(
        batch_handlers,
        "update_batch_state",
        lambda batch_id, state: _batch_row(
            id=batch_id,
            state=state.value,
            creation_mode="manual_semantic_ugc",
            post_type_counts={},
            manual_post_count=2,
        ),
    )
    monkeypatch.setattr(
        batch_handlers,
        "start_seeding_interaction",
        lambda **kwargs: discovery_calls.append(("start", kwargs)),
    )
    monkeypatch.setattr(
        batch_handlers,
        "schedule_batch_discovery",
        lambda *args, **kwargs: discovery_calls.append(("schedule", args, kwargs)),
    )

    response = await batch_handlers.create_batch_endpoint(FakeRequest())

    assert captured["target_length_tier"] is None
    assert captured["target_duration_seconds"] == 50
    assert captured["creation_mode"] == "manual_semantic_ugc"
    assert draft_calls == [("batch-manual-semantic", 2, 8)]
    assert discovery_calls == []
    assert response.data.state.value == "S2_SEEDED"


@pytest.mark.anyio
async def test_semantic_status_recovery_schedules_semantic_discovery(monkeypatch):
    _enable_test_environment()
    from app.features.batches import handlers as batch_handlers

    discovery_calls = []
    monkeypatch.setattr(batch_handlers, "get_batch_by_id", lambda _batch_id: _batch_row())
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_posts_summary",
        lambda _batch_id: {"posts_count": 0, "posts_by_state": {}},
    )
    monkeypatch.setattr(batch_handlers, "get_seeding_progress", lambda _batch_id: None)
    monkeypatch.setattr(batch_handlers, "_batch_has_manual_drafts", lambda _batch: False)
    monkeypatch.setattr(batch_handlers, "is_batch_discovery_active", lambda _batch_id: False)
    monkeypatch.setattr(
        batch_handlers,
        "start_seeding_interaction",
        lambda **kwargs: discovery_calls.append(("start", kwargs)),
    )
    monkeypatch.setattr(
        batch_handlers,
        "schedule_batch_discovery",
        lambda *args, **kwargs: discovery_calls.append(("schedule", args, kwargs)),
    )

    response = await batch_handlers.get_batch_status("batch-semantic")

    assert response.data["state"] == "S1_SETUP"
    assert [call[0] for call in discovery_calls] == ["start", "schedule"]
    assert discovery_calls[1][1:] == (("batch-semantic",), {"reason": "status_recovery"})


@pytest.mark.anyio
async def test_semantic_status_recovery_schedules_partial_discovery(monkeypatch):
    _enable_test_environment()
    from app.features.batches import handlers as batch_handlers

    discovery_calls = []
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_by_id",
        lambda _batch_id: _batch_row(
            post_type_counts={"value": 2, "lifestyle": 0, "product": 0}
        ),
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_posts_summary",
        lambda _batch_id: {"posts_count": 1, "posts_by_state": {"value": 1}},
    )
    monkeypatch.setattr(batch_handlers, "get_seeding_progress", lambda _batch_id: None)
    monkeypatch.setattr(batch_handlers, "_batch_has_manual_drafts", lambda _batch: False)
    monkeypatch.setattr(batch_handlers, "is_batch_discovery_active", lambda _batch_id: False)
    monkeypatch.setattr(
        batch_handlers,
        "start_seeding_interaction",
        lambda **kwargs: discovery_calls.append(("start", kwargs)),
    )
    monkeypatch.setattr(
        batch_handlers,
        "schedule_batch_discovery",
        lambda *args, **kwargs: discovery_calls.append(("schedule", args, kwargs)),
    )

    await batch_handlers.get_batch_status("batch-semantic")

    assert [call[0] for call in discovery_calls] == ["start", "schedule"]
    assert discovery_calls[1][1:] == (
        ("batch-semantic",),
        {"reason": "status_recovery"},
    )


@pytest.mark.anyio
@pytest.mark.parametrize("duration", [None, "not-a-number", "7", "61"])
async def test_semantic_form_validation_raises_project_422_error(monkeypatch, duration):
    _enable_test_environment()
    from app.features.batches import handlers as batch_handlers

    class FakeRequest:
        headers = {"content-type": "application/x-www-form-urlencoded"}

        async def form(self):
            values = [
                ("brand", "AYRA"),
                ("creation_mode", "semantic_ugc"),
                ("post_type_counts.value", "1"),
                ("post_type_counts.lifestyle", "0"),
                ("post_type_counts.product", "0"),
            ]
            if duration is not None:
                values.append(("target_duration_seconds", duration))
            return FormData(values)

    with pytest.raises(FlowForgeValidationError) as exc_info:
        await batch_handlers.create_batch_endpoint(FakeRequest())

    assert exc_info.value.status_code == 422
    assert exc_info.value.code.value == "validation_error"


@pytest.mark.anyio
@pytest.mark.parametrize("duration", [None, "not-a-number", 7, 61])
async def test_semantic_json_validation_raises_project_422_error(duration):
    _enable_test_environment()
    from app.features.batches import handlers as batch_handlers

    class FakeRequest:
        headers = {"content-type": "application/json"}

        async def json(self):
            data = {
                "brand": "AYRA",
                "creation_mode": "semantic_ugc",
                "post_type_counts": _post_counts(),
            }
            if duration is not None:
                data["target_duration_seconds"] = duration
            return data

    with pytest.raises(FlowForgeValidationError) as exc_info:
        await batch_handlers.create_batch_endpoint(FakeRequest())

    assert exc_info.value.status_code == 422
    assert exc_info.value.code.value == "validation_error"


@pytest.mark.anyio
async def test_malformed_json_keeps_explicit_bad_request_status():
    _enable_test_environment()
    from fastapi import HTTPException

    from app.features.batches import handlers as batch_handlers

    class FakeRequest:
        headers = {"content-type": "application/json"}

        async def json(self):
            raise json.JSONDecodeError("Expecting value", "", 0)

    with pytest.raises(HTTPException) as exc_info:
        await batch_handlers.create_batch_endpoint(FakeRequest())

    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_semantic_validation_is_rendered_as_project_error_envelope():
    _enable_test_environment()
    from starlette.requests import Request

    from app.main import flowforge_exception_handler

    response = await flowforge_exception_handler(
        Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/batches",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("testclient", 123),
            }
        ),
        FlowForgeValidationError("Invalid batch creation request."),
    )

    assert response.status_code == 422
    payload = json.loads(response.body)
    assert payload["ok"] is False
    assert payload["status"] == 422
    assert payload["code"] == "validation_error"


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

    semantic_option = (
        '<option value="semantic_ugc" data-semantic-ugc-mode-option>'
        "Semantic UGC - Veo 3.1</option>"
    )
    assert semantic_option in source
    manual_semantic_option = (
        '<option value="manual_semantic_ugc" data-semantic-ugc-mode-option>'
        "Manual Semantic UGC - Veo 3.1</option>"
    )
    assert manual_semantic_option in source
    assert source.index(semantic_option) < source.index(manual_semantic_option)
    assert source.index(semantic_option) < source.index(
        '<option value="manual_character_consistency">Manual Character Consistency</option>'
    )
    assert 'data-semantic-ugc-mode-option' in source
    assert 'data-semantic-ugc-duration-panel' in source
    assert 'Recommended for longer AIUGC videos' in source
    assert 'name="target_duration_seconds"' in source
    assert 'min="8"' in source
    assert 'max="{{ semantic_ugc_max_duration_seconds | default(60) }}"' in source
    assert 'aria-describedby="semantic-duration-help"' in source
    assert "targetDurationSeconds: {{ semantic_ugc_default_duration_seconds" in source
    assert "{% for seconds in semantic_ugc_duration_presets %}" in source
    assert 'data-duration-preset="{{ seconds }}"' in source
    assert "const semanticModes = ['semantic_ugc', 'manual_semantic_ugc']" in source
    assert "semanticModes.includes(creationMode)" in source
    assert "manual_semantic_ugc" in source
    assert "shot plan" in source.lower()
    assert "approval" in source.lower()


def test_semantic_duration_ui_config_never_exceeds_configured_maximum(monkeypatch):
    _enable_test_environment()
    from app.features.batches import handlers as batch_handlers

    monkeypatch.setenv("SEMANTIC_UGC_MAX_DURATION_SECONDS", "20")

    config = batch_handlers._semantic_ugc_duration_ui_config()

    assert config == {
        "maximum": 20,
        "default": 20,
        "presets": [8, 16],
    }


def test_semantic_video_run_rejects_post_from_another_batch_by_composite_fk():
    migration = MIGRATION.read_text().lower()

    assert "create unique index if not exists posts_id_batch_id_unique" in migration
    assert "on public.posts (id, batch_id)" in migration
    assert "foreign key (post_id, batch_id)" in migration
    assert "references public.posts(id, batch_id)" in migration


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


def test_manual_semantic_migration_extends_mode_and_duration_authority_contract():
    assert MANUAL_MODE_MIGRATION.exists(), "Manual Semantic UGC migration is missing"
    sql = MANUAL_MODE_MIGRATION.read_text().lower()

    assert "manual_semantic_ugc" in sql
    assert "batches_creation_mode_check" in sql
    assert "batches_duration_authority_check" in sql
    assert "batches_semantic_pipeline_route_check" in sql
    assert "creation_mode in ('semantic_ugc', 'manual_semantic_ugc')" in sql


def test_qa_resume_migration_reuses_only_checksum_verified_completed_takes():
    sql = QA_RESUME_MIGRATION.read_text().lower()

    assert "resume_semantic_video_qa_review" in sql
    assert "retry_approval_required" in sql
    assert "resume_stage is distinct from 'transcript_qa'" in sql
    assert "latest.submission_state not in ('completed', 'qa_failed')" in sql
    assert "latest.raw_artifact_sha256 !~ '^[0-9a-f]{64}$'" in sql
    assert "set submission_state = 'completed'" in sql


def test_qa_stage_resume_migration_reuses_durable_takes_for_acoustic_review():
    sql = QA_STAGE_RESUME_MIGRATION.read_text().lower()

    assert "resume_semantic_video_qa_review" in sql
    assert "resume_stage not in ('transcript_qa', 'acoustic_qa')" in sql
    assert "latest.submission_state not in ('completed', 'qa_failed')" in sql
    assert "latest.raw_artifact_sha256 !~ '^[0-9a-f]{64}$'" in sql
    assert "set submission_state = 'completed'" in sql


def test_prior_attempt_reuse_migration_reprocesses_paid_raw_without_new_submission():
    sql = QA_PRIOR_ATTEMPT_REUSE_MIGRATION.read_text().lower()

    assert "reuse_semantic_video_prior_attempts" in sql
    assert "retry_approval_required" in sql
    assert "coalesce(locked_run.failure_envelope ->> 'stage', '')" in sql
    assert "not in ('transcript_qa', 'acoustic_qa')" in sql
    assert "selected.raw_artifact_sha256 !~ '^[0-9a-f]{64}$'" in sql
    assert "'qa_reuse'" in sql
    assert "set stage = 'transcript_qa'" in sql
    assert "insert into public.semantic_video_approvals" not in sql
    assert "reserved_submission_count" not in sql


def test_candidate_reclaim_migration_only_releases_expired_empty_reservations():
    sql = CANDIDATE_RECLAIM_MIGRATION.read_text().lower()

    assert "reclaim_semantic_video_candidate_reservation" in sql
    assert "candidate_reservation_expires_at > pg_catalog.clock_timestamp()" in sql
    assert "jsonb_array_length" in sql
    assert "candidate_reservation_owner = null" in sql
    assert "candidate_reservation_token = null" in sql
    assert "candidate_reservation_expires_at = null" in sql
    assert "to service_role" in sql


def test_recovery_coalesce_fix_rewrites_both_exact_function_signatures():
    sql = RECOVERY_COALESCE_FIX_MIGRATION.read_text().lower()

    assert "reuse_semantic_video_prior_attempts(uuid,integer,text,jsonb)" in sql
    assert "reclaim_semantic_video_candidate_reservation(uuid,integer)" in sql
    assert "pg_catalog.pg_get_functiondef" in sql
    assert "'pg_catalog.coalesce'" in sql
    assert "'coalesce'" in sql


def test_visual_remediation_migration_replaces_no_paid_request_and_invalidates_visual_cache():
    sql = VISUAL_REMEDIATION_MIGRATION.read_text().lower()

    assert "apply_semantic_video_visual_remediation" in sql
    assert "failure_envelope ->> 'stage' is distinct from 'identity_qa'" in sql
    assert "set raw_artifact_uri = p_remediated_raw_uri" in sql
    assert "- 'contact_sheet' - 'visual_qa'" in sql
    assert "set stage = 'identity_qa'" in sql
    assert "insert into public.semantic_video_takes" not in sql
    assert "reserved_submission_count" not in sql
