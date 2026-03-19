import os
import json

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "service-key")
os.environ.setdefault("SUPABASE_KEY", "service-key")
os.environ.setdefault("GOOGLE_AI_API_KEY", "test-google-key")
os.environ.setdefault("CLOUDFLARE_R2_ACCOUNT_ID", "account-id")
os.environ.setdefault("CLOUDFLARE_R2_ACCESS_KEY_ID", "access-key")
os.environ.setdefault("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "secret-key")
os.environ.setdefault("CLOUDFLARE_R2_BUCKET_NAME", "bucket-name")
os.environ.setdefault("CLOUDFLARE_R2_PUBLIC_BASE_URL", "https://cdn.example.com")
os.environ.setdefault("CRON_SECRET", "cron-secret")

import workers.video_poller as video_poller
from app.adapters.veo_client import VeoRateLimitError


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name, db):
        self.table_name = table_name
        self.db = db
        self.selected_fields = None
        self.filters = []
        self.update_payload = None

    def select(self, fields):
        self.selected_fields = [field.strip() for field in fields.split(",")]
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def update(self, payload):
        self.update_payload = payload
        return self

    def execute(self):
        rows = [row.copy() for row in self.db[self.table_name]]
        for field, value in self.filters:
            rows = [row for row in rows if row.get(field) == value]

        if self.update_payload is not None:
            for row in self.db[self.table_name]:
                if all(row.get(field) == value for field, value in self.filters):
                    row.update(self.update_payload)
            rows = [row.copy() for row in self.db[self.table_name] if all(row.get(field) == value for field, value in self.filters)]

        if self.selected_fields is not None:
            rows = [
                {field: row.get(field) for field in self.selected_fields}
                for row in rows
            ]

        return _FakeResponse(rows)


class _FakeSupabaseClient:
    def __init__(self, db):
        self.db = db

    def table(self, table_name):
        return _FakeQuery(table_name, self.db)


class _FakeSupabase:
    def __init__(self, db):
        self.client = _FakeSupabaseClient(db)


def test_reconcile_batches_ready_for_qa_heals_stuck_completed_batch(monkeypatch):
    db = {
        "batches": [
            {"id": "batch-1", "state": "S5_PROMPTS_BUILT"},
        ],
        "posts": [
            {
                "id": "post-1",
                "batch_id": "batch-1",
                "video_status": "completed",
                "seed_data": json.dumps({"script_review_status": "approved"}),
            },
            {
                "id": "post-2",
                "batch_id": "batch-1",
                "video_status": "pending",
                "seed_data": json.dumps({"script_review_status": "removed", "video_excluded": True}),
            },
        ],
    }

    monkeypatch.setattr(video_poller, "get_supabase", lambda: _FakeSupabase(db))

    video_poller._reconcile_batches_ready_for_qa()

    assert db["batches"][0]["state"] == "S6_QA"




def test_parse_mp4_duration_seconds_handles_mvhd_version_0_layout():
    # Real Google MP4 mvhd v0 layout with timescale=1000 and duration=25048.
    video_bytes = bytes.fromhex(
        "0000006c6d6f6f76"
        "6d766864000000000000000000000000000003e8000061d80001000001000000"
    )

    assert video_poller._parse_mp4_duration_seconds(video_bytes) == 25.048


def test_veo_extension_rate_limit_keeps_post_pollable(monkeypatch):
    db = {
        "posts": [
            {
                "id": "post-1",
                "video_status": "extended_submitted",
                "video_metadata": {
                    "video_pipeline_route": "veo_extended",
                    "requested_aspect_ratio": "9:16",
                    "provider_target_seconds": 32,
                    "veo_base_seconds": 4,
                    "veo_extension_seconds": 7,
                    "generated_seconds": 0,
                    "operation_ids": ["op-base"],
                    "veo_extension_hops_completed": 0,
                },
                "video_format": "9:16",
            }
        ]
    }

    class _FakeVeoClient:
        def submit_video_extension(self, **kwargs):
            raise VeoRateLimitError("Veo extension rate limited", retry_after_seconds=65)

    monkeypatch.setattr(video_poller, "get_supabase", lambda: _FakeSupabase(db))
    monkeypatch.setattr(video_poller, "get_veo_client", lambda: _FakeVeoClient())
    monkeypatch.setattr(video_poller.time, "time", lambda: 1000.0)

    video_poller._handle_veo_extended_video(
        post=db["posts"][0],
        operation_id="op-base",
        correlation_id="corr",
        video_uri="https://example.com/video.mp4",
        video_data={"video_uri": "https://example.com/video.mp4"},
        existing_metadata=db["posts"][0]["video_metadata"],
    )

    updated_post = db["posts"][0]
    updated_metadata = updated_post["video_metadata"]
    assert updated_post["video_status"] == "extended_processing"
    assert updated_metadata["chain_status"] == "rate_limited"
    assert updated_metadata["next_retry_at"] == 1065
    assert updated_metadata["operation_ids"] == ["op-base"]


def test_veo_extended_chain_submits_next_sentence_not_previous_one(monkeypatch):
    db = {
        "posts": [
            {
                "id": "post-1",
                "seed_data": {
                    "script": "Erster Satz. Zweiter Satz. Dritter Satz."
                },
                "video_status": "extended_submitted",
                "video_metadata": {
                    "video_pipeline_route": "veo_extended",
                    "requested_aspect_ratio": "9:16",
                    "provider_target_seconds": 32,
                    "veo_base_seconds": 4,
                    "veo_extension_seconds": 7,
                    "generated_seconds": 0,
                    "operation_ids": ["op-base"],
                    "veo_extension_hops_completed": 0,
                    "veo_current_segment_index": 0,
                },
                "video_format": "9:16",
            }
        ]
    }

    class _FakeVeoClient:
        def __init__(self):
            self.prompt = None

        def submit_video_extension(self, **kwargs):
            self.prompt = kwargs["prompt"]
            return {"operation_id": "op-ext-1"}

    fake_veo_client = _FakeVeoClient()
    monkeypatch.setattr(video_poller, "get_supabase", lambda: _FakeSupabase(db))
    monkeypatch.setattr(video_poller, "get_veo_client", lambda: fake_veo_client)

    video_poller._handle_veo_extended_video(
        post=db["posts"][0],
        operation_id="op-base",
        correlation_id="corr",
        video_uri="https://example.com/video.mp4",
        video_data={"video_uri": "https://example.com/video.mp4"},
        existing_metadata=db["posts"][0]["video_metadata"],
    )

    updated_post = db["posts"][0]
    updated_metadata = updated_post["video_metadata"]
    assert "Zweiter Satz." in fake_veo_client.prompt
    assert "Erster Satz." not in fake_veo_client.prompt
    assert updated_post["video_status"] == "extended_submitted"
    assert updated_post["video_operation_id"] == "op-ext-1"
    assert updated_metadata["veo_current_segment_index"] == 1
