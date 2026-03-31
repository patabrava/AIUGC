"""Regression tests for batch seeding status progress payloads."""

import os
import asyncio
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "test-account")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "test-access")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://example.r2.dev")
os.environ.setdefault("CRON_SECRET", "test-cron-secret")

from fastapi.testclient import TestClient

from app.main import app
from app.features.batches import handlers as batch_handlers
from app.features.topics import handlers as topic_handlers


def test_get_batch_status_includes_live_progress(monkeypatch):
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "state": "S1_SETUP",
            "updated_at": "2026-03-16T10:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_posts_summary",
        lambda batch_id: {
            "posts_count": 2,
            "posts_by_state": {"value": 2},
        },
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_seeding_progress",
        lambda batch_id: {
            "stage": "collecting",
            "stage_label": "Collecting distinct topic candidates",
            "detail_message": "Filtering duplicate topics before writing posts.",
            "posts_created": 2,
            "expected_posts": 7,
            "current_post_type": "value",
            "attempt": 2,
            "max_attempts": 5,
            "is_retrying": False,
            "retry_message": None,
            "last_updated_at": "2026-03-16T10:00:05+00:00",
        },
    )

    response = asyncio.run(batch_handlers.get_batch_status("batch-123"))

    assert response.ok is True
    assert response.data["progress"]["stage"] == "collecting"
    assert response.data["progress"]["expected_posts"] == 7
    assert response.data["posts_count"] == 2


def test_get_batch_status_returns_none_when_no_live_progress(monkeypatch):
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "state": "S2_SEEDED",
            "updated_at": "2026-03-16T10:03:00+00:00",
        },
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_posts_summary",
        lambda batch_id: {
            "posts_count": 4,
            "posts_by_state": {"value": 2, "lifestyle": 2},
        },
    )
    monkeypatch.setattr(batch_handlers, "get_seeding_progress", lambda batch_id: None)

    response = asyncio.run(batch_handlers.get_batch_status("batch-456"))

    assert response.ok is True
    assert response.data["state"] == "S2_SEEDED"
    assert response.data["progress"] is None


def test_get_batch_status_requeues_stalled_s1_batch(monkeypatch):
    scheduled = []
    started = []

    monkeypatch.setattr(
        batch_handlers,
        "get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "brand": "Recover",
            "state": "S1_SETUP",
            "updated_at": "2026-03-19T21:00:00+00:00",
            "post_type_counts": {"value": 3, "lifestyle": 4, "product": 0},
        },
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_posts_summary",
        lambda batch_id: {
            "posts_count": 0,
            "posts_by_state": {},
        },
    )
    monkeypatch.setattr(batch_handlers, "get_seeding_progress", lambda batch_id: None)
    monkeypatch.setattr(batch_handlers, "is_batch_discovery_active", lambda batch_id: False)
    monkeypatch.setattr(
        batch_handlers,
        "start_seeding_interaction",
        lambda batch_id, brand, expected_posts: started.append((batch_id, brand, expected_posts)),
    )
    monkeypatch.setattr(
        batch_handlers,
        "schedule_batch_discovery",
        lambda batch_id, reason: scheduled.append((batch_id, reason)) or True,
    )

    response = asyncio.run(batch_handlers.get_batch_status("batch-recover"))

    assert response.ok is True
    assert started == [("batch-recover", "Recover", 7)]
    assert scheduled == [("batch-recover", "status_recovery")]


def test_recover_stalled_batches_only_requeues_empty_s1_batches(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        topic_handlers,
        "list_batches",
        lambda archived=False, limit=25, offset=0: (
            [
                {
                    "id": "resume-me",
                    "state": "S1_SETUP",
                    "created_at": now.isoformat(),
                },
                {
                    "id": "has-posts",
                    "state": "S1_SETUP",
                    "created_at": now.isoformat(),
                },
                {
                    "id": "done",
                    "state": "S2_SEEDED",
                    "created_at": now.isoformat(),
                },
            ],
            3,
        ),
    )
    monkeypatch.setattr(
        topic_handlers,
        "get_posts_by_batch",
        lambda batch_id: [] if batch_id == "resume-me" else [{"id": "post-1"}],
    )
    monkeypatch.setattr(topic_handlers, "get_seeding_progress", lambda batch_id: None)

    scheduled = []
    monkeypatch.setattr(
        topic_handlers,
        "schedule_batch_discovery",
        lambda batch_id, reason: scheduled.append((batch_id, reason)) or True,
    )

    recovered = topic_handlers.recover_stalled_batches(limit=10)

    assert recovered == ["resume-me"]
    assert scheduled == [("resume-me", "startup_recovery")]


