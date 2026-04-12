from __future__ import annotations

import os
from copy import deepcopy

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
from app.features.posts import handlers as posts_handlers


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, storage, table_name):
        self.storage = storage
        self.table_name = table_name
        self.filters = []
        self.payload = None
        self.operation = "select"

    def select(self, *_fields):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def execute(self):
        rows = self.storage[self.table_name]
        matches = [row for row in rows if all(row.get(key) == value for key, value in self.filters)]
        if self.operation == "update":
            updated = []
            for row in matches:
                row.update(deepcopy(self.payload))
                updated.append(deepcopy(row))
            return _FakeResponse(updated)
        return _FakeResponse([deepcopy(row) for row in matches])


class _FakeClient:
    def __init__(self, storage):
        self.storage = storage

    def table(self, table_name):
        return _FakeTable(self.storage, table_name)


class _FakeSupabase:
    def __init__(self, storage):
        self.client = _FakeClient(storage)


def test_remove_script_review_marks_post_removed_and_returns_success(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {"script_review_status": "pending", "script": "Hello world"},
                "video_prompt_json": {"existing": True},
                "video_status": "pending",
            }
        ]
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.put("/posts/post-1/script-review", data={"action": "removed"})

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["data"]["script_review_status"] == "removed"
    assert storage["posts"][0]["seed_data"]["script_review_status"] == "removed"
    assert storage["posts"][0]["seed_data"]["video_excluded"] is True
    assert storage["posts"][0]["video_prompt_json"] is None
    assert storage["posts"][0]["video_status"] == "pending"


def test_update_prompt_bootstraps_from_seed_when_prompt_row_missing(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {
                    "script": "Original script sentence.",
                    "script_review_status": "approved",
                },
                "video_prompt_json": None,
            }
        ]
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.patch(
        "/posts/post-1/prompt",
        json={
            "character": "Edited character",
            "style": "Edited style",
            "action": "Edited action",
            "scene": "Edited scene",
            "cinematography": "Edited cinematography",
            "dialogue": "Edited dialogue.",
            "ending": "Edited ending.",
            "audio_block": "Edited audio block.",
            "universal_negatives": "Edited universal negatives.",
            "veo_prompt": "Character:\nEdited character\n\nDialogue:\n\"Edited dialogue.\"",
            "veo_negative_prompt": "Edited veo negatives.",
        },
    )

    assert response.status_code == 200, response.text
    stored_prompt = storage["posts"][0]["video_prompt_json"]
    assert stored_prompt is not None
    assert stored_prompt["character"] == "Edited character"
    assert stored_prompt["audio"]["dialogue"] == "Edited dialogue."
    assert stored_prompt["optimized_prompt"]
    assert stored_prompt["veo_prompt"] == "Character:\nEdited character\n\nDialogue:\n\"Edited dialogue.\""


def test_update_script_accepts_long_edits_within_generated_script_bounds(monkeypatch):
    long_script = " ".join(["Das ist eine sehr lange bearbeitbare Skriptzeile."] * 16)
    assert len(long_script) > 500

    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {"script_review_status": "pending", "script": "Kurzer Ausgangstext."},
                "video_prompt_json": {"existing": True},
                "video_status": "pending",
            }
        ]
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.put("/posts/post-1/script", data={"script_text": long_script})

    assert response.status_code == 200, response.text
    assert storage["posts"][0]["seed_data"]["script"] == long_script
    assert storage["posts"][0]["seed_data"]["script_review_status"] == "pending"
    assert "video_excluded" not in storage["posts"][0]["seed_data"]
    assert storage["posts"][0]["video_prompt_json"] is None


def test_build_prompt_preserves_existing_manual_prompt_edits(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {
                    "script_review_status": "approved",
                    "script": "Original script sentence.",
                },
                "video_prompt_json": {
                    "character": "Edited long character prompt",
                    "style": "Edited style",
                    "action": "Edited action",
                    "scene": "Edited scene",
                    "cinematography": "Edited cinematography",
                    "audio": {
                        "dialogue": "Edited dialogue.",
                        "capture": "Edited audio block.",
                    },
                    "ending_directive": "Edited ending.",
                    "audio_block": "Edited audio block.",
                    "universal_negatives": "Edited negatives.",
                    "veo_prompt": "Character:\nEdited long character prompt",
                    "veo_negative_prompt": "Edited veo negatives.",
                    "optimized_prompt": "Character:\nEdited long character prompt",
                },
            }
        ]
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.post("/posts/post-1/build-prompt")

    assert response.status_code == 200, response.text
    stored_prompt = storage["posts"][0]["video_prompt_json"]
    assert stored_prompt["character"] == "Edited long character prompt"
    assert stored_prompt["veo_prompt"] == "Character:\nEdited long character prompt"
    assert stored_prompt["audio"]["dialogue"] == "Edited dialogue."


def test_update_prompt_rebuilds_veo_prompt_from_structured_fields_when_raw_prompt_unchanged(monkeypatch):
    storage = {
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "seed_data": {
                    "script": "Original script sentence.",
                    "script_review_status": "approved",
                },
                "video_prompt_json": {
                    "character": "Old character",
                    "style": "Old style",
                    "action": "Old action",
                    "scene": "Old scene",
                    "cinematography": "Old cinematography",
                    "audio": {
                        "dialogue": "Old dialogue.",
                        "capture": "Old audio block.",
                    },
                    "ending_directive": "Old ending.",
                    "audio_block": "Old audio block.",
                    "universal_negatives": "Old negatives.",
                    "veo_prompt": "Character:\nOld character\n\nDialogue:\n\"Old dialogue.\"",
                    "veo_negative_prompt": "Old veo negatives.",
                    "optimized_prompt": "Character:\nOld character",
                },
            }
        ]
    }

    monkeypatch.setattr(posts_handlers, "get_supabase", lambda: _FakeSupabase(storage))

    client = TestClient(app, base_url="http://localhost")
    response = client.patch(
        "/posts/post-1/prompt",
        json={
            "character": "Edited long character prompt",
            "style": "Edited style",
            "action": "Edited action",
            "scene": "Edited scene",
            "cinematography": "Edited cinematography",
            "dialogue": "Edited dialogue.",
            "ending": "Edited ending.",
            "audio_block": "Edited audio block.",
            "universal_negatives": "Edited negatives.",
            "veo_prompt": "Character:\nOld character\n\nDialogue:\n\"Old dialogue.\"",
            "veo_negative_prompt": "Edited veo negatives.",
        },
    )

    assert response.status_code == 200, response.text
    stored_prompt = storage["posts"][0]["video_prompt_json"]
    assert stored_prompt["character"] == "Edited long character prompt"
    assert "Edited long character prompt" in stored_prompt["veo_prompt"]
    assert "Edited dialogue." in stored_prompt["veo_prompt"]
