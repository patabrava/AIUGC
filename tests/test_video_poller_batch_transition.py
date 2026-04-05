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

    def in_(self, field, values):
        self.filters.append((field, set(values)))
        return self

    def update(self, payload):
        self.update_payload = payload
        return self

    def execute(self):
        rows = [row.copy() for row in self.db[self.table_name]]
        for field, value in self.filters:
            if isinstance(value, set):
                rows = [row for row in rows if row.get(field) in value]
            else:
                rows = [row for row in rows if row.get(field) == value]

        if self.update_payload is not None:
            for row in self.db[self.table_name]:
                if all((row.get(field) in value if isinstance(value, set) else row.get(field) == value) for field, value in self.filters):
                    row.update(self.update_payload)
            rows = [
                row.copy()
                for row in self.db[self.table_name]
                if all((row.get(field) in value if isinstance(value, set) else row.get(field) == value) for field, value in self.filters)
            ]

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
                "video_status": "caption_completed",
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
    monkeypatch.setattr(
        video_poller,
        "reconcile_batch_video_pipeline_state",
        lambda *, batch_id, correlation_id, supabase_client=None: db["batches"][0].__setitem__("state", "S6_QA"),
    )

    video_poller._reconcile_batches_ready_for_qa()

    assert db["batches"][0]["state"] == "S6_QA"