def test_recover_stalled_batches_skips_old_backlog_and_caps_recovery(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        topic_handlers,
        "list_batches",
        lambda archived=False, limit=25, offset=0: (
            [
                {
                    "id": "newest",
                    "state": "S1_SETUP",
                    "created_at": now.isoformat(),
                },
                {
                    "id": "second-newest",
                    "state": "S1_SETUP",
                    "created_at": (now - timedelta(minutes=10)).isoformat(),
                },
                {
                    "id": "stale-backlog",
                    "state": "S1_SETUP",
                    "created_at": (now - timedelta(hours=12)).isoformat(),
                },
            ],
            3,
        ),
    )
    monkeypatch.setattr(topic_handlers, "get_posts_by_batch", lambda batch_id: [])
    monkeypatch.setattr(topic_handlers, "get_seeding_progress", lambda batch_id: None)

    scheduled = []
    monkeypatch.setattr(
        topic_handlers,
        "schedule_batch_discovery",
        lambda batch_id, reason: scheduled.append((batch_id, reason)) or True,
    )

    recovered = topic_handlers.recover_stalled_batches(limit=1, max_age_hours=6)

    assert recovered == ["newest"]
    assert scheduled == [("newest", "startup_recovery")]


def test_seeding_interaction_emits_resumable_events():
    topic_handlers.clear_seeding_progress("batch-events")

    started = topic_handlers.start_seeding_interaction(
        batch_id="batch-events",
        brand="Demo",
        expected_posts=4,
    )
    updated = topic_handlers.update_seeding_progress(
        "batch-events",
        stage="researching",
        stage_label="Researching current source-backed topics",
        detail_message="Collecting current value topics from the model.",
        posts_created=1,
        expected_posts=4,
        is_retrying=False,
        retry_message=None,
    )

    all_events = topic_handlers.get_seeding_events("batch-events")
    replay_events = topic_handlers.get_seeding_events(
        "batch-events",
        last_event_id=all_events[0]["event_id"],
    )

    assert started["interaction_id"].startswith("seed_")
    assert updated["interaction_id"] == started["interaction_id"]


def test_build_batch_detail_view_exposes_caption_variants():
    batch_payload = {
        "state": "S7_PUBLISH_PLAN",
        "meta_connection": {},
        "tiktok_connection": {},
        "posts": [
            {
                "id": "post-1",
                "post_type": "value",
                "topic_title": "Beispielthema",
                "publish_caption": "",
                "video_url": None,
                "seed_data": {
                    "description": "Ein generischer Abschnitt, der nicht als Review-Caption dienen soll.",
                    "caption": "Ein noch generischerer Alttext.",
                    "caption_bundle": {
                        "selected_key": "short_paragraph",
                        "selected_body": "Kurze Caption mit genug Kontext #Tag1 #Tag2",
                        "variants": [
                            {"key": "short_paragraph", "body": "Kurze Caption mit genug Kontext #Tag1 #Tag2"},
                            {"key": "medium_bullets", "body": "Laengere Caption mit Struktur\n\n• Punkt 1\n• Punkt 2\n\n#Tag1 #Tag2 #Tag3"},
                            {"key": "long_structured", "body": "Noch laengere Caption mit Struktur\n\nIntro.\n\n1. Punkt\n2. Punkt\n\n#Tag1 #Tag2 #Tag3"},
                        ],
                    }
                },
            }
        ],
    }

    view = batch_handlers._build_batch_detail_view(batch_payload)

    assert view["visible_posts"][0]["review_caption"] == "Kurze Caption mit genug Kontext #Tag1 #Tag2"
    assert view["publish_posts_json"][0]["selectedCaptionKey"] == "short_paragraph"
    assert len(view["publish_posts_json"][0]["captionOptions"]) == 3
    assert view["publish_posts_json"][0]["captionOptions"][1]["label"] == "Medium Bullets"


def test_build_batch_detail_view_polls_while_video_is_submitted():
    batch_payload = {
        "state": "S6_QA",
        "meta_connection": {},
        "tiktok_connection": {},
        "posts": [
            {
                "id": "post-1",
                "post_type": "value",
                "topic_title": "Beispielthema",
                "publish_caption": "",
                "video_url": None,
                "video_status": "submitted",
                "seed_data": {
                    "description": "Ein generischer Abschnitt.",
                    "script_review_status": "approved",
                },
            }
        ],
    }

    view = batch_handlers._build_batch_detail_view(batch_payload)

    assert view["should_poll_videos"] is True


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, db, table_name):
        self.db = db
        self.table_name = table_name
        self.filters = []
        self.payload = None
        self.operation = "select"
        self.fields = None

    def select(self, fields):
        self.fields = fields
        self.operation = "select"
        return self

    def update(self, payload):
        self.payload = payload
        self.operation = "update"
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        rows = self.db[self.table_name]
        matches = [row for row in rows if all(row.get(key) == value for key, value in self.filters)]
        if self.operation == "update":
            for row in matches:
                row.update(self.payload)
            return _FakeResponse([row.copy() for row in matches])
        return _FakeResponse([row.copy() for row in matches])


class _FakeSupabaseClient:
    def __init__(self, db):
        self.db = db

    def table(self, table_name):
        return _FakeQuery(self.db, table_name)


def test_maybe_transition_batch_to_prompts_built_advances_when_all_posts_ready():
    db = {
        "batches": [{"id": "batch-1", "state": "S4_SCRIPTED"}],
        "posts": [
            {"id": "post-1", "batch_id": "batch-1", "video_prompt_json": {"prompt": 1}, "seed_data": {"script_review_status": "approved"}},
            {"id": "post-2", "batch_id": "batch-1", "video_prompt_json": {"prompt": 2}, "seed_data": {"script_review_status": "approved"}},
        ],
    }

    from app.features.posts import handlers as posts_handlers

    posts_handlers._maybe_transition_batch_to_prompts_built(
        batch_id="batch-1",
        supabase_client=_FakeSupabaseClient(db),
        correlation_id="test-corr",
    )

    assert db["batches"][0]["state"] == "S5_PROMPTS_BUILT"


def test_batches_routes_return_full_documents_for_history_restore(monkeypatch):
    client = TestClient(app)

    monkeypatch.setattr(
        batch_handlers,
        "list_batches",
        lambda archived=None, limit=50, offset=0: (
            [
                {
                    "id": "batch-1",
                    "brand": "Demo Batch",
                    "state": "S1_SETUP",
                    "archived": False,
                    "post_type_counts": {"value": 1, "lifestyle": 0, "product": 0},
                    "created_at": "2026-03-25T00:00:00Z",
                    "updated_at": "2026-03-25T00:00:00Z",
                }
            ],
            1,
        ),
    )
    monkeypatch.setattr(
        batch_handlers,
        "get_batch_by_id",
        lambda batch_id: {
            "id": batch_id,
            "brand": "Demo Batch",
            "state": "S1_SETUP",
            "archived": False,
            "created_at": "2026-03-25T00:00:00Z",
            "updated_at": "2026-03-25T00:00:00Z",
            "post_type_counts": {"value": 1, "lifestyle": 0, "product": 0},
        },
    )
    monkeypatch.setattr(batch_handlers, "get_batch_posts_summary", lambda batch_id: {"posts_count": 0, "posts_by_state": {}})
    async def _fake_tiktok_state():
        return {"status": "unavailable"}

    monkeypatch.setattr(batch_handlers, "get_tiktok_publish_state", _fake_tiktok_state)
    monkeypatch.setattr(batch_handlers, "_effective_meta_connection", lambda batch_id, meta_connection: {})
    monkeypatch.setattr(
        "app.features.topics.queries.get_posts_by_batch",
        lambda batch_id: [],
    )

    headers = {
        "HX-Request": "true",
        "HX-History-Restore-Request": "true",
        "Accept": "text/html",
    }

    list_response = client.get("/batches", headers=headers)
    detail_response = client.get("/batches/batch-1", headers=headers)

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert "<html" in list_response.text.lower()
    assert "<html" in detail_response.text.lower()
